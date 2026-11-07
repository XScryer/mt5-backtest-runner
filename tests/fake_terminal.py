"""A fake ``terminal64.exe`` for end-to-end tests.

Launched as ``<copied-python>.exe fake_terminal.py /config:<ini>`` — the real
runner sees a normal process and a normal ``/config:`` argument. Behaviour is
driven by environment variables so each test can shape the run:

* ``FAKE_MT5_DATA_DIR``   — where Tester\\logs and MQL5 live (required)
* ``FAKE_MT5_RESULT``     — the OnTester value to emit (default 42.5)
* ``FAKE_MT5_MODE``       — "real" | "simulated" | "gaps" | "none" tick-mode lines
* ``FAKE_MT5_DELAY``      — seconds to "run" before writing the result
* ``FAKE_MT5_HANG``       — never write a result, never exit
* ``FAKE_MT5_NORESULT``   — write log lines but no OnTester result
* ``FAKE_MT5_COMMON``     — filename to drop into Common\\Files (EA artefact)
* ``FAKE_MT5_NOREPORT``   — suppress the report file (Report= set, none written)
* ``FAKE_MT5_CRASH``      — exit(9) immediately after the log header

Everything it writes mimics the real tester: UTF-16LE log with BOM, tabbed
``ID <TAB> severity <TAB> time <TAB> channel <TAB> message`` lines.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from pathlib import Path


def _read_ini(path: Path) -> dict[str, str]:
    vals: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return vals
    for line in text.splitlines():
        line = line.strip()
        if "=" in line and not line.startswith(("[", ";", "#")):
            k, _, v = line.partition("=")
            vals[k.strip().lower()] = v.strip()
    return vals


def _append_log(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("ab") as fh:
        if new:
            fh.write(b"\xff\xfe")
        fh.write(("\r\n".join(lines) + "\r\n").encode("utf-16-le"))


def _line(code: str, sev: int, msg: str) -> str:
    ts = datetime.now().strftime("%H:%M:%S.") + f"{datetime.now().microsecond // 1000:03d}"
    return f"{code}\t{sev}\t{ts}\tCore 1\t{msg}"


def main() -> int:
    config = next(
        (a for a in sys.argv[1:] if a.lower().startswith("/config:")), None
    )
    if config is None:
        return 3
    ini_path = Path(config.partition(":")[2])
    vals = _read_ini(ini_path)

    data_dir = Path(os.environ["FAKE_MT5_DATA_DIR"])
    log_dir = data_dir / "Tester" / "logs"
    log_file = log_dir / (datetime.now().strftime("%Y%m%d") + ".log")

    lines = [_line("GL", 0, "Cloud servers switched off")]

    # parameters loaded only when the staged .set is actually there
    ep = vals.get("expertparameters", "")
    expert = vals.get("expert", "")
    set_name = ep or (f"{expert}.set" if expert else "")
    if set_name:
        set_path = data_dir / "MQL5" / "Profiles" / "Tester" / set_name
        if set_path.is_file():
            size = set_path.stat().st_size
            lines.append(_line("OR", 0, f"{size} bytes of tester parameters loaded"))

    mode = os.environ.get("FAKE_MT5_MODE", "real")
    sym, per = vals.get("symbol", "SYM"), vals.get("period", "M5")
    if mode == "real":
        lines.append(_line("PE", 0, f"{sym},{per} (FakeSrv): generating based on real ticks"))
    elif mode == "gaps":
        lines.append(_line("PE", 0, f"{sym},{per} (FakeSrv): generating based on real ticks"))
        lines.append(_line("KM", 3, f"{sym} : real ticks absent for 50 minutes of 640954 total minute bars, every tick generation used"))
    elif mode == "simulated":
        lines.append(_line("PE", 0, f"{sym},{per} (FakeSrv): every tick generating"))
        lines.append(_line("ZZ", 0, f"{sym}: 1234567 ticks, 890 bars generated"))
    elif mode == "ohlc":
        lines.append(_line("PE", 0, f"{sym},{per} (FakeSrv): 1 minutes OHLC ticks generating"))

    if os.environ.get("FAKE_MT5_CRASH"):
        _append_log(log_file, lines)
        return 9

    _append_log(log_file, lines)

    if os.environ.get("FAKE_MT5_HANG"):
        while True:
            time.sleep(0.2)

    time.sleep(float(os.environ.get("FAKE_MT5_DELAY", "0.05")))

    if not os.environ.get("FAKE_MT5_NORESULT"):
        result = os.environ.get("FAKE_MT5_RESULT", "42.5")
        _append_log(log_file, [_line("QG", 0, f"OnTester result {result}")])

    report = vals.get("report", "")
    if report and not os.environ.get("FAKE_MT5_NOREPORT"):
        rp = Path(report)
        if not rp.is_absolute():
            rp = data_dir / rp
        rp = rp.with_suffix(".htm")
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text("<html><body>fake report</body></html>", encoding="utf-8")

    common_name = os.environ.get("FAKE_MT5_COMMON")
    if common_name:
        common = data_dir.parent / "Common" / "Files"
        common.mkdir(parents=True, exist_ok=True)
        (common / common_name).write_text("time,price\n1,2\n", encoding="utf-8")

    if vals.get("shutdownterminal", "0") == "1":
        return 0
    # no ShutdownTerminal — linger like the real terminal until closed
    while True:
        time.sleep(0.2)


if __name__ == "__main__":
    sys.exit(main())
