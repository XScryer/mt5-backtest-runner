"""Staging of EA parameter files (``.set``) into the tester profile directory.

MT5's rule: ``ExpertParameters=name.set`` in the ``.ini`` points at
``<data_dir>\\MQL5\\Profiles\\Tester\\name.set``. When ``ExpertParameters`` is
absent the tester falls back to ``Profiles\\Tester\\<Expert>.set`` — and if
that doesn't exist either, the expert runs on its **compiled-in defaults**,
which is the single most common silent failure of scripted backtests.

Convention this runner implements: the ``.set`` sits **next to the ``.ini``**
with the same basename (``mytest.ini`` → ``mytest.set``). Before each run the
file is copied to the profile directory under the name the tester will look
for. Copying happens **after** the terminal is closed — a running terminal
rewrites profile .set files on exit and would clobber ours.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .inifile import TesterIni


@dataclass
class SetParam:
    """One line of a .set file: ``name=value||start||step||stop||opt_flag``.

    The four range fields are the optimization bounds MT5 shows in the
    Inputs tab (start/step/stop) plus the Y/N checkbox that marks the
    parameter for optimization.
    """

    name: str
    value: str
    start: str = ""
    step: str = ""
    stop: str = ""
    optimize: bool = False


def parse_set(path: str | Path) -> list[SetParam]:
    """Parse a .set file. Encoding is whatever the BOM says (.set files are
    UTF-16LE by convention, but plain UTF-8/ASCII works too)."""
    raw = Path(path).read_bytes()
    if raw.startswith(b"\xff\xfe"):
        text = raw[2:].decode("utf-16-le", errors="replace")
    elif raw.startswith(b"\xfe\xff"):
        text = raw[2:].decode("utf-16-be", errors="replace")
    elif raw.startswith(b"\xef\xbb\xbf"):
        text = raw[3:].decode("utf-8", errors="replace")
    else:
        text = raw.decode("utf-8", errors="replace")

    params: list[SetParam] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith((";", "#")):
            continue
        key, sep, payload = line.partition("=")
        if not sep:
            continue
        parts = payload.split("||")
        # Pad to 5 fields: value||start||step||stop||flag
        parts += [""] * (5 - len(parts))
        params.append(
            SetParam(
                name=key.strip(),
                value=parts[0].strip(),
                start=parts[1].strip(),
                step=parts[2].strip(),
                stop=parts[3].strip(),
                optimize=parts[4].strip().upper() in ("Y", "1", "TRUE"),
            )
        )
    return params


@dataclass
class StageResult:
    staged: bool
    source: Path | None = None
    dest: Path | None = None
    dest_name: str = ""
    warnings: list[str] = field(default_factory=list)
    n_params: int = 0


def target_name(ini: TesterIni) -> str:
    """The filename the tester will look for in Profiles\\Tester."""
    ep = ini.expert_parameters
    if ep and ep.lower().endswith(".set"):
        return Path(ep).name  # strip any directory part — MT5 ignores it anyway
    if ini.expert:
        return f"{ini.expert}.set"
    return f"{ini.stem}.set"


def source_candidates(ini: TesterIni) -> list[Path]:
    """Where we look for the parameters, in priority order, relative to the
    .ini's own folder."""
    base = ini.path.parent
    candidates = [base / f"{ini.stem}.set"]
    if ini.expert_parameters:
        candidates.append(base / Path(ini.expert_parameters).name)
    if ini.expert:
        candidates.append(base / f"{ini.expert}.set")
    # dedupe, keep order
    seen, out = set(), []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def stage_set(ini: TesterIni, profiles_dir: str | Path) -> StageResult:
    """Copy the parameter file into ``Profiles\\Tester`` under the name MT5
    will load. Never raises — a missing .set is a warning, not a crash."""
    profiles_dir = Path(profiles_dir)
    result = StageResult(staged=False, dest_name=target_name(ini))

    source = next((c for c in source_candidates(ini) if c.is_file()), None)
    if source is None:
        looked = ", ".join(c.name for c in source_candidates(ini))
        result.warnings.append(
            f"no .set found next to {ini.path.name} (looked for: {looked}) — "
            f"the expert will run on defaults or a stale profile"
        )
        return result

    try:
        profiles_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        result.warnings.append(f"cannot create profile dir {profiles_dir}: {exc}")
        return result

    dest = profiles_dir / result.dest_name
    try:
        shutil.copyfile(source, dest)
    except OSError as exc:
        result.warnings.append(f"cannot stage {source} -> {dest}: {exc}")
        return result

    result.staged = True
    result.source = source
    result.dest = dest
    try:
        result.n_params = len(parse_set(dest))
    except OSError:
        result.n_params = 0
    return result
