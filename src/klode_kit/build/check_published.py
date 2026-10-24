"""Gate 1 again, on the published cards ``library/cards/<id>.md`` below the scaffold marker (WI-02b; new).

``klode check --strict`` (gate 2) folds punctuation - an anchor written ``--`` where the source has ``—``
resolves ``FOLDED-ONLY`` and passes - and lets stub cards that were never written through with 0 errors
(03 B6). ``write_card`` now refuses a card gate 1 fails (B2), but a published card can still be wrong: written
with ``--force``, edited by hand afterwards, or never written at all. This check (02 r2.1 row 1.4; run by
``/kb-check``, WI-10a) rebuilds each published card as gate 1 sees a ``cards_content`` file - the frontmatter
``aliases:`` line followed by everything below the marker - and judges it with the same rules and the same
source-shelf policy (``check_cards.judge``: F1-F3, A1-A3, MIN_FULL / COV). Extra codes:

- ``MARKER`` no scaffold marker (nothing below it can be judged);
- ``ZOOM`` the frontmatter ``zoom:`` is not ``full`` (a stub, or never written by ``write_card``);
- ``RECEIPT`` no usable write receipt entry for the card (no / unreadable / non-conforming receipt, or the card
  is not in its ledger);
- ``STALE`` the card's bytes differ from the ``card_sha256`` the write receipt recorded (changed after it was
  written);
- ``FORCED`` the receipt says the card was written with ``--force``.

    klode-kit check-published (CARD ... | --all) [--kb DIR] [--receipt F] [--json] [--project DIR]
    exit 0 all pass; 1 any problem (also --all with no card); 2 usage; 40 configuration

CARD is ``library/cards/<id>.md`` or a bare id; ``--all`` takes every ``library/cards/*.md`` except
``INDEX.md``. The receipt defaults to ``<kb>/write_receipt.json``. ``--json`` prints ``{"schema":
"klode-kit.check_published/1", "kb", "receipt", "pass", "cards": [{"id", "card", "source", "policy", "anchors",
"coverage", "pass", "problems": [{"code", "message"}]}]}`` (``JSON_SCHEMA``).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from klode_kit import config as config_mod
from klode_kit.build import check_cards, write_card

SCHEMA_ID = "klode-kit.check_published/1"
CARD_ARG_RE = re.compile(r"^(?:library/cards/)?([A-Za-z0-9][A-Za-z0-9._-]*?)(\.md)?$")
FRONT_RE = re.compile(r"\A---\n(.*?)\n---\n", re.S)
INDEX = "INDEX.md"
CODES = ["MISSING", "SRC", "POLICY", "F1", "F2", "F3", "A1", "A2", "A3", "MIN_FULL", "COV", "MARKER", "ZOOM",
         "RECEIPT", "STALE", "FORCED"]

JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["schema", "kb", "receipt", "pass", "cards"],
    "properties": {
        "schema": {"const": SCHEMA_ID},
        "kb": {"type": "string"},
        "receipt": {"type": "string"},
        "pass": {"type": "boolean"},
        "cards": {"type": "array", "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["id", "card", "source", "policy", "anchors", "coverage", "pass", "problems"],
            "properties": {
                "id": {"type": "string", "minLength": 1},
                "card": {"type": "string", "pattern": "^library/cards/[^/]+\\.md$"},
                "source": {"type": ["string", "null"]},
                "policy": {"enum": ["authored", "deterministic", None]},
                "anchors": {"type": "integer", "minimum": 0},
                "coverage": check_cards.JSON_SCHEMA["properties"]["cards"]["items"]["properties"]["coverage"],
                "pass": {"type": "boolean"},
                "problems": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False, "required": ["code", "message"],
                    "properties": {"code": {"enum": CODES}, "message": {"type": "string"}}}},
            },
        }},
    },
}


def _frontmatter(text: str) -> str:
    m = FRONT_RE.match(text)
    return m.group(1) if m else ""


def check_one(kb: Path, cid: str, ledger: dict[str, Any] | None, *, min_aliases: int,
              min_anchors_full: int) -> dict[str, Any]:
    """Judge one published card; ``ledger`` is the write receipt's ``cards`` (None: no usable receipt)."""
    rel = f"library/cards/{cid}.md"
    card = kb / rel
    res: dict[str, Any] = {"id": cid, "card": rel, "source": None, "policy": None, "anchors": 0, "coverage": None,
                           "pass": False, "problems": []}
    if not card.is_file():
        res["problems"].append("MISSING 发布卡不存在")
        return res
    data = card.read_bytes()
    text = data.decode("utf-8")
    front = _frontmatter(text)
    probs: list[str] = []
    zoom = re.search(r"^zoom:\s*(.*?)\s*$", front, re.M)
    if not zoom or zoom.group(1) != "full":
        probs.append(f"ZOOM zoom 是 {zoom.group(1) if zoom else '（缺）'}，发布卡须为 full")
    if write_card.MARKER not in text:
        probs.append("MARKER 找不到 scaffold 标记线")
    else:
        aliases = re.search(r"^aliases: \[.*?\]$", front, re.M)
        raw = (aliases.group(0) if aliases else "") + "\n" + text.split(write_card.MARKER, 1)[1]
        judged = check_cards.judge(kb, cid, raw, min_aliases=min_aliases, min_anchors_full=min_anchors_full)
        for key in ("source", "policy", "anchors", "coverage"):
            res[key] = judged[key]
        probs.extend(judged["problems"])
    entry = (ledger or {}).get(cid)
    if entry is None:
        probs.append("RECEIPT 写入 receipt 里没有这张卡（不是经 write-card 写入的，或 receipt 缺失 / 不合规）")
    else:
        if entry["card_sha256"] != hashlib.sha256(data).hexdigest():
            probs.append("STALE 发布卡在写入 receipt 之后被改过（sha256 不符）")
        if entry["forced"]:
            probs.append("FORCED 这张卡是用 --force 写入的（门禁 1 未通过也照写）")
    res["problems"] = probs
    res["pass"] = not probs
    return res


def check_published(kb: Path, ids: list[str] | None = None, *, receipt: Path | None = None,
                    min_aliases: int | None = None, min_anchors_full: int | None = None) -> dict[str, Any]:
    """The ``--json`` document for ``ids`` (None: every published card)."""
    kb = Path(kb).resolve()
    if min_aliases is None or min_anchors_full is None:
        cfg = config_mod.load(None)
        min_aliases = cfg["cards.min_aliases"] if min_aliases is None else min_aliases
        min_anchors_full = cfg["cards.min_anchors_full"] if min_anchors_full is None else min_anchors_full
    receipt = Path(receipt) if receipt is not None else kb / write_card.RECEIPT_NAME
    if ids is None:
        ids = sorted(p.stem for p in (kb / "library" / "cards").glob("*.md") if p.name != INDEX)
    loaded = write_card.load_receipt(receipt)
    ledger = loaded["cards"] if loaded else None
    cards = [check_one(kb, cid, ledger, min_aliases=min_aliases, min_anchors_full=min_anchors_full) for cid in ids]
    return {"schema": SCHEMA_ID, "kb": str(kb), "receipt": str(receipt), "pass": bool(cards) and all(
        c["pass"] for c in cards), "cards": cards}


def _json_doc(doc: dict[str, Any]) -> dict[str, Any]:
    out = dict(doc)
    out["cards"] = []
    for c in doc["cards"]:
        c = dict(c)
        c["problems"] = [dict(zip(("code", "message"), (p.split(" ", 1) + [""])[:2])) for p in c["problems"]]
        out["cards"].append(c)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="klode-kit check-published", description=__doc__.splitlines()[0])
    parser.add_argument("cards", nargs="*", metavar="CARD", help="library/cards/<id>.md or <id>")
    parser.add_argument("--all", action="store_true", help="check every library/cards/*.md (not INDEX.md)")
    parser.add_argument("--kb", type=Path, default=Path("."), help="build / library directory (default: cwd)")
    parser.add_argument("--receipt", type=Path, default=None,
                        help=f"write receipt (default KB/{write_card.RECEIPT_NAME})")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    parser.add_argument("--project", type=Path, default=None, help="data project with klode-kit.toml overrides")
    try:
        args = parser.parse_args(argv)
        if bool(args.cards) == args.all:
            parser.error("give CARD ... or --all (not both)")
        ids = []
        for card in args.cards:
            m = CARD_ARG_RE.match(card)
            if not m or (card.startswith("library/cards/") and not m.group(2)):
                parser.error(f"card must be library/cards/<id>.md or <id>: {card!r}")
            ids.append(m.group(1))
    except SystemExit as exc:
        return 2 if exc.code else 0
    cfg = config_mod.load(args.project)
    doc = check_published(args.kb, ids if not args.all else None, receipt=args.receipt,
                          min_aliases=cfg["cards.min_aliases"], min_anchors_full=cfg["cards.min_anchors_full"])
    if args.all and not doc["cards"]:
        print(f"check-published: no card in {Path(args.kb) / 'library' / 'cards'}", file=sys.stderr)
    if args.json:
        print(json.dumps(_json_doc(doc), ensure_ascii=False, indent=1))
    else:
        for c in doc["cards"]:
            n = len(c["problems"])
            print(f"{'PASS' if c['pass'] else 'FAIL'}  {c['id']}  ({c['anchors']} 锚点{'' if c['pass'] else f'，{n} 个问题'})")
        for c in doc["cards"]:
            for p in c["problems"]:
                print(f"   {c['id']}: {p}")
    return 0 if doc["pass"] else 1


if __name__ == "__main__":  # pragma: no cover - entry is ``klode-kit check-published``
    raise SystemExit(main())
