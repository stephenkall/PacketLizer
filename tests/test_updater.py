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
