"""Field-level OCR metrics of design 02 r2.7 (WI-00c3).

Truth comes from the fixture generator's structured truth (``tests/fixtures/<fx>/truth.json``,
``records[].rows``). A *row* is one table line of a record (``row_id = "<key>/r<row>"``); its *fields* are
``value``, ``unit`` and ``flag``, counted only where the truth is non-empty (an rx row has a value only).

A prediction (one route, or the merged result) is ``{row_id: {"value", "unit", "flag"}}`` with ``None`` for
"not read"; an absent ``row_id`` means the row was not found. Readings compare after ``klode_kit.cer.normalize``
(NFKC + no whitespace): digits are never folded beyond NFKC.

Metrics (r2.7) and their thresholds:

| key | definition | threshold |
|---|---|---|
| ``exact_match_rate`` | fields read and equal to the truth / fields | >= 0.98 |
| ``association_accuracy`` | rows whose value, unit and flag all equal the truth (unread == empty) / rows | >= 0.98 |
| ``missing_rate`` | unread fields / fields | <= 0.02 |
| ``false_auto_pass_rate`` | auto-verified fields != truth / auto-verified fields | == 0 (> 0 fails) |

``evaluate`` reads the *run*: a field is auto-verified when every route read it and all readings are equal
(the four-way agreement; ``all_agree_fields`` is that count and must be >= ``MIN_ALL_AGREE`` = 20). With no
auto-verified field the false-auto-pass rate is ``"n/a"`` and the verdict FAILs with
``no_auto_verified_samples`` - an empty denominator never passes. The first three thresholds are judged on
``merged``: per field the reading shared by a strict majority of routes (else unread), i.e. what the
pipeline would put forward before any human decision. Per-route metrics are reported alongside.

``parse_rows`` turns one page's OCR lines into a prediction by item name (n-th occurrence for an item that
repeats, e.g. three rx rows of the same drug): the tokens after the item (split on whitespace and ``|``) are
lab ``value unit [flag] ref`` or rx ``value usage``.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from klode_kit.cer import normalize

COLS = ("value", "unit", "flag")
THRESHOLDS = {"exact_match_rate": 0.98, "association_accuracy": 0.98, "missing_rate": 0.02,
              "false_auto_pass_rate": 0.0}
MIN_ALL_AGREE = 20
NA = "n/a"
FLAGS = frozenset({"H", "L", "↑", "↓"})
_RANGE = re.compile(r"^[<>≤≥]?\d+(\.\d+)?[-–~～]\d+(\.\d+)?$")
_SPLIT = re.compile(r"[\s|｜]+")

Pred = Mapping[str, Mapping[str, Any]]


def truth_rows(truth_json: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for rec in truth_json.get("records", []):
        for r in rec.get("rows", []):
            rid = f"{rec['key']}/r{r['row']}"
            out[rid] = {"key": rec["key"], "row": r["row"], "kind": rec.get("record_type", "lab"),
                        "item": r["item"], **{c: str(r.get(c) or "") for c in COLS}}
    return out


def _norm(value: Any) -> str:
    return normalize(str(value)) if value is not None else ""


def _fields(truth: Mapping[str, Mapping[str, Any]]) -> list[tuple[str, str]]:
    return [(rid, c) for rid in sorted(truth) for c in COLS if _norm(truth[rid].get(c))]


def _reading(pred: Pred, rid: str, col: str) -> str | None:
    row = pred.get(rid)
    if row is None:
        return None
    v = _norm(row.get(col))
    return v or None


def field_metrics(truth: Mapping[str, Mapping[str, Any]], pred: Pred) -> dict[str, Any]:
    fields = _fields(truth)
    if not fields:
        raise ValueError("truth has no fields")
    exact = missing = 0
    for rid, col in fields:
        got = _reading(pred, rid, col)
        if got is None:
            missing += 1
        elif got == _norm(truth[rid][col]):
            exact += 1
    rows_ok = sum(1 for rid in truth if rid in pred
                  and all((_reading(pred, rid, c) or "") == _norm(truth[rid].get(c)) for c in COLS))
    return {"fields_total": len(fields), "exact_match_rate": exact / len(fields),
            "association_accuracy": rows_ok / len(truth), "missing_rate": missing / len(fields)}


def _merged(truth: Mapping[str, Mapping[str, Any]], routes: Mapping[str, Pred]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    n = len(routes)
    for rid in truth:
        row: dict[str, Any] = {}
        for col in COLS:
            votes = Counter(v for v in (_reading(p, rid, col) for p in routes.values()) if v is not None)
            top = votes.most_common(1)
            row[col] = top[0][0] if top and top[0][1] * 2 > n else None
        out[rid] = row
    return out


def evaluate(truth: Mapping[str, Mapping[str, Any]], routes: Mapping[str, Pred], *,
             min_all_agree: int = MIN_ALL_AGREE) -> dict[str, Any]:
    if not routes:
        raise ValueError("evaluate needs at least one route")
    fields = _fields(truth)
    if not fields:
        raise ValueError("truth has no fields")
    auto = wrong = 0
    for rid, col in fields:
        readings = [_reading(p, rid, col) for p in routes.values()]
        if all(r is not None for r in readings) and len(set(readings)) == 1:
            auto += 1
            wrong += readings[0] != _norm(truth[rid][col])
    rate: float | str = wrong / auto if auto else NA
    merged = field_metrics(truth, _merged(truth, routes))
    checks = {
        "exact_match_rate": merged["exact_match_rate"] >= THRESHOLDS["exact_match_rate"],
        "association_accuracy": merged["association_accuracy"] >= THRESHOLDS["association_accuracy"],
        "missing_rate": merged["missing_rate"] <= THRESHOLDS["missing_rate"],
        "no_auto_verified_samples": auto > 0,
        "false_auto_pass": auto > 0 and wrong == 0,
        "all_agree_below_min": auto >= min_all_agree,
    }
    reasons = [name for name, ok in checks.items() if not ok]
    if not auto:
        reasons.remove("false_auto_pass")  # the rate is n/a, reported as no_auto_verified_samples
    return {
        "routes": {name: field_metrics(truth, p) for name, p in routes.items()},
        "merged": merged,
        "auto_verified_total": auto,
        "false_auto_pass": wrong,
        "false_auto_pass_rate": rate,
        "all_agree_fields": auto,
        "min_all_agree": min_all_agree,
        "thresholds": dict(THRESHOLDS),
        "checks": checks,
        "verdict": "FAIL" if reasons else "PASS",
        "reasons": reasons,
    }


def _tokens_after(line: str, item: str) -> list[str] | None:
    text = unicodedata.normalize("NFKC", line)
    at = text.find(item)
    if at < 0:
        return None
    return [t for t in _SPLIT.split(text[at + len(item):]) if t]


def parse_rows(lines: Iterable[str], truth: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    lines = list(lines)
    out: dict[str, dict[str, Any]] = {}
    seen: Counter[str] = Counter()
    for rid in sorted(truth, key=lambda r: (truth[r]["key"], truth[r]["row"])):
        row = truth[rid]
        item = unicodedata.normalize("NFKC", row["item"])
        nth = seen[(row["key"], item)]
        seen[(row["key"], item)] += 1
        hits = [toks for toks in (_tokens_after(x, item) for x in lines) if toks is not None]
        if nth >= len(hits):
            continue
        toks = hits[nth]
        value = toks[0] if toks else None
        unit = flag = None
        if row.get("kind", "lab") != "rx":
            rest = toks[1:]
            if rest and rest[0] not in FLAGS and not _RANGE.match(rest[0]):
                unit = rest[0]
            flag = next((t for t in rest if t in FLAGS), None)
        out[rid] = {"value": value, "unit": unit, "flag": flag}
    return out
