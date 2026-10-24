"""WI-01c: ``deident`` - two de-identification modes for the export view (design 02 §3.4 脱敏行, r2.1 1.2,
r3.1 segment 3; plan WI-01c; matrix 01c-*).

**Modes** (``deident.mode`` in config, default ``pseudonym``; ``MODES``):

- ``pseudonym``: names and identifiers become stable pseudonyms; dates are left as they are (U5 shows the real
  dates).
- ``dateshift`` (explicit, for exports leaving the machine): the same pseudonyms, and every date moves by the
  project's **one** offset (never per record), so order and intervals are kept and the txt, the L1 cards built
  from it and the records shelf all show the same moved date.

**Key** ``<project>/_private/deident_key.json`` (``KEY_REL``; ``{"schema", "salt", "date_offset_days"}``): created
on first use from the OS random source - a 32-byte salt and an offset of 365..3650 days either way - as an
owner-only file (directory 0700, file 0600, created exclusively) next to a ``_private/.gitignore`` that ignores
everything. It is reused as long as it exists; a malformed key is a ``DeidentError`` whose message never repeats
the file's content. The key is never written anywhere else: not into the export, its map, a receipt or a log, and
``Key``'s ``repr`` hides both values. (doctor, WI-10b, checks that the data project ignores ``_private/``.)

**Pseudonyms** ``pseudonym(value, salt, kind)``: ``[N-<12 letters>]`` for a name, ``[ID-<12 letters>]`` for an
identifier; the letters are the first 48 bits of HMAC-SHA256(salt, kind + NUL + value) in a 16-letter alphabet.
Same value + salt -> same pseudonym; another salt -> another one. A pseudonym holds no digit (it can never look
like a date, a phone number or an ID) and starts with ``[`` (it is never read as the name after a ``姓名：`` label
again), so ``scrub`` is idempotent in ``pseudonym`` mode.

**What is an identifier** (``find_identifiers``; reuses the privacy_scan patterns): the token after a name label
(``姓名`` ``患者`` ``医师`` ...) or an identifier label (``编号`` ``病历号`` ``门诊号`` ``电话`` ...) followed by ``：`` /
``:``, with privacy_scan's name-token character class; anywhere: a resident ID number (privacy_scan ``ID_RE``), a
mobile number (``MOBILE_RE``), a landline ``0xx(x)-xxxxxxx(x)``, a code ``[A-Za-z]{1,3}-?\\d{6,}``, a run of 9 or
more digits.

**Dates** (``find_dates`` / ``shift_date``): ``YYYY-M-D`` with ``-`` ``/`` or ``.``, ``YYYYMMDD`` and ``YYYY年M月D日``,
year 1900..2100, a real calendar date. A moved date keeps its spelling: the separator, and zero padding of
month and day together (padded when either has a leading zero, unpadded when either is one digit, otherwise
padded for ``-/.`` dates and unpadded for ``年月日``).
``shift_date`` takes exactly one such date; anything else is a ``DeidentError``.

``scrub(text, key, mode)``: identifiers -> pseudonyms, then (``dateshift`` only) dates moved.

The export hook lives in ``records_to_txt.export(..., deident=<mode>)``: it uses ``load_key``, ``shift_date``,
``scrub`` and ``find_identifiers`` / ``find_dates`` to decide what a line may show (see that module).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path

from klode_kit.devtools.privacy_scan import ID_RE, MOBILE_RE

MODES = ("pseudonym", "dateshift")
KEY_REL = "_private/deident_key.json"
KEY_SCHEMA = "klode-kit.deident_key/1"
SALT_BYTES = 32
OFFSET_MIN_DAYS, OFFSET_MAX_DAYS = 365, 3650
YEAR_MIN, YEAR_MAX = 1900, 2100
_ALPHABET = "abcdefghjkmnpqrt"
_KINDS = {"name": "N", "id": "ID"}

_TOKEN = r"[^\s，,。；;：:、|<>()（）\[\]{}\"'`*]+"
_NAME_LABELS = ("患者姓名", "姓名", "患者", "病人", "就诊人", "受检人", "受检者", "送检医生", "送检医师", "医生", "医师",
                "检验者", "检验人", "审核者", "审核人", "报告人", "签名", "联系人")
_ID_LABELS = ("住院号", "门诊号", "病历号", "就诊卡号", "就诊号", "卡号", "编号", "身份证号", "身份证", "证件号",
              "医保卡号", "医保号", "联系电话", "手机号", "手机", "电话", "床号", "标本号", "样本号", "条码号", "ID")
_LABELLED = [(kind, re.compile("(?:" + "|".join(labels) + r")[：:][ \t]*(" + _TOKEN + ")"))
             for kind, labels in (("name", _NAME_LABELS), ("id", _ID_LABELS))]
_PATTERNS = [ID_RE, MOBILE_RE,
             re.compile(r"(?<!\d)0\d{2,3}-\d{7,8}(?!\d)"),
             re.compile(r"(?<![0-9A-Za-z])[A-Za-z]{1,3}-?\d{6,}(?!\d)"),
             re.compile(r"(?<!\d)\d{9,}(?!\d)")]

_DATE_SEP = re.compile(r"(?<!\d)(\d{4})([-/.])(\d{1,2})\2(\d{1,2})(?!\d)")
_DATE_CJK = re.compile(r"(?<!\d)(\d{4})年(\d{1,2})月(\d{1,2})日")
_DATE_COMPACT = re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)")


class DeidentError(RuntimeError):
    """De-identification cannot proceed safely (bad mode, bad key, a value that is not a date)."""


@dataclass(frozen=True, repr=False)
class Key:
    salt: bytes
    offset_days: int

    def __repr__(self) -> str:
        return "Key(<hidden>)"


def check_mode(mode: str) -> str:
    if mode not in MODES:
        raise DeidentError(f"unknown deident mode {mode!r} (expected one of {', '.join(MODES)})")
    return mode


# --------------------------------------------------------------------------- key


def new_key() -> Key:
    days = OFFSET_MIN_DAYS + secrets.randbelow(OFFSET_MAX_DAYS - OFFSET_MIN_DAYS + 1)
    return Key(salt=secrets.token_bytes(SALT_BYTES), offset_days=days if secrets.randbelow(2) else -days)


def _parse_key(raw: bytes) -> Key:
    bad = DeidentError(f"{KEY_REL}: malformed key file (content not shown)")
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise bad from None
    if not isinstance(data, dict):
        raise bad
    salt, days = data.get("salt"), data.get("date_offset_days")
    if not isinstance(salt, str) or not re.fullmatch(r"(?:[0-9a-f]{2}){16,}", salt):
        raise bad
    if not isinstance(days, int) or isinstance(days, bool) or days == 0 or abs(days) > 100 * 366:
        raise bad
    return Key(salt=bytes.fromhex(salt), offset_days=days)


def load_key(project: str | os.PathLike, *, create: bool = True) -> Key:
    """The project's key; with ``create`` a missing key is created (see the module docstring)."""
    path = Path(project) / KEY_REL
    if not create:
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            raise DeidentError(f"{KEY_REL}: no key in this project") from None
        return _parse_key(raw)
    path.parent.mkdir(mode=0o700, exist_ok=True)
    os.chmod(path.parent, 0o700)
    ignore = path.parent / ".gitignore"
    if not ignore.exists():
        ignore.write_text("*\n", encoding="utf-8")
    key = new_key()
    try:  # exclusive create: an existing key (or one a concurrent run just wrote) is never replaced
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return _parse_key(path.read_bytes())
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"schema": KEY_SCHEMA, "salt": key.salt.hex(), "date_offset_days": key.offset_days},
                            indent=1, sort_keys=True) + "\n")
    return key


# --------------------------------------------------------------------------- pseudonyms


def pseudonym(value: str, salt: bytes, kind: str = "name") -> str:
    digest = hmac.new(salt, f"{kind}\x00{value}".encode("utf-8"), hashlib.sha256).digest()
    bits = int.from_bytes(digest[:6], "big")
    letters = "".join(_ALPHABET[(bits >> (4 * i)) & 0xF] for i in range(12))
    return f"[{_KINDS[kind]}-{letters}]"


def find_identifiers(text: str) -> list[tuple[int, int, str]]:
    """``(start, end, kind)`` of every identifier in ``text``, non-overlapping, in text order."""
    spans: list[tuple[int, int, str]] = []
    for kind, rx in _LABELLED:
        spans += [(m.start(1), m.end(1), kind) for m in rx.finditer(text)]
    for rx in _PATTERNS:
        spans += [(m.start(), m.end(), "id") for m in rx.finditer(text)]
    # Pattern characters (letters, digits, "-") all belong to the label-token class, so a span starting inside
    # an earlier (longest-first) span also ends inside it: overlaps are dropped, never partial.
    out: list[tuple[int, int, str]] = []
    for s, e, kind in sorted(spans, key=lambda x: (x[0], -x[1])):
        if not out or s >= out[-1][1]:
            out.append((s, e, kind))
    return out


# --------------------------------------------------------------------------- dates


def _valid(y: str, m: str, d: str) -> dt.date | None:
    if not YEAR_MIN <= int(y) <= YEAR_MAX:
        return None
    try:
        return dt.date(int(y), int(m), int(d))
    except ValueError:
        return None


def _pad(m: str, d: str, default: bool) -> bool:
    """Whether a date writes month and day zero-padded: yes if either has a leading zero, no if either is a single
    digit, else (both two digits, no zero) ``default``."""
    if m.startswith("0") or d.startswith("0"):
        return True
    if len(m) == 1 or len(d) == 1:
        return False
    return default


def _matches(text: str) -> list[tuple[int, int, dt.date, str]]:
    """``(start, end, date, spelling)``; the spelling is a format with ``{y} {m} {d}`` fields."""
    found = []
    for m in _DATE_SEP.finditer(text):
        day = _valid(m.group(1), m.group(3), m.group(4))
        if day:
            md = "{m:02d}%s{d:02d}" if _pad(m.group(3), m.group(4), True) else "{m}%s{d}"
            found.append((m.start(), m.end(), day, "{y:04d}" + m.group(2) + md % m.group(2)))
    for m in _DATE_CJK.finditer(text):
        day = _valid(m.group(1), m.group(2), m.group(3))
        if day:
            md = "{m:02d}月{d:02d}日" if _pad(m.group(2), m.group(3), False) else "{m}月{d}日"
            found.append((m.start(), m.end(), day, "{y:04d}年" + md))
    for m in _DATE_COMPACT.finditer(text):
        day = _valid(m.group(1), m.group(2), m.group(3))
        if day:
            found.append((m.start(), m.end(), day, "{y:04d}{m:02d}{d:02d}"))
    # The three shapes cannot overlap: each is bounded by non-digits and ``-/.`` / ``年`` never occur in another.
    return sorted(found)


def find_dates(text: str) -> list[tuple[int, int]]:
    return [(s, e) for s, e, _, _ in _matches(text)]


def _move(day: dt.date, spelling: str, days: int) -> str:
    new = day + dt.timedelta(days=days)  # years 1900..2100 and |days| <= 36600 stay inside date's range
    return spelling.format(y=new.year, m=new.month, d=new.day)


def shift_date(value: str, days: int) -> str:
    found = _matches(value)
    if len(found) != 1 or (found[0][0], found[0][1]) != (0, len(value)):
        raise DeidentError("not a calendar date")
    _, _, day, spelling = found[0]
    return _move(day, spelling, days)


# --------------------------------------------------------------------------- text


def scrub(text: str, key: Key, mode: str) -> str:
    check_mode(mode)
    parts, pos = [], 0
    for s, e, kind in find_identifiers(text):
        parts += [text[pos:s], pseudonym(text[s:e], key.salt, kind)]
        pos = e
    out = "".join(parts) + text[pos:]
    if mode != "dateshift":
        return out
    parts, pos = [], 0
    for s, e, day, spelling in _matches(out):
        parts += [out[pos:s], _move(day, spelling, key.offset_days)]
        pos = e
    return "".join(parts) + out[pos:]
