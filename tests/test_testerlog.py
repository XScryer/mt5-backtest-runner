"""Tester log reading and scanning — the UTF-16, multi-GB, incremental core."""

import os
from pathlib import Path

import pytest

from mt5_runner import testerlog
from mt5_runner.testerlog import TickMode


def write_log(path: Path, lines: list[str], enc: str = "utf-16-le", append: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\r\n".join(lines) + "\r\n"
    if enc == "utf-16-le":
        data = (b"" if append else b"\xff\xfe") + text.encode("utf-16-le")
    elif enc == "utf-8-sig":
        data = (b"" if append else b"\xef\xbb\xbf") + text.encode("utf-8")
    else:
        data = text.encode("utf-8")
    with path.open("ab" if append else "wb") as fh:
        fh.write(data)
    return path


def L(sev: int, msg: str, code: str = "XX", chan: str = "Core 1") -> str:
    return f"{code}\t{sev}\t10:00:00.000\t{chan}\t{msg}"


# -- encoding -----------------------------------------------------------------

@pytest.mark.parametrize("head,enc", [
    (b"\xff\xfeA\x00", "utf-16-le"),
    (b"\xfe\xff\x00A", "utf-16-be"),
    (b"\xef\xbb\xbfABC", "utf-8-sig"),
    (b"plain", "utf-8"),
])
def test_detect_encoding(head, enc):
    assert testerlog.detect_encoding(head) == enc


# -- tail_lines ---------------------------------------------------------------

def test_tail_utf16(tmp_path):
    lines = [L(0, f"line {i}") for i in range(50)]
    p = write_log(tmp_path / "a.log", lines)
    tail = testerlog.tail_lines(p, 5)
    assert len(tail) == 5
    assert tail[-1].endswith("line 49")
    assert tail[0].endswith("line 45")


def test_tail_utf8(tmp_path):
    p = write_log(tmp_path / "b.log", [L(0, f"n{i}") for i in range(10)], enc="utf-8")
    tail = testerlog.tail_lines(p, 3)
    assert [t[-2:] for t in tail] == ["n7", "n8", "n9"]


def test_tail_fewer_than_n(tmp_path):
    p = write_log(tmp_path / "c.log", [L(0, "only")])
    assert testerlog.tail_lines(p, 10) == [L(0, "only")]


def test_tail_large_file_stays_fast(tmp_path):
    """A multi-MB log must not be loaded whole — tail 10 lines of ~6MB."""
    p = tmp_path / "big.log"
    with p.open("wb") as fh:
        fh.write(b"\xff\xfe")
        for i in range(200_000):
            fh.write(f"XX\t0\t10:00:00.000\tCore\tfiller {i}\r\n".encode("utf-16-le"))
        fh.write("QG\t0\t10:00:00.000\tCore 1\tOnTester result 7.5\r\n".encode("utf-16-le"))
    assert p.stat().st_size > 5_000_000
    tail = testerlog.tail_lines(p, 3)
    assert tail[-1].endswith("OnTester result 7.5")


# -- incremental reads ----------------------------------------------------------

def test_mark_and_read_new_only(tmp_path):
    """THE correctness fix: stale 'OnTester result' from a previous run must
    never be attributed to this one."""
    log_dir = tmp_path / "logs"
    p = write_log(log_dir / "20260101.log", [L(0, "old stuff"), L(0, "OnTester result 999")])
    mark = testerlog.mark_log_position(log_dir)
    write_log(p, [L(0, "new run line"), L(0, "OnTester result 12.5")], append=True)
    new = testerlog.read_new_lines(mark, log_dir)
    assert any("OnTester result 12.5" in ln for ln in new)
    assert not any("999" in ln for ln in new)


def test_read_new_lines_no_result(tmp_path):
    log_dir = tmp_path / "logs"
    write_log(log_dir / "a.log", [L(0, "OnTester result 999")])
    mark = testerlog.mark_log_position(log_dir)
    # run produced only noise, no new result
    assert not any("999" in ln for ln in testerlog.read_new_lines(mark, log_dir))


def test_read_new_file_appears(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    mark = testerlog.mark_log_position(log_dir)  # nothing yet
    write_log(log_dir / "a.log", [L(0, "run output"), L(0, "OnTester result 3")])
    new = testerlog.read_new_lines(mark, log_dir)
    assert any("OnTester result 3" in ln for ln in new)


def test_read_new_empty_dir(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    mark = testerlog.mark_log_position(log_dir)
    assert testerlog.read_new_lines(mark, log_dir) == []


def test_read_truncated_file(tmp_path):
    log_dir = tmp_path / "logs"
    p = write_log(log_dir / "a.log", [L(0, f"x{i}") for i in range(100)])
    mark = testerlog.mark_log_position(log_dir)
    write_log(p, [L(0, "short")])  # rewritten smaller
    assert testerlog.read_new_lines(mark, log_dir) == []


def test_newest_log_by_mtime(tmp_path):
    log_dir = tmp_path / "logs"
    a = write_log(log_dir / "a.log", [L(0, "a")])
    b = write_log(log_dir / "b.log", [L(0, "b")])
    os.utime(a, (1000, 1000))
    os.utime(b, (2000, 2000))
    assert testerlog.newest_log(log_dir) == b


# -- byte-level search (early completion probe) ---------------------------------

def test_log_contains_utf16(tmp_path):
    log_dir = tmp_path / "logs"
    p = write_log(log_dir / "a.log", [L(0, "start")])
    mark = testerlog.mark_log_position(log_dir)
    write_log(p, [L(0, "OnTester result 1.0")], append=True)
    assert testerlog.log_contains(mark, log_dir, "OnTester result")
    assert not testerlog.log_contains(mark, log_dir, "never written")


def test_log_contains_utf8(tmp_path):
    log_dir = tmp_path / "logs"
    write_log(log_dir / "a.log", [L(0, "OnTester result 5")], enc="utf-8")
    mark = testerlog.LogMark(None, 0)
    assert testerlog.log_contains(mark, log_dir, "OnTester result")


# -- scanning ---------------------------------------------------------------------

def test_scan_on_tester_last_wins():
    lines = [L(0, "OnTester result 1.0"), L(0, "OnTester result -23.75")]
    scan = testerlog.scan_lines(lines)
    assert scan.on_tester == -23.75


def test_scan_tick_modes():
    real = testerlog.scan_lines([L(0, "EURUSD,M15 (Srv): generating based on real ticks")])
    assert real.tick_mode == TickMode.REAL
    sim = testerlog.scan_lines([L(0, "EURUSD,M15 (Srv): every tick generating")])
    assert sim.tick_mode == TickMode.SIMULATED
    ohlc = testerlog.scan_lines([L(0, "EURUSD,M15: 1 minutes OHLC ticks generating")])
    assert ohlc.tick_mode == TickMode.OHLC_1M
    math = testerlog.scan_lines([L(0, "math calculations mode")])
    assert math.tick_mode == TickMode.MATH
    openp = testerlog.scan_lines([L(0, "open prices only mode generating")])
    assert openp.tick_mode == TickMode.OPEN_PRICES


def test_scan_real_tick_gaps():
    lines = [
        L(0, "US500,M30 (Srv): generating based on real ticks"),
        L(3, "US500 : real ticks absent for 50 minutes of 640954 total minute bars, every tick generation used"),
        L(3, "US500 : real ticks discarded for 937 minutes of 640954 total minute bars"),
    ]
    scan = testerlog.scan_lines(lines)
    assert scan.tick_mode == TickMode.REAL
    assert scan.has_real_tick_gaps
    assert len(scan.real_tick_gap_lines) == 2


def test_scan_parameters_loaded():
    scan = testerlog.scan_lines([L(0, "1478 bytes of tester parameters loaded")])
    assert scan.parameters_loaded
    assert scan.parameters_bytes == 1478


def test_scan_errors_and_severity():
    lines = [
        L(0, "normal"),
        L(3, "cannot load expert MyEA"),
        L(2, "a warning"),
    ]
    scan = testerlog.scan_lines(lines)
    assert any("cannot load expert" in e for e in scan.errors)
    assert len(scan.high_severity) == 2


def test_scan_real_ticks_begin():
    scan = testerlog.scan_lines([L(0, "US500 : real ticks begin from 2021.10.01 00:00:00")])
    assert "2021.10.01" in scan.real_ticks_begin


def test_scan_empty():
    scan = testerlog.scan_lines([])
    assert scan.on_tester is None
    assert scan.tick_mode == TickMode.UNKNOWN


def test_classify_unknown_line():
    assert testerlog.classify_tick_line("some random message") is None
