"""App de bandeja + janela principal.

O icone fica no tray (sem aparecer na barra de tarefas). Ao clicar nele abre a
janela principal, que mostra o estado atual (Em execucao / Instavel / QUEDA /
Em pausa), permite pausar/retomar e encerrar o programa, e gera relatorios
informando data inicial e final opcionais:

  * sem data inicial -> desde o inicio dos dados
  * sem data final    -> ate a amostra mais recente

Fechar a janela (X) apenas a esconde de volta para o tray.
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
        log.warning("Nao consegui abrir %s: %s", path, e)


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

    # -- ciclo de vida --------------------------------------------------
    def start(self) -> int:
        self._mon_thread = threading.Thread(target=self._run_monitor, name="monitor", daemon=True)
        self._mon_thread.start()
        try:
            import tkinter  # noqa: F401
        except Exception as e:  # pragma: no cover - Windows sempre tem tkinter
            log.warning("tkinter indisponivel (%s); rodando so com o menu do tray.", e)
            return self._run_menu_only()
        return self._run_with_window()

    def _run_monitor(self):
        try:
            self.monitor.run()
        except Exception:  # pragma: no cover
            log.exception("Monitor caiu")

    # ------------------------------------------------------------------
    # modo principal: janela tkinter + icone pystray numa thread
    # ------------------------------------------------------------------
    def _run_with_window(self) -> int:
        import pystray
        import tkinter as tk
        from tkinter import ttk

        self._root = root = tk.Tk()
        root.title("PacketLizer")
        root.geometry("470x640")
        root.minsize(440, 560)
        root.protocol("WM_DELETE_WINDOW", self._hide_window)
        # semi-invisivel: sem botao na barra de tarefas, comeca escondida no tray
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
        self._state_lbl = ttk.Label(header, text="Iniciando...", font=("Segoe UI", 13, "bold"))
        self._state_lbl.pack(side="left", padx=8)

        # ---- configuracao editavel -----------------------------------
        conf = ttk.LabelFrame(root, text="Configuracao")
        conf.pack(fill="x", **pad)
        conf.columnconfigure(1, weight=1)
        self._cfg_vars = {
            "target": tk.StringVar(value=self.cfg.target),
            "interval": tk.StringVar(value=f"{self.cfg.interval_seconds:g}"),
            "timeout": tk.StringVar(value=str(self.cfg.timeout_ms)),
            "omin": tk.StringVar(value=str(self.cfg.outage_min_consecutive)),
            "ret": tk.StringVar(value=str(self.cfg.retention_days)),
        }
        conf_rows = [
            ("Alvo (dominio ou IP)", "target"),
            ("Intervalo entre pings (s)", "interval"),
            ("Timeout por ping (ms)", "timeout"),
            ("Perdas seguidas p/ contar queda", "omin"),
            ("Retencao do historico (dias)", "ret"),
        ]
        for i, (label, key) in enumerate(conf_rows):
            ttk.Label(conf, text=label + ":").grid(row=i, column=0, sticky="w", padx=8, pady=3)
            ttk.Entry(conf, textvariable=self._cfg_vars[key]).grid(
                row=i, column=1, sticky="ew", padx=8, pady=3)
        self._apply_btn = ttk.Button(conf, text="Salvar e aplicar", command=self._on_apply_config)
        self._apply_btn.grid(row=len(conf_rows), column=0, columnspan=2, sticky="ew", padx=8, pady=(6, 3))
        self._cfg_status = ttk.Label(conf, text="", foreground="#6b7280", wraplength=420)
        self._cfg_status.grid(row=len(conf_rows) + 1, column=0, columnspan=2, sticky="w", padx=8)

        info = ttk.LabelFrame(root, text="Status")
        info.pack(fill="x", **pad)
        self._info_vars = {k: tk.StringVar(value="-") for k in
                           ("alvo", "metodo", "ultima", "perda", "quedas", "ativo")}
        rows = [("Alvo", "alvo"), ("Metodo", "metodo"), ("Ultima amostra", "ultima"),
                ("Perda de pacotes", "perda"), ("Quedas detectadas", "quedas"),
                ("Monitorando ha", "ativo")]
        for i, (label, key) in enumerate(rows):
            ttk.Label(info, text=label + ":").grid(row=i, column=0, sticky="w", padx=8, pady=2)
            ttk.Label(info, textvariable=self._info_vars[key]).grid(row=i, column=1, sticky="w", padx=8, pady=2)

        rep = ttk.LabelFrame(root, text="Gerar relatorio (HTML + PDF + CSV)")
        rep.pack(fill="x", **pad)
        ttk.Label(rep, text="Data inicial (opcional):").grid(row=0, column=0, sticky="w", padx=8, pady=3)
        self._since_var = tk.StringVar()
        ttk.Entry(rep, textvariable=self._since_var, width=22).grid(row=0, column=1, padx=8, pady=3)
        ttk.Label(rep, text="Data final (opcional):").grid(row=1, column=0, sticky="w", padx=8, pady=3)
        self._until_var = tk.StringVar()
        ttk.Entry(rep, textvariable=self._until_var, width=22).grid(row=1, column=1, padx=8, pady=3)
        ttk.Label(rep, text="Formato: AAAA-MM-DD  (ex.: 2026-09-01). Vazio = tudo.",
                  foreground="#6b7280").grid(row=2, column=0, columnspan=2, sticky="w", padx=8)
        self._report_btn = ttk.Button(rep, text="Gerar relatorio", command=self._on_report)
        self._report_btn.grid(row=3, column=0, columnspan=2, sticky="ew", padx=8, pady=(6, 4))
        self._report_status = ttk.Label(rep, text="", foreground="#6b7280")
        self._report_status.grid(row=4, column=0, columnspan=2, sticky="w", padx=8)

        btns = ttk.Frame(root)
        btns.pack(fill="x", **pad)
        self._pause_btn = ttk.Button(btns, text="Pausar", command=self._on_toggle_pause)
        self._pause_btn.pack(side="left")
        ttk.Button(btns, text="Abrir pasta de dados",
                   command=lambda: _open_path(app_home())).pack(side="left", padx=6)
        ttk.Button(btns, text="Encerrar programa", command=self._on_quit).pack(side="right")

        # icone do tray numa thread separada
        self._icon = pystray.Icon("PacketLizer", _make_icon_image(_GREY), "PacketLizer",
                                  menu=self._build_menu())
        threading.Thread(target=self._icon.run, name="tray", daemon=True).start()

        if getattr(self.cfg, "_created", False):
            # primeira execucao: mostra a janela para o usuario definir o dominio
            root.after(500, self._do_show)
        else:
            root.after(800, lambda: self._notify(
                "PacketLizer rodando na bandeja. Clique no icone para abrir a janela."))

        self._tick()
        root.mainloop()
        return 0

    # -- janela: mostrar / esconder ----------------------------------
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
            self._notify("PacketLizer continua rodando na bandeja. Clique no icone para reabrir.")

    def _tick(self):
        if self._shutting_down or not self._root:
            return
        s = self.state
        self._state_lbl.config(text=s.state_name)
        col = _state_color(s)
        self._dot.itemconfig(self._dot_id, fill="#%02x%02x%02x" % col)

        last = "-"
        if s.total:
            lbl = STATUS_LABEL.get(s.last_status, "?")
            rtt = f"{s.last_rtt_ms:.0f} ms" if s.last_rtt_ms is not None else "sem resposta"
            hhmm = datetime.fromtimestamp(s.last_ts).strftime("%H:%M:%S") if s.last_ts else "-"
            last = f"{hhmm}  -  {lbl} ({rtt})"
        self._info_vars["alvo"].set(self.cfg.target)
        self._info_vars["metodo"].set(f"{s.probe_name}  ({s.probe_reason})" if s.probe_reason else s.probe_name)
        self._info_vars["ultima"].set(last)
        self._info_vars["perda"].set(f"{s.loss_pct:.2f}%   ({s.lost} de {s.total})")
        self._info_vars["quedas"].set(str(s.outages))
        self._info_vars["ativo"].set(humanize_seconds(time.time() - s.started_at))
        self._pause_btn.config(text="Retomar" if self.monitor.is_paused else "Pausar")

        try:
            self._icon.icon = _make_icon_image(col)
            self._icon.title = f"PacketLizer - {s.state_name}\n{self.cfg.target} | perda {s.loss_pct:.2f}%"
        except Exception:
            pass
        self._root.after(1500, self._tick)

    # -- menu do tray ----------------------------------------------
    def _build_menu(self):
        import pystray
        from pystray import MenuItem as Item

        return pystray.Menu(
            Item("Abrir janela", self._show_window, default=True),
            Item("Pausar / Retomar", lambda: self._on_toggle_pause()),
            Item("Gerar relatorio (tudo)", lambda: self._on_report(all_data=True)),
            pystray.Menu.SEPARATOR,
            Item("Encerrar programa", self._on_quit),
        )

    def _notify(self, msg: str, title: str = "PacketLizer"):
        try:
            self._icon.notify(msg, title)
        except Exception:
            log.info("%s: %s", title, msg)

    # -- acoes -----------------------------------------------------
    def _on_toggle_pause(self, *_):
        paused = self.monitor.toggle_pause()
        self._notify("Monitoramento pausado (standby)." if paused else "Monitoramento retomado.")

    def _on_apply_config(self, *_):
        from tkinter import messagebox

        g = self._cfg_vars
        try:
            target = g["target"].get().strip()
            if not target:
                raise ValueError("Informe um dominio ou IP no campo 'Alvo'.")
            if " " in target:
                raise ValueError("O alvo nao pode conter espacos.")
            interval = float(g["interval"].get().strip().replace(",", "."))
            if interval < 0.2:
                raise ValueError("O intervalo minimo entre pings e 0,2 s.")
            timeout = int(float(g["timeout"].get().strip()))
            if timeout < 200:
                raise ValueError("O timeout minimo e 200 ms.")
            omin = int(g["omin"].get().strip())
            if omin < 1:
                raise ValueError("'Perdas seguidas p/ contar queda' precisa ser >= 1.")
            ret = int(g["ret"].get().strip())
            if ret < 0:
                raise ValueError("A retencao nao pode ser negativa (use 0 para nunca apagar).")
        except ValueError as e:
            messagebox.showerror("Configuracao invalida", str(e))
            return

        changed_probe = (target != self.cfg.target or interval != self.cfg.interval_seconds
                         or timeout != self.cfg.timeout_ms)
        self.cfg.target = target
        self.cfg.interval_seconds = interval
        self.cfg.timeout_ms = timeout
        self.cfg.outage_min_consecutive = omin
        self.cfg.retention_days = ret
        try:
            path = self.cfg.save()
        except OSError as e:
            messagebox.showerror("Erro ao salvar", str(e))
            return

        if changed_probe:
            self._restart_monitor()
            self._cfg_status.config(
                text=f"Salvo em {path.name}. Monitor reiniciado com o novo alvo; "
                     f"as estatisticas desta sessao recomecam.",
                foreground="#15803d")
        else:
            self._cfg_status.config(
                text=f"Salvo em {path.name}. Ajustes aplicados sem reiniciar o monitor.",
                foreground="#15803d")

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
            self._report_status.config(text="Gerando relatorio...", foreground="#6b7280")

        def work():
            try:
                from .report import generate_reports

                out = app_home() / "reports"
                paths = generate_reports(self.cfg, out_dir=out, fmt="both",
                                         since=since or None, until=until or None)
                names = ", ".join(p.name for p in paths)
                self._notify("Relatorio gerado: " + names)
                self._set_report_status(f"OK: {names}  (pasta: {out})", ok=True)
                for p in paths:
                    if p.suffix == ".html":
                        webbrowser.open(p.as_uri())
            except SystemExit as e:
                self._set_report_status(str(e), ok=False)
                self._notify(str(e))
            except Exception as e:  # pragma: no cover
                log.exception("Falha no relatorio")
                self._set_report_status(f"Erro: {e}", ok=False)
                self._notify(f"Erro ao gerar relatorio: {e}")
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

                if not messagebox.askokcancel("Encerrar PacketLizer",
                                              "Encerrar o monitoramento e fechar o programa?"):
                    return
            except Exception:
                pass
        self._shutting_down = True
        log.info("Encerrando...")
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
    # fallback sem tkinter: so o menu do tray
    # ------------------------------------------------------------------
    def _run_menu_only(self) -> int:  # pragma: no cover
        import pystray
        from pystray import MenuItem as Item

        menu = pystray.Menu(
            Item(lambda _i: f"Estado: {self.state.state_name}", None, enabled=False),
            Item(lambda _i: f"{self.cfg.target} | perda {self.state.loss_pct:.2f}%", None, enabled=False),
            pystray.Menu.SEPARATOR,
            Item("Pausar / Retomar", lambda: self._on_toggle_pause()),
            Item("Gerar relatorio (tudo)", lambda: self._on_report(all_data=True)),
            Item("Abrir pasta de dados", lambda: _open_path(app_home())),
            pystray.Menu.SEPARATOR,
            Item("Encerrar programa", self._on_quit),
        )
        self._icon = pystray.Icon("PacketLizer", _make_icon_image(_GREY), "PacketLizer", menu=menu)

        def refresh(icon):
            icon.visible = True
            while getattr(icon, "visible", False):
                try:
                    icon.icon = _make_icon_image(_state_color(self.state))
                    icon.title = f"PacketLizer - {self.state.state_name}"
                    icon.menu = menu
                except Exception:
                    pass
                time.sleep(3)

        self._icon.run(setup=refresh)
        return 0


def run_tray_app(cfg: Config) -> int:
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
