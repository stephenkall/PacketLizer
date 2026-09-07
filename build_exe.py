"""Compila o PacketLizer.exe com PyInstaller (modo sem console).

    python build_exe.py

Saida: dist/PacketLizer.exe

Use quando o ambiente bloquear execucao de scripts .py mas permitir .exe, ou
para instalar em maquinas sem Python. O autostart detecta o exe em dist/.

Compila em DOIS passos:
  1. PacketLizerUpdater.exe -- helper standalone (so stdlib) usado pelo
     auto-update para trocar o binario em execucao e reabri-lo; ver
     packetlizer/updater_helper.py e packetlizer/updater.py.
  2. PacketLizer.exe -- o app principal, com o helper do passo 1 embutido
     via --add-binary (extraido de sys._MEIPASS em tempo de execucao), para
     que o usuario baixe um unico arquivo.

O PacketLizer so precisa de: pystray, Pillow, icmplib, matplotlib (+numpy),
reportlab, psutil e a stdlib (tkinter, sqlite3). A lista EXCLUDES abaixo
evita que o PyInstaller arraste pacotes pesados/irrelevantes que porventura
estejam instalados no Python de desenvolvimento (torch, scipy, pandas,
jupyter, etc.), o que deixaria o .exe gigante ou quebraria o build.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent

EXCLUDES = [
    "torch", "torchvision", "torchaudio", "transformers", "tokenizers", "safetensors",
    "accelerate", "datasets", "huggingface_hub", "nltk", "spacy", "gensim", "sympy",
    "scipy", "pandas", "sklearn", "scikit_learn", "numba", "llvmlite", "cv2",
    "tensorboard", "tensorflow", "keras", "jax", "jaxlib",
    "IPython", "ipykernel", "jupyter", "jupyter_client", "jupyter_core", "notebook",
    "nbconvert", "nbformat", "qtconsole", "pytest", "_pytest", "pluggy", "py",
    "black", "mypy", "pylint", "flake8", "isort", "pydoc_data",
    "PyQt5", "PyQt6", "PySide2", "PySide6", "wx",
    "pandas.tests", "numpy.tests", "matplotlib.tests",
]


def _run_pyinstaller(args: list[str], out_name: str) -> Path:
    print("->", " ".join(args))
    rc = subprocess.call(args)
    exe = ROOT / "dist" / (out_name if sys.platform.startswith("win") else out_name.removesuffix(".exe"))
    if rc != 0 or not exe.exists():
        raise SystemExit(f"PyInstaller failed to produce {exe} (exit code {rc})")
    print(f"OK: {exe}  ({exe.stat().st_size / 1_048_576:.1f} MB)")
    return exe


def _build_updater_helper() -> Path:
    """Tiny standalone exe embedded inside the main build (see module
    docstring). Stdlib-only, so no --hidden-import / exclude juggling needed."""
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--name", "PacketLizerUpdater",
        "--onefile",
        "--noconsole",
        str(ROOT / "packetlizer" / "updater_helper.py"),
    ]
    return _run_pyinstaller(args, "PacketLizerUpdater.exe")


def _build_main_exe(helper_exe: Path) -> Path:
    sep = ";" if sys.platform.startswith("win") else ":"
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--name", "PacketLizer",
        "--onefile",
        "--noconsole",
        "--collect-submodules", "packetlizer",
        "--hidden-import", "pystray._win32" if sys.platform.startswith("win") else "pystray._xorg",
        "--add-binary", f"{helper_exe}{sep}.",
    ]
    for mod in EXCLUDES:
        args += ["--exclude-module", mod]
    args.append(str(ROOT / "main.py"))
    return _run_pyinstaller(args, "PacketLizer.exe")


def main() -> int:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller", "-q"])

    subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt", "-q"])

    helper_exe = _build_updater_helper()
    _build_main_exe(helper_exe)
    return 0


if __name__ == "__main__":
    sys.exit(main())
