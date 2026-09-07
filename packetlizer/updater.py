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
    """Download the new .exe, then hand off to a helper script that waits for
    this process to exit, replaces the executable, and relaunches it.

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

    _spawn_replace_and_relaunch(current_exe, tmp_path)
    sys.exit(0)


def _spawn_replace_and_relaunch(current_exe: Path, new_exe: Path) -> None:
    """Write a tiny helper .bat that waits for ``current_exe``'s process to
    exit (it can't overwrite itself while running), swaps in the downloaded
    build, relaunches it, and deletes itself.

    The caller (app.py's ``_on_quit_for_update``) hard-exits the process
    shortly after spawning this, so the wait below is normally very brief --
    but it's bounded (~60s) regardless, so a stuck process can never leave
    this running forever instead of at least attempting the swap.
    """
    bat_path = Path(tempfile.gettempdir()) / "packetlizer_update.bat"
    bat_path.write_text(
        "@echo off\r\n"
        "setlocal\r\n"
        "set tries=0\r\n"
        ":wait\r\n"
        f'tasklist /FI "IMAGENAME eq {current_exe.name}" 2>NUL | find /I "{current_exe.name}" >NUL\r\n'
        "if not errorlevel 1 (\r\n"
        "  set /a tries+=1\r\n"
        "  if %tries% GEQ 60 goto swap\r\n"
        "  timeout /t 1 /nobreak >NUL\r\n"
        "  goto wait\r\n"
        ")\r\n"
        ":swap\r\n"
        f'copy /Y "{new_exe}" "{current_exe}" >NUL\r\n'
        f'del "{new_exe}" >NUL 2>&1\r\n'
        f'start "" "{current_exe}"\r\n'
        'del "%~f0"\r\n',
        encoding="utf-8",
    )
    subprocess.Popen(
        ["cmd", "/c", str(bat_path)],
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
        close_fds=True,
    )
