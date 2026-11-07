"""Parsing and validation of MT5 Strategy Tester ``.ini`` config files.

A tester ``.ini`` is a plain-text INI file with ``[Common]`` and ``[Tester]``
sections. MT5 is extremely unforgiving about its format:

* ``Model=`` only accepts the numeric codes 0-4. A UI name such as
  ``Model=real ticks`` is silently ignored and the tester falls back to
  simulated "Every tick" (mode 0) with no error anywhere.
* ``ExpertParameters=`` must name a ``.set`` file present in
  ``<data_dir>\\MQL5\\Profiles\\Tester\\`` — anything else silently runs the
  expert on its default inputs.
* ``Report=`` takes a path *without* extension; MT5 appends ``.htm``
  (or ``.xml`` when the path already ends in ``.xml``).

This module parses the file tolerantly (BOM, comments, any casing) and reports
issues instead of letting MT5 swallow them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# MT5 tick-generation model codes for the [Tester] Model= key.
MODEL_NAMES: dict[str, int] = {
    "every tick": 0,
    "1 minute ohlc": 1,
    "m1 ohlc": 1,
    "open prices only": 2,
    "open prices": 2,
    "math calculations": 3,
    "math": 3,
    "real ticks": 4,
    "every tick based on real ticks": 4,
}

MODEL_DESCRIPTIONS: dict[int, str] = {
    0: "every tick (simulated)",
    1: "1 minute OHLC",
    2: "open prices only",
    3: "math calculations",
    4: "every tick based on real ticks",
}

# Keys the tester cannot run without.
REQUIRED_TESTER_KEYS = ("Expert", "Symbol", "Period", "FromDate", "ToDate")

_SEVERITIES = ("error", "warning", "info")


@dataclass(frozen=True)
class Issue:
    """A single validation finding for an .ini file."""

    severity: str  # "error" | "warning" | "info"
    key: str  # ini key it relates to, "" when general
    message: str

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        where = f"[{self.key}] " if self.key else ""
        return f"{self.severity.upper()}: {where}{self.message}"


def _read_text(path: Path) -> str:
    """Decode an .ini file regardless of BOM/encoding.

    MT5 accepts ASCII/UTF-8; files saved by some editors come back as UTF-16
    with BOM, which the tester reads but trips naive parsers.
    """
    raw = path.read_bytes()
    if raw.startswith(b"\xff\xfe"):
        return raw[2:].decode("utf-16-le", errors="replace")
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be", errors="replace")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", errors="replace")
    return raw.decode("utf-8", errors="replace")


def _parse_ini_text(text: str) -> dict[str, dict[str, str]]:
    """Minimal INI parser — tolerant of comments and odd whitespace.

    MT5 ini files use ``;`` and ``#`` comments; values never contain ``=``
    in practice but we keep everything after the first one.
    """
    sections: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith((";", "#")):
            continue
        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1].strip()
            current = sections.setdefault(name, {})
            continue
        if current is None:
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            current[key.strip()] = value.strip()
    return sections


def get_ci(section: dict[str, str], key: str, default: str = "") -> str:
    """Case-insensitive lookup — MT5 keys are conventionally CamelCase but the
    tester itself does not care."""
    for k, v in section.items():
        if k.lower() == key.lower():
            return v
    return default


@dataclass
class TesterIni:
    """A parsed tester config plus the validation findings for it."""

    path: Path
    sections: dict[str, dict[str, str]]
    issues: list[Issue] = field(default_factory=list)

    # -- convenient accessors ------------------------------------------------
    @property
    def tester(self) -> dict[str, str]:
        for name, sec in self.sections.items():
            if name.lower() == "tester":
                return sec
        return {}

    @property
    def stem(self) -> str:
        return self.path.stem

    def get(self, key: str, default: str = "") -> str:
        return get_ci(self.tester, key, default)

    @property
    def expert(self) -> str:
        return self.get("Expert")

    @property
    def symbol(self) -> str:
        return self.get("Symbol")

    @property
    def period(self) -> str:
        return self.get("Period")

    @property
    def expert_parameters(self) -> str:
        """Value of ExpertParameters= — the .set filename MT5 will load."""
        return self.get("ExpertParameters")

    @property
    def model_raw(self) -> str:
        return self.get("Model")

    @property
    def model_code(self) -> int | None:
        """Numeric model code when it can be determined, else None."""
        raw = self.model_raw.strip()
        if not raw:
            return None
        if raw.isdigit():
            return int(raw)
        return MODEL_NAMES.get(raw.lower())

    @property
    def optimization(self) -> bool:
        return self.get("Optimization", "0").strip() not in ("", "0")

    @property
    def shutdown_terminal(self) -> bool:
        return self.get("ShutdownTerminal", "0").strip() == "1"

    @property
    def report(self) -> str:
        return self.get("Report")

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warning"]


def parse_ini(path: str | Path) -> TesterIni:
    """Read and validate a tester .ini. Never raises on malformed content —
    problems surface as ``Issue`` entries instead."""
    path = Path(path)
    ini = TesterIni(path=path, sections={})
    if not path.is_file():
        ini.issues.append(Issue("error", "", f"file not found: {path}"))
        return ini

    ini.sections = _parse_ini_text(_read_text(path))
    tester = ini.tester
    if not tester:
        ini.issues.append(Issue("error", "Tester", "missing [Tester] section"))
        return ini

    for key in REQUIRED_TESTER_KEYS:
        if not get_ci(tester, key):
            ini.issues.append(Issue("error", key, f"required key {key}= is missing or empty"))

    # -- Model ----------------------------------------------------------------
    raw_model = ini.model_raw
    if raw_model:
        if raw_model.strip().isdigit():
            code = int(raw_model.strip())
            if code not in MODEL_DESCRIPTIONS:
                ini.issues.append(
                    Issue("error", "Model", f"Model={raw_model} is not a valid code (0-4)")
                )
        elif raw_model.strip().lower() in MODEL_NAMES:
            code = MODEL_NAMES[raw_model.strip().lower()]
            ini.issues.append(
                Issue(
                    "warning",
                    "Model",
                    f"Model={raw_model!r} is a UI name, not a numeric code — some are "
                    f"accepted and some are silently ignored ('real ticks' verifiably "
                    f"falls back to simulated ticks). Model={code} "
                    f"({MODEL_DESCRIPTIONS[code]}) is the only guaranteed form; "
                    "rewrite it or run with --fix.",
                )
            )
        else:
            ini.issues.append(
                Issue(
                    "error",
                    "Model",
                    f"Model={raw_model!r} is not recognised. MT5 will silently fall back "
                    "to simulated 'every tick'. Use a numeric code 0-4 "
                    "(4 = real ticks).",
                )
            )
    else:
        ini.issues.append(
            Issue(
                "warning",
                "Model",
                "no Model= key — the tester uses its remembered default, which may not "
                "be what this config intends. Set Model=4 for real ticks.",
            )
        )

    # -- ExpertParameters vs sibling .set ------------------------------------
    ep = ini.expert_parameters
    if ep:
        if not ep.lower().endswith(".set"):
            ini.issues.append(
                Issue(
                    "error",
                    "ExpertParameters",
                    f"ExpertParameters={ep!r} does not end in .set — MT5 expects a "
                    ".set filename, e.g. ExpertParameters=mybot.set",
                )
            )
        if "/" in ep or "\\" in ep:
            ini.issues.append(
                Issue(
                    "warning",
                    "ExpertParameters",
                    f"ExpertParameters={ep!r} contains a path separator. MT5 resolves "
                    "the name inside MQL5\\Profiles\\Tester — use the bare filename.",
                )
            )

    # -- ShutdownTerminal ------------------------------------------------------
    if not ini.shutdown_terminal:
        ini.issues.append(
            Issue(
                "info",
                "ShutdownTerminal",
                "ShutdownTerminal=1 is absent — the runner will detect the result in "
                "the log and close the terminal itself.",
            )
        )

    return ini


def fix_model_line(path: str | Path) -> int | None:
    """Rewrite the ``Model=`` line to its numeric code, in place.

    Returns the new code, or None when nothing needed fixing. Everything else
    in the file is preserved byte-for-byte.
    """
    path = Path(path)
    raw = path.read_bytes()
    bom = b""
    if raw.startswith(b"\xef\xbb\xbf"):
        bom, raw = raw[:3], raw[3:]
    text = raw.decode("utf-8", errors="replace")
    ini = parse_ini(path)
    code = ini.model_code
    if code is None or (ini.model_raw.strip().isdigit() and int(ini.model_raw) == code):
        return None
    new_text, n = re.subn(
        rf"(?im)^\s*Model\s*=.*$", f"Model={code}", text, count=1
    )
    if n == 0:
        return None
    path.write_bytes(bom + new_text.encode("utf-8"))
    return code
