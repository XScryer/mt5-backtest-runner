"""Config resolution: precedence, TOML, env vars, auto-discovery."""

from pathlib import Path

from mt5_runner import config as configmod


def test_defaults():
    cfg = configmod.Config()
    assert cfg.timeout_sec == 5400
    assert cfg.process_image == "terminal64.exe"
    assert cfg.results_dir == Path("results")


def test_cli_overrides_everything(tmp_path, monkeypatch):
    monkeypatch.setenv("MT5RUNNER_TERMINAL_EXE", "env_exe")
    cfgfile = tmp_path / "mt5-runner.toml"
    cfgfile.write_text('terminal_exe = "file_exe"\n')
    cfg, _ = configmod.load(
        config_file=cfgfile,
        cli_overrides={"terminal_exe": "cli_exe"},
    )
    assert str(cfg.terminal_exe) == "cli_exe"


def test_env_beats_file(tmp_path, monkeypatch):
    monkeypatch.setenv("MT5RUNNER_TERMINAL_EXE", "env_exe")
    cfgfile = tmp_path / "mt5-runner.toml"
    cfgfile.write_text('terminal_exe = "file_exe"\n')
    cfg, _ = configmod.load(config_file=cfgfile)
    assert str(cfg.terminal_exe) == "env_exe"


def test_file_values(tmp_path, monkeypatch):
    monkeypatch.delenv("MT5RUNNER_TERMINAL_EXE", raising=False)
    cfgfile = tmp_path / "mt5-runner.toml"
    cfgfile.write_text(
        'terminal_exe = "C:\\\\MT5\\\\terminal64.exe"\n'
        'data_dir = "D:\\\\data"\n'
        'timeout_sec = 120\n'
        'terminal_args = ["/portable"]\n'
    )
    cfg, notes = configmod.load(config_file=cfgfile)
    assert str(cfg.terminal_exe).endswith("terminal64.exe")
    assert str(cfg.data_dir) == "D:\\data"
    assert cfg.timeout_sec == 120
    assert cfg.terminal_args == ("/portable",)
    assert any("config file" in n for n in notes)


def test_bad_toml_noted_not_fatal(tmp_path):
    cfgfile = tmp_path / "mt5-runner.toml"
    cfgfile.write_text("this is [not toml")
    _, notes = configmod.load(config_file=cfgfile)
    assert any("ignored" in n for n in notes)


def test_autodetect_fills_gaps(tmp_path, monkeypatch):
    """With nothing configured, discovery supplies exe + data dir."""
    term_root = tmp_path / "appdata" / "MetaQuotes" / "Terminal"
    data = term_root / "HASHHASH"
    (data / "MQL5").mkdir(parents=True)
    install = tmp_path / "install"
    install.mkdir()
    exe = install / "terminal64.exe"
    exe.write_bytes(b"MZ")
    (data / "origin.txt").write_text(str(install), encoding="utf-16")
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))

    cfg, notes = configmod.load(config_file=tmp_path / "nope.toml")
    assert cfg.terminal_exe == exe
    assert cfg.data_dir == data
    assert any("auto-detected" in n for n in notes)


def test_check_missing_everything(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "empty"))
    cfg = configmod.Config()
    problems = configmod.check(cfg)
    assert any("terminal_exe" in p for p in problems)
    assert any("data_dir" in p for p in problems)


def test_check_wrong_data_dir(tmp_path):
    d = tmp_path / "notmt5"
    d.mkdir()
    cfg = configmod.Config(data_dir=d)
    problems = configmod.check(cfg)
    assert any("MQL5" in p for p in problems)


def test_check_bad_timeout():
    cfg = configmod.Config(timeout_sec=0)
    assert any("timeout" in p for p in configmod.check(cfg))


def test_derived_paths(fake_mt5):
    cfg = fake_mt5
    assert cfg.log_dir == cfg.data_dir / "Tester" / "logs"
    assert cfg.profiles_dir == cfg.data_dir / "MQL5" / "Profiles" / "Tester"
    assert cfg.common_files.name == "Files"
    assert cfg.summary_csv.name == "summary.csv"
