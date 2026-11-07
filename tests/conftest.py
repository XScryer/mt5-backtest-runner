"""Shared fixtures: a complete fake MT5 install on disk.

Layout created under tmp_path:

    install/fake-terminal64.exe   <- copy of the running Python interpreter
    appdata/MetaQuotes/Terminal/<hash>/
        origin.txt                <- UTF-16, points at install/
        MQL5/Profiles/Tester/
        Tester/logs/
    appdata/MetaQuotes/Terminal/Common/Files/

The copied interpreter keeps a *distinct* image name so tasklist/taskkill
never confuses it with the real python.exe running pytest.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from mt5_runner.config import Config

FAKE_EXE_NAME = "fake-terminal64.exe"
FAKE_SCRIPT = Path(__file__).parent / "fake_terminal.py"


def _copy_python_runtime(exe_path: str, dest_dir: Path, dest_name: str) -> None:
    """Copy the interpreter + its DLLs so the copy actually runs. A bare
    python.exe fails silently — it needs python3xx.dll and vcruntime next to
    it, plus PYTHONHOME pointing at the real install for the stdlib."""
    src = Path(exe_path)
    shutil.copyfile(src, dest_dir / dest_name)
    for dll in src.parent.glob("*.dll"):
        shutil.copyfile(dll, dest_dir / dll.name)


@pytest.fixture()
def fake_mt5(tmp_path, monkeypatch):
    """A full fake installation + a ready Config pointing at it."""
    if not sys.platform.startswith("win"):
        pytest.skip("the fake terminal needs a runnable .exe copy — Windows only")
    install = tmp_path / "install"
    install.mkdir()
    exe = install / FAKE_EXE_NAME
    _copy_python_runtime(sys.executable, install, FAKE_EXE_NAME)
    # a second copy under the canonical name so discovery's terminal64.exe
    # probe finds a runnable install (origin.txt -> install dir)
    shutil.copyfile(sys.executable, install / "terminal64.exe")

    term_root = tmp_path / "appdata" / "MetaQuotes" / "Terminal"
    data_dir = term_root / "0123456789ABCDEF0123456789ABCDEF"
    (data_dir / "MQL5" / "Profiles" / "Tester").mkdir(parents=True)
    (data_dir / "Tester" / "logs").mkdir(parents=True)
    (term_root / "Common" / "Files").mkdir(parents=True)
    (data_dir / "origin.txt").write_text(str(install), encoding="utf-16")

    cfg = Config(
        terminal_exe=exe,
        data_dir=data_dir,
        results_dir=tmp_path / "results",
        timeout_sec=30.0,
        poll_sec=0.05,
        close_grace_sec=1.0,
        collect_common=True,
        process_image=FAKE_EXE_NAME,
        terminal_args=(str(FAKE_SCRIPT),),
    )
    monkeypatch.setenv("FAKE_MT5_DATA_DIR", str(data_dir))
    # the copied interpreter needs this to find the stdlib
    monkeypatch.setenv("PYTHONHOME", str(Path(sys.executable).parent))
    for var in (
        "FAKE_MT5_RESULT", "FAKE_MT5_MODE", "FAKE_MT5_DELAY", "FAKE_MT5_HANG",
        "FAKE_MT5_NORESULT", "FAKE_MT5_COMMON", "FAKE_MT5_CRASH",
        "FAKE_MT5_NOREPORT",
    ):
        monkeypatch.delenv(var, raising=False)
    return cfg


@pytest.fixture()
def ini_dir(tmp_path):
    d = tmp_path / "inis"
    d.mkdir()
    return d


def write_ini(path: Path, **over) -> Path:
    """Write a valid tester .ini; any [Tester] key overridable by kwargs."""
    tester = {
        "Expert": "FakeEA",
        "ExpertParameters": path.stem + ".set",
        "Symbol": "EURUSD",
        "Period": "M15",
        "Model": "4",
        "FromDate": "2025.01.01",
        "ToDate": "2026.01.01",
        "Deposit": "150",
        "Currency": "USD",
        "Leverage": "30",
        "Optimization": "0",
        "Visual": "0",
        "ShutdownTerminal": "1",
    }
    tester.update({k.replace("_", ""): str(v) for k, v in over.items()})
    body = "\n".join(f"{k}={v}" for k, v in tester.items())
    path.write_text(f"[Common]\nLogin=1\n\n[Tester]\n{body}\n", encoding="utf-8")
    return path


def write_set(path: Path, params: dict[str, str] | None = None, utf16: bool = True) -> Path:
    params = params or {"InpLots": "0.1", "InpStop": "25.0"}
    lines = [f"{k}={v}||{v}||0||0||N" for k, v in params.items()]
    text = "\n".join(lines) + "\n"
    if utf16:
        path.write_bytes(b"\xff\xfe" + text.encode("utf-16-le"))
    else:
        path.write_text(text, encoding="utf-8")
    return path
