"""Regression tests for bugs found in pre-release audit."""

import os

import pytest

from mt5_runner import config as configmod
from mt5_runner import inifile

from conftest import write_ini


def test_env_bool_false_means_false(tmp_path, monkeypatch):
    """bool('false') is True — env coercion must parse, not cast."""
    monkeypatch.setenv("MT5RUNNER_COLLECT_COMMON", "false")
    cfg, _ = configmod.load(config_file=tmp_path / "none.toml")
    assert cfg.collect_common is False


def test_env_bool_true(tmp_path, monkeypatch):
    monkeypatch.setenv("MT5RUNNER_COLLECT_COMMON", "true")
    cfg, _ = configmod.load(config_file=tmp_path / "none.toml")
    assert cfg.collect_common is True


def test_env_float_garbage_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("MT5RUNNER_TIMEOUT_SEC", "not-a-number")
    cfg, _ = configmod.load(config_file=tmp_path / "none.toml")
    assert cfg.timeout_sec == 5400.0


def test_fix_model_line_utf16(ini_dir):
    """A UTF-16 ini must get its Model= line fixed in the same encoding."""
    p = ini_dir / "u16.ini"
    text = ("[Tester]\nExpert=E\nSymbol=S\nPeriod=M5\nModel=real ticks\n"
            "FromDate=2025.01.01\nToDate=2026.01.01\n")
    p.write_bytes(b"\xff\xfe" + text.encode("utf-16-le"))
    assert inifile.fix_model_line(p) == 4
    raw = p.read_bytes()
    assert raw.startswith(b"\xff\xfe")  # BOM preserved
    decoded = raw[2:].decode("utf-16-le")
    assert "Model=4" in decoded
    # and still parses
    assert inifile.parse_ini(p).model_code == 4
