import os
import sys

import pytest

from packetlizer import updater


def test_parse_tag_handles_v_prefix_and_extra_dots():
    assert updater._parse_tag("v1.0.0.17") == (1, 0, 0, 17)
    assert updater._parse_tag("1.2.3") == (1, 2, 3)


def test_is_newer_compares_numerically_not_lexically():
    assert updater.is_newer("v1.0.0.9", "v1.0.0.10") is False
    assert updater.is_newer("v1.0.0.10", "v1.0.0.9") is True
    assert updater.is_newer("v1.0.0.5", "v1.0.0.5") is False


def test_current_version_falls_back_to_dunder_version_when_no_build_tag(monkeypatch):
    monkeypatch.setattr(updater, "BUILD_TAG", None)
    from packetlizer import __version__

    assert updater.current_version() == f"v{__version__}"


def test_current_version_prefers_build_tag(monkeypatch):
    monkeypatch.setattr(updater, "BUILD_TAG", "v1.0.0.42")
    assert updater.current_version() == "v1.0.0.42"


def test_check_for_update_skips_when_not_frozen(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert updater.check_for_update() is None


def test_check_for_update_returns_none_when_up_to_date(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(updater, "BUILD_TAG", "v1.0.0.42")
    monkeypatch.setattr(updater, "fetch_latest_release",
                         lambda: {"tag": "v1.0.0.42", "notes": "", "asset_url": "x", "asset_name": "x.exe"})
    assert updater.check_for_update() is None


def test_check_for_update_returns_info_when_newer(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(updater, "BUILD_TAG", "v1.0.0.42")
    info = {"tag": "v1.0.0.43", "notes": "fixes", "asset_url": "x", "asset_name": "x.exe"}
    monkeypatch.setattr(updater, "fetch_latest_release", lambda: info)
    assert updater.check_for_update() == info


def test_extract_helper_exe_requires_frozen_bundle(monkeypatch):
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    with pytest.raises(RuntimeError, match="packaged build"):
        updater._extract_helper_exe()


def test_extract_helper_exe_requires_helper_present_in_bundle(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    with pytest.raises(RuntimeError, match="missing from the bundle"):
        updater._extract_helper_exe()


def test_extract_helper_exe_copies_to_a_stable_temp_path(monkeypatch, tmp_path):
    meipass = tmp_path / "meipass"
    meipass.mkdir()
    (meipass / updater.HELPER_EXE_NAME).write_bytes(b"fake helper exe")
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)

    out_dir = tmp_path / "temp_out"
    out_dir.mkdir()
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(out_dir))

    dst = updater._extract_helper_exe()

    assert dst == out_dir / updater.HELPER_EXE_NAME
    assert dst.read_bytes() == b"fake helper exe"


def test_spawn_updater_helper_invokes_it_with_pid_old_and_new(monkeypatch, tmp_path):
    helper_path = tmp_path / updater.HELPER_EXE_NAME
    monkeypatch.setattr(updater, "_extract_helper_exe", lambda: helper_path)
    calls = []
    monkeypatch.setattr(updater.subprocess, "Popen", lambda cmd, **kw: calls.append((cmd, kw)))

    old_exe = tmp_path / "app" / "PacketLizer.exe"
    new_exe = tmp_path / "dl" / "new.exe"
    updater._spawn_updater_helper(old_exe, new_exe)

    assert len(calls) == 1
    cmd, kwargs = calls[0]
    assert cmd[0] == str(helper_path)
    assert "--pid" in cmd and str(os.getpid()) in cmd
    assert "--old" in cmd and str(old_exe) in cmd
    assert "--new" in cmd and str(new_exe) in cmd
    assert kwargs.get("close_fds") is True
