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
from .netiface import any_interface_ready, interface_status
from .probe import ProbeResult, select_probe
from .storage import Storage

log = logging.getLogger("packetlizer.monitor")

# How often (seconds) to retry while waiting for a usable connection -- after
# waking from sleep, an adapter reconnecting, etc. Short enough to resume
# quickly, long enough not to busy-loop.
_LINK_WAIT_S = 1.0

# Stable state keys; the UI maps them to localized text, logs use the English name.
_STATE_NAMES_EN = {
    "paused": "paused (standby)",
    "no_link": "waiting for network",
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
    iface_name: str = ""    # network adapter name this state belongs to, "" = unbound/default
    link_down: bool = False  # waiting for a usable connection (sleep/resume, adapter down, ...)
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
        if self.link_down:
            return "no_link"
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
    def __init__(self, cfg: Config, state: LiveState | None = None, iface_name: str | None = None):
        self.cfg = cfg
        # iface_name binds every probe this Monitor sends to one specific network
        # adapter's IPv4 (e.g. "Wi-Fi"); None = unbound, the OS picks the route
        # (the pre-existing single-probe behavior).
        self.iface_name = iface_name
        self.state = state or LiveState(iface_name=iface_name or "")
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

    def _link_ready(self) -> bool:
        """True if it's worth sending a probe right now.

        Bound mode (``iface_name`` set): that specific adapter must be up with
        a real IPv4. Unbound mode: at least *some* adapter must look usable.
        Either way this is what keeps a laptop waking from sleep -- or an
        adapter mid-reconnect -- from being charged with packet loss for a
        window it never had a real chance to probe through.
        """
        if self.iface_name:
            info = interface_status(self.iface_name)
            return bool(info and info.usable)
        return any_interface_ready()

    def _current_source_ip(self) -> str | None:
        if not self.iface_name:
            return None
        info = interface_status(self.iface_name)
        return info.ipv4 if info else None

    def run(self) -> None:
        cfg = self.cfg
        st = Storage(cfg.resolved_db_path())
        self._storage = st
        self._apply_retention(st)

        source = self._current_source_ip()
        probe, reason = select_probe(cfg.target, cfg.timeout_ms, cfg.prefer_raw_icmp, source=source)
        self.state.probe_name = probe.name
        self.state.probe_reason = reason
        st.set_meta("target", cfg.target)
        st.set_meta("probe", probe.name)
        st.set_meta("interval_seconds", str(cfg.interval_seconds))
        st.set_meta("timeout_ms", str(cfg.timeout_ms))
        label = f"{cfg.target} via {self.iface_name}" if self.iface_name else cfg.target
        log.info("Monitoring %s using %s (%s). interval=%.1fs", label, probe.name, reason, cfg.interval_seconds)

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

            if not self._link_ready():
                if not self.state.link_down:
                    self.state.link_down = True
                    log.info("No usable connection%s; waiting before probing again.",
                             f" on {self.iface_name}" if self.iface_name else "")
                self._stop.wait(_LINK_WAIT_S)
                continue
            if self.state.link_down:
                self.state.link_down = False
                log.info("Connection back%s; resuming probes.",
                         f" on {self.iface_name}" if self.iface_name else "")

            # Adapters can pick up a new IP (DHCP renewal, rejoining a
            # different network after sleep, ...) -- rebind the probe if so.
            if self.iface_name:
                current_source = self._current_source_ip()
                if current_source != source:
                    source = current_source
                    probe, reason = select_probe(cfg.target, cfg.timeout_ms, cfg.prefer_raw_icmp, source=source)
                    self.state.probe_name, self.state.probe_reason = probe.name, reason

            cycle_start = time.monotonic()
            ts = int(time.time())
            try:
                res: ProbeResult = probe.probe()
            except PermissionError:
                log.warning("Raw ICMP lost privileges; switching to the OS ping.")
                probe, reason = select_probe(cfg.target, cfg.timeout_ms, prefer_raw=False, source=source)
                self.state.probe_name, self.state.probe_reason = probe.name, reason
                st.set_meta("probe", probe.name)
                continue
            except Exception as e:  # never let a one-off error kill the monitor
                log.debug("Probe error: %s", e)
                res = ProbeResult(None, 1)

            st.add(res.rtt_ms, res.status, ts, target=cfg.target, iface=self.iface_name)
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


class MonitorGroup:
    """Runs one :class:`Monitor` per selected network interface, in parallel.

    With ``cfg.interfaces`` empty this is just a thin wrapper around a single
    unbound ``Monitor`` -- the pre-existing behavior. With interfaces
    selected, each gets its own thread, its own probe bound to that adapter's
    IP, and its own :class:`LiveState`; samples land in the same database
    tagged with their ``iface`` so the report can build one tab per adapter.
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        names = list(dict.fromkeys(n for n in cfg.interfaces if n)) or [None]
        self.monitors: dict[str | None, Monitor] = {name: Monitor(cfg, iface_name=name) for name in names}
        self._threads: dict[str | None, threading.Thread] = {}

    @property
    def states(self) -> dict[str | None, LiveState]:
        return {name: m.state for name, m in self.monitors.items()}

    def _run_one(self, name: str | None, m: Monitor) -> None:
        try:
            m.run()
        except Exception:  # pragma: no cover - never let one interface take down the app
            log.exception("Monitor for interface %r crashed", name)

    def start(self) -> None:
        if len(self.monitors) > 1:
            # Every child opens the same database file concurrently. Priming
            # it once here (schema + WAL mode) up front avoids them racing
            # each other over that same one-time setup on a fresh database
            # ("database is locked") the instant they all start together.
            Storage(self.cfg.resolved_db_path()).close()
        for name, m in self.monitors.items():
            th = threading.Thread(target=self._run_one, args=(name, m),
                                  name=f"monitor-{name or 'default'}", daemon=True)
            self._threads[name] = th
            th.start()

    def request_stop(self) -> None:
        for m in self.monitors.values():
            m.request_stop()

    def join(self, timeout: float | None = None) -> None:
        for th in self._threads.values():
            th.join(timeout=timeout)

    def is_alive(self) -> bool:
        return any(th.is_alive() for th in self._threads.values())

    def toggle_pause(self) -> bool:
        turning_on = not self.is_paused
        for m in self.monitors.values():
            (m.pause if turning_on else m.resume)()
        return turning_on

    @property
    def is_paused(self) -> bool:
        return all(m.is_paused for m in self.monitors.values())

    @property
    def aggregate(self) -> LiveState:
        """Synthetic LiveState summarizing every child, for the single
        status indicator in the main window when >1 interface is active."""
        states = list(self.states.values())
        if len(states) == 1:
            return states[0]
        agg = LiveState()
        agg.paused = all(s.paused for s in states)
        agg.total = sum(s.total for s in states)
        agg.lost = sum(s.lost for s in states)
        agg.outages = sum(s.outages for s in states)
        agg.in_outage = any(s.in_outage for s in states)
        agg.consecutive_lost = max((s.consecutive_lost for s in states), default=0)
        # "waiting for network" only if *every* interface is down -- one
        # working adapter means the overall picture is fine, even if another
        # (e.g. an unplugged Ethernet cable) is not.
        agg.link_down = all(s.link_down for s in states)
        agg.started_at = min((s.started_at for s in states), default=time.time())
        latest = max((s for s in states if s.last_ts), key=lambda s: s.last_ts, default=None)
        if latest:
            agg.last_ts = latest.last_ts
            agg.last_status = latest.last_status
            agg.last_rtt_ms = latest.last_rtt_ms
        names = sorted({s.probe_name for s in states if s.probe_name != "?"})
        agg.probe_name = " + ".join(names) or "?"
        return agg


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
    ifaces_txt = f" interfaces={','.join(cfg.interfaces)}" if cfg.interfaces else ""
    print(f"PacketLizer -- headless monitor. target={cfg.target} interval={cfg.interval_seconds}s "
          f"db={cfg.resolved_db_path()}{ifaces_txt}" + (f" deadline={duration}s" if duration else ""),
          flush=True)

    group = MonitorGroup(cfg)

    def _handler(_sig, _frm):
        if not group.is_alive():
            raise SystemExit(1)
        log.info("Ctrl+C -> stopping and saving progress (press again to force-quit).")
        group.request_stop()

    signal.signal(signal.SIGINT, _handler)
    if platform.system() != "Windows":
        signal.signal(signal.SIGTERM, _handler)

    group.start()

    start = time.monotonic()
    last_hb = start
    try:
        while group.is_alive():
            group.join(timeout=5)
            now = time.monotonic()
            s = group.aggregate
            if s.total and (now - last_hb) >= 15:
                last_hb = now
                log.info("[status] %s | samples=%d loss=%.2f%% outages=%d last=%s",
                         s.state_name, s.total, s.loss_pct, s.outages,
                         STATUS_LABEL.get(s.last_status, "?"))
            if duration and (now - start) >= duration:
                log.info("Run deadline of %ss reached; stopping.", duration)
                group.request_stop()
                break
    except SystemExit:
        group.request_stop()
        group.join(timeout=8)
        return 1
    group.join(timeout=8)
    return 0
