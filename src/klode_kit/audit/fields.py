"""WI-07a: field extraction from one OCR line (plan WI-07a; matrix 07a-01..12).

``extract(line)`` returns the ``number | date | drug | unit | flag`` fields of a line, in order, never overlapping.

- ``value`` is always the literal source text, ``line[start:end] == value``; nothing is corrected in place.
- ``norm`` is the comparison form: NFKC-folded (full-width digits / letters / punctuation equal their half-width
  forms), range separators ``~ ～ – —`` -> ``-``, dates ISO ``YYYY-MM-DD``, powers of ten ``10^N/L``.
- Matching runs on a per-character NFKC fold of the line that keeps the length (a character whose NFKC form is
  longer than one character, or a superscript/subscript, is kept as is), so folded offsets are source offsets.
- Whitespace separates table cells: a unit glued to its number (``10mg``, ``4.2×10^9/L``, rx where the unit
  column is empty, WI-00a deviation 7) is part of one ``number`` field whose ``unit`` is the normalised unit;
  a unit standing on its own (``128 g/L``) is a ``unit`` field.
- Powers of ten accept ``10^9/L``, ``×10^9/L``, ``10⁹/L`` and ``10^×9/L`` (MLX ``{10}^{\\times 9}`` after the
  WI-04a F-2 flattening, fx-lowres). ``10~9/L`` (a vision misread) is deliberately *not* a unit: normalising a
  misread into the true value would let a wrong route vote "agree".
- Letter flags (``H``, ``L``) count only when no ASCII letter, digit, ``/`` or ``^`` touches them (the H in HGB,
  the L in g/L are not flags); symbol flags (``↑ ↓ *``) count unless the same symbol repeats (``***``).
- A number is not part of an identifier: digits right after ``<letter>-`` (``T-0000001``) or touching letters
  are skipped.
- A drug (``config/fields/drugs.txt``) followed only by whitespace and then a number with a glued unit carries
  that number's ``norm`` as ``dose``.

Word lists: ``klode_kit/config/fields/{units,flags,drugs}.txt`` (one entry per line, ``#`` comments; drug names
are synthetic only, the repository is public).
"""

from __future__ import annotations

import datetime as _dt
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources

KINDS = ("number", "date", "drug", "unit", "flag")
FIELDS_DIR = "fields"


@dataclass(frozen=True)
class Field:
    kind: str
    value: str
    start: int
    end: int
    norm: str
    unit: str | None = None
    dose: str | None = None


# --------------------------------------------------------------------------- word lists


def _read_list(name: str) -> tuple[str, ...]:
    text = (resources.files("klode_kit.config") / FIELDS_DIR / name).read_text(encoding="utf-8")
    items = []
    for raw in text.splitlines():
        item = fold(raw.split("#", 1)[0].strip()) if not raw.lstrip().startswith("#") else ""
        if item and item not in items:
            items.append(item)
    return tuple(sorted(items, key=len, reverse=True))


@lru_cache(maxsize=1)
def _lists() -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    return _read_list("units.txt"), _read_list("flags.txt"), _read_list("drugs.txt")


def units() -> tuple[str, ...]:
    return _lists()[0]


def flags() -> tuple[str, ...]:
    return _lists()[1]


def drugs() -> tuple[str, ...]:
    return _lists()[2]


# --------------------------------------------------------------------------- folding


def _fold_char(ch: str) -> str:
    decomp = unicodedata.decomposition(ch)
    if decomp.startswith(("<super>", "<sub>")):
        return ch
    nf = unicodedata.normalize("NFKC", ch)
    return nf if len(nf) == 1 else ch


def fold(text: str) -> str:
    """Per-character NFKC that never changes the length (offsets in the fold are offsets in ``text``)."""
    return "".join(_fold_char(ch) for ch in text)


# --------------------------------------------------------------------------- patterns (on the folded line)

_NUM = r"\d+(?:\.\d+)?"
_SUPER = "⁰¹²³⁴⁵⁶⁷⁸⁹"
_SUPER_MAP = {c: str(i) for i, c in enumerate(_SUPER)}
_RANGE_SEP = "-~–—"

_DATE = re.compile(
    r"(?<![0-9])(?P<y>\d{4})(?:-(?P<m1>\d{1,2})-(?P<d1>\d{1,2})|/(?P<m2>\d{1,2})/(?P<d2>\d{1,2})"
    r"|年(?P<m3>\d{1,2})月(?P<d3>\d{1,2})日)(?![0-9])"
)
_POWER = re.compile(rf"(?P<x>×)?10(?:\^×?(?P<e>\d{{1,2}})|(?P<s>[{_SUPER}]{{1,2}}))/L(?![A-Za-z0-9])")
_NUMBER = re.compile(
    rf"(?<![A-Za-z0-9.^~])(?<![A-Za-z]-)(?:(?P<cmp>[<>≤≥]){_NUM}|{_NUM}(?:[{_RANGE_SEP}]{_NUM})?)(?![0-9])"
)
_NOT_AFTER_NUMBER = re.compile(r"[A-Za-z^]")
_UNIT_BEFORE = re.compile(r"[A-Za-z0-9μ]")
_UNIT_AFTER = re.compile(r"[A-Za-z0-9/]")
_LETTER_FLAG_TOUCH = re.compile(r"[A-Za-z0-9/^]")


def _power_at(f: str, i: int) -> tuple[int, str] | None:
    m = _POWER.match(f, i)
    if m is None:
        return None
    exp = m.group("e") if m.group("e") is not None else "".join(_SUPER_MAP[c] for c in m.group("s"))
    return m.end(), f"10^{exp}/L"


def _list_unit_at(f: str, i: int) -> tuple[int, str] | None:
    for u in units():
        if f.startswith(u, i):
            end = i + len(u)
            if end < len(f) and _UNIT_AFTER.match(f, end):
                continue
            return end, u
    return None


def _unit_at(f: str, i: int) -> tuple[int, str] | None:
    return _power_at(f, i) or _list_unit_at(f, i)


def _date_at(f: str, i: int) -> tuple[int, str] | None:
    m = _DATE.match(f, i)
    if m is None:
        return None
    month = m.group("m1") or m.group("m2") or m.group("m3")
    day = m.group("d1") or m.group("d2") or m.group("d3")
    try:
        d = _dt.date(int(m.group("y")), int(month), int(day))
    except ValueError:
        return None
    return m.end(), d.isoformat()


def _number_at(f: str, i: int) -> tuple[int, str, str | None] | None:
    m = _NUMBER.match(f, i)
    if m is None:
        return None
    end = m.end()
    num_norm = m.group(0) if m.group("cmp") else re.sub(f"[{_RANGE_SEP}]", "-", m.group(0))
    glued = _unit_at(f, end)
    if glued is not None:
        uend, unorm = glued
        sep = "×" if f[end] == "×" else ""
        return uend, num_norm + sep + unorm, unorm
    if end < len(f) and _NOT_AFTER_NUMBER.match(f, end):
        return None
    return end, num_norm, None


def _flag_at(f: str, i: int) -> tuple[int, str] | None:
    for flag in flags():
        if not f.startswith(flag, i):
            continue
        end = i + len(flag)
        before = f[i - 1] if i > 0 else ""
        after = f[end] if end < len(f) else ""
        if flag.isascii() and flag.isalnum():
            if (before and _LETTER_FLAG_TOUCH.match(before)) or (after and _LETTER_FLAG_TOUCH.match(after)):
                continue
        elif before == flag[-1] or after == flag[0]:
            continue
        return end, flag
    return None


def _drug_at(f: str, i: int) -> tuple[int, str] | None:
    for name in drugs():
        if f.startswith(name, i):
            return i + len(name), name
    return None


# --------------------------------------------------------------------------- extract


def extract(line: str) -> list[Field]:
    """All fields of one line, in order (see the module docstring for the rules)."""
    f = fold(line)
    out: list[Field] = []
    i = 0
    while i < len(f):
        ch = f[i]
        if ch.isspace():
            i += 1
            continue
        hit = _date_at(f, i)
        if hit is not None:
            out.append(Field("date", line[i:hit[0]], i, hit[0], hit[1]))
            i = hit[0]
            continue
        hit = _drug_at(f, i)
        if hit is not None:
            out.append(Field("drug", line[i:hit[0]], i, hit[0], hit[1]))
            i = hit[0]
            continue
        if i == 0 or not _UNIT_BEFORE.match(f, i - 1):
            hit = _power_at(f, i)
            if hit is not None:
                out.append(Field("unit", line[i:hit[0]], i, hit[0], hit[1]))
                i = hit[0]
                continue
        num = _number_at(f, i)
        if num is not None:
            end, norm, unit = num
            out.append(Field("number", line[i:end], i, end, norm, unit=unit))
            i = end
            continue
        if i == 0 or not _UNIT_BEFORE.match(f, i - 1):
            hit = _list_unit_at(f, i)
            if hit is not None:
                out.append(Field("unit", line[i:hit[0]], i, hit[0], hit[1]))
                i = hit[0]
                continue
        hit = _flag_at(f, i)
        if hit is not None:
            out.append(Field("flag", line[i:hit[0]], i, hit[0], hit[1]))
            i = hit[0]
            continue
        i += 1
    return _attach_doses(f, out)


def _attach_doses(f: str, fields: list[Field]) -> list[Field]:
    out = list(fields)
    for k, field in enumerate(out[:-1]):
        nxt = out[k + 1]
        if field.kind == "drug" and nxt.kind == "number" and nxt.unit is not None and not f[field.end:nxt.start].strip():
            out[k] = Field(field.kind, field.value, field.start, field.end, field.norm, dose=nxt.norm)
    return out
