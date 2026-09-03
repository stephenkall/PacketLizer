"""Inicio automatico com o Windows, sem exigir privilegio de administrador.

Dois metodos:
  * registro  -> HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run  (padrao)
  * pasta     -> %APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup\\PacketLizer.cmd

O modo "script" roda `pythonw.exe main.py` (sem console, so o icone na bandeja).
O modo "exe" aponta para dist\\PacketLizer.exe quando existir / o processo estiver "frozen".
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

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


def set_autostart(enable: bool, mode: str = "auto", use_startup_folder: bool = False) -> tuple[bool, str]:
    if not sys.platform.startswith("win"):
        return False, "Autostart automatico so e suportado no Windows (use o cron/systemd no Linux)."

    cmd = resolve_command(mode)

    if use_startup_folder:
        d = _startup_dir()
        d.mkdir(parents=True, exist_ok=True)
        cmdfile = d / f"{APP_NAME}.cmd"
        if enable:
            cmdfile.write_text(f'@echo off\r\nstart "" {cmd}\r\n', encoding="utf-8")
            return True, f"Autostart ativado via pasta Inicializar: {cmdfile}"
        if cmdfile.exists():
            cmdfile.unlink()
        return True, "Autostart (pasta Inicializar) removido."

    import winreg  # type: ignore

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_ALL_ACCESS) as key:
        if enable:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
            return True, f"Autostart ativado no registro (HKCU\\...\\Run):\n  {cmd}"
        try:
            winreg.DeleteValue(key, APP_NAME)
            return True, "Autostart removido do registro."
        except FileNotFoundError:
            return True, "Autostart ja estava desativado."


def is_autostart_enabled() -> bool:
    if not sys.platform.startswith("win"):
        return False
    try:
        import winreg  # type: ignore

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except FileNotFoundError:
        return (_startup_dir() / f"{APP_NAME}.cmd").exists()
    except OSError:
        return False
