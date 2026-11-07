"""Process control for ``terminal64.exe`` — find, close, launch, wait.

MT5 is single-instance per install and ``/config:`` only works on a fresh
process, so every run starts by closing whatever terminal is open: graceful
first (``taskkill`` without ``/F`` posts WM_CLOSE), force-kill after a grace
period. Everything is implemented over stdlib so the tool has no dependencies.
"""

from __future__ import annotations

import csv
import os
import signal
import subprocess
import sys
import time
from io import StringIO
from pathlib import Path

from . import testerlog

_IS_WINDOWS = sys.platform.startswith("win")


def pids_for_exe(exe_path: str | Path, image_name: str) -> list[int]:
    """PIDs running *exactly* ``exe_path`` (case-insensitive path match).

    Uses CIM to get each process's ExecutablePath — needed because killing by
    image name alone would hit EVERY MT5 install on the machine, including a
    live trading terminal from a different broker. Falls back to image-name
    matching only when the process table can't be queried per-path.
    """
    target = os.path.normpath(str(exe_path)).lower()
    if _IS_WINDOWS:
        try:
            out = subprocess.run(
                [
                    "powershell", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_Process -Filter \"Name='"
                    + image_name.replace("'", "")
                    + "'\" | Select-Object ProcessId,ExecutablePath | ConvertTo-Csv -NoTypeInformation",
                ],
                capture_output=True, text=True, timeout=30,
            ).stdout
        except (OSError, subprocess.TimeoutExpired):
            return find_pids(image_name)
        pids: list[int] = []
        for row in csv.reader(StringIO(out)):
            if len(row) >= 2 and row[0].strip().isdigit():
                exe = row[1].strip()
                if exe and os.path.normpath(exe).lower() == target:
                    pids.append(int(row[0]))
        return pids

    # POSIX: compare /proc/<pid>/exe symlink
    pids = []
    proc_root = Path("/proc")
    if proc_root.is_dir():
        for entry in proc_root.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                if os.path.normpath(str((entry / "exe").resolve())).lower() == target:
                    pids.append(int(entry.name))
            except OSError:
                continue
    return pids


def find_pids(image_name: str = "terminal64.exe") -> list[int]:
    """PIDs of running processes whose executable is ``image_name``.

    Windows: ``tasklist`` CSV output (always present). POSIX fallback (the
    tool targets Windows, but this keeps the module importable/testable
    elsewhere): scan ``/proc/*/comm``.
    """
    if _IS_WINDOWS:
        try:
            out = subprocess.run(
                [
                    "tasklist",
                    "/FI",
                    f"IMAGENAME eq {image_name}",
                    "/FO",
                    "CSV",
                    "/NH",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            ).stdout
        except (OSError, subprocess.TimeoutExpired):
            return []
        pids: list[int] = []
        for row in csv.reader(StringIO(out)):
            if len(row) >= 2 and row[0].lower() == image_name.lower():
                try:
                    pids.append(int(row[1]))
                except ValueError:
                    continue
        return pids

    # POSIX fallback — matches by comm name (used in tests on Linux CI)
    pids = []
    proc = Path("/proc")
    if proc.is_dir():
        stem = image_name.lower().removesuffix(".exe")
        for entry in proc.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                comm = (entry / "comm").read_text().strip().lower()
            except OSError:
                continue
            if comm == stem:
                pids.append(int(entry.name))
    return pids


def pid_alive(pid: int) -> bool:
    if _IS_WINDOWS:
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=15,
            ).stdout
        except (OSError, subprocess.TimeoutExpired):
            return False
        return f'"{pid}"' in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _taskkill(pid: int, force: bool) -> None:
    cmd = ["taskkill", "/PID", str(pid), "/T"]
    if force:
        cmd.append("/F")
    subprocess.run(cmd, capture_output=True, timeout=30)


def close_pids(
    pids: list[int], grace_sec: float = 8.0
) -> list[int]:
    """Ask each pid to exit; force-kill survivors after ``grace_sec``.
    Returns pids that had to be force-killed."""
    if not pids:
        return []
    for pid in pids:
        try:
            if _IS_WINDOWS:
                _taskkill(pid, force=False)
            else:
                os.kill(pid, signal.SIGTERM)
        except (OSError, subprocess.TimeoutExpired):
            pass

    deadline = time.monotonic() + grace_sec
    while time.monotonic() < deadline:
        if not any(pid_alive(p) for p in pids):
            return []
        time.sleep(0.25)

    killed = [p for p in pids if pid_alive(p)]
    for pid in killed:
        try:
            if _IS_WINDOWS:
                _taskkill(pid, force=True)
            else:
                os.kill(pid, signal.SIGKILL)
        except (OSError, subprocess.TimeoutExpired):
            pass
    # verify the force-kill actually landed — a denied or still-tearing-down
    # process would clobber the staged .set on its eventual exit
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and any(pid_alive(p) for p in killed):
        time.sleep(0.2)
    return [p for p in pids if pid_alive(p)]


def close_all(
    image_name: str, grace_sec: float = 8.0, exe_path: str | Path | None = None
) -> tuple[int, int]:
    """Close running terminals. Returns (found, still_alive).

    ``exe_path`` scopes the close to one install — without it every process
    named ``image_name`` on the machine is matched.
    """
    if exe_path is not None:
        pids = pids_for_exe(exe_path, image_name)
    else:
        pids = find_pids(image_name)
    return len(pids), len(close_pids(pids, grace_sec))


def start_terminal(exe: str | Path, ini_path: str | Path, extra_args=()) -> subprocess.Popen:
    """Launch ``terminal64.exe /config:<ini>`` — returns the Popen handle.

    ``extra_args`` are inserted before ``/config:`` (e.g. ``/portable``).
    The ini path is forced to native separators: MT5 silently ignores the
    ``[Tester]`` section when the config path uses forward slashes.
    """
    ini_native = os.path.normpath(str(ini_path))
    cmd = [str(exe), *extra_args, f"/config:{ini_native}"]
    return subprocess.Popen(
        cmd,
        cwd=str(Path(exe).parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def wait_for_run(
    proc: subprocess.Popen,
    *,
    timeout_sec: float,
    poll_sec: float,
    early_complete=None,
) -> str:
    """Wait for the tester process to finish.

    Returns ``"exited"`` when the process ended, ``"completed"`` when
    ``early_complete()`` fired first (result visible in the log but the
    terminal stayed open — happens without ``ShutdownTerminal=1``), or
    ``"timeout"``.
    """
    deadline = time.monotonic() + timeout_sec
    while True:
        if proc.poll() is not None:
            return "exited"
        if time.monotonic() >= deadline:
            return "timeout"
        if early_complete is not None and early_complete():
            return "completed"
        time.sleep(poll_sec)


#: Log lines that prove a run ended even without an OnTester result — a
#: crashed expert or a tester abort must not linger until the full timeout.
_END_NEEDLES = (
    "OnTester result",
    "tester stopped",
    "cannot load expert",
    "cannot find expert",
    "oninit returned non-zero",
    "critical error",
)


def ontester_probe(mark: testerlog.LogMark, log_dir: str | Path):
    """Build an early-completion probe for wait_for_run: fires once the bytes
    appended since ``mark`` contain a result or a fatal error line."""

    def probe() -> bool:
        return testerlog.log_contains_any(mark, log_dir, _END_NEEDLES)

    return probe
