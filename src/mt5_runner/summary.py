"""One CSV row per run — the durable record of what was tested and what came
back. Appended incrementally so a killed run never loses finished results."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from .runner import RunResult

COLUMNS = [
    "timestamp",
    "ini",
    "status",
    "expert",
    "symbol",
    "period",
    "model",
    "from_date",
    "to_date",
    "deposit",
    "on_tester",
    "tick_mode",
    "duration_sec",
    "report",
    "artifacts",
    "warnings",
    "errors",
]


def result_row(res: RunResult, when: datetime | None = None) -> dict:
    when = when or datetime.now()
    return {
        "timestamp": when.isoformat(timespec="seconds"),
        "ini": res.ini,
        "status": res.status,
        "expert": res.expert,
        "symbol": res.symbol,
        "period": res.period,
        "model": res.model,
        "from_date": res.from_date,
        "to_date": res.to_date,
        "deposit": res.deposit,
        "on_tester": "" if res.on_tester is None else repr(res.on_tester),
        "tick_mode": res.tick_mode,
        "duration_sec": res.duration_sec,
        "report": res.report,
        "artifacts": "|".join(res.artifacts),
        "warnings": " | ".join(res.warnings),
        "errors": " | ".join(res.errors),
    }


def append(path: str | Path, res: RunResult) -> None:
    """Append one result row, writing the header first when the file is new."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        if is_new:
            w.writeheader()
        w.writerow(result_row(res))
