"""Compila um PacketLizer.exe unico com PyInstaller (modo sem console).

    python build_exe.py

Saida: dist/PacketLizer.exe

Use quando o ambiente bloquear execucao de scripts .py mas permitir .exe, ou
para instalar em maquinas sem Python. O autostart detecta o exe em dist/.

O PacketLizer so precisa de: pystray, Pillow, icmplib, matplotlib (+numpy),
reportlab e a stdlib (tkinter, sqlite3). A lista EXCLUDES abaixo evita que o
PyInstaller arraste pacotes pesados/irrelevantes que porventura estejam
instalados no Python de desenvolvimento (torch, scipy, pandas, jupyter, etc.),
o que deixaria o .exe gigante ou quebraria o build.
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
    ]
    for mod in EXCLUDES:
        args += ["--exclude-module", mod]
    args.append(str(ROOT / "main.py"))

    print("->", " ".join(args))
    rc = subprocess.call(args)
    exe = ROOT / "dist" / ("PacketLizer.exe" if sys.platform.startswith("win") else "PacketLizer")
    if rc == 0 and exe.exists():
        print(f"\nOK: {exe}  ({exe.stat().st_size / 1_048_576:.1f} MB)")
    return rc


if __name__ == "__main__":
    sys.exit(main())
