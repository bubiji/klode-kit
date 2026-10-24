"""Shared fixtures for WI-02b (write_card / check_published): synthetic aurora chapters, klode-shaped scaffolds."""

from __future__ import annotations

import hashlib
from pathlib import Path

from _support.flows import REPO

SOURCE = (REPO / "tests" / "fixtures" / "fx-book-aurora" / "aurora-kettle.txt").read_text(encoding="utf-8")
CH1 = SOURCE[SOURCE.index("Chapter 1:"):SOURCE.index("Chapter 2:")]
CH2 = SOURCE[SOURCE.index("Chapter 2:"):SOURCE.index("Chapter 3:")]
A1 = [
    "copper shell that is hammered thin and then annealed twice",
    "Annealing relaxes the metal so that the shell can expand",
    "A brass rim is folded over the upper edge of the shell",
    "A domed base flexes outward when heated",
    "Makers tin the inside of the shell with a thin food-safe layer",
]
A2 = [
    "opens only when the steam pressure passes a small threshold",
    "a reed of spring steel vibrates against two slots",
    "the reed is usually coated with scale and needs a vinegar soak",
    "A lever on the handle lifts the valve cap",
    "Spare reeds are cut from the same strip as the original",
]
FOLDED = "copper shell -- hammered thin and then annealed twice"  # not verbatim in the source
ALIAS_LIST = ["copper shell", "kettle shell", "annealing", "brass rim", "domed base", "tinning", "retinning",
              "food-safe layer", "hammered copper", "kettle body"]
SCAFFOLD = """---
id: {cid}
shelf: {shelf}
file: library/{shelf}/{cid}.txt
framework: none
zoom: stub
aliases: []
grep_ready: true
source_sha256: {sha}
---

# {cid}

## Content
`library/{shelf}/{cid}.txt` — full text (git-ignored, grep-ready). Never duplicated here; grep it to verify.

<!-- scaffold: managed by klode — edit Thin/Full below the marker by hand -->

## Thin
_(L1 owed — 1-3 sentences, grep-grounded to the .txt)_

## Full
_(L2 owed — main points outlined, grep-cited)_
"""


def body(anchors: list[str]) -> str:
    full = "".join(f"- point {k} (grep: `{a}`)\n" for k, a in enumerate(anchors))
    return f"\n# Card\n\n## Thin\nSummary (grep: `{anchors[0]}`).\n\n## Full\n### Points\n{full}"


def content(anchors: list[str], aliases: list[str] = ALIAS_LIST) -> str:
    return "aliases: [" + ", ".join(aliases) + "]\n" + body(anchors)


def add_card(kb: Path, cid: str, source: str, anchors: list[str] | None, shelf: str = "book",
             scaffold: bool = True) -> None:
    (kb / "library" / shelf).mkdir(parents=True, exist_ok=True)
    (kb / "library" / "cards").mkdir(parents=True, exist_ok=True)
    (kb / "cards_content").mkdir(exist_ok=True)
    (kb / "library" / shelf / f"{cid}.txt").write_text(source, encoding="utf-8")
    if scaffold:
        sha = hashlib.sha256(source.encode()).hexdigest()
        (kb / "library" / "cards" / f"{cid}.md").write_text(SCAFFOLD.format(cid=cid, shelf=shelf, sha=sha),
                                                          encoding="utf-8")
    if anchors is not None:
        (kb / "cards_content" / f"{cid}.md").write_text(content(anchors), encoding="utf-8")


def snapshot(kb: Path) -> dict[str, bytes]:
    return {p.relative_to(kb).as_posix(): p.read_bytes() for p in sorted((kb / "library" / "cards").rglob("*"))
            if p.is_file()}
