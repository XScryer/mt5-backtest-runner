"""Reading and parsing MT5 Strategy Tester logs.

Facts about the tester log that drive this design:

* Lives at ``<data_dir>\\Tester\\logs\\YYYYMMDD.log``.
* Written as **UTF-16LE with BOM** (some builds write plain UTF-8 — we detect).
* **Appends every run of the day** to the same file and can reach multiple
  gigabytes. Loading it whole is not an option — everything here streams.
* To attribute output to *this* run we mark the file+size before launch and
  read only the bytes appended afterwards. Tailing blindly would happily pick
  up a *previous* run's ``OnTester result`` when the current one produced none.

Log line shape (tab-separated): ``ID <TAB> severity <TAB> HH:MM:SS.mmm <TAB>
channel <TAB> message`` — severity 3+ marks problems worth surfacing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

#: Cap on how much of a single run's log we read back (the interesting lines —
#: parameters, tick mode, result — always sit at the end).
MAX_READ_BYTES = 512 * 1024 * 1024

_TAIL_CHUNK = 256 * 1024

_ONTESTER_RE = re.compile(r"OnTester result\s+(-?[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)")
_PARAMS_RE = re.compile(r"(\d+)\s+bytes of tester parameters loaded")
_GAP_RE = re.compile(r"real ticks (absent|discarded) for (\d+) minutes")
_BEGIN_RE = re.compile(r"real ticks begin from ([0-9. :]+)")

_ERROR_PATTERNS = (
    "cannot load expert",
    "expert not found",
    "cannot find expert",
    "tester stopped",
    "tester cannot",
    "oninit returned non-zero",
    "critical error",
    "initialization failed",
    "no data for testing",
    "history data absent",
    "symbol not found",
    "cannot synchronize",
    "access violation",
)


class TickMode:
    """What the log says the tester actually used for tick generation."""

    REAL = "real"
    SIMULATED = "simulated"
    OHLC_1M = "ohlc_1m"
    OPEN_PRICES = "open_prices"
    MATH = "math"
    UNKNOWN = "unknown"


def detect_encoding(head: bytes) -> str:
    """Return the codec name matching the BOM (utf-8 fallback)."""
    if head[:2] == b"\xff\xfe":
        return "utf-16-le"
    if head[:2] == b"\xfe\xff":
        return "utf-16-be"
    if head[:3] == b"\xef\xbb\xbf":
        return "utf-8-sig"
    return "utf-8"


def _message(line: str) -> str:
    """Extract the message column (5th tab-separated field) of a log line."""
    parts = line.split("\t", 4)
    return parts[4] if len(parts) >= 5 else line


def _severity(line: str) -> int:
    parts = line.split("\t", 2)
    if len(parts) >= 2 and parts[1].isdigit():
        return int(parts[1])
    return 0


def tail_lines(path: str | Path, n: int) -> list[str]:
    """Last ``n`` lines of a possibly multi-GB file, read backwards in chunks —
    never loads the whole file. Encoding follows the BOM."""
    path = Path(path)
    with path.open("rb") as fh:
        head = fh.read(4)
        enc = detect_encoding(head)
        fh.seek(0, 2)
        size = fh.tell()
        if size == 0:
            return []
        buf = b""
        pos = size
        # read backwards until we have enough line separators or hit the start
        unit = 2 if enc.startswith("utf-16") else 1
        newline = b"\x0a\x00" if enc == "utf-16-le" else (b"\x00\x0a" if enc == "utf-16-be" else b"\n")
        needed = n
        while pos > 0 and buf.count(newline) <= needed:
            step = min(_TAIL_CHUNK, pos)
            # keep reads aligned to the encoding unit to avoid splitting a char
            step -= step % unit
            if step <= 0:
                step = unit
            pos -= step
            fh.seek(pos)
            buf = fh.read(step) + buf
        if pos > 0:
            # drop the partial first line
            idx = buf.find(newline)
            if idx >= 0:
                buf = buf[idx + len(newline):]
        text = buf.decode(enc, errors="replace").lstrip("\ufeff")
        lines = [ln for ln in text.splitlines() if ln.strip()]
        return lines[-n:]


@dataclass(frozen=True)
class LogMark:
    """Where the log stood before a run — everything after belongs to us."""

    path: Path | None
    size: int


def mark_log_position(log_dir: str | Path) -> LogMark:
    log_dir = Path(log_dir)
    newest = newest_log(log_dir)
    if newest is None:
        return LogMark(None, 0)
    try:
        return LogMark(newest, newest.stat().st_size)
    except OSError:
        return LogMark(newest, 0)


def newest_log(log_dir: str | Path) -> Path | None:
    log_dir = Path(log_dir)
    try:
        logs = [p for p in log_dir.iterdir() if p.is_file() and p.suffix.lower() == ".log"]
    except OSError:
        return None
    if not logs:
        return None
    return max(logs, key=lambda p: p.stat().st_mtime)


def read_new_lines(mark: LogMark, log_dir: str | Path) -> list[str]:
    """Lines appended to the tester log since ``mark`` was taken.

    * same file → read from the marked byte offset
    * a newer log appeared → it belongs to this run, read it (tail-capped)
    * no log at all → empty
    """
    newest = newest_log(log_dir)
    if newest is None:
        return []
    offset = mark.size if mark.path == newest else 0
    try:
        size = newest.stat().st_size
    except OSError:
        return []
    if size <= offset:
        return []
    with newest.open("rb") as fh:
        head = fh.read(4)
        enc = detect_encoding(head)
        start = offset
        if size - start > MAX_READ_BYTES:
            start = size - MAX_READ_BYTES
        fh.seek(start)
        data = fh.read()
    text = data.decode(enc, errors="replace").lstrip("\ufeff")
    lines = text.splitlines()
    # first line may be a partial write — drop it unless we read from 0
    if start > 0 and lines:
        lines = lines[1:]
    return [ln for ln in lines if ln.strip()]


#: Codec aliases that add a signature when encoding — never wanted mid-stream.
_NEEDLE_CODECS = {"utf-8-sig": "utf-8", "utf-16": "utf-16-le"}


def encoded_needle(needle: str, encoding: str) -> bytes:
    """``needle`` encoded the way the log is — lets us byte-search a UTF-16
    stream without decoding it."""
    codec = _NEEDLE_CODECS.get(encoding, encoding)
    return needle.encode(codec, errors="ignore")


def log_contains(mark: LogMark, log_dir: str | Path, needle: str) -> bool:
    """True when ``needle`` appears in bytes appended since ``mark`` — used to
    detect early that a run produced its result (ShutdownTerminal absent)."""
    newest = newest_log(log_dir)
    if newest is None:
        return False
    offset = mark.size if mark.path == newest else 0
    try:
        with newest.open("rb") as fh:
            head = fh.read(4)
            enc = detect_encoding(head)
            fh.seek(offset)
            data = fh.read()
    except OSError:
        return False
    return encoded_needle(needle, enc) in data


@dataclass
class LogScan:
    """Everything worth knowing about one run's worth of log lines."""

    on_tester: float | None = None
    tick_mode: str = TickMode.UNKNOWN
    parameters_loaded: bool = False
    parameters_bytes: int = 0
    real_ticks_begin: str = ""
    real_tick_gap_lines: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    high_severity: list[str] = field(default_factory=list)
    n_lines: int = 0

    @property
    def has_real_tick_gaps(self) -> bool:
        return bool(self.real_tick_gap_lines)


def classify_tick_line(msg: str) -> str | None:
    """Map a tick-generation log message to a TickMode, or None."""
    low = msg.lower()
    if "based on real ticks" in low:
        return TickMode.REAL
    if "real ticks" in low and "generating" in low:
        return TickMode.REAL
    if "1 minute" in low and "ohlc" in low:
        return TickMode.OHLC_1M
    if "open prices" in low:
        return TickMode.OPEN_PRICES
    if "math" in low and "calculat" in low:
        return TickMode.MATH
    if "every tick" in low and "generat" in low:
        return TickMode.SIMULATED
    return None


def scan_lines(lines: list[str]) -> LogScan:
    """Distil a run's log lines into a LogScan."""
    scan = LogScan(n_lines=len(lines))
    for line in lines:
        msg = _message(line)

        m = _ONTESTER_RE.search(msg)
        if m:
            try:
                scan.on_tester = float(m.group(1))
            except ValueError:  # pragma: no cover
                pass

        mode = classify_tick_line(msg)
        if mode is not None:
            # real > everything: a later generic line must not downgrade it
            if mode == TickMode.REAL or scan.tick_mode in (
                TickMode.UNKNOWN,
                TickMode.SIMULATED,
            ):
                scan.tick_mode = mode

        m = _PARAMS_RE.search(msg)
        if m:
            scan.parameters_loaded = True
            scan.parameters_bytes = int(m.group(1))

        m = _GAP_RE.search(msg)
        if m:
            scan.real_tick_gap_lines.append(msg.strip())

        m = _BEGIN_RE.search(msg)
        if m:
            scan.real_ticks_begin = m.group(1)

        if _severity(line) >= 2:
            scan.high_severity.append(msg.strip())

        low = msg.lower()
        if any(p in low for p in _ERROR_PATTERNS):
            scan.errors.append(msg.strip())
    return scan
