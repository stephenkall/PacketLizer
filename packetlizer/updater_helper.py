"""Standalone helper that swaps in a freshly downloaded PacketLizer.exe and
relaunches it, run as its own tiny process (``PacketLizerUpdater.exe``).

Why a separate process at all: the main app can't overwrite its own running
.exe file, and a generated .bat file polling `tasklist` by image name turned
out to be unreliable (wrong match, or the parent process not actually
terminating in time) -- silently leaving a downloaded update on disk but
never applied. This helper instead waits on the *exact* parent PID via the
Win32 API, which is unambiguous, then does the swap and relaunch itself.

This module is built by ``build_exe.py`` into its own PyInstaller onefile
exe and embedded inside the main .exe's bundle (see ``updater.py``), so
users only ever download one file -- the main app extracts and runs this
helper itself when an update is accepted. Deliberately stdlib-only (no
project dependencies) to keep that side-build small and fast.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

_WAIT_TIMEOUT_S = 60
_POST_EXIT_GRACE_S = 0.5  # Windows can hold the file handle a moment after exit
_COPY_RETRIES = 10
_COPY_RETRY_DELAY_S = 1.0


def _pid_alive(pid: int) -> bool:
    if sys.platform.startswith("win"):
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            STILL_ACTIVE = 259
            exit_code = ctypes.c_ulong()
            if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    import os

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True  # exists but e.g. owned by someone else -- treat as alive
    return True


def wait_for_pid_exit(pid: int, timeout_s: float = _WAIT_TIMEOUT_S) -> None:
    """Block until ``pid`` is gone, using a real OS wait on Windows (falls
    back to polling if that's unavailable), bounded by ``timeout_s`` either
    way so this helper can never hang forever."""
    if sys.platform.startswith("win"):
        import ctypes

        SYNCHRONIZE = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if handle:
            try:
                ctypes.windll.kernel32.WaitForSingleObject(handle, int(timeout_s * 1000))
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
            return
        # OpenProcess failing usually means the pid is already gone.
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline and _pid_alive(pid):
        time.sleep(0.5)


def replace_with_retries(old: Path, new: Path, retries: int = _COPY_RETRIES,
                         delay_s: float = _COPY_RETRY_DELAY_S) -> bool:
    """Copy ``new`` over ``old``, retrying briefly in case the old file is
    still momentarily locked (AV scan, slow teardown, ...)."""
    for attempt in range(retries):
        try:
            shutil.copy2(new, old)
            return True
        except OSError:
            if attempt == retries - 1:
                return False
            time.sleep(delay_s)
    return False


def _relaunch(exe: Path) -> bool:
    kwargs = {}
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    try:
        subprocess.Popen([str(exe)], close_fds=True, **kwargs)
    except OSError:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="PacketLizer self-update helper")
    p.add_argument("--pid", type=int, required=True, help="PID of the running PacketLizer to wait out")
    p.add_argument("--old", required=True, help="Path of the .exe to replace (also relaunched after)")
    p.add_argument("--new", required=True, help="Path of the freshly downloaded .exe")
    args = p.parse_args(argv)

    old, new = Path(args.old), Path(args.new)

    wait_for_pid_exit(args.pid)
    time.sleep(_POST_EXIT_GRACE_S)

    ok = replace_with_retries(old, new)
    try:
        new.unlink(missing_ok=True)
    except OSError:
        pass

    if not ok:
        return 1
    return 0 if _relaunch(old) else 1


if __name__ == "__main__":
    sys.exit(main())
