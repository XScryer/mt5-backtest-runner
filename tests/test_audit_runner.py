"""Regression tests for the pre-release audit — runner paths."""

import csv
import time
from pathlib import Path

import pytest

from mt5_runner import proc, runner, testerlog

from conftest import write_ini, write_set


def _seed_log_with_result(log_dir: Path, value: str = "999") -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    import datetime
    p = log_dir / (datetime.datetime.now().strftime("%Y%m%d") + ".log")
    p.write_bytes(b"\xff\xfe" +
        f"XX\t0\t09:00:00.000\tCore 1\tOnTester result {value}\r\n".encode("utf-16-le"))
    return p


def test_stale_result_written_during_close_not_attributed(fake_mt5, ini_dir, monkeypatch):
    """A terminal mid-backtest writes its OnTester WHILE being closed. With the
    mark taken AFTER close, those bytes can't leak into our run."""
    import datetime
    log_dir = fake_mt5.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FAKE_MT5_NORESULT", "1")

    real_close = proc.close_all

    def closing_writes_stale(*a, **k):
        out = real_close(*a, **k)
        p = log_dir / (datetime.datetime.now().strftime("%Y%m%d") + ".log")
        mode = "ab" if p.exists() else "wb"
        with p.open(mode) as fh:
            if mode == "wb":
                fh.write(b"\xff\xfe")
            fh.write("XX\t0\t10:00:00.000\tCore 1\tOnTester result 999\r\n".encode("utf-16-le"))
        return out

    monkeypatch.setattr(runner.proc, "close_all", closing_writes_stale)
    ini = write_ini(ini_dir / "fresh.ini")
    res = runner.run_one(ini, fake_mt5)
    assert res.status == "no_result"
    assert res.on_tester is None


def test_close_all_path_scoped(fake_mt5, ini_dir):
    """close_all with exe_path kills only that exe — a same-image process from
    another path (a second 'install') is untouched."""
    import subprocess, sys
    other_dir = ini_dir.parent / "other-install"
    other_dir.mkdir(exist_ok=True)
    other_exe = other_dir / "fake-terminal64.exe"
    from conftest import _copy_python_runtime
    _copy_python_runtime(sys.executable, other_dir, "fake-terminal64.exe")

    ours = subprocess.Popen(
        [str(fake_mt5.terminal_exe), "-c", "import time;time.sleep(60)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    theirs = subprocess.Popen(
        [str(other_exe), "-c", "import time;time.sleep(60)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0)
    try:
        found, survivors = proc.close_all(
            "fake-terminal64.exe", 2.0, exe_path=fake_mt5.terminal_exe)
        assert not proc.pid_alive(ours.pid)
        assert proc.pid_alive(theirs.pid), "other install's process must be untouched"
    finally:
        for p in (ours, theirs):
            if proc.pid_alive(p.pid):
                p.kill()


def test_mode_none_warns_no_confirmation(fake_mt5, ini_dir, monkeypatch):
    monkeypatch.setenv("FAKE_MT5_MODE", "none")
    ini = write_ini(ini_dir / "nm.ini")
    write_set(ini_dir / "nm.set")
    res = runner.run_one(ini, fake_mt5)
    assert res.tick_mode == "unknown"
    assert any("does not confirm real ticks" in w for w in res.warnings)


def test_crash_mid_run_is_not_timeout(fake_mt5, ini_dir, monkeypatch):
    monkeypatch.setenv("FAKE_MT5_CRASH", "1")
    ini = write_ini(ini_dir / "cr.ini")
    res = runner.run_one(ini, fake_mt5)
    assert res.status == "no_result"
    assert res.duration_sec < 20


def test_staged_set_without_params_line_warns(fake_mt5, ini_dir, monkeypatch):
    """Staged .set + no 'parameters loaded' in log -> ran-on-defaults warning."""
    monkeypatch.setenv("FAKE_MT5_NOPARAMLOG", "1")
    ini = write_ini(ini_dir / "np.ini")
    write_set(ini_dir / "np.set")
    res = runner.run_one(ini, fake_mt5)
    assert res.status == "ok"
    assert any("defaults" in w for w in res.warnings)


def test_dead_run_no_log_warns(fake_mt5, ini_dir, monkeypatch):
    monkeypatch.setenv("FAKE_MT5_NOLOG", "1")
    ini = write_ini(ini_dir / "dl.ini")
    res = runner.run_one(ini, fake_mt5)
    assert any("no new tester log output" in w for w in res.warnings)


def test_error_line_triggers_early_completion(fake_mt5, ini_dir, monkeypatch):
    """A fatal log line ends a lingering (no ShutdownTerminal) run early —
    no 90-min wait for a run that died at startup."""
    monkeypatch.setenv("FAKE_MT5_ERRORLINE", "1")
    ini = write_ini(ini_dir / "er.ini", ShutdownTerminal="0")
    write_set(ini_dir / "er.set")
    t0 = time.monotonic()
    res = runner.run_one(ini, fake_mt5)
    assert time.monotonic() - t0 < 25  # far under the 30s test timeout
    assert any("cannot load expert" in e for e in res.errors)


def test_relative_ini_path_resolved(fake_mt5, ini_dir, monkeypatch):
    """run with a relative .ini — /config must get an absolute path."""
    monkeypatch.chdir(ini_dir)
    ini = write_ini(Path("rel.ini"))
    write_set(Path("rel.set"))
    res = runner.run_one("rel.ini", fake_mt5)
    assert res.status == "ok"
    assert res.on_tester == 42.5


def test_run_one_never_raises(fake_mt5, ini_dir):
    """Unwritable-but-existent ini -> status error, no exception."""
    p = ini_dir / "unreadable.ini"
    p.write_bytes(b"\xff")  # parses fine (no sections -> invalid)
    res = runner.run_one(p, fake_mt5)
    assert res.status in ("invalid", "error")


def test_dotted_report_basename(fake_mt5, ini_dir, tmp_path):
    """Report=...run.2025.01 must find run.2025.01.htm, not run.htm."""
    base = ini_dir / "out" / "run.2025.01"
    ini = write_ini(ini_dir / "dot.ini", Report=str(base))
    res = runner.run_one(ini, fake_mt5)
    # fake writes <base>.htm verbatim
    assert res.report.endswith("run.2025.01.htm")


def test_forward_slashes_in_ini_path(fake_mt5, ini_dir):
    """ini given with / separators — /config must still use backslashes."""
    fwd = str(write_ini(ini_dir / "fwd.ini")).replace("\\", "/")
    write_set(ini_dir / "fwd.set")
    res = runner.run_one(fwd, fake_mt5)
    assert res.status == "ok"


def test_close_terminal_false_skips(fake_mt5, ini_dir):
    ini = write_ini(ini_dir / "nc.ini")
    write_set(ini_dir / "nc.set")
    res = runner.run_one(ini, fake_mt5, close_terminal=False)
    assert res.status == "ok"


def test_optimization_note(fake_mt5, ini_dir):
    ini = write_ini(ini_dir / "op.ini", Optimization="1")
    write_set(ini_dir / "op.set")
    res = runner.run_one(ini, fake_mt5)
    assert res.status == "ok"
    assert any("Optimization" in w for w in res.warnings)


# -- testerlog boundary + probe window ------------------------------------------

def test_first_complete_line_not_dropped(tmp_path):
    """Regression: a complete first appended line at a clean boundary must be
    kept (it can carry 'parameters loaded' or the result itself)."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    p = log_dir / "a.log"
    p.write_bytes(b"\xff\xfe" + "XX\t0\t10:00:00.000\tC\told line\r\n".encode("utf-16-le"))
    mark = testerlog.mark_log_position(log_dir)
    with p.open("ab") as fh:
        fh.write("XX\t0\t10:00:01.000\tC\tOnTester result 5\r\n".encode("utf-16-le"))
    new = testerlog.read_new_lines(mark, log_dir)
    assert any("OnTester result 5" in ln for ln in new)


def test_log_contains_caps_read_window(tmp_path, monkeypatch):
    """A needle only in the OLD bytes beyond the probe window -> not found."""
    monkeypatch.setattr(testerlog, "_PROBE_WINDOW", 4096)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    p = log_dir / "a.log"
    mark = testerlog.LogMark(None, 0)
    filler = ("XX\t0\t10:00:00.000\tC\tfiller" + "x" * 40 + "\r\n").encode("utf-16-le")
    with p.open("wb") as fh:
        fh.write(b"\xff\xfe")
        fh.write("XX\t0\t10:00:00.000\tC\tOnTester result 1\r\n".encode("utf-16-le"))
        fh.write(filler * 200)  # > 4KB of trailing bytes
    assert not testerlog.log_contains(mark, log_dir, "OnTester result")
    # but a needle near the end IS found
    with p.open("ab") as fh:
        fh.write("XX\t0\t10:00:01.000\tC\tOnTester result 2\r\n".encode("utf-16-le"))
    assert testerlog.log_contains(mark, log_dir, "OnTester result")
