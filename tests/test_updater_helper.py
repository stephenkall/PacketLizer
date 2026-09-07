import os
import subprocess
import sys
import time

from packetlizer import updater_helper as uh


def _spawn_and_wait_dead_pid() -> int:
    """A PID guaranteed to have already exited (vanishingly small chance of
    reuse within the test)."""
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait(timeout=10)
    return p.pid


def test_pid_alive_true_for_current_process():
    assert uh._pid_alive(os.getpid()) is True


def test_pid_alive_false_for_an_exited_process():
    assert uh._pid_alive(_spawn_and_wait_dead_pid()) is False


def test_wait_for_pid_exit_returns_promptly_for_a_dead_pid():
    pid = _spawn_and_wait_dead_pid()
    t0 = time.monotonic()
    uh.wait_for_pid_exit(pid, timeout_s=10)
    assert time.monotonic() - t0 < 5  # should not need to wait out the timeout


def test_replace_with_retries_copies_new_content_over_old(tmp_path):
    old = tmp_path / "old.exe"
    new = tmp_path / "new.exe"
    old.write_bytes(b"old content")
    new.write_bytes(b"new content")

    ok = uh.replace_with_retries(old, new, retries=3, delay_s=0)

    assert ok is True
    assert old.read_bytes() == b"new content"


def test_replace_with_retries_gives_up_after_exhausting_attempts(monkeypatch, tmp_path):
    old = tmp_path / "old.exe"
    new = tmp_path / "new.exe"
    old.write_bytes(b"old content")
    new.write_bytes(b"new content")

    calls = []

    def _always_fails(*_a, **_k):
        calls.append(1)
        raise OSError("locked")

    monkeypatch.setattr(uh.shutil, "copy2", _always_fails)

    ok = uh.replace_with_retries(old, new, retries=3, delay_s=0)

    assert ok is False
    assert len(calls) == 3
    assert old.read_bytes() == b"old content"  # untouched


def test_main_relaunches_after_a_successful_swap(monkeypatch, tmp_path):
    old = tmp_path / "old.exe"
    new = tmp_path / "new.exe"
    old.write_bytes(b"old")
    new.write_bytes(b"new")

    monkeypatch.setattr(uh, "wait_for_pid_exit", lambda pid, timeout_s=60: None)
    relaunched = []
    monkeypatch.setattr(uh, "_relaunch", lambda exe: relaunched.append(exe) or True)
    monkeypatch.setattr(uh.time, "sleep", lambda *_: None)

    rc = uh.main(["--pid", str(os.getpid()), "--old", str(old), "--new", str(new)])

    assert rc == 0
    assert old.read_bytes() == b"new"
    assert not new.exists()  # temp download file cleaned up
    assert relaunched == [old]


def test_main_does_not_relaunch_when_the_swap_fails(monkeypatch, tmp_path):
    old = tmp_path / "old.exe"
    new = tmp_path / "new.exe"
    old.write_bytes(b"old")
    new.write_bytes(b"new")

    monkeypatch.setattr(uh, "wait_for_pid_exit", lambda pid, timeout_s=60: None)
    monkeypatch.setattr(uh, "replace_with_retries", lambda *a, **k: False)
    relaunched = []
    monkeypatch.setattr(uh, "_relaunch", lambda exe: relaunched.append(exe) or True)
    monkeypatch.setattr(uh.time, "sleep", lambda *_: None)

    rc = uh.main(["--pid", str(os.getpid()), "--old", str(old), "--new", str(new)])

    assert rc == 1
    assert relaunched == []


def test_relaunch_passes_creationflags_only_on_windows(monkeypatch, tmp_path):
    exe = tmp_path / "old.exe"
    calls = []
    monkeypatch.setattr(uh.subprocess, "Popen", lambda cmd, **kw: calls.append((cmd, kw)))

    assert uh._relaunch(exe) is True

    (cmd, kwargs), = calls
    assert cmd == [str(exe)]
    assert kwargs.get("close_fds") is True
    if sys.platform.startswith("win"):
        assert "creationflags" in kwargs
    else:
        assert "creationflags" not in kwargs


def test_relaunch_returns_false_when_popen_fails(monkeypatch, tmp_path):
    exe = tmp_path / "old.exe"

    def _boom(*_a, **_k):
        raise OSError("nope")

    monkeypatch.setattr(uh.subprocess, "Popen", _boom)

    assert uh._relaunch(exe) is False
