"""App de bandeja (system tray): roda o monitor numa thread e mostra um icone
proximo ao relogio, sem aparecer na barra de tarefas.

Menu:
  * status atual (alvo, metodo, perda %, ultima latencia)
  * Gerar relatorio (HTML+PDF)  /  Exportar CSV completo
  * Abrir pasta de dados  /  Editar configuracao
  * Iniciar com o Windows (marcavel)
  * Sair
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
from .config import STATUS_LABEL, STATUS_OK, Config, app_home
from .monitor import LiveState, Monitor

log = logging.getLogger("packetlizer.app")

_GREEN = (34, 197, 94)
_RED = (220, 38, 38)
_AMBER = (245, 158, 11)
_GREY = (148, 163, 184)


def _make_icon_image(color):
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((6, 6, 58, 58), fill=color)
    # tres "barras de sinal"
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


class TrayApp:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.state = LiveState()
        self.monitor = Monitor(cfg, self.state)
        self._mon_thread: threading.Thread | None = None
        self._icon = None
        self._busy = False

    # -- ciclo de vida --------------------------------------------------
    def start(self) -> int:
        import pystray

        self._mon_thread = threading.Thread(target=self._run_monitor, name="monitor", daemon=True)
        self._mon_thread.start()

        self._icon = pystray.Icon(
            "PacketLizer",
            _make_icon_image(_GREY),
            "PacketLizer",
            menu=self._build_menu(),
        )
        self._icon.run(setup=self._on_ready)
        return 0

    def _run_monitor(self):
        try:
            self.monitor.run()
        except Exception:  # pragma: no cover
            log.exception("Monitor caiu")

    def _on_ready(self, icon):
        icon.visible = True
        threading.Thread(target=self._refresh_loop, name="tray-refresh", daemon=True).start()

    def _refresh_loop(self):
        while self._icon and getattr(self._icon, "visible", False):
            self._apply_state()
            time.sleep(3)

    # -- UI -----------------------------------------------------------
    def _apply_state(self):
        s = self.state
        if s.total == 0:
            color, tip = _GREY, "PacketLizer - iniciando..."
        elif s.in_outage:
            color = _RED
            tip = f"PacketLizer - QUEDA em curso ({s.consecutive_lost} perdas)"
        elif s.consecutive_lost > 0:
            color = _AMBER
            tip = f"PacketLizer - {s.consecutive_lost} perda(s) recente(s)"
        else:
            color = _GREEN
            rtt = f"{s.last_rtt_ms:.0f} ms" if s.last_rtt_ms is not None else "-"
            tip = f"PacketLizer - OK ({rtt})"
        tip += f"\nAlvo: {self.cfg.target} | perda {s.loss_pct:.2f}% | quedas {s.outages}"
        try:
            self._icon.icon = _make_icon_image(color)
            self._icon.title = tip
            self._icon.menu = self._build_menu()
        except Exception:
            pass

    def _status_text(self, _item=None) -> str:
        s = self.state
        last = STATUS_LABEL.get(s.last_status, "?") if s.total else "aguardando"
        rtt = f"{s.last_rtt_ms:.0f} ms" if s.last_rtt_ms is not None else "-"
        return f"{self.cfg.target}  [{s.probe_name}]  ultimo: {last} {rtt}"

    def _stats_text(self, _item=None) -> str:
        s = self.state
        up = humanize_seconds(time.time() - s.started_at)
        return f"Perda {s.loss_pct:.2f}%  |  quedas {s.outages}  |  amostras {s.total}  |  ativo ha {up}"

    def _build_menu(self):
        import pystray
        from pystray import MenuItem as Item

        from .autostart import is_autostart_enabled

        return pystray.Menu(
            Item(self._status_text, None, enabled=False),
            Item(self._stats_text, None, enabled=False),
            pystray.Menu.SEPARATOR,
            Item("Gerar relatorio (HTML + PDF)", self._on_report),
            Item("Exportar CSV completo", self._on_export_csv),
            pystray.Menu.SEPARATOR,
            Item("Abrir pasta de dados", lambda: _open_path(app_home())),
            Item("Editar configuracao", self._on_edit_config),
            Item(
                "Iniciar com o Windows",
                self._on_toggle_autostart,
                checked=lambda _i: is_autostart_enabled(),
            ),
            pystray.Menu.SEPARATOR,
            Item("Sair", self._on_quit),
        )

    # -- acoes -------------------------------------------------------
    def _notify(self, msg: str, title: str = "PacketLizer"):
        try:
            self._icon.notify(msg, title)
        except Exception:
            log.info("%s: %s", title, msg)

    def _on_report(self, _icon=None, _item=None):
        if self._busy:
            return
        self._busy = True

        def work():
            try:
                from .report import generate_reports

                out = app_home() / "reports"
                paths = generate_reports(self.cfg, out_dir=out, fmt="both")
                self._notify("Relatorio gerado: " + ", ".join(p.name for p in paths))
                for p in paths:
                    if p.suffix == ".html":
                        webbrowser.open(p.as_uri())
            except SystemExit as e:
                self._notify(str(e))
            except Exception as e:  # pragma: no cover
                log.exception("Falha no relatorio")
                self._notify(f"Erro ao gerar relatorio: {e}")
            finally:
                self._busy = False

        threading.Thread(target=work, name="report", daemon=True).start()

    def _on_export_csv(self, _icon=None, _item=None):
        def work():
            try:
                from .report import export_csv

                out = app_home() / "reports" / f"packetlizer_{datetime.now():%Y%m%d_%H%M%S}.csv"
                n = export_csv(self.cfg, out)
                self._notify(f"CSV com {n} amostras: {out.name}")
                _open_path(out.parent)
            except Exception as e:  # pragma: no cover
                self._notify(f"Erro no CSV: {e}")

        threading.Thread(target=work, name="csv", daemon=True).start()

    def _on_edit_config(self, _icon=None, _item=None):
        path = self.cfg._source_path or (app_home() / "config.json")
        if not Path(path).exists():
            self.cfg.save(path)
        _open_path(path)
        self._notify("Editei config.json - reinicie o PacketLizer para aplicar.")

    def _on_toggle_autostart(self, _icon=None, _item=None):
        from .autostart import is_autostart_enabled, set_autostart

        ok, msg = set_autostart(not is_autostart_enabled())
        self._notify(msg if ok else f"Falhou: {msg}")

    def _on_quit(self, _icon=None, _item=None):
        log.info("Encerrando via bandeja...")
        self.monitor.request_stop()
        if self._mon_thread:
            self._mon_thread.join(timeout=8)
        if self._icon:
            self._icon.visible = False
            self._icon.stop()


def run_tray_app(cfg: Config) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        filename=str(app_home() / "packetlizer.log"),
    )
    app_home().mkdir(parents=True, exist_ok=True)
    try:
        return TrayApp(cfg).start()
    except KeyboardInterrupt:
        return 0
