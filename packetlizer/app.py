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
from .monitor import LiveState, MonitorGroup
from .netiface import list_interfaces
from .storage import Storage

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
    if s.link_down:
        return _GREY
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
        self.group = MonitorGroup(cfg)
        self._icon = None
        self._root = None
        self._report_busy = False
        self._shutting_down = False
        # widgets whose text must be refreshed on a language change
        self._retext_map: list = []

    @property
    def state(self) -> LiveState:
        """Aggregate view across every monitored interface, for the single
        status indicator (dot/label/tray icon)."""
        return self.group.aggregate

    # -- lifecycle --------------------------------------------------
    def start(self) -> int:
        self.group.start()
        try:
            import tkinter  # noqa: F401
        except Exception as e:  # pragma: no cover - Windows always ships tkinter
            log.warning("tkinter unavailable (%s); running with the tray menu only.", e)
            return self._run_menu_only()
        return self._run_with_window()

    # ------------------------------------------------------------------
    # main mode: tkinter window + pystray icon on a thread
    # ------------------------------------------------------------------
    def _run_with_window(self) -> int:
        import pystray
        import tkinter as tk
        from tkinter import ttk

        self._root = root = tk.Tk()
        root.title("PacketLizer")
        root.geometry("600x680")
        root.minsize(560, 600)
        root.resizable(True, True)
        root.protocol("WM_DELETE_WINDOW", self._hide_window)
        # Show in the taskbar while visible; drop off it when minimized
        # (minimize-to-tray -- the tray icon brings it back).
        root.bind("<Unmap>", self._on_unmap)
        root.withdraw()  # start hidden in the tray

        pad = {"padx": 10, "pady": 3}
        header = ttk.Frame(root)
        header.pack(side="top", fill="x", padx=10, pady=(6, 2))
        self._dot = tk.Canvas(header, width=16, height=16, highlightthickness=0)
        self._dot.pack(side="left")
        self._dot_id = self._dot.create_oval(1, 1, 15, 15, fill="#94a3b8", outline="")
        self._state_lbl = ttk.Label(header, text="", font=("Segoe UI", 12, "bold"))
        self._state_lbl.pack(side="left", padx=8)

        # The bottom button bar is packed FIRST with side=bottom so it always
        # keeps its strip and is never pushed off-screen by the content above.
        btns = ttk.Frame(root)
        btns.pack(side="bottom", fill="x", padx=10, pady=6)
        self._pause_btn = ttk.Button(btns, text="", width=10, command=self._on_toggle_pause)
        self._pause_btn.pack(side="left")
        self._open_btn = ttk.Button(btns, text="", command=lambda: _open_path(app_home()))
        self._open_btn.pack(side="left", padx=6)
        self._quit_btn = ttk.Button(btns, text="", width=10, command=self._on_quit)
        self._quit_btn.pack(side="right")

        self._field_labels: dict[str, object] = {}
        self._status_labels: dict[str, object] = {}

        # ---- editable configuration (two field pairs per row) -------
        conf = ttk.LabelFrame(root, text="")
        conf.pack(side="top", fill="x", **pad)
        conf.columnconfigure(1, weight=1)
        conf.columnconfigure(3, weight=1)
        self._lf_config = conf
        self._cfg_vars = {
            "target": tk.StringVar(value=self.cfg.target),
            "interval": tk.StringVar(value=f"{self.cfg.interval_seconds:g}"),
            "timeout": tk.StringVar(value=str(self.cfg.timeout_ms)),
            "omin": tk.StringVar(value=str(self.cfg.outage_min_consecutive)),
            "ret": tk.StringVar(value=str(self.cfg.retention_days)),
        }

        def cfield(key, var, r, col, span=1, width=9):
            lbl = ttk.Label(conf, text="")
            lbl.grid(row=r, column=col, sticky="w", padx=(8, 4), pady=2)
            self._field_labels[key] = lbl
            ttk.Entry(conf, textvariable=self._cfg_vars[var], width=width).grid(
                row=r, column=col + 1, columnspan=span, sticky="ew", padx=(0, 8), pady=2)

        cfield("win.field.target", "target", 0, 0, span=3, width=20)
        cfield("win.field.interval", "interval", 1, 0)
        cfield("win.field.timeout", "timeout", 1, 2)
        cfield("win.field.outage_min", "omin", 2, 0)
        cfield("win.field.retention", "ret", 2, 2)

        self._lang_label = ttk.Label(conf, text="")
        self._lang_label.grid(row=3, column=0, sticky="w", padx=(8, 4), pady=2)
        self._lang_codes = available_languages()
        self._lang_combo = ttk.Combobox(conf, state="readonly", width=18,
                                        values=[language_display_name(c) for c in self._lang_codes])
        cur_lang = self.cfg.language if self.cfg.language in self._lang_codes else "auto"
        self._lang_combo.current(self._lang_codes.index(cur_lang))
        self._lang_combo.grid(row=3, column=1, sticky="w", padx=(0, 8), pady=2)
        self._lang_combo.bind("<<ComboboxSelected>>", self._on_language_change)

        self._autostart_var = tk.BooleanVar(value=self._autostart_enabled())
        self._autostart_chk = ttk.Checkbutton(conf, text="", variable=self._autostart_var,
                                              command=self._on_toggle_autostart)
        self._autostart_chk.grid(row=3, column=2, columnspan=2, sticky="w", padx=(0, 8), pady=2)

        self._apply_btn = ttk.Button(conf, text="", command=self._on_apply_config)
        self._apply_btn.grid(row=4, column=0, columnspan=4, sticky="ew", padx=8, pady=(6, 2))
        cfg_status_holder = ttk.Frame(conf, height=36)
        cfg_status_holder.grid(row=5, column=0, columnspan=4, sticky="ew", padx=8, pady=(0, 4))
        cfg_status_holder.grid_propagate(False)
        self._cfg_status = ttk.Label(cfg_status_holder, text="", foreground="#6b7280",
                                     wraplength=540, justify="left")
        self._cfg_status.pack(anchor="w", fill="x")

        # ---- network interfaces (multi-select) -------------------
        ifaces = ttk.LabelFrame(root, text="")
        ifaces.pack(side="top", fill="x", **pad)
        ifaces.columnconfigure(0, weight=1)
        self._lf_interfaces = ifaces
        self._iface_hint = ttk.Label(ifaces, text="", foreground="#6b7280", wraplength=540, justify="left")
        self._iface_hint.grid(row=0, column=0, columnspan=2, sticky="w", padx=8, pady=(4, 2))
        self._iface_listbox = tk.Listbox(ifaces, selectmode="multiple", exportselection=False, height=4)
        self._iface_listbox.grid(row=1, column=0, sticky="ew", padx=(8, 4), pady=(0, 6))
        self._iface_refresh_btn = ttk.Button(ifaces, text="", command=self._on_refresh_interfaces)
        self._iface_refresh_btn.grid(row=1, column=1, sticky="n", padx=(0, 8), pady=(0, 6))
        self._iface_names: list[str] = []
        self._populate_interfaces_listbox()

        # ---- live status (two columns) ---------------------------
        info = ttk.LabelFrame(root, text="")
        info.pack(side="top", fill="x", **pad)
        info.columnconfigure(1, weight=1)
        info.columnconfigure(3, weight=1)
        self._lf_status = info
        self._info_vars = {k: tk.StringVar(value="-") for k in
                           ("target", "method", "last", "loss", "outages", "uptime")}

        def sfield(key, var, r, col, span=1):
            lbl = ttk.Label(info, text="")
            lbl.grid(row=r, column=col, sticky="w", padx=(8, 4), pady=1)
            self._status_labels[key] = lbl
            ttk.Label(info, textvariable=self._info_vars[var]).grid(
                row=r, column=col + 1, columnspan=span, sticky="w", padx=(0, 8), pady=1)

        sfield("win.status.method", "method", 0, 0, span=3)
        sfield("win.status.target", "target", 1, 0)
        sfield("win.status.last_sample", "last", 1, 2)
        sfield("win.status.loss", "loss", 2, 0)
        sfield("win.status.outages", "outages", 2, 2)
        sfield("win.status.monitoring_for", "uptime", 3, 0)
        self._iface_status_var = tk.StringVar(value="")
        self._iface_status_lbl = ttk.Label(info, textvariable=self._iface_status_var,
                                           foreground="#6b7280", justify="left")
        self._iface_status_lbl.grid(row=4, column=0, columnspan=4, sticky="w", padx=(8, 8), pady=(4, 2))
        self._iface_status_lbl.grid_remove()  # only shown with >1 interface selected

        # ---- report ---------------------------------------------
        rep = ttk.LabelFrame(root, text="")
        rep.pack(side="top", fill="x", **pad)
        rep.columnconfigure(1, weight=1)
        rep.columnconfigure(3, weight=1)
        self._lf_report = rep
        self._since_lbl = ttk.Label(rep, text="")
        self._since_lbl.grid(row=0, column=0, sticky="w", padx=(8, 4), pady=2)
        self._since_var = tk.StringVar()
        ttk.Entry(rep, textvariable=self._since_var, width=12).grid(row=0, column=1, sticky="ew", padx=(0, 8), pady=2)
        self._until_lbl = ttk.Label(rep, text="")
        self._until_lbl.grid(row=0, column=2, sticky="w", padx=(8, 4), pady=2)
        self._until_var = tk.StringVar()
        ttk.Entry(rep, textvariable=self._until_var, width=12).grid(row=0, column=3, sticky="ew", padx=(0, 8), pady=2)
        self._hint_lbl = ttk.Label(rep, text="", foreground="#6b7280")
        self._hint_lbl.grid(row=1, column=0, columnspan=4, sticky="w", padx=8)
        self._report_btn = ttk.Button(rep, text="", command=self._on_report)
        self._report_btn.grid(row=2, column=0, columnspan=4, sticky="ew", padx=8, pady=(4, 2))
        rep_status_holder = ttk.Frame(rep, height=36)
        rep_status_holder.grid(row=3, column=0, columnspan=4, sticky="ew", padx=8, pady=(0, 4))
        rep_status_holder.grid_propagate(False)
        self._report_status = ttk.Label(rep_status_holder, text="", foreground="#6b7280",
                                        wraplength=540, justify="left")
        self._report_status.pack(anchor="w", fill="x")

        # ---- data & logs maintenance ---------------------------
        maint = ttk.LabelFrame(root, text="")
        maint.pack(side="top", fill="x", **pad)
        self._lf_maint = maint
        self._clear_logs_btn = ttk.Button(maint, text="", command=self._on_clear_logs)
        self._clear_logs_btn.pack(side="left", padx=8, pady=4)
        self._delete_logs_btn = ttk.Button(maint, text="", command=self._open_delete_logs_dialog)
        self._delete_logs_btn.pack(side="left", padx=8, pady=4)

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

        root.after(4000, self._check_for_update_async)
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
        self._lf_interfaces.config(text=t("win.section.interfaces"))
        self._iface_hint.config(text=t("win.hint.interfaces"))
        self._iface_refresh_btn.config(text=t("win.btn.refresh_interfaces"))
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
        self._lf_maint.config(text=t("win.section.maintenance"))
        self._clear_logs_btn.config(text=t("win.btn.clear_logs"))
        self._delete_logs_btn.config(text=t("win.btn.delete_logs"))
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
        r.deiconify()  # un-withdraw / un-minimize -> visible + taskbar button
        r.state("normal")
        r.lift()
        r.attributes("-topmost", True)
        r.after(300, lambda: r.attributes("-topmost", False))
        r.focus_force()

    def _hide_window(self):
        if self._root:
            self._root.withdraw()  # removes the taskbar button too
            self._notify(t("notify.hidden"))

    def _on_unmap(self, event):
        # Fires on minimize and on withdraw. On minimize, withdraw instead so the
        # taskbar button disappears; the tray icon is the way back.
        if event.widget is self._root and self._root.state() == "iconic":
            self._root.withdraw()

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
        self._pause_btn.config(text=t("win.btn.resume") if self.group.is_paused else t("win.btn.pause"))
        if self._autostart_var.get() != self._autostart_enabled():
            self._autostart_var.set(self._autostart_enabled())
        self._update_iface_status()

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
        paused = self.group.toggle_pause()
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

    # -- network interfaces -------------------------------------------
    def _populate_interfaces_listbox(self) -> None:
        """(Re)fill the multi-select from what's currently detected, keeping
        whatever is already selected (by name) or, on first build, whatever
        is saved in the config."""
        selected_names = set(self._selected_interfaces()) if self._iface_names else set(self.cfg.interfaces)
        detected = list_interfaces()
        # Keep configured names that aren't currently detected (e.g. a USB
        # adapter that's unplugged right now) so the selection isn't lost.
        known_names = {i.name for i in detected}
        extra = [n for n in selected_names if n not in known_names]

        self._iface_listbox.delete(0, "end")
        self._iface_names = [i.name for i in detected] + extra
        for i in detected:
            self._iface_listbox.insert("end", i.label)
        for n in extra:
            self._iface_listbox.insert("end", t("win.value.iface_unavailable_fmt", name=n))
        for idx, name in enumerate(self._iface_names):
            if name in selected_names:
                self._iface_listbox.selection_set(idx)

    def _on_refresh_interfaces(self, *_):
        self._populate_interfaces_listbox()

    def _selected_interfaces(self) -> list[str]:
        return [self._iface_names[i] for i in self._iface_listbox.curselection()]

    def _update_iface_status(self) -> None:
        """Small per-interface breakdown line, shown only when >1 interface
        is actively selected (the single-interface / default case is already
        fully covered by the main status block above)."""
        states = self.group.states
        if len(states) <= 1:
            self._iface_status_lbl.grid_remove()
            return
        parts = []
        for name, s in states.items():
            label = name or t("rpt.unbound_iface")
            state_txt = t("state." + s.state_key)
            parts.append(t("win.value.iface_status_fmt", name=label, state=state_txt, pct=s.loss_pct))
        self._iface_status_var.set("  |  ".join(parts))
        self._iface_status_lbl.grid()

    # -- auto-update -------------------------------------------------
    def _check_for_update_async(self):
        def work():
            from .updater import check_for_update

            try:
                info = check_for_update()
            except Exception:  # pragma: no cover - network edge cases
                log.exception("Update check failed")
                return
            if info and info["tag"] != self.cfg.skip_update_version and self._root:
                self._root.after(0, lambda: self._show_update_dialog(info))

        threading.Thread(target=work, name="update-check", daemon=True).start()

    def _show_update_dialog(self, info: dict):
        import tkinter as tk
        from tkinter import scrolledtext, ttk

        from .updater import current_version

        win = tk.Toplevel(self._root)
        win.title(t("dlg.update_title"))
        win.transient(self._root)
        win.resizable(False, False)
        pad = {"padx": 12, "pady": 6}

        ttk.Label(
            win,
            text=t("dlg.update_available_fmt", current=current_version(), new=info["tag"]),
            font=("Segoe UI", 10, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", **pad)

        ttk.Label(win, text=t("dlg.update_notes_label")).grid(
            row=1, column=0, columnspan=2, sticky="w", padx=12)
        notes = scrolledtext.ScrolledText(win, width=60, height=10, wrap="word")
        notes.insert("1.0", info.get("notes") or t("dlg.update_no_notes"))
        notes.config(state="disabled")
        notes.grid(row=2, column=0, columnspan=2, sticky="nsew", padx=12, pady=(2, 6))

        skip_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(win, text=t("dlg.update_chk_skip"), variable=skip_var).grid(
            row=3, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 6))

        status_var = tk.StringVar(value="")
        ttk.Label(win, textvariable=status_var, foreground="#6b7280").grid(
            row=4, column=0, columnspan=2, sticky="w", padx=12)

        def remember_skip_if_checked():
            if skip_var.get():
                self.cfg.skip_update_version = info["tag"]
                try:
                    self.cfg.save()
                except OSError:
                    pass

        def do_later():
            remember_skip_if_checked()
            win.destroy()

        def do_update():
            remember_skip_if_checked()
            update_btn.config(state="disabled")
            later_btn.config(state="disabled")
            status_var.set(t("dlg.update_downloading", pct=0))

            def on_progress(written, total):
                pct = int(written * 100 / total) if total else 0
                win.after(0, lambda: status_var.set(t("dlg.update_downloading", pct=pct)))

            def work():
                from .updater import download_and_apply

                try:
                    download_and_apply(info["asset_url"], on_progress=on_progress)
                except SystemExit:
                    win.after(0, self._on_quit_for_update)
                except Exception as e:  # pragma: no cover - network/filesystem edge cases
                    log.exception("Auto-update failed")
                    # "as e" is cleared when the except block exits, so capture the
                    # message now -- the lambda below only runs later, on the Tk loop.
                    err_msg = str(e)
                    win.after(0, lambda: status_var.set(t("dlg.update_failed", err=err_msg)))
                    win.after(0, lambda: (update_btn.config(state="normal"), later_btn.config(state="normal")))

            threading.Thread(target=work, name="update-download", daemon=True).start()

        bar = ttk.Frame(win)
        bar.grid(row=5, column=0, columnspan=2, sticky="ew", padx=12, pady=(4, 10))
        later_btn = ttk.Button(bar, text=t("dlg.update_btn_later"), command=do_later)
        later_btn.pack(side="right")
        update_btn = ttk.Button(bar, text=t("dlg.update_btn_update"), command=do_update)
        update_btn.pack(side="right", padx=6)

        win.grid_columnconfigure(0, weight=1)
        win.update_idletasks()
        win.grab_set()
        win.focus_force()

    def _on_quit_for_update(self):
        """Close down cleanly so the helper script can replace + relaunch the exe."""
        self._notify(t("notify.update_restarting"))
        self._shutting_down = True
        self._persist_config_on_exit()
        self.group.request_stop()
        try:
            if self._icon:
                self._icon.visible = False
                self._icon.stop()
        except Exception:
            pass
        if self._root:
            self._root.after(300, self._root.destroy)

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
                "language": self._lang_codes[self._lang_combo.current()],
                "interfaces": self._selected_interfaces()}

    def _apply_fields_to_cfg(self, vals: dict) -> bool:
        """Copy values into self.cfg. Returns True if target/interval/timeout/interfaces changed."""
        changed_probe = (vals["target"] != self.cfg.target
                         or vals["interval_seconds"] != self.cfg.interval_seconds
                         or vals["timeout_ms"] != self.cfg.timeout_ms
                         or vals["interfaces"] != self.cfg.interfaces)
        self.cfg.target = vals["target"]
        self.cfg.interval_seconds = vals["interval_seconds"]
        self.cfg.timeout_ms = vals["timeout_ms"]
        self.cfg.outage_min_consecutive = vals["outage_min_consecutive"]
        self.cfg.retention_days = vals["retention_days"]
        self.cfg.language = vals.get("language", self.cfg.language)
        self.cfg.interfaces = vals["interfaces"]
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
        self.group.request_stop()
        self.group.join(timeout=8)
        self.group = MonitorGroup(self.cfg)
        self.group.start()

    # -- data & logs maintenance -----------------------------------
    def _db_targets(self) -> list:
        """Distinct targets currently stored (may include None)."""
        try:
            with Storage(self.cfg.resolved_db_path()) as st:
                return st.distinct_targets()
        except Exception:  # pragma: no cover
            return []

    def _run_db_op(self, fn):
        """Stop the monitor, run ``fn(storage)`` with exclusive DB access, then
        start a fresh monitor (which also resets the session counters)."""
        self.group.request_stop()
        self.group.join(timeout=8)
        try:
            with Storage(self.cfg.resolved_db_path()) as st:
                return fn(st)
        finally:
            self.group = MonitorGroup(self.cfg)
            self.group.start()

    def _on_clear_logs(self, *_):
        from tkinter import messagebox

        if not messagebox.askyesno(t("dlg.clear_logs_title"), t("dlg.clear_logs_confirm"),
                                   icon="warning", default="no"):
            return
        removed = self._run_db_op(lambda st: st.clear_all_samples())
        self._notify(t("notify.logs_cleared", n=removed))
        self._cfg_status.config(text=t("notify.logs_cleared", n=removed), foreground="#15803d")

    def _open_delete_logs_dialog(self, *_):
        import tkinter as tk
        from tkinter import messagebox, ttk

        from .report import parse_report_dates

        targets = self._db_targets()
        win = tk.Toplevel(self._root)
        win.title(t("dlg.delete_logs_title"))
        win.transient(self._root)  # keeps it off the taskbar, on top of the main window
        win.resizable(False, False)
        pad = {"padx": 12, "pady": 5}

        ttk.Label(win, text=t("win.field.start_date") + ":").grid(row=0, column=0, sticky="w", **pad)
        since_var = tk.StringVar()
        ttk.Entry(win, textvariable=since_var, width=24).grid(row=0, column=1, sticky="w", **pad)
        ttk.Label(win, text=t("win.field.end_date") + ":").grid(row=1, column=0, sticky="w", **pad)
        until_var = tk.StringVar()
        ttk.Entry(win, textvariable=until_var, width=24).grid(row=1, column=1, sticky="w", **pad)
        ttk.Label(win, text=t("win.hint.date_format"), foreground="#6b7280").grid(
            row=2, column=0, columnspan=2, sticky="w", padx=12)

        ttk.Label(win, text=t("dlg.delete_logs_targets") + ":").grid(
            row=3, column=0, columnspan=2, sticky="w", padx=12, pady=(8, 0))
        lb = tk.Listbox(win, selectmode="extended", height=min(8, max(3, len(targets))),
                        exportselection=False, width=40)
        for tg in targets:
            lb.insert("end", tg if tg is not None else t("target.unknown"))
        lb.grid(row=4, column=0, columnspan=2, sticky="ew", padx=12)
        ttk.Label(win, text=t("dlg.delete_logs_hint"), foreground="#6b7280").grid(
            row=5, column=0, columnspan=2, sticky="w", padx=12, pady=(2, 6))

        def do_delete():
            try:
                start, end = parse_report_dates(since_var.get(), until_var.get())
            except ValueError:
                messagebox.showerror(t("dlg.invalid_title"), t("dlg.invalid_numbers"), parent=win)
                return
            sel = [targets[i] for i in lb.curselection()]
            if not sel and start is None and end is None:
                messagebox.showerror(t("dlg.delete_logs_title"), t("dlg.delete_logs_none"), parent=win)
                return
            with Storage(self.cfg.resolved_db_path()) as st:
                n = st.count_samples(start, end, sel or None)
            if n == 0:
                messagebox.showinfo(t("dlg.delete_logs_title"), t("dlg.delete_logs_nomatch"), parent=win)
                return
            if not messagebox.askyesno(t("dlg.delete_logs_title"),
                                       t("dlg.delete_logs_confirm", n=n),
                                       icon="warning", default="no", parent=win):
                return
            removed = self._run_db_op(lambda st: st.delete_samples(start, end, sel or None))
            win.destroy()
            self._notify(t("notify.logs_cleared", n=removed))
            self._cfg_status.config(text=t("notify.logs_cleared", n=removed), foreground="#15803d")

        bar = ttk.Frame(win)
        bar.grid(row=6, column=0, columnspan=2, sticky="ew", padx=12, pady=10)
        ttk.Button(bar, text=t("win.btn.cancel"), command=win.destroy).pack(side="right")
        ttk.Button(bar, text=t("win.btn.delete"), command=do_delete).pack(side="right", padx=6)

        win.grid_columnconfigure(1, weight=1)
        win.update_idletasks()
        win.grab_set()
        win.focus_force()

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
        self.group.request_stop()
        self.group.join(timeout=8)
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
