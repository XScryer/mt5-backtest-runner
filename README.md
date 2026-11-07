# mt5-backtest-runner

**Run MetaTrader 5 Strategy Tester backtests from the command line — queued, unattended, verified.**

The MT5 Strategy Tester is GUI-only: every run means opening the terminal,
loading the expert, setting dates/deposit/model, waiting, then digging the
result out of a log file. Do that dozens of times and it eats your day. The
CLI route exists (`terminal64.exe /config:test.ini`) but is full of silent
traps — this tool wraps the whole lifecycle and turns those traps into loud
errors or automatic fixes.

```
queue.txt ──► validate .ini ──► close terminal ──► stage .set ──► terminal64.exe /config
                                                                   │
              summary.csv ◄── collect report + EA files ◄── parse UTF-16 log ◄─┘
```

## Features

- **Batch queue** — one `.ini` per line in a text file; the runner pops each
  entry as it starts it, so killing and relaunching resumes where it stopped.
- **Zero-config start** — finds your MT5 install automatically
  (`%APPDATA%\MetaQuotes\Terminal\<hash>` + `origin.txt`), including multiple
  installs.
- **Parameter staging done right** — copies `mytest.set` next to
  `mytest.ini` into `MQL5\Profiles\Tester\` under the name `ExpertParameters`
  expects — *after* closing the terminal, so a running MT5 can't clobber it
  on exit.
- **Run-only log parsing** — the tester appends every run of the day to one
  UTF-16 file that reaches multiple GB. The runner marks the file position
  before launch and reads only the bytes your run appended — a stale
  `OnTester result` from a previous run is never attributed to yours.
- **Tick-mode verification** — `Model=4` (real ticks) is checked against the
  log. If MT5 fell back to simulated ticks — or real ticks had gaps — you get
  a warning in the row, not a false result.
- **Graceful then forceful terminal handling** — closes *the configured
  install's* `terminal64.exe` (matched by full path — a live trading terminal
  from another install is never touched), politely first, force-kills after a
  grace period, handles runs that finish but leave the terminal open (no
  `ShutdownTerminal=1`), and kills on timeout.
- **Artifact collection** — the `.htm`/`.xml` report plus any file your EA
  wrote to `Terminal\Common\Files` during the run are copied into the results
  folder, prefixed with the run name.
- **Machine-readable everywhere** — CSV summary, `--json` discovery, clean
  exit codes. Stdlib only, no dependencies.

## Requirements

- Windows (MetaTrader 5 is Windows software)
- Python 3.11+
- An MT5 terminal with the data history your test needs — the runner drives
  the tester you already have; it doesn't replace it.

## Install

```powershell
git clone https://github.com/XScryer/mt5-backtest-runner
cd mt5-backtest-runner
pip install -e .
```

That puts `mt5-runner` on your PATH (or use `python -m mt5_runner`).

## Quickstart

```powershell
mt5-runner discover            # what did we find?
mt5-runner init                # write mt5-runner.toml from auto-detection
mt5-runner doctor              # is everything ready?
mt5-runner run test.ini        # run one test
mt5-runner run --queue queue.txt   # run a whole batch, unattended
```

## The `.ini` / `.set` contract

The tester config is a plain INI file (see `examples/`):

```ini
[Common]
Login=12345678
Server=YourBroker-Live

[Tester]
Expert=MyBot
; the .set staged under this exact name
ExpertParameters=mybot-eurusd-m15.set
Symbol=EURUSD
Period=M15
; 4 = real ticks — NUMBERS ONLY (see below); inline comments like this one
; are fine on their OWN line, never after a value
Model=4
FromDate=2025.01.01
ToDate=2026.01.01
Deposit=1000
Currency=USD
Leverage=30
Optimization=0
; no extension — MT5 appends .htm
Report=reports\mybot-eurusd-m15
ShutdownTerminal=1
```

Parameter convention: `mytest.ini` ⟷ `mytest.set` in the same folder. The
`.set` is the standard MT5 export format
(`name=value||start||step||stop||Y/N`, UTF-16 or UTF-8 — both work).

### `Model=` — the biggest silent failure

| value | meaning |
|---|---|
| `Model=0` | Every tick — **simulated** |
| `Model=1` | 1-minute OHLC |
| `Model=2` | Open prices only |
| `Model=3` | Math calculations |
| `Model=4` | Every tick based on **real ticks** |

The UI names are **not** reliable: `Model=real ticks` is silently ignored on
verified builds and the tester falls back to simulated ticks *with no error
message*. The runner warns on any non-numeric value, `validate` flags it, and
`--fix` rewrites it to the numeric code before running.

And `Model=4` only does what you think if the broker actually has real ticks
for that symbol+period — the runner reads the log afterwards and warns when
the test degraded (`real ticks absent for N minutes`, `every tick
generating`).

## The queue

```text
# queue.txt — one .ini per line, # comments and blanks are fine
C:\tests\baseline.ini
variant-a.ini            # relative to this file's folder
variant-b.ini
```

`mt5-runner run --queue queue.txt` pops entries top-first and rewrites the
file atomically, so a crash or Ctrl+C loses at most the in-flight test — the
queue on disk always reflects what's left.

## Output

Every run appends one row to `results/summary.csv`:

| column | |
|---|---|
| `timestamp, ini, status` | when, which config, how it ended |
| `expert, symbol, period, model, from_date, to_date, deposit` | parsed from the .ini — what the test *claimed* to be |
| `on_tester` | the last `OnTester result` your run produced |
| `tick_mode` | what the log says really generated ticks: `real` / `simulated` / `ohlc_1m` / ... |
| `duration_sec, report, artifacts` | wall time, report path, collected files |
| `warnings, errors` | every silent fallback caught |

`status`: `ok` (finished, result parsed) · `no_result` (finished, nothing
returned) · `timeout` (killed) · `invalid` (bad .ini, never launched) ·
`error` (couldn't start).

**Exit codes:** `0` all green · `1` at least one run failed/timed out ·
`2` usage or config error.

## CLI

```text
mt5-runner [--config F] [--terminal-exe P] [--data-dir D] [-v] <command>

mt5-runner run <a.ini> [b.ini ...] [--queue q.txt] [--dry-run] [--fix]
               [--force] [--timeout S] [--results-dir D]
mt5-runner validate <a.ini> ... [--strict]   lint configs, launch nothing
mt5-runner discover [--json]               list detected MT5 installs
mt5-runner doctor                          config + terminal readiness check
mt5-runner init [--output F] [--force]     write mt5-runner.toml
```

Global flags (`--config`, `--terminal-exe`, `--data-dir`, `-v`) go **before**
the subcommand.

Config precedence: **CLI flags > `MT5RUNNER_*` env vars > `mt5-runner.toml`
> auto-discovery**.

```toml
# mt5-runner.toml
terminal_exe = "C:\\Program Files\\MetaTrader 5\\terminal64.exe"
data_dir = "C:\\Users\\me\\AppData\\Roaming\\MetaQuotes\\Terminal\\<hash>"
results_dir = "results"
timeout_sec = 5400        # real-tick runs over years take a while
poll_sec = 5
close_grace_sec = 8       # polite-close window before force-kill
collect_common = true     # copy files the EA writes to Terminal\Common\Files
process_image = "terminal64.exe"
# terminal_args = ["/portable"]
```

Every key has an env twin in screaming snake case under `MT5RUNNER_`:
`MT5RUNNER_TERMINAL_EXE`, `MT5RUNNER_DATA_DIR`, `MT5RUNNER_RESULTS_DIR`,
`MT5RUNNER_TIMEOUT_SEC`, `MT5RUNNER_POLL_SEC`, `MT5RUNNER_CLOSE_GRACE_SEC`,
`MT5RUNNER_COLLECT_COMMON`, `MT5RUNNER_PROCESS_IMAGE`,
`MT5RUNNER_TERMINAL_ARGS` (a single argument — use the TOML list for several).

## The MT5 traps this handles

Each of these cost real debugging time before it became code:

| trap | what the runner does |
|---|---|
| `Model=real ticks` silently means simulated | validates, warns, `--fix` rewrites to `4`, log cross-checked after |
| `.set` written before closing a running terminal gets overwritten on exit | always **close → mark log → stage → launch** — close is path-scoped to the configured install, so other MT5 terminals (e.g. live trading) are untouched |
| tester log is one UTF-16 file per day, multi-GB | marks byte offset *after* closing any running terminal (its shutdown writes could otherwise leak in), streams only new lines; stale results can't leak in |
| forward slashes in `/config:` make MT5 ignore `[Tester]` | path forced to native separators; "no new log output" becomes a loud warning |
| missing `ExpertParameters`/`.set` runs on compiled-in defaults | warning before launch *and* after (`parameters loaded` line checked) |
| terminal stays open without `ShutdownTerminal=1` | result detected in log → runner closes it itself |
| killing mid-queue loses track | atomic queue pops; finished results already in `summary.csv` |

## Optimization runs

`Optimization=1` works — the `.set` is staged with its `Y` flags honored and
the run is awaited like any other. `on_tester` then holds the *last* pass'
criterion; the full pass table lives in the terminal's opt cache and report.
A note is added to `warnings` so nobody mistakes it for a single-run result.

## Limitations

- Windows only, by nature of the thing it drives.
- One terminal at a time — MT5 is single-instance per install; concurrent
  runs against the same data dir would corrupt each other's logs.
- Parses results from the tester log and report file. If your EA doesn't
  return anything from `OnTester()`, `on_tester` stays empty (status
  `no_result`) — the report and Common\Files artifacts are still collected.
- `Report=` is honored when MT5 writes a file, but on some builds single
  backtests produce no report at all — the runner warns instead of staying
  silent.

## Development

```powershell
pip install -e ".[dev]"
python -m pytest
```

149 tests, including a full end-to-end suite against a **fake terminal**: a
renamed interpreter copy plays `terminal64.exe` (writes UTF-16 logs, reads
the staged `.set`, lingers or hangs on command), so the traps above are
exercised for real — ordering, timeouts, stale-result attribution,
path-scoped kills, queue draining — without needing MT5 installed.
Process-control tests are Windows-only; parsers/queue/config run anywhere.

## License

MIT — see [LICENSE](LICENSE).
