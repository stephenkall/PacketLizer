"""Monitoring loop: probes the target at a fixed cadence and writes to SQLite.

Runs either in the foreground (``--monitor``, with logs and graceful Ctrl+C) or
inside a thread of the tray app.
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

# Stable state keys; the UI maps them to localized text, logs use the English name.
_STATE_NAMES_EN = {
    "paused": "paused (standby)",
    "starting": "starting",
    "outage": "OUTAGE in progress",
    "unstable": "unstable (recent losses)",
    "running": "running",
}


@dataclass
class LiveState:
    """State shared with the tray app (read-only enough to be thread-safe)."""

    probe_name: str = "?"
    probe_reason: str = ""  # a probe.REASON_* key; the UI localizes it
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
    def state_key(self) -> str:
        if self.paused:
            return "paused"
        if self.total == 0:
            return "starting"
        if self.in_outage:
            return "outage"
        if self.consecutive_lost > 0:
            return "unstable"
        return "running"

    @property
    def state_name(self) -> str:
        """English label, for logs. The window uses ``state_key`` + i18n."""
        return _STATE_NAMES_EN[self.state_key]

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
                log.info("Retention: removed %d old samples; compacting...", removed)
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
        st.set_meta("timeout_ms", str(cfg.timeout_ms))
        log.info("Monitoring %s via %s (%s). interval=%.1fs", cfg.target, probe.name, reason, cfg.interval_seconds)

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
                    log.info("Monitor paused (standby).")
                self._stop.wait(0.5)
                continue
            if self.state.paused:
                self.state.paused = False
                log.info("Monitor resumed.")

            cycle_start = time.monotonic()
            ts = int(time.time())
            try:
                res: ProbeResult = probe.probe()
            except PermissionError:
                log.warning("Raw ICMP lost privileges; switching to the OS ping.")
                probe, reason = select_probe(cfg.target, cfg.timeout_ms, prefer_raw=False)
                self.state.probe_name, self.state.probe_reason = probe.name, reason
                st.set_meta("probe", probe.name)
                continue
            except Exception as e:  # never let a one-off error kill the monitor
                log.debug("Probe error: %s", e)
                res = ProbeResult(None, 1)

            st.add(res.rtt_ms, res.status, ts, target=cfg.target)
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
        log.info("Monitor stopped. total=%d lost=%d (%.2f%%) outages=%d",
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
                log.warning("OUTAGE detected at %s (%d consecutive losses, status=%s)",
                            time.strftime("%H:%M:%S", time.localtime(ts)),
                            s.consecutive_lost, STATUS_LABEL.get(res.status, "?"))


def run_monitor_foreground(cfg: Config, duration: float | None = None) -> int:
    """Headless mode (--monitor): no window or icon, console logs only.

    ``duration`` (seconds) stops automatically when the deadline is reached.
    """
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    print(f"PacketLizer -- headless monitor. target={cfg.target} interval={cfg.interval_seconds}s "
          f"db={cfg.resolved_db_path()}" + (f" deadline={duration}s" if duration else ""), flush=True)

    mon = Monitor(cfg)

    def _handler(_sig, _frm):
        if mon._stop.is_set():
            raise SystemExit(1)
        log.info("Ctrl+C -> stopping and saving progress (press again to force-quit).")
        mon.request_stop()

    signal.signal(signal.SIGINT, _handler)
    if platform.system() != "Windows":
        signal.signal(signal.SIGTERM, _handler)

    t = threading.Thread(target=mon.run, name="monitor-run", daemon=True)
    t.start()

    start = time.monotonic()
    last_hb = start
    try:
        while t.is_alive():
            t.join(timeout=5)
            now = time.monotonic()
            s = mon.state
            if s.total and (now - last_hb) >= 15:
                last_hb = now
                log.info("[status] %s | samples=%d loss=%.2f%% outages=%d last=%s",
                         s.state_name, s.total, s.loss_pct, s.outages,
                         STATUS_LABEL.get(s.last_status, "?"))
            if duration and (now - start) >= duration:
                log.info("Run deadline of %ss reached; stopping.", duration)
                mon.request_stop()
                break
    except SystemExit:
        mon.request_stop()
        t.join(timeout=8)
        return 1
    t.join(timeout=8)
    return 0
