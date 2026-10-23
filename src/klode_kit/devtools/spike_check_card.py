"""Minimal check_cards-style gate for the WI-00d2 host spike (replaced by ``build/check_cards`` in WI-02a).

``klode-kit check-cards <cards_content/<id>.md> [--kb DIR]`` - DIR (default: the current directory, i.e. the
build directory the card writer runs in) holds ``cards_content/<id>.md`` and the source ``library/*/<id>.txt``.

Rules (the user's klode-build ``check_cards.py`` plus the authored-card minimum of r2.6 / R2-02):

- F1 first line ``aliases: [...]`` with >= ``MIN_ALIASES`` entries;
- F2 a ``## Thin`` section containing an anchor;
- F3 a ``## Full`` section with a ``###`` subsection, every ``- `` bullet in it carrying an anchor;
- MIN_FULL at least ``MIN_ANCHORS_FULL`` anchors in Full;
- A1 anchor >= 15 characters; A2 found verbatim in the source exactly once, or >= N times with ``#N``;
  A3 no newline, no leading/trailing whitespace.

An anchor is ``(grep: `<text>`)`` or ``(grep: `<text>` #N)``. Exit 0 PASS, 1 FAIL (one problem per line), 2 usage.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ANCHOR_RE = re.compile(r"\(grep:\s*`([^`]*)`(?:\s*#(\d+))?\)")
CARD_RE = re.compile(r"^cards_content/(?!.*\.\./)[^/]+\.md$")
MIN_ALIASES = 10
MIN_ANCHORS_FULL = 5


def check(kb: Path, card: str) -> list[str]:
    kb = Path(kb)
    path = kb / card
    cid = Path(card).stem
    if not path.is_file():
        return [f"MISSING card file {card}"]
    sources = sorted((kb / "library").glob(f"*/{cid}.txt"))
    if not sources:
        return [f"MISSING source library/*/{cid}.txt"]
    raw = path.read_text(encoding="utf-8")
    text = sources[0].read_text(encoding="utf-8")
    probs: list[str] = []

    m = re.match(r"^aliases: \[(.*?)\]\n", raw, flags=re.S)
    if not m:
        probs.append("F1 first line must be aliases: [...]")
    else:
        aliases = [a.strip() for a in m.group(1).split(",") if a.strip()]
        if len(aliases) < MIN_ALIASES:
            probs.append(f"F1 only {len(aliases)} aliases, need >= {MIN_ALIASES}")

    thin = re.search(r"^## Thin\n(.*?)(?=^## Full|\Z)", raw, flags=re.S | re.M)
    full = re.search(r"^## Full\n(.*)\Z", raw, flags=re.S | re.M)
    if not thin:
        probs.append("F2 no ## Thin section")
    elif not ANCHOR_RE.search(thin.group(1)):
        probs.append("F2 Thin section has no anchor")
    if not full:
        probs.append("F3 no ## Full section")
    else:
        body = full.group(1)
        if not re.search(r"^### ", body, flags=re.M):
            probs.append("F3 Full section has no ### subsection")
        for ln in body.splitlines():
            if ln.startswith("- ") and not ANCHOR_RE.search(ln):
                probs.append(f"F3 bullet without anchor: {ln[:60]}")
        n_full = len(ANCHOR_RE.findall(body))
        if n_full < MIN_ANCHORS_FULL:
            probs.append(f"MIN_FULL only {n_full} anchors in Full, need >= {MIN_ANCHORS_FULL}")

    for mm in ANCHOR_RE.finditer(raw):
        q, nth = mm.group(1), int(mm.group(2) or 1)
        tag = q[:50]
        if "\n" in q or q != q.strip():
            probs.append(f"A3 anchor has a newline or outer whitespace: `{tag}`")
            continue
        if len(q) < 15:
            probs.append(f"A1 anchor too short ({len(q)} chars): `{tag}`")
        n = text.count(q)
        if n == 0:
            probs.append(f"A2 NOT FOUND: `{tag}`")
        elif nth == 1 and n > 1:
            probs.append(f"A2 AMBIGUOUS ({n} hits, lengthen or add #N): `{tag}`")
        elif n < nth:
            probs.append(f"A2 #{nth} but only {n} hits: `{tag}`")
    return probs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="klode-kit check-cards", description=__doc__.splitlines()[0])
    parser.add_argument("card")
    parser.add_argument("--kb", type=Path, default=Path("."))
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    if not CARD_RE.match(args.card):
        print(f"check-cards: card must be cards_content/<id>.md: {args.card!r}", file=sys.stderr)
        return 2
    problems = check(args.kb, args.card)
    print(f"{'PASS' if not problems else 'FAIL'}  {args.card}")
    for p in problems:
        print("   " + p)
    return 1 if problems else 0
