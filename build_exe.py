"""Compila um PacketLizer.exe unico com PyInstaller (modo sem console).

    python build_exe.py

Saida: dist/PacketLizer.exe
Use quando o ambiente bloquear execucao de scripts .py mas permitir .exe,
ou para instalar em maquinas sem Python. O autostart detecta o exe em dist/.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent


def main() -> int:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller", "-q"])

    subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt", "-q"])

    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--name", "PacketLizer",
        "--onefile",
        "--noconsole",
        "--collect-submodules", "packetlizer",
        "--hidden-import", "pystray._win32" if sys.platform.startswith("win") else "pystray._xorg",
        str(ROOT / "main.py"),
    ]
    print("->", " ".join(args))
    return subprocess.call(args)


if __name__ == "__main__":
    sys.exit(main())
