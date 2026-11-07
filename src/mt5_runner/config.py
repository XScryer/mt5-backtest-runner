"""Configuration resolution: CLI > env vars > config file > auto-discovery.

Config file is TOML, looked up in this order:

1. ``--config <path>`` (explicit)
2. ``./mt5-runner.toml``
3. ``~/.mt5-runner.toml``

Every key can also be set via an ``MT5RUNNER_*`` environment variable, and the
two essentials (``terminal_exe``, ``data_dir``) fall back to auto-discovery of
installed terminals when unset.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import discovery

ENV_PREFIX = "MT5RUNNER_"
CONFIG_NAMES = ("mt5-runner.toml",)


@dataclass
class Config:
    """Everything a run needs. ``terminal_exe`` and ``data_dir`` are the only
    two that cannot have defaults."""

    terminal_exe: Path | None = None
    data_dir: Path | None = None
    results_dir: Path = field(default_factory=lambda: Path("results"))
    timeout_sec: float = 5400.0  # 90 min — real-tick runs over years are slow
    poll_sec: float = 5.0
    close_grace_sec: float = 8.0
    collect_common: bool = True
    process_image: str = "terminal64.exe"
    terminal_args: tuple[str, ...] = ()  # e.g. ("/portable", "/skipupdate")
    source: str = "defaults"  # where the resolved values came from (diagnostics)

    # -- derived paths ---------------------------------------------------------
    @property
    def log_dir(self) -> Path:
        return self.data_dir / "Tester" / "logs"  # type: ignore[operator]

    @property
    def profiles_dir(self) -> Path:
        return self.data_dir / "MQL5" / "Profiles" / "Tester"  # type: ignore[operator]

    @property
    def common_files(self) -> Path:
        # Common is shared by all terminals on the machine.
        return self.data_dir.parent / "Common" / "Files"  # type: ignore[operator]

    @property
    def summary_csv(self) -> Path:
        return self.results_dir / "summary.csv"

    @property
    def runner_log(self) -> Path:
        return self.results_dir / "runner.log"


def _find_config_file(explicit: str | Path | None) -> Path | None:
    if explicit:
        p = Path(explicit)
        return p if p.is_file() else None
    for name in CONFIG_NAMES:
        for base in (Path.cwd(), Path.home()):
            p = base / name
            if p.is_file():
                return p
    return None


def _as_path(value) -> Path | None:
    if value in (None, ""):
        return None
    return Path(os.path.expandvars(str(value))).expanduser()


def _as_args(value) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value else ()
    return tuple(str(v) for v in value)


def _env(key: str) -> str | None:
    return os.environ.get(ENV_PREFIX + key.upper())


def load(
    *,
    config_file: str | Path | None = None,
    cli_overrides: dict | None = None,
) -> tuple[Config, list[str]]:
    """Resolve configuration. Returns (config, notes) where notes explain what
    was auto-detected vs configured — useful for ``doctor`` output."""
    notes: list[str] = []
    file_vals: dict = {}
    cfg_path = _find_config_file(config_file)
    if cfg_path:
        try:
            file_vals = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, OSError) as exc:
            notes.append(f"config file {cfg_path} ignored: {exc}")
        else:
            notes.append(f"config file: {cfg_path}")
    file_vals = {k.lower(): v for k, v in file_vals.items()}

    cli_overrides = {k.lower(): v for k, v in (cli_overrides or {}).items() if v is not None}

    def resolve(key: str, default=None):
        if key in cli_overrides:
            return cli_overrides[key]
        env = _env(key)
        if env not in (None, ""):
            return env
        if key in file_vals:
            return file_vals[key]
        return default

    cfg = Config(
        terminal_exe=_as_path(resolve("terminal_exe")),
        data_dir=_as_path(resolve("data_dir")),
        results_dir=_as_path(resolve("results_dir")) or Path("results"),
        timeout_sec=float(resolve("timeout_sec", 5400.0)),
        poll_sec=float(resolve("poll_sec", 5.0)),
        close_grace_sec=float(resolve("close_grace_sec", 8.0)),
        collect_common=bool(resolve("collect_common", True)),
        process_image=str(resolve("process_image", "terminal64.exe")),
        terminal_args=_as_args(resolve("terminal_args")),
        source=(
            "cli"
            if cli_overrides
            else ("env" if _env("terminal_exe") or _env("data_dir") else (str(cfg_path) if cfg_path else "defaults"))
        ),
    )

    # -- auto-discovery for anything still missing -----------------------------
    if cfg.terminal_exe is None or cfg.data_dir is None:
        installs = discovery.find_installations()
        picked = discovery.pick_installation(installs)
        if picked:
            if cfg.terminal_exe is None:
                cfg.terminal_exe = picked.exe
                notes.append(f"terminal_exe auto-detected: {picked.exe}")
            if cfg.data_dir is None:
                cfg.data_dir = picked.data_dir
                notes.append(f"data_dir auto-detected: {picked.data_dir}")
            cfg.source += "+autodetect"
        elif installs:
            names = ", ".join(str(i.exe) for i in installs)
            notes.append(
                f"{len(installs)} MT5 installs found, none uniquely runnable — "
                f"set terminal_exe/data_dir explicitly. Candidates: {names}"
            )
        else:
            notes.append("no MT5 installation auto-detected")

    return cfg, notes


def check(cfg: Config) -> list[str]:
    """Sanity problems with a resolved config; empty list = ready to run."""
    problems: list[str] = []
    if cfg.terminal_exe is None:
        problems.append("terminal_exe is not set and no install was auto-detected")
    elif not Path(cfg.terminal_exe).is_file():
        problems.append(f"terminal_exe does not exist: {cfg.terminal_exe}")
    if cfg.data_dir is None:
        problems.append("data_dir is not set and no data dir was auto-detected")
    else:
        if not Path(cfg.data_dir).is_dir():
            problems.append(f"data_dir does not exist: {cfg.data_dir}")
        elif not (Path(cfg.data_dir) / "MQL5").is_dir():
            problems.append(f"data_dir has no MQL5 subdir — wrong folder? {cfg.data_dir}")
    if cfg.timeout_sec <= 0:
        problems.append(f"timeout_sec must be positive, got {cfg.timeout_sec}")
    if cfg.poll_sec <= 0:
        problems.append(f"poll_sec must be positive, got {cfg.poll_sec}")
    return problems
