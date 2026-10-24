"""Cue suggestions for the desk's routing loop L1 - from card aliases, for the tuning set only (WI-02c; new).

02 r2.8 L1: when ``desk-build --verify`` misses tuning questions, this deterministic modifier proposes cues
("question tokens ∩ card aliases") for ``src/desk.json``; ``desk-build --apply`` then regenerates
``_diagnostics.md``. **It reads the tuning set only**: the holdout file is never opened and holdout results in a
verify receipt are ignored, so the holdout stays an independent check (klode-kb SKILL.md iron law 2: the holdout
is never used to tune cues; only real, common phrasings - here: the cards' own aliases - become cues).

    klode-kit cue-suggest KB [--spec F] [--receipt KB/desk_receipt.json] [--write]
    exit 0 something suggested; 1 nothing to suggest (no progress: the loop stops); 2 usage; 30 spec contract

Considered questions: every tuning question of the spec, or - with ``--receipt`` - only those the receipt's
``tune.results`` marks ``ok: false`` (entries whose question is not a tuning question of the spec are ignored).
For a considered question of dimension D, an alias of one of D's cards becomes a suggested cue for D when it
occurs in the question (lower-case substring - how klode's diagnose matches cues), has at least 2 characters, holds
no ``,`` / ``|`` / newline, is not already a cue of any dimension (case-insensitive), and does not occur in a tuning
question of another dimension (it would pull that question away). Order: spec dimension order, then card order,
then alias order. ``unresolved`` lists considered questions that got no new cue.

Output (stdout, JSON): ``{"schema": "klode-kit.cue_suggest/1", "suggestions": {dim: [cue, ...]},
"unresolved": [question, ...]}``. ``--write`` appends the suggestions to the dimensions' ``cues`` in the spec.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from klode_kit.build.desk_build import CUE_FORBIDDEN, DeskError, _atomic_write, load

SCHEMA_ID = "klode-kit.cue_suggest/1"
FRONT_RE = re.compile(r"\A---\n(.*?)\n---\n", re.S)
ALIASES_RE = re.compile(r"^aliases:\s*\[(.*)\]\s*$", re.M)


def card_aliases(kb: Path, cid: str) -> list[str]:
    """The ``aliases: [...]`` of ``library/cards/<cid>.md`` (front-matter), in order."""
    p = Path(kb) / "library" / "cards" / f"{cid}.md"
    m = FRONT_RE.match(p.read_text(encoding="utf-8")) if p.is_file() else None
    a = ALIASES_RE.search(m.group(1)) if m else None
    return [x.strip() for x in a.group(1).split(",") if x.strip()] if a else []


def _missed(receipt: Path | None, tuning: set[str]) -> set[str]:
    if receipt is None:
        return set(tuning)
    try:
        doc = json.loads(Path(receipt).read_text(encoding="utf-8"))
        results = doc["tune"]["results"]
        missed = {r["q"] for r in results if isinstance(r, dict) and r.get("ok") is False and isinstance(r.get("q"), str)}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise DeskError(30, f"verify receipt 读不了：{receipt}（{exc}）") from None
    return missed & tuning


def suggest(kb: Path, spec: Path | None = None, receipt: Path | None = None) -> dict[str, Any]:
    kb = Path(kb).resolve()
    d = load(kb, spec)
    dims = d["dimensions"]
    tuning = {q for x in dims for q in x["tests"]}
    considered = _missed(receipt, tuning)
    taken = {c.lower() for x in dims for c in x["cues"]}
    suggestions: dict[str, list[str]] = {}
    unresolved: list[str] = []
    for x in dims:
        qs = [q for q in x["tests"] if q in considered]
        if not qs:
            continue
        others = [q.lower() for y in dims if y is not x for q in y["tests"]]
        found: list[str] = []
        helped: set[str] = set()
        for cid in x["cards"]:
            for alias in card_aliases(kb, cid):
                al = alias.lower()
                if len(al) < 2 or any(ch in alias for ch in CUE_FORBIDDEN) or al in taken:
                    continue
                if any(al in o for o in others):
                    continue
                hits = [q for q in qs if al in q.lower()]
                if not hits:
                    continue
                found.append(alias)
                taken.add(al)
                helped.update(hits)
        if found:
            suggestions[x["dimension"]] = found
        unresolved += [q for q in qs if q not in helped]
    return {"schema": SCHEMA_ID, "suggestions": suggestions, "unresolved": unresolved}


def write(kb: Path, result: dict[str, Any], spec: Path | None = None) -> None:
    """Append ``result["suggestions"]`` to the spec's cues (atomic)."""
    p = Path(spec) if spec is not None else Path(kb) / "src" / "desk.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    for x in d["dimensions"]:
        x["cues"] = x["cues"] + result["suggestions"].get(x["dimension"], [])
    _atomic_write(p, (json.dumps(d, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="klode-kit cue-suggest", description=__doc__.splitlines()[0])
    ap.add_argument("kb", type=Path)
    ap.add_argument("--spec", type=Path)
    ap.add_argument("--receipt", type=Path, help="desk verify receipt: only its missed tuning questions count")
    ap.add_argument("--write", action="store_true", help="append the suggestions to the spec's cues")
    try:
        a = ap.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    try:
        result = suggest(a.kb, a.spec, a.receipt)
    except DeskError as exc:
        print(f"cue-suggest: {exc}", file=sys.stderr)
        return exc.code
    if a.write and result["suggestions"]:
        write(a.kb.resolve(), result, a.spec)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0 if result["suggestions"] else 1


if __name__ == "__main__":  # pragma: no cover - entry is ``klode-kit cue-suggest``
    raise SystemExit(main())
