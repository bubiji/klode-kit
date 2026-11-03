"""WI-08d: where a record's ``type`` and ``date`` come from (WI-08c remainder 1; design 02 R2-10 / r4.1 views
``records/<date>-<type>.md``).

kb-ocr's batch receipt carries no ``record_type`` / ``date``. When it does not, the writeback derives them from what
the voting routes read on the page, deterministically:

``record_type(lines)``
    Per route, the first line in reading order holding a title keyword decides that route's type (``TYPE_KEYWORDS``:
    ``处方`` -> ``rx``; ``检验`` / ``化验`` -> ``lab``; matched on the NFKC fold with white space removed). Across
    routes the most common type wins; a tie goes to the route first in ``gate.ENGINES`` order. No keyword anywhere
    -> ``None``. (Cloud routes read the masked image whose header band is black; the local routes see the title.)

``page_date(lines)``
    Per route, the earliest date (WI-07a ``date`` field, ISO) on a line holding one of ``DATE_LABELS`` (``日期``,
    ``採取日``, ``検査日``, ... - Japanese lab reports do not say ``日期``); failing that, the earliest date anywhere
    on the page. **A date of birth is never a candidate** (WI-40): not a date on a line holding one of
    ``BIRTH_LABELS`` (``生年月日``, ``DOB``, ...), nor the date on the line right after a line that holds only a birth
    label (label and value split over two lines). Across routes the most common date wins; a tie goes to the earliest
    date. No date -> ``None`` (then the inbox directory date wins in ``derive``).

``dir_date(source)``
    The nearest directory of the inbox path named like a date (``YYYY-MM-DD`` or ``YYYYMMDD``, a real calendar day).

``derive(batch_dir, page, routes)``
    Reads the receipt page's route files (routes of ``routes`` whose status is ``done``, plus a text-layer file) and
    returns ``{record_type, date, date_source}``; the date falls back from the page (``page``) to the inbox directory
    (``inbox_dir``, the page's ``source``) to the batch day (``batch``: the date part of ``receipts/ocr.json``
    ``started``), else ``None``.

Every rule depends only on the set of ``(route, lines)``: the order routes are handed over never changes a result.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from collections import Counter
from pathlib import Path, PurePath
from typing import Any, Mapping, Sequence

from klode_kit.audit import fields as F
from klode_kit.audit.gate import ENGINES

TYPE_KEYWORDS: tuple[tuple[str, str], ...] = (("处方", "rx"), ("检验", "lab"), ("化验", "lab"))
DATE_LABELS: tuple[str, ...] = ("日期", "采样", "采集", "送检", "报告时间", "检验时间", "検査日", "採取日", "採血日", "受付日",
                                "報告日", "依頼日", "実施日", "診療日", "処方日", "交付年月日", "調剤日")
BIRTH_LABELS: tuple[str, ...] = ("生年月日", "出生日期", "出生", "生日", "誕生日", "DOB", "Birth")
TEXT_ROUTE = "text"
_DIR_DATE = re.compile(r"(\d{4})-?(\d{2})-?(\d{2})")


def _rank(route: str) -> int:
    return ENGINES.index(route) if route in ENGINES else len(ENGINES)


def _ordered(lines: Mapping[str, Sequence[str]]) -> list[tuple[str, Sequence[str]]]:
    return sorted(lines.items(), key=lambda kv: (_rank(kv[0]), kv[0]))


def _squash(text: str) -> str:
    return "".join(F.fold(text).split())


def _route_type(lines: Sequence[str]) -> str | None:
    for line in lines:
        s = _squash(line)
        hits = [(s.find(k), t) for k, t in TYPE_KEYWORDS if k in s]
        if hits:
            return min(hits)[1]
    return None


def record_type(lines: Mapping[str, Sequence[str]]) -> str | None:
    votes = [(r, t) for r, ls in _ordered(lines) if (t := _route_type(ls)) is not None]
    if not votes:
        return None
    counts = Counter(t for _, t in votes)
    best = max(counts.values())
    return next(t for _, t in votes if counts[t] == best)


def _birth_line(squashed: str) -> bool:
    low = squashed.lower()
    return any(b.lower() in low for b in BIRTH_LABELS)


def _bare_birth_label(squashed: str) -> bool:
    return squashed.strip(":").lower() in {b.lower() for b in BIRTH_LABELS}


def _route_date(lines: Sequence[str]) -> str | None:
    labelled, anywhere = [], []
    after_label = False
    for line in lines:
        s = _squash(line)
        birth = after_label or _birth_line(s)
        after_label = _bare_birth_label(s)
        if birth:
            continue
        dates = [f.norm for f in F.extract(line) if f.kind == "date"]
        anywhere += dates
        if any(label in s for label in DATE_LABELS):
            labelled += dates
    pool = labelled or anywhere
    return min(pool) if pool else None


def page_date(lines: Mapping[str, Sequence[str]]) -> str | None:
    counts = Counter(d for _, ls in lines.items() if (d := _route_date(ls)) is not None)
    if not counts:
        return None
    best = max(counts.values())
    return min(d for d, n in counts.items() if n == best)


def _calendar(y: str, m: str, d: str) -> str | None:
    try:
        return _dt.date(int(y), int(m), int(d)).isoformat()
    except ValueError:
        return None


def dir_date(source: str | None) -> str | None:
    if not source:
        return None
    for part in reversed(PurePath(source).parent.parts):
        m = _DIR_DATE.fullmatch(part)
        if m:
            return _calendar(*m.groups())
    return None


def _lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.strip():
            row = json.loads(raw)
            out.append((row.get("line_idx", len(out)), str(row.get("text", ""))))
    return [t for _, t in sorted(out, key=lambda x: x[0])]


def _batch_day(batch_dir: Path) -> str | None:
    path = batch_dir / "receipts" / "ocr.json"
    if not path.is_file():
        return None
    started = str(json.loads(path.read_text(encoding="utf-8")).get("started") or "")
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})T", started)
    return _calendar(*m.groups()) if m else None


def derive(batch_dir: Path, page: Mapping[str, Any], routes: Sequence[str]) -> dict:
    lines: dict[str, list[str]] = {}
    for r, res in (page.get("routes") or {}).items():
        if r in routes and res.get("status") == "done" and res.get("file"):
            lines[r] = _lines(batch_dir / res["file"])
    if page.get("text_file"):
        lines[TEXT_ROUTE] = _lines(batch_dir / page["text_file"])
    date, source = page_date(lines), "page"
    if date is None:
        date, source = dir_date(page.get("source")), "inbox_dir"
    if date is None:
        date, source = _batch_day(batch_dir), "batch"
    return {"record_type": record_type(lines), "date": date, "date_source": source if date is not None else None}
