"""CLI surface: subcommands, exit codes, env-configured end-to-end run."""

import csv
import json
import sys
from pathlib import Path

import pytest

from mt5_runner import cli

from conftest import FAKE_SCRIPT, write_ini, write_set


def run_cli(*argv) -> int:
    return cli.main(list(argv))


# -- validate ---------------------------------------------------------------------

def test_validate_ok(ini_dir, capsys):
    ini = write_ini(ini_dir / "v.ini")
    assert run_cli("--config", str(ini_dir / "none.toml"), "validate", str(ini)) == 0
    assert "ok" in capsys.readouterr().out


def test_validate_errors_exit_1(ini_dir):
    bad = ini_dir / "bad.ini"
    bad.write_text("[Tester]\nExpert=E\n")
    assert run_cli("validate", str(bad)) == 1


# -- discover --------------------------------------------------------------------

def test_discover_json(fake_mt5, capsys, monkeypatch):
    # point APPDATA at the fake terminal root
    appdata = fake_mt5.data_dir.parent.parent  # .../MetaQuotes/Terminal -> parent is appdata
    monkeypatch.setenv("APPDATA", str(appdata.parent))
    assert run_cli("discover", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data) == 1
    assert data[0]["runnable"] is True
    assert data[0]["exe"].endswith("terminal64.exe")


def test_discover_none(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("APPDATA", str(tmp_path / "empty"))
    assert run_cli("discover") == 0
    assert "no MT5" in capsys.readouterr().out


# -- doctor ------------------------------------------------------------------------

def test_doctor_ready_via_env(fake_mt5, monkeypatch, capsys):
    monkeypatch.setenv("MT5RUNNER_TERMINAL_EXE", str(fake_mt5.terminal_exe))
    monkeypatch.setenv("MT5RUNNER_DATA_DIR", str(fake_mt5.data_dir))
    assert run_cli("--config", "nope.toml", "doctor") == 0
    assert "ready" in capsys.readouterr().out


def test_doctor_broken(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("APPDATA", str(tmp_path / "empty"))
    monkeypatch.setenv("MT5RUNNER_TERMINAL_EXE", str(tmp_path / "missing.exe"))
    monkeypatch.setenv("MT5RUNNER_DATA_DIR", str(tmp_path / "missing-dir"))
    assert run_cli("--config", "nope.toml", "doctor") == 2
    out = capsys.readouterr().out
    assert "problems" in out


# -- init --------------------------------------------------------------------------

def test_init_writes_toml(fake_mt5, tmp_path, monkeypatch, capsys):
    appdata = fake_mt5.data_dir.parent.parent
    monkeypatch.setenv("APPDATA", str(appdata.parent))
    out = tmp_path / "mt5-runner.toml"
    assert run_cli("init", "--output", str(out)) == 0
    text = out.read_text()
    assert "terminal_exe" in text and "terminal64.exe" in text


def test_init_no_overwrite(fake_mt5, tmp_path, capsys):
    out = tmp_path / "mt5-runner.toml"
    out.write_text("keep = true\n")
    assert run_cli("init", "--output", str(out)) == 2
    assert out.read_text() == "keep = true\n"


# -- run ---------------------------------------------------------------------------

def _env_for(cfg, monkeypatch):
    monkeypatch.setenv("MT5RUNNER_TERMINAL_EXE", str(cfg.terminal_exe))
    monkeypatch.setenv("MT5RUNNER_DATA_DIR", str(cfg.data_dir))
    monkeypatch.setenv("MT5RUNNER_RESULTS_DIR", str(cfg.results_dir))
    monkeypatch.setenv("MT5RUNNER_TERMINAL_ARGS", str(FAKE_SCRIPT))
    monkeypatch.setenv("MT5RUNNER_PROCESS_IMAGE", "fake-terminal64.exe")
    monkeypatch.setenv("MT5RUNNER_TIMEOUT_SEC", "30")


def test_run_dry_run(fake_mt5, ini_dir, monkeypatch, capsys):
    _env_for(fake_mt5, monkeypatch)
    ini = write_ini(ini_dir / "d.ini")
    assert run_cli("--config", "none.toml", "run", "--dry-run", str(ini)) == 0
    assert "dry-run" in capsys.readouterr().out
    # nothing ran — no summary was written
    assert not fake_mt5.summary_csv.exists()


def test_run_end_to_end(fake_mt5, ini_dir, monkeypatch, capsys):
    """Full pipeline through the CLI: env config, fake terminal, real summary."""
    _env_for(fake_mt5, monkeypatch)
    ini = write_ini(ini_dir / "e2e.ini")
    write_set(ini_dir / "e2e.set")
    code = run_cli("--config", "none.toml", "run", str(ini))
    assert code == 0
    rows = list(csv.DictReader(fake_mt5.summary_csv.open()))
    assert len(rows) == 1
    assert rows[0]["ini"] == "e2e.ini"
    assert rows[0]["status"] == "ok"
    assert rows[0]["on_tester"] == "42.5"


def test_run_queue_via_cli(fake_mt5, ini_dir, tmp_path, monkeypatch):
    _env_for(fake_mt5, monkeypatch)
    ini = write_ini(ini_dir / "q1.ini")
    write_set(ini_dir / "q1.set")
    q = tmp_path / "queue.txt"
    q.write_text(str(ini) + "\n")
    code = run_cli("--config", "none.toml", "run", "--queue", str(q))
    assert code == 0
    assert q.read_text().strip() == ""


def test_run_missing_queue(tmp_path, monkeypatch, capsys):
    _env_for_missing(monkeypatch, tmp_path)
    code = run_cli("--config", "none.toml", "run", "--queue", str(tmp_path / "no.txt"))
    assert code == 2


def _env_for_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("MT5RUNNER_TERMINAL_EXE", str(tmp_path / "t.exe"))
    monkeypatch.setenv("MT5RUNNER_DATA_DIR", str(tmp_path / "d"))


def test_run_nothing_to_do(monkeypatch, tmp_path):
    _env_for_missing(monkeypatch, tmp_path)
    code = run_cli("--config", "none.toml", "run")
    assert code == 2


def test_run_invalid_ini_refuses(fake_mt5, ini_dir, monkeypatch):
    _env_for(fake_mt5, monkeypatch)
    bad = ini_dir / "bad.ini"
    bad.write_text("[Tester]\nExpert=E\n")
    assert run_cli("--config", "none.toml", "run", str(bad)) == 2


def test_run_fix_model_flag(fake_mt5, ini_dir, monkeypatch):
    _env_for(fake_mt5, monkeypatch)
    ini = write_ini(ini_dir / "fix.ini", Model="real ticks")
    write_set(ini_dir / "fix.set")
    code = run_cli("--config", "none.toml", "run", "--fix", str(ini))
    assert code == 0
    assert "Model=4" in ini.read_text()
