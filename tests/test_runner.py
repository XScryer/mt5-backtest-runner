"""End-to-end runs against the fake terminal — the whole pipeline exercised:
mark log → close → stage .set → launch /config → wait → parse → collect."""

import csv
import time
from pathlib import Path

import pytest

from mt5_runner import runner, setfile, summary, testerlog
from mt5_runner.runner import RunResult

from conftest import write_ini, write_set


# -- single runs ---------------------------------------------------------------

def test_run_happy_path(fake_mt5, ini_dir):
    ini = write_ini(ini_dir / "win.ini")
    write_set(ini_dir / "win.set", {"InpLots": "0.1"})
    res = runner.run_one(ini, fake_mt5)
    assert res.status == "ok"
    assert res.on_tester == 42.5
    assert res.expert == "FakeEA"
    assert res.symbol == "EURUSD"
    assert res.tick_mode == "real"
    assert res.duration_sec < 30


def test_set_staged_under_expert_parameters_name(fake_mt5, ini_dir):
    ini = write_ini(ini_dir / "myrun.ini", ExpertParameters="special.set")
    write_set(ini_dir / "myrun.set", {"InpA": "7"})
    res = runner.run_one(ini, fake_mt5)
    staged = fake_mt5.profiles_dir / "special.set"
    assert staged.is_file()
    assert staged.read_bytes() == (ini_dir / "myrun.set").read_bytes()
    assert res.status == "ok"


def test_missing_set_warns_but_runs(fake_mt5, ini_dir):
    ini = write_ini(ini_dir / "noset.ini")
    res = runner.run_one(ini, fake_mt5)
    assert res.status == "ok"
    assert any("no .set" in w for w in res.warnings)


def test_invalid_ini_not_run(fake_mt5, ini_dir):
    bad = ini_dir / "bad.ini"
    bad.write_text("[Tester]\nExpert=E\n")  # missing required keys
    res = runner.run_one(bad, fake_mt5)
    assert res.status == "invalid"
    assert res.errors


def test_missing_ini_file(fake_mt5, ini_dir):
    res = runner.run_one(ini_dir / "ghost.ini", fake_mt5)
    assert res.status == "invalid"


def test_simulated_ticks_warned(fake_mt5, ini_dir, monkeypatch):
    """Model=4 asked, log shows 'every tick generating' -> loud warning."""
    monkeypatch.setenv("FAKE_MT5_MODE", "simulated")
    ini = write_ini(ini_dir / "sim.ini")
    write_set(ini_dir / "sim.set")
    res = runner.run_one(ini, fake_mt5)
    assert res.tick_mode == "simulated"
    assert any("SIMULATED" in w for w in res.warnings)


def test_real_tick_gaps_warned(fake_mt5, ini_dir, monkeypatch):
    monkeypatch.setenv("FAKE_MT5_MODE", "gaps")
    ini = write_ini(ini_dir / "gap.ini")
    write_set(ini_dir / "gap.set")
    res = runner.run_one(ini, fake_mt5)
    assert res.tick_mode == "real"
    assert any("gaps" in w for w in res.warnings)


def test_no_result_status(fake_mt5, ini_dir, monkeypatch):
    monkeypatch.setenv("FAKE_MT5_NORESULT", "1")
    ini = write_ini(ini_dir / "none.ini")
    res = runner.run_one(ini, fake_mt5)
    assert res.status == "no_result"
    assert res.on_tester is None


def test_stale_result_not_attributed(fake_mt5, ini_dir, monkeypatch):
    """An old 'OnTester result 999' in today's log must NOT become this run's."""
    monkeypatch.setenv("FAKE_MT5_NORESULT", "1")
    log_dir = fake_mt5.log_dir
    import datetime
    p = log_dir / (datetime.datetime.now().strftime("%Y%m%d") + ".log")
    p.write_bytes(b"\xff\xfe" + "XX\t0\t09:00:00.000\tCore 1\tOnTester result 999\r\n".encode("utf-16-le"))
    ini = write_ini(ini_dir / "fresh.ini")
    res = runner.run_one(ini, fake_mt5)
    assert res.status == "no_result"
    assert res.on_tester is None


def test_timeout_kills_and_marks(fake_mt5, ini_dir, monkeypatch):
    monkeypatch.setenv("FAKE_MT5_HANG", "1")
    fake_mt5.timeout_sec = 1.5
    fake_mt5.poll_sec = 0.1
    ini = write_ini(ini_dir / "hang.ini")
    t0 = time.monotonic()
    res = runner.run_one(ini, fake_mt5)
    assert res.status == "timeout"
    assert time.monotonic() - t0 < 15
    # nothing left running
    from mt5_runner import proc
    assert proc.find_pids("fake-terminal64.exe") == []


def test_no_shutdown_terminal_early_completion(fake_mt5, ini_dir):
    """Without ShutdownTerminal=1 the terminal lingers — the runner detects the
    result in the log and closes it itself."""
    ini = write_ini(ini_dir / "linger.ini", ShutdownTerminal="0")
    write_set(ini_dir / "linger.set")
    res = runner.run_one(ini, fake_mt5)
    assert res.status == "ok"
    assert res.on_tester == 42.5
    from mt5_runner import proc
    assert proc.find_pids("fake-terminal64.exe") == []


def test_report_collected(fake_mt5, ini_dir):
    report_base = ini_dir / "out" / "rep"
    ini = write_ini(ini_dir / "rep.ini", Report=str(report_base))
    write_set(ini_dir / "rep.set")
    res = runner.run_one(ini, fake_mt5)
    assert res.status == "ok"
    assert res.report and res.report.endswith(".htm")
    copied = list(fake_mt5.results_dir.glob("rep_*.htm"))
    assert copied, f"report not copied into {fake_mt5.results_dir}"


def test_common_files_collected(fake_mt5, ini_dir, monkeypatch):
    monkeypatch.setenv("FAKE_MT5_COMMON", "trades.csv")
    ini = write_ini(ini_dir / "art.ini")
    write_set(ini_dir / "art.set")
    res = runner.run_one(ini, fake_mt5)
    assert "art_trades.csv" in res.artifacts
    assert (fake_mt5.results_dir / "art_trades.csv").is_file()


def test_terminal_closed_before_set_staged(fake_mt5, ini_dir, monkeypatch):
    """Ordering trap: stage must happen AFTER close, never before."""
    calls = []
    monkeypatch.setattr(
        runner.proc, "close_all",
        lambda *a, **k: calls.append("close") or (0, 0),
    )
    orig_stage = setfile.stage_set
    monkeypatch.setattr(
        runner.setfile, "stage_set",
        lambda *a, **k: calls.append("stage") or orig_stage(*a, **k),
    )
    orig_start = runner.proc.start_terminal
    monkeypatch.setattr(
        runner.proc, "start_terminal",
        lambda *a, **k: calls.append("start") or orig_start(*a, **k),
    )
    ini = write_ini(ini_dir / "ord.ini")
    write_set(ini_dir / "ord.set")
    runner.run_one(ini, fake_mt5)
    assert calls[:3] == ["close", "stage", "start"]


def test_closes_preexisting_terminal(fake_mt5, ini_dir):
    """A terminal already open gets closed before the run (it would otherwise
    clobber the staged .set on exit)."""
    import subprocess
    from mt5_runner import proc
    exe = fake_mt5.terminal_exe
    zombie = subprocess.Popen(
        [str(exe), "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.5)
    try:
        ini = write_ini(ini_dir / "z.ini")
        res = runner.run_one(ini, fake_mt5)
        assert res.status == "ok"
        assert not proc.pid_alive(zombie.pid)
    finally:
        if proc.pid_alive(zombie.pid):
            zombie.kill()


# -- queue -----------------------------------------------------------------------

def test_read_queue_skips_comments_blanks_bom(tmp_path):
    q = tmp_path / "q.txt"
    q.write_bytes(b"\xef\xbb\xbf" + b"# comment\n\nC:\\a\\one.ini\n  \nC:\\b\\two.ini\n")
    assert runner.read_queue(q) == ["C:\\a\\one.ini", "C:\\b\\two.ini"]


def test_pop_queue_preserves_comments(tmp_path):
    q = tmp_path / "q.txt"
    q.write_text("# keep me\nfirst.ini\nsecond.ini\n", encoding="utf-8")
    assert runner.pop_queue(q) == "first.ini"
    remaining = q.read_text()
    assert "# keep me" in remaining and "second.ini" in remaining
    assert runner.pop_queue(q) == "second.ini"
    assert runner.pop_queue(q) is None


def test_resolve_queue_entry_relative(tmp_path):
    q = tmp_path / "sub" / "q.txt"
    q.parent.mkdir()
    target = q.parent / "a.ini"
    target.write_text("x")
    assert runner.resolve_queue_entry("a.ini", q) == target
    assert runner.resolve_queue_entry(str(target), q) == target


def test_run_queue_end_to_end(fake_mt5, ini_dir, tmp_path):
    q = tmp_path / "queue.txt"
    inis = []
    for name in ("qa", "qb", "qc"):
        write_set(ini_dir / f"{name}.set")
        inis.append(write_ini(ini_dir / f"{name}.ini"))
    q.write_text("# batch\n" + "\n".join(str(i) for i in inis) + "\n")

    seen = []
    results = runner.run_queue(q, fake_mt5, on_result=seen.append)
    assert [r.status for r in results] == ["ok", "ok", "ok"]
    assert [r.ini for r in results] == ["qa.ini", "qb.ini", "qc.ini"]
    assert seen == results
    # queue drained, comment preserved
    assert q.read_text().strip() == "# batch"


def test_run_queue_survives_one_bad_entry(fake_mt5, ini_dir, tmp_path):
    q = tmp_path / "queue.txt"
    good = write_ini(ini_dir / "good.ini")
    write_set(ini_dir / "good.set")
    bad = ini_dir / "bad.ini"
    bad.write_text("[Tester]\nExpert=E\n")
    q.write_text(f"{bad}\n{good}\n")
    results = runner.run_queue(q, fake_mt5)
    assert [r.status for r in results] == ["invalid", "ok"]


# -- summary -------------------------------------------------------------------

def test_summary_row_and_append(tmp_path):
    res = RunResult(ini="a.ini", status="ok", expert="EA", symbol="EURUSD",
                    period="M15", model="4", on_tester=12.5, tick_mode="real",
                    duration_sec=3.2, warnings=["w1"], errors=[])
    out = tmp_path / "summary.csv"
    summary.append(out, res)
    summary.append(out, res)
    rows = list(csv.DictReader(out.open()))
    assert len(rows) == 2
    r = rows[0]
    assert r["ini"] == "a.ini" and r["on_tester"] == "12.5"
    assert r["tick_mode"] == "real" and r["warnings"] == "w1"
    assert len(out.read_text().splitlines()) == 3  # header + 2


def test_summary_columns_complete():
    res = RunResult(ini="x.ini", status="timeout")
    row = summary.result_row(res)
    for col in summary.COLUMNS:
        assert col in row
