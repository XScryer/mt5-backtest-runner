"""Regression tests for the pre-release audit — CLI paths."""

import csv
from pathlib import Path

import pytest

from mt5_runner import cli

from conftest import FAKE_SCRIPT, write_ini, write_set


def run_cli(*argv) -> int:
    return cli.main(list(argv))


def _env_for(cfg, monkeypatch):
    monkeypatch.setenv("MT5RUNNER_TERMINAL_EXE", str(cfg.terminal_exe))
    monkeypatch.setenv("MT5RUNNER_DATA_DIR", str(cfg.data_dir))
    monkeypatch.setenv("MT5RUNNER_RESULTS_DIR", str(cfg.results_dir))
    monkeypatch.setenv("MT5RUNNER_TERMINAL_ARGS", str(FAKE_SCRIPT))
    monkeypatch.setenv("MT5RUNNER_PROCESS_IMAGE", "fake-terminal64.exe")
    monkeypatch.setenv("MT5RUNNER_TIMEOUT_SEC", "30")


def test_queue_relative_entries(fake_mt5, ini_dir, monkeypatch):
    """THE documented queue format — entries relative to the queue's own
    folder. Was exit 2 'invalid' for every entry before the fix."""
    _env_for(fake_mt5, monkeypatch)
    write_set(ini_dir / "relq.set")
    write_ini(ini_dir / "relq.ini")
    q = ini_dir / "queue.txt"
    q.write_text("relq.ini\n")
    code = run_cli("--config", "none.toml", "run", "--queue", str(q))
    assert code == 0
    rows = list(csv.DictReader(fake_mt5.summary_csv.open()))
    assert rows[0]["status"] == "ok"


def test_dry_run_fix_does_not_mutate(fake_mt5, ini_dir, monkeypatch):
    _env_for(fake_mt5, monkeypatch)
    ini = write_ini(ini_dir / "dryfix.ini", Model="real ticks")
    before = ini.read_bytes()
    code = run_cli("--config", "none.toml", "run", "--dry-run", "--fix", str(ini))
    assert ini.read_bytes() == before
    assert code == 0  # warning-level issue is not fatal


def test_force_runs_invalid_ini(fake_mt5, ini_dir, monkeypatch):
    _env_for(fake_mt5, monkeypatch)
    bad = ini_dir / "bad.ini"
    bad.write_text("[Tester]\nExpert=E\n")
    code = run_cli("--config", "none.toml", "run", "--force", str(bad))
    assert code == 1  # ran, recorded as failed
    rows = list(csv.DictReader(fake_mt5.summary_csv.open()))
    assert rows[0]["status"] == "invalid"


def test_queue_plus_positional_inis(fake_mt5, ini_dir, tmp_path, monkeypatch):
    _env_for(fake_mt5, monkeypatch)
    write_set(ini_dir / "a.set")
    write_set(ini_dir / "b.set")
    a = write_ini(ini_dir / "a.ini")
    b = write_ini(ini_dir / "b.ini")
    q = ini_dir / "queue.txt"
    q.write_text("a.ini\n")
    code = run_cli("--config", "none.toml", "run", "--queue", str(q), str(b))
    assert code == 0
    rows = list(csv.DictReader(fake_mt5.summary_csv.open()))
    assert [r["ini"] for r in rows] == ["a.ini", "b.ini"]


def test_validate_strict_fails_on_warning(ini_dir):
    ini = write_ini(ini_dir / "w.ini", Model="real ticks")
    assert run_cli("validate", str(ini)) == 0          # warning only
    assert run_cli("validate", "--strict", str(ini)) == 1


def test_validate_ok_files_strict(ini_dir):
    ini = write_ini(ini_dir / "ok.ini")
    assert run_cli("validate", "--strict", str(ini)) == 0


def test_env_args_with_spaces_noted(fake_mt5, ini_dir, monkeypatch, capsys):
    _env_for(fake_mt5, monkeypatch)
    monkeypatch.setenv("MT5RUNNER_TERMINAL_ARGS", "/portable /skipupdate")
    ini = write_ini(ini_dir / "x.ini")
    run_cli("--config", "none.toml", "-v", "doctor")
    err = capsys.readouterr().err
    assert "ONE" in err or "argument" in err
