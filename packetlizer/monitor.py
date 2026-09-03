"""Laco de monitoramento: sonda o alvo em cadencia fixa e grava no SQLite.

Pode rodar em primeiro plano (`--monitor`, com logs e Ctrl+C gracioso) ou
dentro de uma thread do app de bandeja.
"""
from __future__ import annotations

import logging
import platform
import signal
import threading
import time
from dataclasses import dataclass, field

from .config import Config, STATUS_LABEL, STATUS_OK
from .probe import ProbeResult, select_probe
from .storage import Storage

log = logging.getLogger("packetlizer.monitor")


@dataclass
class LiveState:
    """Estado compartilhado com a bandeja (thread-safe o suficiente para leitura)."""

    probe_name: str = "?"
    probe_reason: str = ""
    last_ts: float = 0.0
    last_status: int = STATUS_OK
    last_rtt_ms: float | None = None
    total: int = 0
    lost: int = 0
    consecutive_lost: int = 0
    in_outage: bool = False
    outages: int = 0
    paused: bool = False
    started_at: float = field(default_factory=time.time)

    @property
    def state_name(self) -> str:
        if self.paused:
            return "Em pausa (standby)"
        if self.total == 0:
            return "Iniciando..."
        if self.in_outage:
            return "QUEDA em andamento"
        if self.consecutive_lost > 0:
            return "Instavel (perdas recentes)"
        return "Em execucao"

    @property
    def loss_pct(self) -> float:
        return (self.lost / self.total * 100.0) if self.total else 0.0


class Monitor:
    def __init__(self, cfg: Config, state: LiveState | None = None):
        self.cfg = cfg
        self.state = state or LiveState()
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._storage: Storage | None = None

    def request_stop(self) -> None:
        self._stop.set()

    def pause(self) -> None:
        self._pause.set()

    def resume(self) -> None:
        self._pause.clear()

    def toggle_pause(self) -> bool:
        if self._pause.is_set():
            self._pause.clear()
        else:
            self._pause.set()
        return self._pause.is_set()

    @property
    def is_paused(self) -> bool:
        return self._pause.is_set()

    def _apply_retention(self, st: Storage) -> None:
        if self.cfg.retention_days and self.cfg.retention_days > 0:
            cutoff = int(time.time() - self.cfg.retention_days * 86400)
            removed = st.purge_older_than(cutoff)
            if removed:
                log.info("Retencao: %d amostras antigas removidas; compactando...", removed)
                st.vacuum()

    def run(self) -> None:
        cfg = self.cfg
        st = Storage(cfg.resolved_db_path())
        self._storage = st
        self._apply_retention(st)

        probe, reason = select_probe(cfg.target, cfg.timeout_ms, cfg.prefer_raw_icmp)
        self.state.probe_name = probe.name
        self.state.probe_reason = reason
        st.set_meta("target", cfg.target)
        st.set_meta("probe", probe.name)
        st.set_meta("interval_seconds", str(cfg.interval_seconds))
        st.set_meta("timeout_sentinel_ms", str(cfg.timeout_sentinel_ms))
        log.info("Monitorando %s via %s (%s). Intervalo=%.1fs", cfg.target, probe.name, reason, cfg.interval_seconds)

        pending = 0
        last_flush = time.monotonic()
        interval = max(0.2, float(cfg.interval_seconds))

        while not self._stop.is_set():
            if self._pause.is_set():
                if not self.state.paused:
                    if pending:
                        st.commit()
                        pending = 0
                    self.state.paused = True
                    log.info("Monitor em pausa (standby).")
                self._stop.wait(0.5)
                continue
            if self.state.paused:
                self.state.paused = False
                log.info("Monitor retomado.")

            cycle_start = time.monotonic()
            ts = int(time.time())
            try:
                res: ProbeResult = probe.probe()
            except PermissionError:
                log.warning("ICMP raw perdeu privilegio; trocando para o ping do SO.")
                probe, reason = (select_probe(cfg.target, cfg.timeout_ms, prefer_raw=False))
                self.state.probe_name, self.state.probe_reason = probe.name, reason
                st.set_meta("probe", probe.name)
                continue
            except Exception as e:  # nunca derruba o monitor por um erro pontual
                log.debug("Erro na sonda: %s", e)
                res = ProbeResult(None, 1)

            st.add(res.rtt_ms, res.status, ts)
            pending += 1
            self._update_state(res, ts)

            now = time.monotonic()
            if pending >= 10 or (now - last_flush) >= 10:
                st.commit()
                pending, last_flush = 0, now

            sleep_for = interval - (time.monotonic() - cycle_start)
            if sleep_for > 0:
                self._stop.wait(sleep_for)

        st.commit()
        st.close()
        log.info("Monitor encerrado. total=%d perdas=%d (%.2f%%) quedas=%d",
                 self.state.total, self.state.lost, self.state.loss_pct, self.state.outages)

    def _update_state(self, res: ProbeResult, ts: int) -> None:
        s = self.state
        s.last_ts = ts
        s.last_status = res.status
        s.last_rtt_ms = res.rtt_ms
        s.total += 1
        if res.status == STATUS_OK:
            s.consecutive_lost = 0
            if s.in_outage:
                s.in_outage = False
        else:
            s.lost += 1
            s.consecutive_lost += 1
            if not s.in_outage and s.consecutive_lost >= self.cfg.outage_min_consecutive:
                s.in_outage = True
                s.outages += 1
                log.warning("QUEDA detectada as %s (%d perdas consecutivas, status=%s)",
                            time.strftime("%H:%M:%S", time.localtime(ts)),
                            s.consecutive_lost, STATUS_LABEL.get(res.status, "?"))


def run_monitor_foreground(cfg: Config) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    mon = Monitor(cfg)

    def _handler(_sig, _frm):
        if mon._stop.is_set():
            raise SystemExit(1)
        log.info("Ctrl+C -> encerrando e salvando progresso (pressione de novo para forcar).")
        mon.request_stop()

    signal.signal(signal.SIGINT, _handler)
    if platform.system() != "Windows":
        signal.signal(signal.SIGTERM, _handler)

    try:
        mon.run()
    except SystemExit:
        return 1
    return 0
