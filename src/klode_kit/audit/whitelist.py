"""WI-07c: engine weakness whitelist (design 03 N4; plan WI-07c; matrix 07c-10/11).

``config/engine_weakness.toml`` (packaged as ``klode_kit/config/engine_weakness.toml``) lists, per engine,
character pairs the engine is known not to tell apart (189's ``SMALL_KANA`` rule generalised)::

    [[weakness]]
    engine = "vision"               # one of align.ENGINE_ORDER
    class  = "small-kana"           # a name for the confirm page / receipts
    pairs  = [["ァ", "ア"], ...]     # [variant, canonical]
    kinds  = ["unit", "flag", "drug"]   # optional; a subset of TEXT_KINDS (default: all of them)

``Whitelist.key(kind, norm, engines)`` is the comparison form of a candidate ``norm`` on a cell of ``kind`` whose
voting routes are ``engines``: every pair of an entry whose engine is among ``engines`` and whose ``kinds`` hold
``kind`` is folded to its canonical side (longest variant first), for *every* candidate of the cell, so a
difference that lies only in a class one of the voters cannot distinguish is not a disagreement. Candidates
themselves are never rewritten (WI-07c ``compare`` keeps the literal readings).

The whitelist never reaches numbers: ``number`` and ``date`` cells are compared on their ``norm`` as is, an entry
may not list a numeric kind, and no pair may contain a digit (any Unicode ``isdigit`` character). A malformed file
is ``ConfigError`` (exit 40). The default file does **not** fold vision's ``10~9/L`` misread (WI-07a: a misread
must never be normalised into agreement).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any, Iterable

from klode_kit.config import ConfigError

TEXT_KINDS = ("unit", "flag", "drug")
NUMERIC_KINDS = ("number", "date")
ENGINES = ("vision", "tesseract", "pdftext", "mlx", "claude", "codex")   # align.ENGINE_ORDER
FILE_NAME = "engine_weakness.toml"
_RULE_KEYS = {"engine", "class", "pairs", "kinds"}


@dataclass(frozen=True)
class Rule:
    engine: str
    cls: str
    kinds: tuple[str, ...]
    pairs: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Whitelist:
    rules: tuple[Rule, ...] = ()

    def key(self, kind: str, norm: str, engines: Iterable[str]) -> str:
        """The comparison form of ``norm`` on a ``kind`` cell voted on by ``engines`` (see the module docstring)."""
        if kind not in TEXT_KINDS:
            return norm
        present = set(engines)
        pairs = sorted({p for r in self.rules if r.engine in present and kind in r.kinds for p in r.pairs},
                       key=lambda p: (-len(p[0]), p))
        if not pairs:
            return norm
        out, i = [], 0
        while i < len(norm):
            for variant, canonical in pairs:
                if norm.startswith(variant, i):
                    out.append(canonical)
                    i += len(variant)
                    break
            else:
                out.append(norm[i])
                i += 1
        return "".join(out)


EMPTY = Whitelist()


def _fail(source: str, message: str) -> ConfigError:
    return ConfigError(f"{source}: {message}", key="engine_weakness")


def _rule(entry: Any, n: int, source: str) -> Rule:
    where = f"weakness[{n}]"
    if not isinstance(entry, dict):
        raise _fail(source, f"{where} must be a table")
    unknown = set(entry) - _RULE_KEYS
    if unknown:
        raise _fail(source, f"{where} has unknown keys {sorted(unknown)}")
    engine, cls = entry.get("engine"), entry.get("class")
    if engine not in ENGINES:
        raise _fail(source, f"{where}.engine must be one of {list(ENGINES)}, got {engine!r}")
    if not isinstance(cls, str) or not cls:
        raise _fail(source, f"{where}.class must be a non-empty string")
    kinds = entry.get("kinds", list(TEXT_KINDS))
    if not isinstance(kinds, list) or not kinds or not all(isinstance(k, str) for k in kinds):
        raise _fail(source, f"{where}.kinds must be a non-empty list of kinds")
    bad = [k for k in kinds if k not in TEXT_KINDS]
    if bad:
        raise _fail(source, f"{where}.kinds {bad}: the whitelist applies to text kinds {list(TEXT_KINDS)} only, "
                            "never to numeric fields")
    pairs = entry.get("pairs")
    if not isinstance(pairs, list) or not pairs:
        raise _fail(source, f"{where}.pairs must be a non-empty list of [variant, canonical]")
    out = []
    for pair in pairs:
        if (not isinstance(pair, list) or len(pair) != 2 or not all(isinstance(s, str) and s for s in pair)
                or pair[0] == pair[1]):
            raise _fail(source, f"{where}.pairs: {pair!r} is not a pair of two different non-empty strings")
        if any(ch.isdigit() for ch in pair[0] + pair[1]):
            raise _fail(source, f"{where}.pairs: {pair!r} touches a digit; the whitelist never changes numbers")
        out.append((pair[0], pair[1]))
    return Rule(engine, cls, tuple(dict.fromkeys(kinds)), tuple(out))


def parse(text: str, *, source: str = "<string>") -> Whitelist:
    """Parse an ``engine_weakness.toml`` text; ``ConfigError`` on any malformed entry."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise _fail(source, f"not valid TOML ({exc})") from exc
    unknown = set(data) - {"weakness"}
    if unknown:
        raise _fail(source, f"unknown top-level keys {sorted(unknown)}")
    entries = data.get("weakness", [])
    if not isinstance(entries, list):
        raise _fail(source, "weakness must be an array of tables ([[weakness]])")
    return Whitelist(tuple(_rule(e, n, source) for n, e in enumerate(entries)))


def load(path: Path | None = None) -> Whitelist:
    """Load ``path``, or the packaged default when ``None``."""
    if path is None:
        text = (resources.files("klode_kit.config") / FILE_NAME).read_text(encoding="utf-8")
        return parse(text, source=FILE_NAME)
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise _fail(str(path), f"cannot read ({exc.strerror})") from exc
    return parse(text, source=str(path))


@lru_cache(maxsize=1)
def default() -> Whitelist:
    """The packaged whitelist."""
    return load()
