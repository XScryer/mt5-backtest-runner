"""``mt5-runner`` command-line interface.

Subcommands:

* ``run``       — run .ini files now, or consume a queue file
* ``validate``  — lint .ini files without running anything
* ``discover``  — list detected MT5 installations
* ``doctor``    — check that config + terminal are ready to run
* ``init``      — write a starter mt5-runner.toml from auto-detection
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import config as configmod
from . import discovery, inifile, runner, summary
from .runner import RunResult

EXIT_OK = 0
EXIT_RUN_FAILED = 1
EXIT_USAGE = 2


def _print_result(res: RunResult) -> None:
    value = "" if res.on_tester is None else f"{res.on_tester:g}"
    extra = f" | {res.tick_mode}" if res.tick_mode != "unknown" else ""
    print(f"  {res.ini:<45} {res.status:<10} {value:>12}  {res.duration_sec:>7.1f}s{extra}")
    for w in res.warnings:
        print(f"      warning: {w}")
    for e in res.errors:
        print(f"      error:   {e}")


def _build_config(args) -> "configmod.Config":
    overrides = {
        "terminal_exe": getattr(args, "terminal_exe", None),
        "data_dir": getattr(args, "data_dir", None),
        "results_dir": getattr(args, "results_dir", None),
        "timeout_sec": getattr(args, "timeout", None),
    }
    cfg, notes = configmod.load(config_file=getattr(args, "config", None), cli_overrides=overrides)
    for n in notes:
        if getattr(args, "verbose", False):
            print(f"config: {n}", file=sys.stderr)
    return cfg


def cmd_run(args) -> int:
    cfg = _build_config(args)
    problems = configmod.check(cfg)
    if problems:
        for p in problems:
            print(f"error: {p}", file=sys.stderr)
        return EXIT_USAGE

    entries: list[str] = list(args.inis)
    if args.queue:
        if not Path(args.queue).is_file():
            print(f"error: queue file not found: {args.queue}", file=sys.stderr)
            return EXIT_USAGE
        entries.extend(runner.read_queue(args.queue))
    if not entries:
        print("error: nothing to run — pass .ini files or --queue <file>", file=sys.stderr)
        return EXIT_USAGE

    # -- validate everything first ---------------------------------------------
    parsed = [inifile.parse_ini(e) for e in entries]
    fatal = [p for p in parsed if p.errors]
    for p in parsed:
        for i in p.issues:
            loc = p.path.name
            print(f"{i.severity.upper():<8} {loc}: {i}", file=sys.stderr)

    if args.fix:
        for p in parsed:
            code = inifile.fix_model_line(p.path)
            if code is not None:
                print(f"fixed {p.path.name}: Model={code}")
                # re-parse so the run sees the corrected file
                idx = parsed.index(p)
                parsed[idx] = inifile.parse_ini(p.path)
        fatal = [p for p in parsed if p.errors]

    if args.dry_run:
        print(f"\ndry-run: {len(entries)} entr(ies), {len(fatal)} invalid")
        for p in parsed:
            mark = "INVALID" if p.errors else "ok"
            print(f"  [{mark:>7}] {p.path}")
        return EXIT_RUN_FAILED if fatal else EXIT_OK

    if fatal and not args.force:
        print(
            f"error: {len(fatal)} invalid .ini file(s) — fix them or pass --force",
            file=sys.stderr,
        )
        return EXIT_USAGE

    # -- run ---------------------------------------------------------------------
    cfg.results_dir.mkdir(parents=True, exist_ok=True)
    results: list[RunResult] = []

    def record(res: RunResult) -> None:
        results.append(res)
        summary.append(cfg.summary_csv, res)
        _print_result(res)

    print(f"running {len(entries)} test(s) -> {cfg.summary_csv}\n")
    if args.queue:
        # pop entries as we go so a restart resumes where it stopped
        runner.run_queue(args.queue, cfg, on_result=record)
        # explicit inis run after the queue is drained
        for e in args.inis:
            record(runner.run_one(e, cfg))
    else:
        for e in entries:
            record(runner.run_one(e, cfg))

    failed = [r for r in results if r.status not in ("ok", "no_result")]
    print(f"\ndone: {len(results)} run(s), {len(failed)} failed — {cfg.summary_csv}")
    return EXIT_RUN_FAILED if failed else EXIT_OK



def cmd_validate(args) -> int:
    worst = EXIT_OK
    for name in args.inis:
        ini = inifile.parse_ini(name)
        if not ini.issues:
            print(f"{name}: ok")
            continue
        for i in ini.issues:
            print(f"{i.severity.upper():<8} {name}: {i.message}")
            if i.severity == "error":
                worst = EXIT_RUN_FAILED
            elif i.severity == "warning" and worst == EXIT_OK:
                worst = EXIT_OK
    return worst


def cmd_discover(args) -> int:
    installs = discovery.find_installations()
    if args.json:
        print(json.dumps(
            [
                {
                    "exe": str(i.exe),
                    "data_dir": str(i.data_dir),
                    "exe_exists": i.exe_exists,
                    "runnable": i.ok,
                    "origin": i.origin,
                }
                for i in installs
            ],
            indent=2,
        ))
        return EXIT_OK
    if not installs:
        print("no MT5 installations found")
        return EXIT_OK
    for i in installs:
        mark = "OK " if i.ok else "---"
        print(f"{mark} exe:      {i.exe}{'' if i.exe_exists else '  (missing!)'}")
        print(f"    data dir: {i.data_dir}")
    runnable = [i for i in installs if i.ok]
    if len(runnable) > 1:
        print(f"\n{len(runnable)} runnable installs — pin one in mt5-runner.toml or --data-dir")
    return EXIT_OK


def cmd_doctor(args) -> int:
    cfg = _build_config(args)
    problems = configmod.check(cfg)
    print(f"terminal_exe : {cfg.terminal_exe or '(unset)'}")
    print(f"data_dir     : {cfg.data_dir or '(unset)'}")
    print(f"results_dir  : {cfg.results_dir}")
    print(f"timeout      : {cfg.timeout_sec:.0f}s   poll: {cfg.poll_sec:.0f}s")
    print(f"config source: {cfg.source}")
    if cfg.data_dir:
        log_dir = cfg.data_dir / "Tester" / "logs"
        newest = None
        try:
            from .testerlog import newest_log
            newest = newest_log(log_dir)
        except OSError:
            pass
        print(f"log_dir      : {log_dir}{' (newest: ' + newest.name + ')' if newest else ''}")
    if not problems:
        print("\nready to run.")
        return EXIT_OK
    print("\nproblems:")
    for p in problems:
        print(f"  - {p}")
    return EXIT_USAGE


def cmd_init(args) -> int:
    installs = discovery.find_installations()
    picked = discovery.pick_installation(installs)
    if picked is None:
        print("no unique MT5 install detected — edit mt5-runner.toml by hand:")
        exe, data = 'C:\\\\Program Files\\\\MetaTrader 5\\\\terminal64.exe', \
            'C:\\\\Users\\\\<you>\\\\AppData\\\\Roaming\\\\MetaQuotes\\\\Terminal\\\\<hash>'
    else:
        exe, data = str(picked.exe), str(picked.data_dir)
    content = (
        "# mt5-backtest-runner configuration — CLI flags and MT5RUNNER_* env vars\n"
        "# override anything set here.\n"
        f'terminal_exe = "{exe.replace(chr(92), chr(92) * 2)}"\n'
        f'data_dir = "{data.replace(chr(92), chr(92) * 2)}"\n'
        'results_dir = "results"\n'
        "timeout_sec = 5400   # real-tick runs over years take a while\n"
        "poll_sec = 5\n"
        "collect_common = true # copy files the EA writes to Terminal\\Common\\Files\n"
        '# terminal_args = ["/portable"]\n'
    )
    target = Path(args.output)
    if target.exists() and not args.force:
        print(f"{target} already exists — pass --force to overwrite")
        return EXIT_USAGE
    target.write_text(content, encoding="utf-8")
    print(f"wrote {target}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mt5-runner",
        description="Batch runner for the MetaTrader 5 Strategy Tester.",
    )
    p.add_argument("--config", help="path to mt5-runner.toml")
    p.add_argument("--terminal-exe", help="path to terminal64.exe")
    p.add_argument("--data-dir", help="terminal data dir (contains MQL5\\)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", help="run .ini files or consume a queue")
    r.add_argument("inis", nargs="*", help=".ini config files to run")
    r.add_argument("--queue", help="queue file: one .ini path per line, consumed as it runs")
    r.add_argument("--results-dir", help="where summary.csv and artifacts go")
    r.add_argument("--timeout", type=float, help="per-run timeout in seconds")
    r.add_argument("--fix", action="store_true",
                   help="rewrite UI model names in .ini (e.g. 'real ticks') to numeric codes")
    r.add_argument("--force", action="store_true", help="run even with invalid .ini files")
    r.add_argument("--dry-run", action="store_true", help="validate only, launch nothing")
    r.set_defaults(func=cmd_run)

    v = sub.add_parser("validate", help="lint .ini files, run nothing")
    v.add_argument("inis", nargs="+")
    v.set_defaults(func=cmd_validate)

    d = sub.add_parser("discover", help="list detected MT5 installations")
    d.add_argument("--json", action="store_true")
    d.set_defaults(func=cmd_discover)

    doc = sub.add_parser("doctor", help="check config and terminal readiness")
    doc.set_defaults(func=cmd_doctor)

    i = sub.add_parser("init", help="write mt5-runner.toml from auto-detection")
    i.add_argument("--output", default="mt5-runner.toml")
    i.add_argument("--force", action="store_true")
    i.set_defaults(func=cmd_init)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
