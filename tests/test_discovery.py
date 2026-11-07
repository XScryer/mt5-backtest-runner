"""Auto-discovery of MT5 installs via %APPDATA%\\MetaQuotes\\Terminal + origin.txt."""

from pathlib import Path

from mt5_runner import discovery


def _make_install(root: Path, name: str, install_dir: Path | None, mql5: bool = True):
    data = root / name
    data.mkdir(parents=True, exist_ok=True)
    if mql5:
        (data / "MQL5").mkdir(parents=True, exist_ok=True)
    if install_dir is not None:
        (data / "origin.txt").write_text(str(install_dir), encoding="utf-16")
    return data


def test_find_via_origin_txt(tmp_path):
    root = tmp_path / "Terminal"
    install = tmp_path / "MT5 Install"
    install.mkdir()
    exe = install / "terminal64.exe"
    exe.write_bytes(b"MZ")
    _make_install(root, "ABC123", install)

    installs = discovery.find_installations(root)
    assert len(installs) == 1
    i = installs[0]
    assert i.exe == exe
    assert i.exe_exists
    assert i.ok
    assert i.data_dir.name == "ABC123"


def test_skips_common_and_community(tmp_path):
    root = tmp_path / "Terminal"
    _make_install(root, "Common", None)
    _make_install(root, "Community", None)
    _make_install(root, "REALHASH", tmp_path / "x")
    installs = discovery.find_installations(root)
    assert [i.data_dir.name for i in installs] == ["REALHASH"]


def test_skips_dirs_without_mql5(tmp_path):
    root = tmp_path / "Terminal"
    (root / "junk").mkdir(parents=True)
    assert discovery.find_installations(root) == []


def test_portable_install(tmp_path):
    """/portable installs keep exe inside the data dir itself."""
    root = tmp_path / "Terminal"
    data = _make_install(root, "PORTABLE", None)
    exe = data / "terminal64.exe"
    exe.write_bytes(b"MZ")
    installs = discovery.find_installations(root)
    assert installs[0].exe == exe
    assert installs[0].ok


def test_origin_points_to_missing_exe(tmp_path):
    root = tmp_path / "Terminal"
    _make_install(root, "H", tmp_path / "gone")
    installs = discovery.find_installations(root)
    assert not installs[0].exe_exists
    assert not installs[0].ok


def test_pick_single_runnable(tmp_path):
    root = tmp_path / "Terminal"
    install = tmp_path / "i"
    install.mkdir()
    (install / "terminal64.exe").write_bytes(b"MZ")
    _make_install(root, "H", install)
    picked = discovery.pick_installation(discovery.find_installations(root))
    assert picked is not None and picked.ok


def test_pick_ambiguous_returns_none(tmp_path):
    root = tmp_path / "Terminal"
    for name, inst in (("H1", tmp_path / "i1"), ("H2", tmp_path / "i2")):
        inst.mkdir()
        (inst / "terminal64.exe").write_bytes(b"MZ")
        _make_install(root, name, inst)
    assert discovery.pick_installation(discovery.find_installations(root)) is None


def test_no_root(tmp_path):
    assert discovery.find_installations(tmp_path / "nope") == []


def test_installation_derived_paths(tmp_path):
    root = tmp_path / "Terminal"
    data = _make_install(root, "H", tmp_path / "i")
    i = discovery.Installation(exe=Path("x"), data_dir=data, exe_exists=False, origin="")
    assert i.log_dir == data / "Tester" / "logs"
    assert i.profiles_dir == data / "MQL5" / "Profiles" / "Tester"
    assert i.common_files == root / "Common" / "Files"
