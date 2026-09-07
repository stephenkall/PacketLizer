"""Self-update: check GitHub for a newer release, offer it to the user, and
(if accepted) download the new .exe and replace the running one.

Only meaningful for the packaged executable (``sys.frozen``): a source /
script run has no single file to replace and no build tag to compare, so
:func:`check_for_update` always returns ``None`` in that case.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from . import __version__

try:
    from ._build_tag import BUILD_TAG
except ImportError:  # pragma: no cover - _build_tag.py always ships
    BUILD_TAG = None

log = logging.getLogger("packetlizer.updater")

REPO = "stephenkall/PacketLizer"
API_LATEST_RELEASE = f"https://api.github.com/repos/{REPO}/releases/latest"
HELPER_EXE_NAME = "PacketLizerUpdater.exe"
_TIMEOUT_CHECK = 6
_TIMEOUT_DOWNLOAD = 30


def current_version() -> str:
    """The tag this running build corresponds to (``"v1.0.0"`` in dev)."""
    return BUILD_TAG or f"v{__version__}"


def _parse_tag(tag: str) -> tuple[int, ...]:
    parts = tag.strip().lstrip("vV").split(".")
    out = []
    for p in parts:
        digits = "".join(ch for ch in p if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


def is_newer(candidate: str, baseline: str) -> bool:
    return _parse_tag(candidate) > _parse_tag(baseline)


def fetch_latest_release() -> dict | None:
    """Return ``{"tag": str, "notes": str, "asset_url": str, "asset_name": str}``
    for the latest GitHub release, or ``None`` if it can't be determined
    (offline, rate-limited, no release published yet, ...)."""
    req = urllib.request.Request(
        API_LATEST_RELEASE,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "PacketLizer-Updater"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_CHECK) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
        log.info("Update check failed: %s", e)
        return None

    tag = data.get("tag_name") or ""
    notes = (data.get("body") or "").strip()
    asset_url = asset_name = None
    for asset in data.get("assets", []):
        name = asset.get("name", "")
        if name.lower().endswith(".exe"):
            asset_url = asset.get("browser_download_url")
            asset_name = name
            break
    if not tag or not asset_url:
        return None
    return {"tag": tag, "notes": notes, "asset_url": asset_url, "asset_name": asset_name}


def check_for_update() -> dict | None:
    """Return release info if a newer version is published on GitHub, else None."""
    if not getattr(sys, "frozen", False):
        return None
    info = fetch_latest_release()
    if not info:
        return None
    return info if is_newer(info["tag"], current_version()) else None


def download_and_apply(asset_url: str, on_progress: Callable[[int, int], None] | None = None) -> None:
    """Download the new .exe, then hand off to the bundled updater helper,
    which waits for this exact process to exit, replaces the executable, and
    relaunches it.

    Calls ``sys.exit(0)`` on success so the caller should invoke this from a
    context that can unwind cleanly (e.g. after closing the main window).
    """
    if not getattr(sys, "frozen", False):
        raise RuntimeError("Self-update only supported for the packaged executable.")

    current_exe = Path(sys.executable)
    fd, tmp_name = tempfile.mkstemp(suffix=".exe", prefix="PacketLizer_new_")
    os.close(fd)
    tmp_path = Path(tmp_name)

    req = urllib.request.Request(asset_url, headers={"User-Agent": "PacketLizer-Updater"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT_DOWNLOAD) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        written = 0
        with open(tmp_path, "wb") as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                written += len(chunk)
                if on_progress:
                    on_progress(written, total)

    _spawn_updater_helper(current_exe, tmp_path)
    sys.exit(0)


def _extract_helper_exe() -> Path:
    """Copy the bundled updater helper out to a stable temp path.

    It ships inside the onefile bundle's extraction dir (``sys._MEIPASS``),
    which the bootloader tears down as part of *this* process's own exit --
    the helper must keep running after that, so it needs its own copy of the
    file that survives independently.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if not meipass:
        raise RuntimeError("Updater helper is only available in the packaged build.")
    src = Path(meipass) / HELPER_EXE_NAME
    if not src.exists():
        raise RuntimeError(f"Updater helper missing from the bundle: {src}")
    dst = Path(tempfile.gettempdir()) / HELPER_EXE_NAME
    shutil.copy2(src, dst)
    return dst


def _spawn_updater_helper(current_exe: Path, new_exe: Path) -> None:
    """Launch the (now-extracted) helper exe, telling it exactly which PID
    to wait out and which files are involved. It does the actual swap +
    relaunch once this process is confirmed gone -- see updater_helper.py."""
    helper = _extract_helper_exe()
    kwargs = {}
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    subprocess.Popen(
        [str(helper), "--pid", str(os.getpid()), "--old", str(current_exe), "--new", str(new_exe)],
        close_fds=True,
        **kwargs,
    )
