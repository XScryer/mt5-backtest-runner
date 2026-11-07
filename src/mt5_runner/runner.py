"""Run orchestration: one .ini at a time, start to summary row.

Order matters — two of MT5's traps are sequencing traps:

1. **Close the terminal, THEN stage the .set.** A running terminal rewrites
   ``Profiles\\Tester\\*.set`` on exit; staging first gets silently clobbered
   and the test runs on yesterday's parameters.
2. **Mark the log position BEFORE launching.** The tester appends to one file
   per day; reading "the newest log" afterwards could attribute yesterday's
   ``OnTester result`` to today's run.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import inifile, proc, setfile, testerlog
from .config import Config

log = logging.getLogger("mt5_runner")


@dataclass
class RunResult:
    """The outcome of one .ini — everything the summary row needs."""

    ini: str
    status: str  # ok | no_result | timeout | invalid | error
    expert: str = ""
    symbol: str = ""
    period: str = ""
    model: str = ""
    from_date: str = ""
    to_date: str = ""
    deposit: str = ""
    on_tester: float | None = None
    tick_mode: str = testerlog.TickMode.UNKNOWN
    duration_sec: float = 0.0
    report: str = ""
    artifacts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _resolve_report_path(ini: inifile.TesterIni, cfg: Config) -> Path | None:
    """Where MT5 will have written the report, if Report= was set.

    ``Report=`` takes a path *without* extension; MT5 appends ``.htm``, and
    writes XML when the path already ends in ``.xml``. Relative paths resolve
    against the terminal's data dir.
    """
    raw = ini.report
    if not raw:
        return None
    base = Path(os.path.normpath(raw))
    if not base.is_absolute():
        base = cfg.data_dir / base
    # MT5 appends the extension to the literal name — with_suffix would eat
    # a dot in the basename ("run.2025.01" -> "run.htm" instead of
    # "run.2025.01.htm")
    for cand in (Path(str(base) + ".htm"), Path(str(base) + ".xml"), base):
        if cand.is_file():
            return cand
    return None


def _collect_common_files(cfg: Config, since_ts: float, stem: str) -> list[str]:
    """Copy files the expert wrote to Terminal\\Common\\Files during the run
    into the results dir, prefixed with the run name."""
    out: list[str] = []
    common = cfg.common_files
    if not common.is_dir():
        return out
    for f in sorted(common.iterdir()):
        if not f.is_file():
            continue
        try:
            if f.stat().st_mtime < since_ts:
                continue
        except OSError:
            continue
        dest = cfg.results_dir / f"{stem}_{f.name}"
        try:
            shutil.copyfile(f, dest)
            out.append(dest.name)
        except OSError as exc:
            log.warning("could not collect %s: %s", f, exc)
    return out


def _check_warnings(
    ini: inifile.TesterIni, scan: testerlog.LogScan, staged: setfile.StageResult
) -> list[str]:
    """Cross-check what the .ini asked for against what the log says happened —
    this is where silent MT5 fallbacks surface."""
    warns: list[str] = []

    code = ini.model_code
    if code == 4 and scan.tick_mode == testerlog.TickMode.SIMULATED:
        warns.append(
            "Model=4 requested but the log shows SIMULATED ticks "
            "('every tick generating') — no real ticks for this symbol/period"
        )
    elif code == 4 and scan.tick_mode == testerlog.TickMode.UNKNOWN and scan.n_lines:
        warns.append("Model=4 requested but the log does not confirm real ticks")
    if scan.tick_mode == testerlog.TickMode.REAL and scan.has_real_tick_gaps:
        warns.append(
            f"real ticks with gaps: {len(scan.real_tick_gap_lines)} gap lines — "
            "part of the period fell back to generated ticks"
        )

    if staged.staged and not scan.parameters_loaded and scan.on_tester is not None:
        warns.append(
            "a .set was staged but the log shows no 'tester parameters loaded' — "
            "the expert may have run on defaults"
        )
    if scan.n_lines == 0:
        warns.append(
            "no new tester log output — the terminal may have ignored /config "
            "(check for forward slashes in the path or a stale lock)"
        )
    return warns


def run_one(
    ini_path: str | Path,
    cfg: Config,
    *,
    close_terminal: bool = True,
) -> RunResult:
    """Run a single tester .ini end to end. Never raises — every failure mode
    lands in ``status``/``errors`` so a queue keeps moving."""
    ini_path = Path(ini_path)
    res = RunResult(ini=ini_path.name, status="error")
    t0 = time.monotonic()
    try:
        return _run_one_inner(ini_path.resolve(), res, cfg, close_terminal)
    except Exception as exc:  # noqa: BLE001 - the contract is "never raises"
        res.errors.append(f"unexpected failure: {exc}")
        res.duration_sec = round(time.monotonic() - t0, 1)
        return res


def _run_one_inner(
    ini_path: Path, res: RunResult, cfg: Config, close_terminal: bool
) -> RunResult:
    t0 = time.monotonic()

    ini = inifile.parse_ini(ini_path)
    res.expert = ini.expert
    res.symbol = ini.symbol
    res.period = ini.period
    res.model = ini.model_raw
    res.from_date = ini.get("FromDate")
    res.to_date = ini.get("ToDate")
    res.deposit = ini.get("Deposit")

    if ini.errors:
        res.status = "invalid"
        res.errors = [i.message for i in ini.errors]
        res.warnings = [i.message for i in ini.warnings]
        return res
    res.warnings = [i.message for i in ini.warnings]

    if cfg.terminal_exe is None or not Path(cfg.terminal_exe).is_file():
        res.errors.append(f"terminal exe not found: {cfg.terminal_exe}")
        return res
    if cfg.data_dir is None or not Path(cfg.data_dir).is_dir():
        res.errors.append(f"data dir not found: {cfg.data_dir}")
        return res

    cfg.results_dir.mkdir(parents=True, exist_ok=True)
    run_start_ts = time.time()

    # 1. close any running terminal FIRST — it would rewrite the staged .set
    #    on exit. Path-scoped: only the configured install's terminal64.exe.
    if close_terminal:
        found, survivors = proc.close_all(
            cfg.process_image, cfg.close_grace_sec, exe_path=cfg.terminal_exe
        )
        if found:
            log.info("closed %d terminal process(es), %d survived",
                     found, survivors)
        if survivors:
            res.warnings.append(
                f"{survivors} terminal process(es) could not be killed — a live "
                "terminal may rewrite the staged .set on exit"
            )

    # 2. mark the log AFTER the close — closing a terminal appends shutdown
    #    lines (possibly a stale 'OnTester result') that must never count as ours
    mark = testerlog.mark_log_position(cfg.log_dir)

    # 3. stage parameters under the name the tester will request
    staged = setfile.stage_set(ini, cfg.profiles_dir)
    res.warnings.extend(staged.warnings)

    # 4. launch
    try:
        process = proc.start_terminal(cfg.terminal_exe, ini_path, cfg.terminal_args)
    except OSError as exc:
        res.errors.append(f"failed to start terminal: {exc}")
        return res

    # 5. wait — process exit, early result in log (no ShutdownTerminal), timeout
    early = None
    if not ini.shutdown_terminal:
        early = proc.ontester_probe(mark, cfg.log_dir)
    outcome = proc.wait_for_run(
        process,
        timeout_sec=cfg.timeout_sec,
        poll_sec=cfg.poll_sec,
        early_complete=early,
    )
    res.duration_sec = round(time.monotonic() - t0, 1)

    if outcome == "timeout":
        res.status = "timeout"
        res.warnings.append(
            f"killed after {cfg.timeout_sec:.0f}s without finishing"
        )
        proc.close_all(cfg.process_image, cfg.close_grace_sec)
    elif outcome == "completed":
        # result in the log but terminal left open — tidy up ourselves
        proc.close_all(cfg.process_image, cfg.close_grace_sec)

    # 6. read ONLY this run's log lines
    lines = testerlog.read_new_lines(mark, cfg.log_dir)
    scan = testerlog.scan_lines(lines)
    res.tick_mode = scan.tick_mode
    if scan.on_tester is not None:
        res.on_tester = scan.on_tester
        if res.status != "timeout":
            res.status = "ok"
    elif res.status != "timeout":
        res.status = "no_result"

    res.errors.extend(scan.errors[:20])
    res.warnings.extend(_check_warnings(ini, scan, staged))

    # 7. collect outputs: report file + files the expert dropped in Common\Files
    report = _resolve_report_path(ini, cfg)
    if ini.report and report is None:
        res.warnings.append(
            f"Report={ini.report} was set but no report file was produced "
            "(some MT5 builds only write reports for optimization runs)"
        )
    if report:
        res.report = str(report)
        if report.parent != cfg.results_dir.resolve() and report.parent != cfg.results_dir:
            dest = cfg.results_dir / f"{ini.stem}_{report.name}"
            try:
                shutil.copyfile(report, dest)
            except OSError:
                pass
    if cfg.collect_common:
        res.artifacts = _collect_common_files(cfg, run_start_ts, ini.stem)

    if ini.optimization and res.status == "ok":
        res.warnings.append(
            "Optimization run — on_tester is the last pass' criterion; the full "
            "pass table lives in the terminal's opt cache"
        )
    return res


def read_queue(path: str | Path) -> list[str]:
    """Queue entries: one .ini path per line; ``#`` comments and blanks
    skipped; BOM tolerated."""
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return []
    text = raw.decode("utf-8-sig", errors="replace")
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def pop_queue(path: str | Path) -> str | None:
    """Remove and return the first queue entry, rewriting the file atomically
    (temp file + rename) so a crash mid-write never corrupts the queue.
    Comment and blank lines are preserved."""
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    lines = raw.decode("utf-8-sig", errors="replace").splitlines(keepends=True)
    first = None
    kept: list[str] = []
    for line in lines:
        if first is None and line.strip() and not line.strip().startswith("#"):
            first = line.strip()
            continue
        kept.append(line)
    if first is None:
        return None
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            fh.write("".join(kept))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError:
        # queue file locked or unwritable — leave it untouched rather than
        # lose the entry silently
        return None
    return first


def resolve_queue_entry(entry: str, queue_path: str | Path) -> Path:
    """Relative queue entries resolve against the queue file's own folder
    first, then the cwd."""
    p = Path(entry)
    if p.is_absolute():
        return p
    by_queue = Path(queue_path).parent / p
    return by_queue if by_queue.exists() else p


def run_queue(
    queue_path: str | Path,
    cfg: Config,
    *,
    on_result=None,
    close_terminal: bool = True,
) -> list[RunResult]:
    """Consume a queue file until empty. ``on_result`` is called with each
    RunResult (for live progress display / summary appending)."""
    results: list[RunResult] = []
    while True:
        entry = pop_queue(queue_path)
        if entry is None:
            break
        res = run_one(resolve_queue_entry(entry, queue_path), cfg,
                      close_terminal=close_terminal)
        results.append(res)
        if on_result:
            on_result(res)
    return results
