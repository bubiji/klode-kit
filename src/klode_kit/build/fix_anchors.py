"""Rewrite NOT FOUND card anchors to the source's own spelling of equivalent characters (WI-02a; vendored).

Vendored from the user's klode-build skill ``scripts/fix_anchors.py`` (read-only source, 2026-10-24).

Card writers often type the source's ``’ “ ” — …`` as ``' " -- ...``, which breaks the anchor. For every anchor
of ``cards_content/<id>.md`` not found verbatim in ``library/*/<id>.txt``:

1. an anchor with outer whitespace whose stripped form occurs exactly once is stripped;
2. else the first variant (``variants``) that occurs **exactly once** in the source replaces it (``#N`` kept);
3. else it is left as written and listed.

The card file is rewritten only when at least one anchor was fixed. Kept as in the original: the substitution
table, the "exactly once" rule, the cap of 12 substitutable places, the output and exit codes.

Changed (design 02 §3.3 B1; baseline 03 B1): the original resolved overlapping places (``--`` and ``-`` at the
same start) by keeping the short match, so ``"reset -- this"`` never became ``"reset — this"``. ``variants`` now
yields the original enumeration first (so every fix the original made is still made, in the same order) and
then the **longest-match** enumeration (multi-character groups first), so its set is a superset of the original's.
A card without a source, or a source without a card, is reported, not a crash.

    klode-kit fix-anchors <kb_dir> [id ...]      # no id: every cards_content/*.md
    exit 0 nothing left unfound, 1 something left, 2 usage
"""

from __future__ import annotations

import itertools
import re
import sys
from pathlib import Path
from typing import Iterator

ANCHOR_RE = re.compile(r"\(grep:\s*`([^`]*)`(?:\s*#(\d+))?\)")
MAX_PLACES = 12

# each group: what the card typically has -> what the source may have (several)
SUBS = [
    ("'", ["’", "‘"]),
    ('"', ["“", "”"]),
    ("--", ["—", "–"]),
    ("-", ["—", "–", "‐"]),
    ("...", ["…"]),
]

Spot = tuple[int, int, list[str]]


def _spots(q: str) -> list[Spot]:
    spots = []
    for src, dsts in SUBS:
        for m in re.finditer(re.escape(src), q):
            spots.append((m.start(), m.end(), [src] + dsts))
    return spots


def _tile(spots: list[Spot]) -> list[Spot]:
    """Keep spots left to right, dropping any that overlaps one already kept."""
    pruned, last_end = [], -1
    for s, e, opts in spots:
        if s >= last_end:
            pruned.append((s, e, opts))
            last_end = e
    return pruned


def _expand(q: str, pruned: list[Spot]) -> Iterator[str]:
    if not pruned or len(pruned) > MAX_PLACES:
        return
    for choice in itertools.product(*[o for _, _, o in pruned]):
        out, pos = [], 0
        for (s, e, _), c in zip(pruned, choice):
            out.append(q[pos:s])
            out.append(c)
            pos = e
        out.append(q[pos:])
        yield "".join(out)


def variants(q: str) -> Iterator[str]:
    """Equivalent spellings of ``q``: the original short-first tiling, then the longest-match tiling (B1)."""
    spots = _spots(q)
    short_first = _tile(sorted(spots, key=lambda x: (x[0], x[1])))
    longest = _tile(sorted(spots, key=lambda x: (x[0], -(x[1] - x[0]))))
    yield from _expand(q, short_first)
    if longest != short_first:
        yield from _expand(q, longest)


def _anchor(q: str, nth: str | None) -> str:
    return f"(grep: `{q}`" + (f" #{nth}" if nth else "") + ")"


def fix_one(kb: Path, cid: str) -> tuple[int, list[str]]:
    f = kb / "cards_content" / f"{cid}.md"
    src = next(iter(sorted((kb / "library").glob(f"*/{cid}.txt"))), None)
    if not src:
        return 0, [f"{cid}: 找不到源"]
    if not f.is_file():
        return 0, [f"{cid}: cards_content 文件不存在"]
    text, raw = src.read_text(encoding="utf-8"), f.read_text(encoding="utf-8")
    fixed, left = 0, []

    def repl(m: re.Match) -> str:
        nonlocal fixed
        q, nth = m.group(1), m.group(2)
        if q == q.strip() and text.count(q) >= 1:
            return m.group(0)
        # outer whitespace is the commonest mechanical slip: strip it when the stripped form is unique
        if q != q.strip() and text.count(q.strip()) == 1:
            fixed += 1
            return _anchor(q.strip(), nth)
        for v in variants(q):
            if text.count(v) == 1:
                fixed += 1
                return _anchor(v, nth)
        left.append(q)
        return m.group(0)

    new = ANCHOR_RE.sub(repl, raw)
    if fixed:
        f.write_text(new, encoding="utf-8")
    return fixed, left


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if len(argv) < 1:
        print(__doc__)
        return 2
    kb = Path(argv[0]).resolve()
    ids = argv[1:] or sorted(p.stem for p in (kb / "cards_content").glob("*.md"))
    total_left = 0
    for cid in ids:
        fixed, left = fix_one(kb, cid)
        if fixed or left:
            print(f"{cid}: 修好 {fixed}，仍找不到 {len(left)}")
            for q in left:
                print(f"   ✗ `{q[:70]}`")
        total_left += len(left)
    return 1 if total_left else 0


if __name__ == "__main__":  # pragma: no cover - entry is ``klode-kit fix-anchors``
    raise SystemExit(main())
