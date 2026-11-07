"""Discovery of MetaTrader 5 installations on this machine.

MT5 splits into two locations:

* the **install dir** (``terminal64.exe``), usually under ``C:\\Program Files\\``
* the **data dir** under ``%APPDATA%\\MetaQuotes\\Terminal\\<hash>`` — this is
  where ``MQL5\\Profiles\\Tester``, ``Tester\\logs`` and ``Common`` live.

Every data dir contains ``origin.txt`` (UTF-16) holding the install dir it was
created from — that file is the reliable link between the two, so we never
have to guess which of several installs a data dir belongs to.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Installation:
    """One MT5 install: the executable plus its data directory."""

    exe: Path
    data_dir: Path
    exe_exists: bool
    origin: str  # contents of origin.txt, "" when absent

    @property
    def ok(self) -> bool:
        """True when both halves exist — runnable."""
        return self.exe_exists and self.data_dir.is_dir()

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "Tester" / "logs"

    @property
    def profiles_dir(self) -> Path:
        return self.data_dir / "MQL5" / "Profiles" / "Tester"

    @property
    def common_files(self) -> Path:
        return self.data_dir.parent / "Common" / "Files"


def _read_origin(path: Path) -> str:
    raw = path.read_bytes()
    # BOM first — a bare ASCII file of even length would decode under utf-16
    # as CJK garbage, silently pointing at a nonexistent install dir
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16").strip()
    for enc in ("utf-8-sig", "utf-8"):
        try:
            text = raw.decode(enc).strip()
        except UnicodeDecodeError:
            continue
        if text:  # sanity: an install path has a drive letter or a slash
            return text
    return ""


def _terminal_root() -> Path | None:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    root = Path(appdata) / "MetaQuotes" / "Terminal"
    return root if root.is_dir() else None


def _exe_candidates(install_dir: Path) -> list[Path]:
    """Possible exe paths inside an install dir (64-bit first)."""
    return [install_dir / "terminal64.exe", install_dir / "terminal.exe"]


def find_installations(root: Path | None = None) -> list[Installation]:
    """Scan ``%APPDATA%\\MetaQuotes\\Terminal`` for MT5 data dirs.

    Skips the shared ``Common`` and ``Community`` folders. For each data dir,
    resolves the exe via ``origin.txt``; when origin.txt is absent or the exe
    it names is gone, also probes ``terminal64.exe`` next to the data dir
    (``/portable`` installs keep everything in one place).
    """
    if root is None:
        root = _terminal_root()
    if root is None or not root.is_dir():
        return []

    installs: list[Installation] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or entry.name in ("Common", "Community"):
            continue
        if not (entry / "MQL5").is_dir():
            continue

        origin_file = entry / "origin.txt"
        origin = _read_origin(origin_file) if origin_file.is_file() else ""

        candidates: list[Path] = []
        if origin:
            candidates.extend(_exe_candidates(Path(origin)))
        candidates.extend(_exe_candidates(entry))  # /portable layout

        exe = next((c for c in candidates if c.is_file()), candidates[0])
        installs.append(
            Installation(
                exe=exe,
                data_dir=entry,
                exe_exists=exe.is_file(),
                origin=origin,
            )
        )
    return installs


def pick_installation(
    installs: list[Installation] | None = None,
) -> Installation | None:
    """Return the single runnable install, or the only one present.

    With several runnable installs we refuse to guess — the caller must ask the
    user to pin one via config/CLI.
    """
    if installs is None:
        installs = find_installations()
    runnable = [i for i in installs if i.ok]
    if len(runnable) == 1:
        return runnable[0]
    if not runnable and len(installs) == 1:
        return installs[0]
    return None
