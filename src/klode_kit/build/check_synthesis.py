"""The desk synthesis gate: format, plus "anchors are only copied from cards" (WI-02c; vendored).

Vendored from the user's klode-build skill ``scripts/check_synthesis.py`` (read-only source, 2026-10-24).
klode's rule: every ``(grep: `...`)`` in a synthesis must be the very string of an anchor in one of the cards its
``cards:`` lists - never re-cut from the source text, or ``klode check`` reports anchor drift. This gate checks it
before the synthesis is accepted.

    klode-kit check-synthesis KB [DIM ...]      # no DIM: every _syntheses/*.md not starting with "_"
    klode-kit check-synthesis KB --syntheses DIR [DIM ...]   # the files of DIR, judged against the cards of KB
    python3 -m klode_kit.build.check_synthesis KB [DIM ...]
    exit 0 all pass; 1 any problem (also: nothing to check); 2 usage

``--syntheses DIR`` (WI-13a): the desk workers of a book build write ``DIR/<dim>.md`` outside the sealed build whose
cards they cite; the gate is the same, only the place of the files differs (the syntheses of KB itself are not read).

Checks (kept from the original):
  S1 front-matter has dimension / title / cards / status / tier, and dimension == the file name
  S2 every card in ``cards:`` exists in library/cards/
  S3 ``## Craft`` with the three ``###`` sections 机制 / 动作 / 可选项; ``## Provenance``
  S4 every 动作 item starts ``- **<ASCII-led bold head>`` (gate derive needs ASCII in the head)
  S5 every anchor string is, verbatim, in the anchor set of the cards ``cards:`` lists
  S6 no engine words in the Craft section (scorers / RT- / ε-test / gate)

Changed:
- **B4** (03 §2 step 21): the 动作 section was found with ``^### .*动作.*$\\n(.*?)(?=...)`` under ``re.S``; ``.*``
  crossed lines, swallowed the rest of the file and left an empty body, so S4 never ran and a purely Chinese
  bold head passed. The heading is now matched with ``[^\\n]*``.
- No synthesis to check at all (no DIM given and no ``_syntheses/*.md``) is a FAIL, never a silent PASS.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ANCHOR_RE = re.compile(r"\(grep:\s*`([^`]*)`(?:\s*#\d+)?\)")
FM_RE = re.compile(r"^---\n(.*?)\n---\n", re.S)
ENGINE_WORDS = ("scorers", "RT-", "ε-test", "gate ")
ACTIONS_RE = re.compile(r"^### [^\n]*动作[^\n]*\n(.*?)(?=^### |^## |\Z)", re.M | re.S)  # B4: no `.*` in the heading
CRAFT_RE = re.compile(r"^## Craft\n(.*?)(?=^## |\Z)", re.M | re.S)
ASCII_HEAD_RE = re.compile(r"- \*\*[A-Za-z0-9][^*]*\*\*")


def syntheses_dir(kb: Path) -> Path:
    return Path(kb) / "library" / "frameworks" / "_syntheses"


def card_anchors(kb: Path, cid: str) -> set[str] | None:
    p = Path(kb) / "library" / "cards" / f"{cid}.md"
    if not p.exists():
        return None
    return set(ANCHOR_RE.findall(p.read_text(encoding="utf-8")))


def check_one(kb: Path, dim: str, syn: Path | None = None) -> list[str]:
    """Problems of ``_syntheses/<dim>.md`` (or ``syn/<dim>.md``), each prefixed ``<dim>: ``; ``[]`` = PASS."""
    probs: list[str] = []
    f = (syntheses_dir(kb) if syn is None else Path(syn)) / f"{dim}.md"
    if not f.exists():
        return [f"{dim}: 文件不存在"]
    raw = f.read_text(encoding="utf-8")
    fm = FM_RE.match(raw)
    if not fm:
        return [f"{dim}: S1 缺 front-matter"]
    meta = dict(re.findall(r"^(\w+):\s*(.*)$", fm.group(1), flags=re.M))
    for k in ("dimension", "title", "cards", "status", "tier"):
        if k not in meta:
            probs.append(f"S1 front-matter 缺 {k}")
    if meta.get("dimension") != dim:
        probs.append(f"S1 dimension={meta.get('dimension')} 与文件名 {dim} 不一致")
    cards = [c.strip() for c in meta.get("cards", "").strip("[]").split(",") if c.strip()]
    if not cards:
        probs.append("S2 cards: 为空")
    pool: set[str] = set()
    for c in cards:
        a = card_anchors(kb, c)
        if a is None:
            probs.append(f"S2 cards: 里的 {c} 不存在")
        else:
            pool |= a

    body = raw[fm.end():]
    if "## Craft" not in body:
        probs.append("S3 缺 ## Craft")
    for sec in ("机制", "动作", "可选项"):
        if not re.search(rf"^### .*{sec}", body, flags=re.M):
            probs.append(f"S3 Craft 缺「{sec}」小节")
    if "## Provenance" not in body:
        probs.append("S3 缺 ## Provenance")

    m = ACTIONS_RE.search(body)
    if m:
        for ln in m.group(1).splitlines():
            if ln.startswith("- ") and not ASCII_HEAD_RE.match(ln):
                probs.append(f"S4 动作条目需 `- **ASCII头 …**` 起：{ln[:50]}…")

    craft = CRAFT_RE.search(body)
    if craft:
        for w in ENGINE_WORDS:
            if w in craft.group(1):
                probs.append(f"S6 Craft 段出现引擎术语「{w.strip()}」")

    for q in ANCHOR_RE.findall(body):
        if q not in pool:
            probs.append(f"S5 锚点不在 cards: 的卡片锚点里（必须从卡片复制）：`{q[:60]}`")
    return [f"{dim}: {p}" for p in probs]


def run(kb: Path, dims: list[str] | None = None, syn: Path | None = None) -> tuple[int, list[str]]:
    """``(exit code, output lines)`` for ``dims`` (default: every synthesis not starting with ``_``); ``syn``: the
    directory that holds the files (default: the ``_syntheses`` of ``kb``)."""
    kb = Path(kb).resolve()
    syn = syntheses_dir(kb) if syn is None else Path(syn).resolve()
    dims = list(dims) if dims else sorted(p.stem for p in syn.glob("*.md") if not p.name.startswith("_"))
    if not dims:
        return 1, [f"FAIL  没有可检查的 synthesis（{syn} 下没有维度文件）"]
    lines: list[str] = []
    allp: list[str] = []
    for d in dims:
        ps = check_one(kb, d, syn)
        f = syn / f"{d}.md"
        n = len(ANCHOR_RE.findall(f.read_text(encoding="utf-8"))) if f.exists() else 0
        lines.append(f"{'PASS' if not ps else 'FAIL'}  {d}  ({n} 锚点{'' if not ps else f'，{len(ps)} 个问题'})")
        allp += ps
    lines += ["   " + p for p in allp]
    return (1 if allp else 0), lines


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 2 if not argv else 0
    rest, syn = argv[1:], None
    if "--syntheses" in rest:
        i = rest.index("--syntheses")
        if i + 1 >= len(rest) or not Path(rest[i + 1]).is_dir():
            print("check-synthesis: --syntheses needs an existing directory", file=sys.stderr)
            return 2
        syn = Path(rest[i + 1])
        rest = rest[:i] + rest[i + 2:]
    rc, lines = run(Path(argv[0]), rest, syn)
    print("\n".join(lines))
    return rc


if __name__ == "__main__":  # pragma: no cover - entry is ``python3 -m klode_kit.build.check_synthesis``
    raise SystemExit(main())
