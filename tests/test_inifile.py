"""Parsing and validation of tester .ini files."""

from pathlib import Path

import pytest

from mt5_runner import inifile

from conftest import write_ini


def test_parse_valid_ini(ini_dir):
    p = write_ini(ini_dir / "ok.ini")
    ini = inifile.parse_ini(p)
    assert ini.errors == []
    assert ini.expert == "FakeEA"
    assert ini.symbol == "EURUSD"
    assert ini.period == "M15"
    assert ini.expert_parameters == "ok.set"
    assert ini.model_code == 4
    assert ini.stem == "ok"
    assert ini.shutdown_terminal is True
    assert ini.optimization is False


def test_missing_file(ini_dir):
    ini = inifile.parse_ini(ini_dir / "nope.ini")
    assert ini.errors and "not found" in ini.errors[0].message


def test_missing_tester_section(ini_dir):
    p = ini_dir / "x.ini"
    p.write_text("[Common]\nLogin=1\n")
    ini = inifile.parse_ini(p)
    assert any("missing [Tester]" in e.message for e in ini.errors)


def test_missing_required_keys(ini_dir):
    p = ini_dir / "x.ini"
    p.write_text("[Tester]\nExpert=EA\n")
    ini = inifile.parse_ini(p)
    missing = {e.key for e in ini.errors}
    assert {"Symbol", "Period", "FromDate", "ToDate"} <= missing


def test_model_numeric_codes(ini_dir):
    for code in range(5):
        p = write_ini(ini_dir / f"m{code}.ini", Model=str(code))
        ini = inifile.parse_ini(p)
        assert ini.model_code == code
        assert not any(i.key == "Model" and i.severity == "error" for i in ini.issues)


def test_model_ui_name_maps_to_code_with_warning(ini_dir):
    """Model=real ticks is the classic silent-fallback trap."""
    p = write_ini(ini_dir / "rt.ini", Model="real ticks")
    ini = inifile.parse_ini(p)
    assert ini.model_code == 4
    warns = [i for i in ini.issues if i.key == "Model" and i.severity == "warning"]
    assert warns and "silently" in warns[0].message


@pytest.mark.parametrize("name,code", [
    ("every tick", 0), ("1 minute ohlc", 1), ("m1 ohlc", 1),
    ("open prices only", 2), ("math calculations", 3),
    ("every tick based on real ticks", 4),
])
def test_model_name_table(ini_dir, name, code):
    p = write_ini(ini_dir / "x.ini", Model=name)
    assert inifile.parse_ini(p).model_code == code


def test_model_invalid_is_error(ini_dir):
    p = write_ini(ini_dir / "x.ini", Model="quantum ticks")
    ini = inifile.parse_ini(p)
    assert ini.model_code is None
    assert any(e.key == "Model" for e in ini.errors)


def test_model_out_of_range_is_error(ini_dir):
    p = write_ini(ini_dir / "x.ini", Model="7")
    ini = inifile.parse_ini(p)
    assert any(e.key == "Model" for e in ini.errors)


def test_model_absent_warns(ini_dir):
    p = ini_dir / "x.ini"
    p.write_text("[Tester]\nExpert=E\nSymbol=S\nPeriod=M5\nFromDate=2025.01.01\nToDate=2026.01.01\n")
    ini = inifile.parse_ini(p)
    assert any(i.key == "Model" and i.severity == "warning" for i in ini.issues)
    assert ini.model_code is None


def test_expert_parameters_with_path_warns(ini_dir):
    p = write_ini(ini_dir / "x.ini", ExpertParameters="subdir\\my.set")
    ini = inifile.parse_ini(p)
    assert any(i.key == "ExpertParameters" and i.severity == "warning" for i in ini.issues)


def test_expert_parameters_wrong_extension_errors(ini_dir):
    p = write_ini(ini_dir / "x.ini", ExpertParameters="params.txt")
    ini = inifile.parse_ini(p)
    assert any(i.key == "ExpertParameters" for i in ini.errors)


def test_no_shutdown_terminal_is_info(ini_dir):
    p = ini_dir / "x.ini"
    p.write_text(
        "[Tester]\nExpert=E\nSymbol=S\nPeriod=M5\n"
        "FromDate=2025.01.01\nToDate=2026.01.01\nModel=4\n"
    )
    ini = inifile.parse_ini(p)
    assert ini.shutdown_terminal is False
    assert any(i.severity == "info" and i.key == "ShutdownTerminal" for i in ini.issues)


def test_utf16_ini_parses(ini_dir):
    p = ini_dir / "u.ini"
    text = "[Tester]\nExpert=E\nSymbol=S\nPeriod=M5\nFromDate=2025.01.01\nToDate=2026.01.01\nModel=4\n"
    p.write_bytes(b"\xff\xfe" + text.encode("utf-16-le"))
    ini = inifile.parse_ini(p)
    assert ini.expert == "E"
    assert ini.model_code == 4


def test_bom_utf8_ini_parses(ini_dir):
    p = ini_dir / "b.ini"
    text = "[Tester]\nExpert=E\nSymbol=S\nPeriod=M5\nFromDate=2025.01.01\nToDate=2026.01.01\nModel=4\n"
    p.write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))
    ini = inifile.parse_ini(p)
    assert ini.expert == "E"


def test_comments_and_whitespace_tolerated(ini_dir):
    p = ini_dir / "c.ini"
    p.write_text(
        "; a comment\n"
        "[Tester]\n"
        "  Expert =  MyEA   \n"
        "# another comment\n"
        "Symbol=EURUSD\nPeriod=M15\nFromDate=2025.01.01\nToDate=2026.01.01\nModel=4\n"
    )
    ini = inifile.parse_ini(p)
    assert ini.expert == "MyEA"


def test_fix_model_line(ini_dir):
    p = write_ini(ini_dir / "rt.ini", Model="real ticks")
    code = inifile.fix_model_line(p)
    assert code == 4
    text = p.read_text()
    assert "Model=4" in text
    assert "real ticks" not in text
    # idempotent
    assert inifile.fix_model_line(p) is None


def test_fix_model_line_invalid_noop(ini_dir):
    p = write_ini(ini_dir / "x.ini", Model="bogus")
    assert inifile.fix_model_line(p) is None
    assert "Model=bogus" in p.read_text()


def test_optimization_flag(ini_dir):
    p = write_ini(ini_dir / "x.ini", Optimization="1")
    assert inifile.parse_ini(p).optimization is True
    p2 = write_ini(ini_dir / "y.ini", Optimization="0")
    assert inifile.parse_ini(p2).optimization is False
