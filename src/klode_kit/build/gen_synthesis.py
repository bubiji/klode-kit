"""Mechanical desk syntheses for the medical (records) build (WI-09b; design 02 r2.4 step 10, r2.1 row "书桌蒸馏").

A desk dimension of the medical shelves has no author: its cards are deterministic and say nothing a model could
add (no clinical reading, no advice). ``write(kb, dimensions)`` therefore writes, for each dimension of
``src/desk.json``, a synthesis that is only an index of the dimension's own cards: every ``(grep: ...)`` it holds is
copied verbatim from one of those cards (the S5 rule of ``check_synthesis``), the format is the S1-S6 one, and the
action heads are plain ASCII (``Review <card id>``). Nothing is invented; the same cards give the same bytes.

``dimensions`` are the ``desk.json`` entries to write (``dimension / title / question / cards``); a file that
already exists is never overwritten (exit with ``FileExistsError``) - the caller passes only the dimensions that
still need one.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable, Mapping

from klode_kit.build.check_synthesis import ANCHOR_RE, syntheses_dir

PER_CARD = 2


def _anchors(kb: Path, cid: str) -> list[str]:
    text = (kb / "library" / "cards" / f"{cid}.md").read_text(encoding="utf-8")
    return list(dict.fromkeys(ANCHOR_RE.findall(text)))


def render(kb: Path, dim: Mapping[str, Any]) -> str:
    cards = list(dim["cards"])
    picked = {cid: _anchors(kb, cid)[:PER_CARD] for cid in cards}
    first = next((a for anchors in picked.values() for a in anchors), None)
    if first is None:
        raise ValueError(f"dimension {dim['dimension']}: its cards hold no anchor to index")
    mechanism = "".join(f"- 卡 {cid} 的原行 (grep: `{a}`)\n" for cid, anchors in picked.items() for a in anchors)
    actions = "".join(f"- **Review {cid}** — 按日期核对这张卡列出的原行 (grep: `{anchors[0]}`)\n"
                      for cid, anchors in picked.items() if anchors)
    return (f"---\ndimension: {dim['dimension']}\ntitle: {dim.get('title') or dim['dimension']}\n"
            f"cards: [{', '.join(cards)}]\nstatus: deterministic\ntier: 1\n---\n\n"
            f"**Core question:** {dim.get('question') or dim['dimension']}\n\n"
            "## Craft\n\n"
            "### 机制（数据来自哪里）\n"
            "本维度只汇总已核对记录的原行，不作临床解读，也不给用药或诊疗建议。\n"
            f"{mechanism}\n"
            "### 动作（按顺序做什么）\n"
            f"{actions}\n"
            "### 可选项（几条路怎么选）\n"
            f"- 需要原始数值时 → 以卡中的整行为准 (grep: `{first}`)\n\n"
            "## Operational spec\n（留空）\n\n"
            f"## Provenance\n- 来源卡 {', '.join(cards)}（确定性导出，records_to_txt）。\n")


def write(kb: str | os.PathLike, dimensions: Iterable[Mapping[str, Any]]) -> list[str]:
    """Write ``library/frameworks/_syntheses/<dimension>.md`` for each dimension; returns the names written."""
    kb = Path(kb)
    out = syntheses_dir(kb)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for dim in dimensions:
        path = out / f"{dim['dimension']}.md"
        with path.open("x", encoding="utf-8") as fh:
            fh.write(render(kb, dim))
        written.append(dim["dimension"])
    return written
