"""Tray app + main window.

The icon sits in the tray (no taskbar button). Clicking it opens the main
window, which shows the current state (Running / Unstable / OUTAGE / Paused),
lets you pause/resume, toggle start-with-Windows, quit, and generate reports
with optional start/end dates:

  * no start date -> since the beginning of the data
  * no end date   -> up to the most recent sample

Closing the window (X) just hides it back to the tray. All visible text is
localized through :mod:`packetlizer.i18n`.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path

from .analytics import humanize_seconds
from .config import STATUS_LABEL, Config, app_home
from .i18n import (
    available_languages,
    language_display_name,
    set_language,
    t,
)
from .monitor import LiveState, Monitor

log = logging.getLogger("packetlizer.app")

_GREEN = (34, 197, 94)
_RED = (220, 38, 38)
_AMBER = (245, 158, 11)
_BLUE = (59, 130, 246)
_GREY = (148, 163, 184)


def _make_icon_image(color):
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((6, 6, 58, 58), fill=color)
    for i, h in enumerate((16, 26, 36)):
        x = 20 + i * 10
        d.rectangle((x, 44 - h, x + 6, 44), fill=(255, 255, 255, 230))
    return img


def _open_path(path: Path) -> None:
    path = Path(path)
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as e:  # pragma: no cover
        log.warning("Could not open %s: %s", path, e)


def _state_color(s: LiveState) -> tuple:
    if s.paused:
        return _BLUE
    if s.total == 0:
        return _GREY
    if s.in_outage:
        return _RED
    if s.consecutive_lost > 0:
        return _AMBER
    return _GREEN


class TrayApp:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.state = LiveState()
        self.monitor = Monitor(cfg, self.state)
        self._mon_thread: threading.Thread | None = None
        self._icon = None
        self._root = None
        self._report_busy = False
        self._shutting_down = False
        # widgets whose text must be refreshed on a language change
        self._retext_map: list = []

    # -- lifecycle --------------------------------------------------
    def start(self) -> int:
        self._mon_thread = threading.Thread(target=self._run_monitor, name="monitor", daemon=True)
        self._mon_thread.start()
        try:
            import tkinter  # noqa: F401
        except Exception as e:  # pragma: no cover - Windows always ships tkinter
            log.warning("tkinter unavailable (%s); running with the tray menu only.", e)
            return self._run_menu_only()
        return self._run_with_window()

    def _run_monitor(self):
        try:
            self.monitor.run()
        except Exception:  # pragma: no cover
            log.exception("Monitor crashed")

    # ------------------------------------------------------------------
    # main mode: tkinter window + pystray icon on a thread
    # ------------------------------------------------------------------
    def _run_with_window(self) -> int:
        import pystray
        import tkinter as tk
        from tkinter import ttk

        self._root = root = tk.Tk()
        root.title("PacketLizer")
        root.geometry("480x680")
        root.minsize(450, 600)
        root.protocol("WM_DELETE_WINDOW", self._hide_window)
        # semi-hidden: no taskbar button, starts withdrawn to the tray
        try:
            root.attributes("-toolwindow", True)
        except tk.TclError:
            pass
        root.withdraw()

        pad = {"padx": 12, "pady": 6}
        header = ttk.Frame(root)
        header.pack(fill="x", **pad)
        self._dot = tk.Canvas(header, width=18, height=18, highlightthickness=0)
        self._dot.pack(side="left")
        self._dot_id = self._dot.create_oval(2, 2, 16, 16, fill="#94a3b8", outline="")
        self._state_lbl = ttk.Label(header, text="", font=("Segoe UI", 13, "bold"))
        self._state_lbl.pack(side="left", padx=8)

        # ---- editable configuration ---------------------------------
        conf = ttk.LabelFrame(root, text="")
        conf.pack(fill="x", **pad)
        conf.columnconfigure(1, weight=1)
        self._lf_config = conf
        self._cfg_vars = {
            "target": tk.StringVar(value=self.cfg.target),
            "interval": tk.StringVar(value=f"{self.cfg.interval_seconds:g}"),
            "timeout": tk.StringVar(value=str(self.cfg.timeout_ms)),
            "omin": tk.StringVar(value=str(self.cfg.outage_min_consecutive)),
            "ret": tk.StringVar(value=str(self.cfg.retention_days)),
        }
        conf_rows = [
            ("win.field.target", "target"),
            ("win.field.interval", "interval"),
            ("win.field.timeout", "timeout"),
            ("win.field.outage_min", "omin"),
            ("win.field.retention", "ret"),
        ]
        self._field_labels: dict[str, object] = {}
        for i, (key, var) in enumerate(conf_rows):
            lbl = ttk.Label(conf, text="")
            lbl.grid(row=i, column=0, sticky="w", padx=8, pady=3)
            self._field_labels[key] = lbl
            ttk.Entry(conf, textvariable=self._cfg_vars[var]).grid(
                row=i, column=1, sticky="ew", padx=8, pady=3)

        lang_row = len(conf_rows)
        self._lang_label = ttk.Label(conf, text="")
        self._lang_label.grid(row=lang_row, column=0, sticky="w", padx=8, pady=3)
        self._lang_codes = available_languages()
        self._lang_combo = ttk.Combobox(conf, state="readonly",
                                        values=[language_display_name(c) for c in self._lang_codes])
        cur_lang = self.cfg.language if self.cfg.language in self._lang_codes else "auto"
        self._lang_combo.current(self._lang_codes.index(cur_lang))
        self._lang_combo.grid(row=lang_row, column=1, sticky="ew", padx=8, pady=3)
        self._lang_combo.bind("<<ComboboxSelected>>", self._on_language_change)

        self._autostart_var = tk.BooleanVar(value=self._autostart_enabled())
        self._autostart_chk = ttk.Checkbutton(conf, text="", variable=self._autostart_var,
                                              command=self._on_toggle_autostart)
        self._autostart_chk.grid(row=lang_row + 1, column=0, columnspan=2, sticky="w", padx=8, pady=(4, 2))

        self._apply_btn = ttk.Button(conf, text="", command=self._on_apply_config)
        self._apply_btn.grid(row=lang_row + 2, column=0, columnspan=2, sticky="ew", padx=8, pady=(6, 3))
        self._cfg_status = ttk.Label(conf, text="", foreground="#6b7280",
                                     wraplength=430, justify="left")
        self._cfg_status.grid(row=lang_row + 3, column=0, columnspan=2, sticky="w", padx=8)

        # ---- live status ------------------------------------------
        info = ttk.LabelFrame(root, text="")
        info.pack(fill="x", **pad)
        self._lf_status = info
        self._info_vars = {k: tk.StringVar(value="-") for k in
                           ("target", "method", "last", "loss", "outages", "uptime")}
        status_rows = [
            ("win.status.target", "target"),
            ("win.status.method", "method"),
            ("win.status.last_sample", "last"),
            ("win.status.loss", "loss"),
            ("win.status.outages", "outages"),
            ("win.status.monitoring_for", "uptime"),
        ]
        self._status_labels: dict[str, object] = {}
        for i, (key, var) in enumerate(status_rows):
            lbl = ttk.Label(info, text="")
            lbl.grid(row=i, column=0, sticky="w", padx=8, pady=2)
            self._status_labels[key] = lbl
            ttk.Label(info, textvariable=self._info_vars[var]).grid(
                row=i, column=1, sticky="w", padx=8, pady=2)

        # ---- report -----------------------------------------------
        rep = ttk.LabelFrame(root, text="")
        rep.pack(fill="x", **pad)
        self._lf_report = rep
        self._since_lbl = ttk.Label(rep, text="")
        self._since_lbl.grid(row=0, column=0, sticky="w", padx=8, pady=3)
        self._since_var = tk.StringVar()
        ttk.Entry(rep, textvariable=self._since_var, width=22).grid(row=0, column=1, padx=8, pady=3)
        self._until_lbl = ttk.Label(rep, text="")
        self._until_lbl.grid(row=1, column=0, sticky="w", padx=8, pady=3)
        self._until_var = tk.StringVar()
        ttk.Entry(rep, textvariable=self._until_var, width=22).grid(row=1, column=1, padx=8, pady=3)
        self._hint_lbl = ttk.Label(rep, text="", foreground="#6b7280")
        self._hint_lbl.grid(row=2, column=0, columnspan=2, sticky="w", padx=8)
        self._report_btn = ttk.Button(rep, text="", command=self._on_report)
        self._report_btn.grid(row=3, column=0, columnspan=2, sticky="ew", padx=8, pady=(6, 4))
        self._report_status = ttk.Label(rep, text="", foreground="#6b7280",
                                        wraplength=440, justify="left")
        self._report_status.grid(row=4, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 4))

        # ---- bottom buttons -------------------------------------
        btns = ttk.Frame(root)
        btns.pack(fill="x", **pad)
        self._pause_btn = ttk.Button(btns, text="", command=self._on_toggle_pause)
        self._pause_btn.pack(side="left")
        self._open_btn = ttk.Button(btns, text="", command=lambda: _open_path(app_home()))
        self._open_btn.pack(side="left", padx=6)
        self._quit_btn = ttk.Button(btns, text="", command=self._on_quit)
        self._quit_btn.pack(side="right")

        self._retext()

        # tray icon on its own thread
        self._icon = pystray.Icon("PacketLizer", _make_icon_image(_GREY), "PacketLizer",
                                  menu=self._build_menu())
        threading.Thread(target=self._icon.run, name="tray", daemon=True).start()

        if getattr(self.cfg, "_created", False):
            # first run: show the window so the user can set the target
            root.after(500, self._do_show)
        else:
            root.after(800, lambda: self._notify(t("notify.running_tray")))

        self._tick()
        root.mainloop()
        return 0

    # -- localization ------------------------------------------------
    def _retext(self) -> None:
        """(Re)apply all static widget text for the current language."""
        r = self._root
        if not r:
            return
        self._lf_config.config(text=t("win.section.config"))
        self._lf_status.config(text=t("win.section.status"))
        self._lf_report.config(text=t("win.section.report"))
        for key, lbl in self._field_labels.items():
            lbl.config(text=t(key) + ":")
        for key, lbl in self._status_labels.items():
            lbl.config(text=t(key) + ":")
        self._lang_label.config(text=t("win.field.language") + ":")
        self._autostart_chk.config(text=t("win.chk.autostart"))
        self._apply_btn.config(text=t("win.btn.save_apply"))
        self._since_lbl.config(text=t("win.field.start_date") + ":")
        self._until_lbl.config(text=t("win.field.end_date") + ":")
        self._hint_lbl.config(text=t("win.hint.date_format"))
        self._report_btn.config(text=t("win.btn.generate_report"))
        self._open_btn.config(text=t("win.btn.open_data_folder"))
        self._quit_btn.config(text=t("win.btn.quit"))
        idx = self._lang_combo.current()
        self._lang_combo.config(values=[language_display_name(c) for c in self._lang_codes])
        if idx >= 0:
            self._lang_combo.current(idx)

    def _on_language_change(self, *_):
        code = self._lang_codes[self._lang_combo.current()]
        self.cfg.language = code
        set_language(code)
        self._retext()
        try:
            self.cfg.save()
        except OSError:
            pass

    # -- window: show / hide ---------------------------------------
    def _show_window(self, *_):
        if self._root:
            self._root.after(0, self._do_show)

    def _do_show(self):
        r = self._root
        r.deiconify()
        r.lift()
        r.attributes("-topmost", True)
        r.after(300, lambda: r.attributes("-topmost", False))
        r.focus_force()

    def _hide_window(self):
        if self._root:
            self._root.withdraw()
            self._notify(t("notify.hidden"))

    def _tick(self):
        if self._shutting_down or not self._root:
            return
        s = self.state
        state_txt = t("state." + s.state_key)
        self._state_lbl.config(text=state_txt)
        col = _state_color(s)
        self._dot.itemconfig(self._dot_id, fill="#%02x%02x%02x" % col)

        last = "-"
        if s.total:
            status = STATUS_LABEL.get(s.last_status, "?")
            rtt = f"{s.last_rtt_ms:.0f} ms" if s.last_rtt_ms is not None else t("win.value.no_response")
            hhmm = datetime.fromtimestamp(s.last_ts).strftime("%H:%M:%S") if s.last_ts else "-"
            last = t("win.value.last_fmt", time=hhmm, status=status, rtt=rtt)
        reason_txt = t("probe." + s.probe_reason) if s.probe_reason else ""
        self._info_vars["target"].set(self.cfg.target)
        self._info_vars["method"].set(
            t("win.value.method_fmt", name=s.probe_name, reason=reason_txt) if reason_txt else s.probe_name)
        self._info_vars["last"].set(last)
        self._info_vars["loss"].set(t("win.value.loss_fmt", pct=s.loss_pct, lost=s.lost, total=s.total))
        self._info_vars["outages"].set(str(s.outages))
        self._info_vars["uptime"].set(humanize_seconds(time.time() - s.started_at))
        self._pause_btn.config(text=t("win.btn.resume") if self.monitor.is_paused else t("win.btn.pause"))
        if self._autostart_var.get() != self._autostart_enabled():
            self._autostart_var.set(self._autostart_enabled())

        try:
            self._icon.icon = _make_icon_image(col)
            self._icon.title = (t("menu.state_fmt", state=state_txt) + "\n"
                                + t("menu.loss_fmt", target=self.cfg.target, pct=s.loss_pct))
            self._icon.menu = self._build_menu()
        except Exception:
            pass
        self._root.after(1500, self._tick)

    # -- tray menu -----------------------------------------------
    def _build_menu(self):
        import pystray
        from pystray import MenuItem as Item

        return pystray.Menu(
            Item(t("menu.open_window"), self._show_window, default=True),
            Item(t("menu.pause_resume"), lambda: self._on_toggle_pause()),
            Item(t("menu.generate_report_all"), lambda: self._on_report(all_data=True)),
            pystray.Menu.SEPARATOR,
            Item(t("menu.quit"), self._on_quit),
        )

    def _notify(self, msg: str, title: str = "PacketLizer"):
        try:
            self._icon.notify(msg, title)
        except Exception:
            log.info("%s: %s", title, msg)

    # -- actions -----------------------------------------------
    def _on_toggle_pause(self, *_):
        paused = self.monitor.toggle_pause()
        self._notify(t("notify.paused") if paused else t("notify.resumed"))

    def _autostart_enabled(self) -> bool:
        from .autostart import is_autostart_enabled

        return is_autostart_enabled()

    def _on_toggle_autostart(self, *_):
        from .autostart import set_autostart

        want = bool(self._autostart_var.get())
        try:
            mode = "exe" if getattr(sys, "frozen", False) else "script"
            ok, _msg = set_autostart(want, mode=mode)
        except Exception as e:  # pragma: no cover - registry edge cases
            ok, e_repr = False, e
            self._notify(t("notify.autostart_fail", err=e_repr))
        else:
            if ok:
                self._notify(t("notify.autostart_on") if want else t("notify.autostart_off"))
        # reflect the real state back into the checkbox
        self._autostart_var.set(self._autostart_enabled())

    def _read_config_fields(self) -> dict:
        """Read and validate the window fields. Raises ValueError with a localized message."""
        g = self._cfg_vars
        target = g["target"].get().strip()
        if not target:
            raise ValueError(t("dlg.invalid_target_empty"))
        if " " in target:
            raise ValueError(t("dlg.invalid_target_space"))
        try:
            interval = float(g["interval"].get().strip().replace(",", "."))
            timeout = int(float(g["timeout"].get().strip()))
            omin = int(g["omin"].get().strip())
            ret = int(g["ret"].get().strip())
        except ValueError:
            raise ValueError(t("dlg.invalid_numbers"))
        if interval < 0.2:
            raise ValueError(t("dlg.invalid_interval_min"))
        if timeout < 200:
            raise ValueError(t("dlg.invalid_timeout_min"))
        if omin < 1:
            raise ValueError(t("dlg.invalid_outage_min"))
        if ret < 0:
            raise ValueError(t("dlg.invalid_retention_neg"))
        return {"target": target, "interval_seconds": interval, "timeout_ms": timeout,
                "outage_min_consecutive": omin, "retention_days": ret,
                "language": self._lang_codes[self._lang_combo.current()]}

    def _apply_fields_to_cfg(self, vals: dict) -> bool:
        """Copy values into self.cfg. Returns True if target/interval/timeout changed."""
        changed_probe = (vals["target"] != self.cfg.target
                         or vals["interval_seconds"] != self.cfg.interval_seconds
                         or vals["timeout_ms"] != self.cfg.timeout_ms)
        self.cfg.target = vals["target"]
        self.cfg.interval_seconds = vals["interval_seconds"]
        self.cfg.timeout_ms = vals["timeout_ms"]
        self.cfg.outage_min_consecutive = vals["outage_min_consecutive"]
        self.cfg.retention_days = vals["retention_days"]
        self.cfg.language = vals.get("language", self.cfg.language)
        return changed_probe

    def _on_apply_config(self, *_):
        from tkinter import messagebox

        try:
            vals = self._read_config_fields()
        except ValueError as e:
            messagebox.showerror(t("dlg.invalid_title"), str(e))
            return

        changed_probe = self._apply_fields_to_cfg(vals)
        try:
            path = self.cfg.save()
        except OSError as e:
            messagebox.showerror(t("dlg.save_error_title"), str(e))
            return

        if changed_probe:
            self._restart_monitor()
            self._cfg_status.config(text=t("win.cfg.saved_restarted", file=path.name),
                                    foreground="#15803d")
        else:
            self._cfg_status.config(text=t("win.cfg.saved_applied", file=path.name),
                                    foreground="#15803d")

    def _persist_config_on_exit(self) -> None:
        """Save the configuration on quit, including edits not yet applied."""
        try:
            self._apply_fields_to_cfg(self._read_config_fields())
        except ValueError:
            pass  # invalid field: keep the last valid config in memory
        except Exception:
            pass
        try:
            self.cfg.save()
        except OSError:
            log.warning("Could not save the configuration on exit.")

    def _restart_monitor(self):
        self.monitor.request_stop()
        if self._mon_thread:
            self._mon_thread.join(timeout=8)
        self.state = LiveState()
        self.monitor = Monitor(self.cfg, self.state)
        self._mon_thread = threading.Thread(target=self._run_monitor, name="monitor", daemon=True)
        self._mon_thread.start()

    def _on_report(self, *_, all_data: bool = False):
        if self._report_busy:
            return
        self._report_busy = True
        since = "" if all_data else self._since_var.get()
        until = "" if all_data else self._until_var.get()
        if self._root:
            self._report_btn.config(state="disabled")
            self._report_status.config(text=t("win.report.generating"), foreground="#6b7280")

        def work():
            try:
                from .report import generate_reports

                out = app_home() / "reports"
                paths = generate_reports(self.cfg, out_dir=out, fmt="both",
                                         since=since or None, until=until or None)
                names = ", ".join(p.name for p in paths)
                self._notify(t("notify.report_done", names=names))
                self._set_report_status(t("win.report.done", n=len(paths), path=out), ok=True)
                for p in paths:
                    if p.suffix == ".html":
                        webbrowser.open(p.as_uri())
            except SystemExit as e:
                self._set_report_status(str(e), ok=False)
                self._notify(str(e))
            except Exception as e:  # pragma: no cover
                log.exception("Report failed")
                self._set_report_status(t("win.report.error", err=e), ok=False)
                self._notify(t("win.report.error", err=e))
            finally:
                self._report_busy = False
                if self._root:
                    self._root.after(0, lambda: self._report_btn.config(state="normal"))

        threading.Thread(target=work, name="report", daemon=True).start()

    def _set_report_status(self, text: str, ok: bool):
        if not self._root:
            return
        self._root.after(0, lambda: self._report_status.config(
            text=text, foreground="#15803d" if ok else "#b91c1c"))

    def _on_quit(self, *_):
        if self._shutting_down:
            return
        if self._root:
            try:
                from tkinter import messagebox

                if not messagebox.askokcancel(t("dlg.quit_title"), t("dlg.quit_confirm")):
                    return
            except Exception:
                pass
        self._shutting_down = True
        log.info("Shutting down...")
        self._persist_config_on_exit()
        self.monitor.request_stop()
        if self._mon_thread:
            self._mon_thread.join(timeout=8)
        try:
            if self._icon:
                self._icon.visible = False
                self._icon.stop()
        except Exception:
            pass
        if self._root:
            self._root.after(0, self._root.destroy)

    # ------------------------------------------------------------------
    # fallback without tkinter: tray menu only
    # ------------------------------------------------------------------
    def _run_menu_only(self) -> int:  # pragma: no cover
        import pystray
        from pystray import MenuItem as Item

        menu = pystray.Menu(
            Item(lambda _i: t("menu.state_fmt", state=t("state." + self.state.state_key)),
                 None, enabled=False),
            Item(lambda _i: t("menu.loss_fmt", target=self.cfg.target, pct=self.state.loss_pct),
                 None, enabled=False),
            pystray.Menu.SEPARATOR,
            Item(t("menu.pause_resume"), lambda: self._on_toggle_pause()),
            Item(t("menu.generate_report_all"), lambda: self._on_report(all_data=True)),
            Item(t("win.btn.open_data_folder"), lambda: _open_path(app_home())),
            pystray.Menu.SEPARATOR,
            Item(t("menu.quit"), self._on_quit),
        )
        self._icon = pystray.Icon("PacketLizer", _make_icon_image(_GREY), "PacketLizer", menu=menu)

        def refresh(icon):
            icon.visible = True
            while getattr(icon, "visible", False):
                try:
                    icon.icon = _make_icon_image(_state_color(self.state))
                    icon.title = t("menu.state_fmt", state=t("state." + self.state.state_key))
                    icon.menu = menu
                except Exception:
                    pass
                time.sleep(3)

        self._icon.run(setup=refresh)
        return 0


def run_tray_app(cfg: Config) -> int:
    set_language(cfg.language)
    app_home().mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        filename=str(app_home() / "packetlizer.log"),
    )
    try:
        return TrayApp(cfg).start()
    except KeyboardInterrupt:
        return 0
