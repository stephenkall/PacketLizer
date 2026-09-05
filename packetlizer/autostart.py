"""Start automatically with Windows, without requiring administrator privileges.

Two methods, both entirely per-user (no admin rights needed):
  * registry -> HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run  (tried first)
  * folder   -> %APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup\\PacketLizer.cmd
               (automatic fallback if the registry write is blocked, e.g. by a
               restrictive Group Policy on a machine where the user isn't a
               local administrator)

The "script" mode runs `pythonw.exe main.py` (no console, tray icon only).
The "exe" mode points at dist\\PacketLizer.exe when it exists / the process is frozen.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    import winreg  # type: ignore
except ImportError:  # non-Windows: registry methods below become no-ops
    winreg = None  # type: ignore

APP_NAME = "PacketLizer"
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _pythonw() -> str:
    exe = Path(sys.executable)
    cand = exe.with_name("pythonw.exe")
    return str(cand if cand.exists() else exe)


def resolve_command(mode: str = "auto") -> str:
    root = _repo_root()
    exe = root / "dist" / f"{APP_NAME}.exe"
    frozen = getattr(sys, "frozen", False)
    if mode == "exe" or (mode == "auto" and (frozen or exe.exists())):
        target = Path(sys.executable) if frozen else exe
        return f'"{target}"'
    return f'"{_pythonw()}" "{root / "main.py"}"'


def _startup_dir() -> Path:
    appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def _startup_cmdfile() -> Path:
    return _startup_dir() / f"{APP_NAME}.cmd"


def _set_via_startup_folder(enable: bool, cmd: str) -> tuple[bool, str]:
    d = _startup_dir()
    d.mkdir(parents=True, exist_ok=True)
    cmdfile = d / f"{APP_NAME}.cmd"
    if enable:
        cmdfile.write_text(f'@echo off\r\nstart "" {cmd}\r\n', encoding="utf-8")
        return True, f"Autostart enabled via the Startup folder: {cmdfile}"
    if cmdfile.exists():
        cmdfile.unlink()
    return True, "Autostart (Startup folder) removed."


def _set_via_registry(enable: bool, cmd: str) -> tuple[bool, str]:
    if winreg is None:
        raise OSError("winreg is not available on this platform")

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_ALL_ACCESS) as key:
        if enable:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
            return True, f"Autostart enabled in the registry (HKCU\\...\\Run):\n  {cmd}"
        try:
            winreg.DeleteValue(key, APP_NAME)
            return True, "Autostart removed from the registry."
        except FileNotFoundError:
            return True, "Autostart was already disabled."


def set_autostart(enable: bool, mode: str = "auto", use_startup_folder: bool = False) -> tuple[bool, str]:
    """Enable/disable start-with-Windows. Returns ``(ok, english_message)`` for the CLI.

    Both methods only touch the current user's own profile (HKCU / the
    per-user Startup folder), so neither needs administrator rights. Some
    locked-down machines still block HKCU writes via Group Policy even for a
    non-admin user; when that happens (``use_startup_folder`` left at its
    default, "auto") we transparently fall back to dropping a shortcut in the
    Startup folder instead of failing outright.
    """
    if not sys.platform.startswith("win"):
        return False, "Automatic start is only supported on Windows (use cron/systemd on Linux)."

    cmd = resolve_command(mode)

    if use_startup_folder:
        return _set_via_startup_folder(enable, cmd)

    try:
        return _set_via_registry(enable, cmd)
    except OSError:
        # Registry write blocked (e.g. Group Policy on a non-admin machine).
        # Make sure disabling one method also clears the other, then fall back.
        if not enable:
            try:
                cmdfile = _startup_cmdfile()
                if cmdfile.exists():
                    cmdfile.unlink()
            except OSError:
                pass
        ok, msg = _set_via_startup_folder(enable, cmd)
        if ok and enable:
            msg += "\n(Registry access was blocked, so the Startup-folder method was used instead.)"
        return ok, msg


def is_autostart_enabled() -> bool:
    if not sys.platform.startswith("win"):
        return False
    if _startup_cmdfile().exists():
        return True
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except FileNotFoundError:
        return False
    except OSError:
        return False
