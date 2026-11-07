"""Staging and parsing of .set parameter files."""

from mt5_runner import inifile, setfile

from conftest import write_ini, write_set


def test_parse_utf16_set(ini_dir):
    p = write_set(ini_dir / "a.set", {"InpLots": "0.1", "InpStop": "25.0"}, utf16=True)
    params = setfile.parse_set(p)
    assert len(params) == 2
    lots = params[0]
    assert lots.name == "InpLots"
    assert lots.value == "0.1"
    assert lots.optimize is False


def test_parse_utf8_set(ini_dir):
    p = write_set(ini_dir / "a.set", {"InpX": "5"}, utf16=False)
    params = setfile.parse_set(p)
    assert params[0].name == "InpX" and params[0].value == "5"


def test_parse_set_optimize_flag(ini_dir):
    p = ini_dir / "o.set"
    p.write_bytes(b"\xff\xfe" + "InpA=1||0||0||10||Y\nInpB=2||0||0||10||N\n".encode("utf-16-le"))
    params = setfile.parse_set(p)
    assert params[0].optimize is True
    assert params[1].optimize is False


def test_parse_set_skips_comments(ini_dir):
    p = ini_dir / "c.set"
    p.write_text("; header comment\nInpA=1||1||0||9||N\n", encoding="utf-8")
    assert len(setfile.parse_set(p)) == 1


def test_target_name_prefers_expert_parameters(ini_dir):
    p = write_ini(ini_dir / "mytest.ini", ExpertParameters="custom-name.set")
    ini = inifile.parse_ini(p)
    assert setfile.target_name(ini) == "custom-name.set"


def test_target_name_strips_directory(ini_dir):
    p = write_ini(ini_dir / "mytest.ini", ExpertParameters="sub/dir\\x.set")
    ini = inifile.parse_ini(p)
    assert setfile.target_name(ini) == "x.set"


def test_target_name_falls_back_to_expert(ini_dir):
    p = ini_dir / "t.ini"
    p.write_text("[Tester]\nExpert=MyRobot\nSymbol=S\nPeriod=M5\nFromDate=1\nToDate=2\n")
    ini = inifile.parse_ini(p)
    assert setfile.target_name(ini) == "MyRobot.set"


def test_stage_copies_to_profiles(ini_dir, tmp_path):
    ini_path = write_ini(ini_dir / "exp.ini", ExpertParameters="params.set")
    write_set(ini_dir / "exp.set", {"InpLots": "0.5"})
    ini = inifile.parse_ini(ini_path)
    profiles = tmp_path / "data" / "MQL5" / "Profiles" / "Tester"

    res = setfile.stage_set(ini, profiles)
    assert res.staged
    assert res.dest == profiles / "params.set"
    assert res.dest.is_file()
    assert res.n_params == 1
    # bytes preserved (UTF-16 in, UTF-16 out)
    assert res.dest.read_bytes() == (ini_dir / "exp.set").read_bytes()


def test_stage_missing_set_warns(ini_dir, tmp_path):
    ini_path = write_ini(ini_dir / "lonely.ini")
    ini = inifile.parse_ini(ini_path)
    res = setfile.stage_set(ini, tmp_path / "profiles")
    assert not res.staged
    assert res.warnings and "defaults" in res.warnings[0]


def test_stage_finds_expert_named_set(ini_dir, tmp_path):
    ini_path = ini_dir / "q.ini"
    ini_path.write_text(
        "[Tester]\nExpert=Robot\nSymbol=S\nPeriod=M5\nFromDate=1\nToDate=2\n"
    )
    write_set(ini_dir / "Robot.set", {"InpA": "1"})
    ini = inifile.parse_ini(ini_path)
    res = setfile.stage_set(ini, tmp_path / "prof")
    assert res.staged
    assert res.dest.name == "Robot.set"


def test_source_candidates_dedupes(ini_dir):
    p = write_ini(ini_dir / "x.ini", ExpertParameters="x.set")
    ini = inifile.parse_ini(p)
    names = [c.name for c in setfile.source_candidates(ini)]
    assert names.count("x.set") == 1  # stem.set == expertparams deduped
