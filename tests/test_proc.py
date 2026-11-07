"""Process control: find, close, launch, wait — exercised against real
processes via a renamed copy of the Python interpreter (unique image name, so
tasklist/taskkill never touches other python processes)."""

import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from mt5_runner import proc

FAKE_IMG = "mt5fake-term.exe" if sys.platform.startswith("win") else "mt5fake-term"


@pytest.fixture()
def sleeper_exe(tmp_path, monkeypatch):
    if not sys.platform.startswith("win"):
        pytest.skip("renamed-.exe process control needs Windows")
    from conftest import _copy_python_runtime
    exe = tmp_path / FAKE_IMG
    _copy_python_runtime(sys.executable, tmp_path, FAKE_IMG)
    # the copied interpreter needs PYTHONHOME to find the stdlib
    monkeypatch.setenv("PYTHONHOME", str(Path(sys.executable).parent))
    return exe


def _spawn_sleep(exe: Path, secs: float = 60) -> subprocess.Popen:
    return subprocess.Popen(
        [str(exe), "-c", f"import time; time.sleep({secs})"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def test_find_and_close_by_image_name(sleeper_exe):
    p = _spawn_sleep(sleeper_exe)
    time.sleep(0.5)
    pids = proc.find_pids(FAKE_IMG)
    assert p.pid in pids

    killed = proc.close_pids([p.pid], grace_sec=2.0)
    p.wait(timeout=10)
    assert not proc.pid_alive(p.pid)


def test_close_all_terminates_everything(sleeper_exe):
    p1 = _spawn_sleep(sleeper_exe)
    p2 = _spawn_sleep(sleeper_exe)
    time.sleep(0.5)
    found, forced = proc.close_all(FAKE_IMG, grace_sec=2.0)
    assert found >= 2
    assert not proc.pid_alive(p1.pid)
    assert not proc.pid_alive(p2.pid)


def test_find_pids_none_running():
    assert proc.find_pids("definitely-not-running-xyzzy.exe") == []


def test_wait_for_run_exited(sleeper_exe):
    p = _spawn_sleep(sleeper_exe, secs=0.2)
    outcome = proc.wait_for_run(p, timeout_sec=10, poll_sec=0.05)
    assert outcome == "exited"


def test_wait_for_run_timeout(sleeper_exe):
    p = _spawn_sleep(sleeper_exe, secs=60)
    try:
        outcome = proc.wait_for_run(p, timeout_sec=0.5, poll_sec=0.05)
        assert outcome == "timeout"
    finally:
        p.kill()
        p.wait()


def test_wait_for_run_early_complete(sleeper_exe):
    p = _spawn_sleep(sleeper_exe, secs=60)
    calls = {"n": 0}

    def probe():
        calls["n"] += 1
        return calls["n"] >= 2

    try:
        outcome = proc.wait_for_run(p, timeout_sec=10, poll_sec=0.05, early_complete=probe)
        assert outcome == "completed"
    finally:
        p.kill()
        p.wait()


def test_start_terminal_passes_config_arg(sleeper_exe, tmp_path):
    """The fake exe just records argv — proves /config: uses native separators."""
    out = tmp_path / "argv.txt"
    script = tmp_path / "rec.py"
    script.write_text(
        "import sys, pathlib\n"
        f"pathlib.Path(r'{out}').write_text(repr(sys.argv))\n"
    )
    ini = tmp_path / "cfg.ini"
    ini.write_text("[Tester]\n")
    p = proc.start_terminal(sleeper_exe, ini, extra_args=(str(script),))
    p.wait(timeout=15)
    argv = eval(out.read_text())
    config_arg = next(a for a in argv if a.lower().startswith("/config:"))
    assert str(ini) in config_arg or config_arg.endswith("cfg.ini")
    # native separators — the path after /config: must have no forward slashes
    if sys.platform.startswith("win"):
        path_part = config_arg.partition(":")[2]
        assert "/" not in path_part
