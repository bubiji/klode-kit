"""Gate 1: check ``cards_content/<id>.md`` and test every grep anchor against its source (WI-02a; vendored).

Vendored from the user's klode-build skill ``scripts/check_cards.py`` (read-only source, 2026-10-24); replaces
the WI-00d2 stand-in ``devtools/spike_check_card.py`` behind the same command ``klode-kit check-cards``.

Earlier and stricter than ``klode check --strict``: klode folds punctuation (``FOLDED-ONLY``) and lets stub cards
through (03 B6), so this gate cannot be skipped. Rules kept from the original (03 A2):

- F1 first line ``aliases: [...]`` with >= ``cards.min_aliases`` (10) entries on an authored card, >=
  ``MIN_ALIASES_DETERMINISTIC`` (1) on a deterministic one (user decision 2026-10-24, WI-01b: deterministic cards
  take their aliases only from deterministic sources and are not padded to 10);
- F2 a ``## Thin`` section with >= 1 anchor;
- F3 a ``## Full`` section with >= 1 ``###`` subsection; every top-level ``- `` bullet in it has an anchor;
- A1 every anchor >= 15 characters;
- A2 every anchor found verbatim in its source: exactly once, or with ``#N`` at least N times;
- A3 no newline in an anchor, no leading/trailing whitespace.

An anchor is ``(grep: `<text>`)`` or ``(grep: `<text>` #N)``.

**Policy** (design 02 r3.4 R2-02; plan WI-02a) comes from where the source lives, never from the card's own
``kind:`` line (reported as ``kind_declared`` only):

- source under ``library/items/`` or ``library/records/`` (the deterministic shelves of ``records_to_txt``) ->
  **deterministic**: every non-empty source line (an observation / source line) carries exactly one Full anchor,
  so anchors == lines and no line twice (``COV``); ``min_anchors_full`` does not apply;
- any other source -> **authored**: >= ``cards.min_anchors_full`` (5) anchors in Full (``MIN_FULL``).

``--policy auto`` (default) applies the derived policy. ``--policy authored|deterministic`` states what the
caller expects; when the source says otherwise the card gets ``POLICY`` and is still judged by the source's
policy - the flag can never select a weaker rule. A card id with sources on more than one shelf is ``SRC``; a
missing card or source is ``MISSING``.

Changed from the original: policy rules (MIN_FULL / COV / POLICY / SRC, new); ``--json``; ``--all`` is needed to
check every card (no card argument is a usage error, as the WI-00d2 command was), and ``--all`` over an empty
``cards_content/`` fails instead of passing with nothing checked; a CARD may be ``cards_content/<id>.md`` (the
form the card writer is told) or a bare id; ``--kb`` (default: the current directory); thresholds from the
configuration (``--project`` override). Problem messages, text output and exit codes are the original's.

    klode-kit check-cards (CARD ... | --all) [--kb DIR] [--json] [--policy auto|authored|deterministic]
    exit 0 all pass; 1 any problem; 2 usage; 40 configuration

``--json`` prints ``{"schema": "klode-kit.check_cards/1", "kb", "pass", "cards": [{"id", "card", "source",
"policy", "kind_declared", "anchors", "coverage", "pass", "problems": [{"code", "message"}]}]}`` (``JSON_SCHEMA``).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from klode_kit import config as config_mod
from klode_kit.build.records_to_txt import ITEMS_SHELF, RECORDS_SHELF

ANCHOR_RE = re.compile(r"\(grep:\s*`([^`]*)`(?:\s*#(\d+))?\)")
CARD_ARG_RE = re.compile(r"^(?:cards_content/)?([A-Za-z0-9][A-Za-z0-9._-]*?)(\.md)?$")
KIND_RE = re.compile(r"^kind:\s*(\S+)\s*$", re.M)
DETERMINISTIC_SHELVES = frozenset(s.split("/", 1)[1] for s in (ITEMS_SHELF, RECORDS_SHELF))
POLICIES = ("auto", "authored", "deterministic")
SCHEMA_ID = "klode-kit.check_cards/1"
MIN_ANCHOR_LEN = 15
# F1 floor for cards whose source is on a deterministic shelf (items / records). A code constant, not a config
# key: the authored floor stays ``cards.min_aliases`` (10). User decision 2026-10-24 (WI-01b, option B).
MIN_ALIASES_DETERMINISTIC = 1

JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["schema", "kb", "pass", "cards"],
    "properties": {
        "schema": {"const": SCHEMA_ID},
        "kb": {"type": "string"},
        "pass": {"type": "boolean"},
        "cards": {"type": "array", "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["id", "card", "source", "policy", "kind_declared", "anchors", "coverage", "pass",
                         "problems"],
            "properties": {
                "id": {"type": "string", "minLength": 1},
                "card": {"type": "string", "pattern": "^cards_content/[^/]+\\.md$"},
                "source": {"type": ["string", "null"]},
                "policy": {"enum": ["authored", "deterministic", None]},
                "kind_declared": {"type": ["string", "null"]},
                "anchors": {"type": "integer", "minimum": 0},
                "coverage": {"oneOf": [
                    {"type": "null"},
                    {"type": "object", "additionalProperties": False, "required": ["covered", "lines"],
                     "properties": {"covered": {"type": "integer", "minimum": 0},
                                    "lines": {"type": "integer", "minimum": 0}}},
                ]},
                "pass": {"type": "boolean"},
                "problems": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False, "required": ["code", "message"],
                    "properties": {"code": {"enum": ["MISSING", "SRC", "POLICY", "F1", "F2", "F3", "A1", "A2",
                                                     "A3", "MIN_FULL", "COV"]},
                                   "message": {"type": "string"}}}},
            },
        }},
    },
}


def _sources(kb: Path, cid: str) -> list[Path]:
    return sorted((kb / "library").glob(f"*/{cid}.txt"))


def find_source(kb: Path, cid: str) -> Path | None:
    hits = _sources(kb, cid)
    return hits[0] if hits else None


def source_policy(kb: Path, source: Path) -> str:
    """``deterministic`` for a source on a deterministic shelf (``library/items``, ``library/records``)."""
    shelf = source.relative_to(kb / "library").parts[0]
    return "deterministic" if shelf in DETERMINISTIC_SHELVES else "authored"


def _line_index(text: str, q: str, nth: int) -> int | None:
    """0-based line of the ``nth`` occurrence of ``q`` in ``text`` (None when there are fewer)."""
    pos = -1
    for _ in range(nth):
        pos = text.find(q, pos + 1)
        if pos < 0:
            return None
    return text.count("\n", 0, pos)


def _coverage(text: str, full: str) -> tuple[dict, list[str]]:
    lines = text.split("\n")
    obs = [i for i, ln in enumerate(lines) if ln.strip()]
    hits: dict[int, int] = {}
    n_anchors = 0
    for mm in ANCHOR_RE.finditer(full):
        q, nth = mm.group(1), int(mm.group(2) or 1)
        n_anchors += 1
        if not q or "\n" in q:
            continue
        i = _line_index(text, q, nth)
        if i is not None:
            hits[i] = hits.get(i, 0) + 1
    covered = sum(1 for i in obs if i in hits)
    probs = []
    if covered < len(obs) or n_anchors != len(obs) or any(c > 1 for c in hits.values()):
        missing = [i + 1 for i in obs if i not in hits]
        twice = sorted(i + 1 for i, c in hits.items() if c > 1)
        detail = []
        if missing:
            detail.append(f"缺锚点的行 {missing[:10]}")
        if twice:
            detail.append(f"重复锚定的行 {twice[:10]}")
        if n_anchors != len(obs):
            detail.append(f"Full 锚点 {n_anchors} 个 ≠ 观测行 {len(obs)} 行")
        probs.append(f"COV 观测覆盖率 {covered}/{len(obs)}（每个观测行恰好一个 Full 锚点）：" + "；".join(detail))
    return {"covered": covered, "lines": len(obs)}, probs


def check_card(kb: Path, cid: str, policy: str = "auto", *, min_aliases: int | None = None,
               min_anchors_full: int | None = None) -> dict[str, Any]:
    """Gate 1 for one card; ``problems`` are strings ``"<CODE> <message>"``."""
    if policy not in POLICIES:
        raise ValueError(f"policy must be one of {POLICIES}, got {policy!r}")
    kb = Path(kb).resolve()
    if min_aliases is None or min_anchors_full is None:
        cfg = config_mod.load(None)
        min_aliases = cfg["cards.min_aliases"] if min_aliases is None else min_aliases
        min_anchors_full = cfg["cards.min_anchors_full"] if min_anchors_full is None else min_anchors_full
    res: dict[str, Any] = {"id": cid, "card": f"cards_content/{cid}.md", "source": None, "policy": None,
                           "kind_declared": None, "anchors": 0, "coverage": None, "pass": False, "problems": []}
    f = kb / "cards_content" / f"{cid}.md"
    if not f.is_file():
        res["problems"].append("MISSING cards_content 文件不存在")
        return res
    return _judge(kb, cid, f.read_text(encoding="utf-8"), policy, min_aliases, min_anchors_full, res)


def judge(kb: Path, cid: str, raw: str, policy: str = "auto", *, min_aliases: int, min_anchors_full: int,
          card: str | None = None) -> dict[str, Any]:
    """Gate 1 on card text ``raw`` (the ``cards_content`` form) for card ``cid``; used by ``check_published``."""
    if policy not in POLICIES:
        raise ValueError(f"policy must be one of {POLICIES}, got {policy!r}")
    res: dict[str, Any] = {"id": cid, "card": card or f"cards_content/{cid}.md", "source": None, "policy": None,
                           "kind_declared": None, "anchors": 0, "coverage": None, "pass": False, "problems": []}
    return _judge(Path(kb).resolve(), cid, raw, policy, min_aliases, min_anchors_full, res)


def _judge(kb: Path, cid: str, raw: str, policy: str, min_aliases: int, min_anchors_full: int,
           res: dict[str, Any]) -> dict[str, Any]:
    probs: list[str] = res["problems"]
    res["anchors"] = len(ANCHOR_RE.findall(raw))
    head = raw.split("\n## ", 1)[0]
    kind = KIND_RE.search(head)
    res["kind_declared"] = kind.group(1) if kind else None
    srcs = _sources(kb, cid)
    if not srcs:
        probs.append(f"MISSING 找不到源文本 library/*/{cid}.txt")
        return res
    src = srcs[0]
    res["source"] = src.relative_to(kb).as_posix()
    if len(srcs) > 1:
        probs.append("SRC 同名源在多个书架：" + ", ".join(s.relative_to(kb).as_posix() for s in srcs))
        return res
    text = src.read_text(encoding="utf-8")
    derived = source_policy(kb, src)
    res["policy"] = derived
    if policy != "auto" and policy != derived:
        probs.append(f"POLICY 要求 {policy}，但源 {res['source']} 决定为 {derived}（按 {derived} 判定）")

    m = re.match(r"^aliases: \[(.*?)\]\n", raw, flags=re.S)
    if not m:
        probs.append("F1 首行必须是 aliases: [...]")
    else:
        aliases = [a.strip() for a in m.group(1).split(",") if a.strip()]
        floor = MIN_ALIASES_DETERMINISTIC if derived == "deterministic" else min_aliases
        if len(aliases) < floor:
            probs.append(f"F1 aliases 只有 {len(aliases)} 个，需 ≥ {floor}")

    thin = re.search(r"^## Thin\n(.*?)(?=^## Full|\Z)", raw, flags=re.S | re.M)
    full = re.search(r"^## Full\n(.*)\Z", raw, flags=re.S | re.M)
    if not thin:
        probs.append("F2 缺 ## Thin 段")
    elif not ANCHOR_RE.search(thin.group(1)):
        probs.append("F2 Thin 段没有锚点")
    if not full:
        probs.append("F3 缺 ## Full 段")
    else:
        body = full.group(1)
        if not re.search(r"^### ", body, flags=re.M):
            probs.append("F3 Full 段没有 ### 小节")
        for ln in body.splitlines():
            if ln.startswith("- ") and not ANCHOR_RE.search(ln):
                probs.append(f"F3 要点无锚点：{ln[:60]}…")
        if derived == "deterministic":
            res["coverage"], cov = _coverage(text, body)
            probs.extend(cov)
        else:
            n_full = len(ANCHOR_RE.findall(body))
            if n_full < min_anchors_full:
                probs.append(f"MIN_FULL Full 段只有 {n_full} 个锚点，需 ≥ {min_anchors_full}")

    for mm in ANCHOR_RE.finditer(raw):
        q, nth = mm.group(1), int(mm.group(2) or 1)
        tag = q[:50] + ("…" if len(q) > 50 else "")
        if "\n" in q or q != q.strip():
            probs.append(f"A3 锚点含换行或首尾空白：`{tag}`")
            continue
        if len(q) < MIN_ANCHOR_LEN:
            probs.append(f"A1 锚点太短（{len(q)} 字符）：`{tag}`")
        n = text.count(q)
        if n == 0:
            probs.append(f"A2 NOT FOUND：`{tag}`")
        elif nth == 1 and n > 1:
            probs.append(f"A2 AMBIGUOUS（命中 {n} 处，加长或加 #N）：`{tag}`")
        elif n < nth:
            probs.append(f"A2 #{nth} 但只命中 {n} 处：`{tag}`")
    res["pass"] = not probs
    return res


def check_one(kb: Path, cid: str) -> list[str]:
    """The original interface: ``["<cid>: <problem>", ...]`` (empty = PASS)."""
    return [f"{cid}: {p}" for p in check_card(kb, cid)["problems"]]


def _json_card(r: dict[str, Any]) -> dict[str, Any]:
    out = dict(r)
    out["problems"] = [dict(zip(("code", "message"), (p.split(" ", 1) + [""])[:2])) for p in r["problems"]]
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="klode-kit check-cards", description=__doc__.splitlines()[0])
    parser.add_argument("cards", nargs="*", metavar="CARD", help="cards_content/<id>.md or <id>")
    parser.add_argument("--all", action="store_true", help="check every cards_content/*.md")
    parser.add_argument("--kb", type=Path, default=Path("."), help="build / library directory (default: cwd)")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    parser.add_argument("--policy", choices=POLICIES, default="auto",
                        help="expected policy; the source's shelf decides (default auto)")
    parser.add_argument("--project", type=Path, default=None, help="data project with klode-kit.toml overrides")
    try:
        args = parser.parse_args(argv)
        if bool(args.cards) == args.all:
            parser.error("give CARD ... or --all (not both)")
        ids = []
        for card in args.cards:
            m = CARD_ARG_RE.match(card)
            if not m or (card.startswith("cards_content/") and not m.group(2)):
                parser.error(f"card must be cards_content/<id>.md or <id>: {card!r}")
            ids.append(m.group(1))
    except SystemExit as exc:
        return 2 if exc.code else 0
    kb = args.kb
    cfg = config_mod.load(args.project)
    if args.all:
        ids = sorted(p.stem for p in (kb / "cards_content").glob("*.md"))
        if not ids:
            print(f"check-cards: no card in {kb / 'cards_content'}", file=sys.stderr)
            return 1
    results = [check_card(kb, cid, args.policy, min_aliases=cfg["cards.min_aliases"],
                          min_anchors_full=cfg["cards.min_anchors_full"]) for cid in ids]
    ok = all(r["pass"] for r in results)
    if args.json:
        doc = {"schema": SCHEMA_ID, "kb": str(kb), "pass": ok, "cards": [_json_card(r) for r in results]}
        print(json.dumps(doc, ensure_ascii=False, indent=1))
    else:
        for r in results:
            n = len(r["problems"])
            print(f"{'PASS' if r['pass'] else 'FAIL'}  {r['id']}  ({r['anchors']} 锚点{'' if r['pass'] else f'，{n} 个问题'})")
        for r in results:
            for p in r["problems"]:
                print(f"   {r['id']}: {p}")
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover - entry is ``klode-kit check-cards``
    raise SystemExit(main())
