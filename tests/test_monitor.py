import threading
import time

import pytest

from packetlizer import monitor as monitor_mod
from packetlizer.config import STATUS_OK, Config
from packetlizer.monitor import LiveState, Monitor, MonitorGroup
from packetlizer.netiface import InterfaceInfo
from packetlizer.probe import ProbeResult
from packetlizer.storage import Storage


class _FakeProbe:
    name = "fake"

    def __init__(self):
        self.calls = 0

    def probe(self) -> ProbeResult:
        self.calls += 1
        return ProbeResult(5.0, STATUS_OK)


def _cfg(tmp_path, **overrides) -> Config:
    kwargs = dict(
        target="example.test",
        interval_seconds=0.05,
        timeout_ms=100,
        retention_days=0,
        db_path=str(tmp_path / "m.db"),
    )
    kwargs.update(overrides)
    return Config(**kwargs)


def _run_briefly(mon: Monitor, seconds: float) -> threading.Thread:
    th = threading.Thread(target=mon.run, daemon=True)
    th.start()
    time.sleep(seconds)
    return th


def test_monitor_skips_probing_while_link_is_down(tmp_path, monkeypatch):
    fake = _FakeProbe()
    monkeypatch.setattr(monitor_mod, "select_probe", lambda *a, **k: (fake, "reason"))
    ready = {"value": False}
    monkeypatch.setattr(monitor_mod, "any_interface_ready", lambda: ready["value"])

    mon = Monitor(_cfg(tmp_path))
    th = _run_briefly(mon, 0.2)
    try:
        assert fake.calls == 0
        assert mon.state.link_down is True
        assert mon.state.state_key == "no_link"
    finally:
        mon.request_stop()
        th.join(timeout=2)


def test_monitor_resumes_probing_once_link_is_back(tmp_path, monkeypatch):
    fake = _FakeProbe()
    monkeypatch.setattr(monitor_mod, "select_probe", lambda *a, **k: (fake, "reason"))
    monkeypatch.setattr(monitor_mod, "_LINK_WAIT_S", 0.02)
    ready = {"value": False}
    monkeypatch.setattr(monitor_mod, "any_interface_ready", lambda: ready["value"])

    mon = Monitor(_cfg(tmp_path))
    th = threading.Thread(target=mon.run, daemon=True)
    th.start()
    try:
        time.sleep(0.15)
        assert fake.calls == 0

        ready["value"] = True
        time.sleep(0.25)
        assert fake.calls > 0
        assert mon.state.link_down is False
    finally:
        mon.request_stop()
        th.join(timeout=2)


def test_monitor_tags_samples_with_iface_name(tmp_path, monkeypatch):
    fake = _FakeProbe()
    monkeypatch.setattr(monitor_mod, "select_probe", lambda *a, **k: (fake, "reason"))
    monkeypatch.setattr(monitor_mod, "any_interface_ready", lambda: True)
    monkeypatch.setattr(monitor_mod, "interface_status",
                         lambda name: InterfaceInfo(name=name, ipv4="10.0.0.5", is_up=True))

    cfg = _cfg(tmp_path)
    mon = Monitor(cfg, iface_name="Wi-Fi")
    th = threading.Thread(target=mon.run, daemon=True)
    th.start()
    time.sleep(0.2)
    mon.request_stop()
    th.join(timeout=2)

    with Storage(cfg.resolved_db_path()) as st:
        samples = list(st.iter_samples())
    assert samples
    assert all(s.iface == "Wi-Fi" for s in samples)


def test_monitor_group_defaults_to_single_unbound_monitor(tmp_path):
    group = MonitorGroup(_cfg(tmp_path))
    assert list(group.monitors) == [None]
    assert group.monitors[None].iface_name is None


def test_monitor_group_creates_one_monitor_per_interface(tmp_path):
    group = MonitorGroup(_cfg(tmp_path, interfaces=["Wi-Fi", "Ethernet"]))
    assert set(group.monitors) == {"Wi-Fi", "Ethernet"}
    assert group.monitors["Wi-Fi"].iface_name == "Wi-Fi"


def test_monitor_group_toggle_pause_affects_all_children(tmp_path):
    group = MonitorGroup(_cfg(tmp_path, interfaces=["Wi-Fi", "Ethernet"]))
    assert group.is_paused is False
    turned_on = group.toggle_pause()
    assert turned_on is True
    assert all(m.is_paused for m in group.monitors.values())
    group.toggle_pause()
    assert all(not m.is_paused for m in group.monitors.values())


def test_monitor_group_aggregate_sums_children():
    group = MonitorGroup.__new__(MonitorGroup)  # bypass __init__, build states directly
    group.monitors = {
        "Wi-Fi": type("M", (), {"state": LiveState(total=10, lost=2, outages=1, iface_name="Wi-Fi")})(),
        "Ethernet": type("M", (), {"state": LiveState(total=8, lost=0, outages=0, iface_name="Ethernet")})(),
    }
    agg = group.aggregate
    assert agg.total == 18
    assert agg.lost == 2
    assert agg.outages == 1


def test_monitor_group_aggregate_link_down_only_when_all_interfaces_are_down():
    group = MonitorGroup.__new__(MonitorGroup)
    group.monitors = {
        "Wi-Fi": type("M", (), {"state": LiveState(total=10, iface_name="Wi-Fi", link_down=False)})(),
        "Ethernet": type("M", (), {"state": LiveState(total=0, iface_name="Ethernet", link_down=True)})(),
    }
    # one working interface -> overall picture is fine, not "waiting for network"
    assert group.aggregate.link_down is False

    group.monitors["Wi-Fi"].state.link_down = True
    assert group.aggregate.link_down is True
