import pytest

from packetlizer import autostart


class _FakeWinreg:
    """Stands in for the stdlib ``winreg`` module.

    Tests here never exercise the real registry-fallback path (that's
    monkeypatched separately) -- this only needs to satisfy
    ``is_autostart_enabled``'s ``OpenKey`` probe, acting as if the Run key
    never has our value set. Always installed (even on a real Windows dev
    machine that may have a genuine PacketLizer autostart entry already) so
    these tests never depend on -- or disturb -- actual machine state.
    """

    HKEY_CURRENT_USER = object()

    def OpenKey(self, *_a, **_k):
        raise FileNotFoundError()


@pytest.fixture(autouse=True)
def _fake_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(autostart.sys, "platform", "win32")
    monkeypatch.setattr(autostart, "_repo_root", lambda: tmp_path)
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    monkeypatch.setattr(autostart, "winreg", _FakeWinreg())
    yield


def test_set_autostart_falls_back_to_startup_folder_when_registry_blocked(monkeypatch):
    def _blocked(*_a, **_k):
        raise PermissionError("Access is denied")

    monkeypatch.setattr(autostart, "_set_via_registry", _blocked)

    ok, msg = autostart.set_autostart(True, mode="script")

    assert ok
    assert "Startup" in msg or "Startup-folder" in msg
    assert autostart._startup_cmdfile().exists()
    assert autostart.is_autostart_enabled()


def test_set_autostart_disable_clears_both_methods_after_fallback(monkeypatch):
    def _blocked(*_a, **_k):
        raise PermissionError("Access is denied")

    monkeypatch.setattr(autostart, "_set_via_registry", _blocked)

    autostart.set_autostart(True, mode="script")
    assert autostart._startup_cmdfile().exists()

    ok, _msg = autostart.set_autostart(False, mode="script")
    assert ok
    assert not autostart._startup_cmdfile().exists()
    assert not autostart.is_autostart_enabled()


def test_set_autostart_uses_registry_when_available(monkeypatch):
    calls = []
    monkeypatch.setattr(autostart, "_set_via_registry",
                         lambda enable, cmd: (calls.append((enable, cmd)) or (True, "ok")))

    ok, msg = autostart.set_autostart(True, mode="script")

    assert ok and msg == "ok"
    assert calls == [(True, autostart.resolve_command("script"))]
    assert not autostart._startup_cmdfile().exists()
