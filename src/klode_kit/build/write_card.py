"""Write ``cards_content/<id>.md`` below the scaffold marker of ``library/cards/<id>.md`` - gated (WI-02b; vendored).

Vendored from the user's klode-build skill ``scripts/write_card.py`` (read-only source, 2026-10-24). ``klode
build`` owns everything above the marker; below it is the human / agent area and this command is its only
writer. ``cards_content/<id>.md``: first line ``aliases: [a, b, ...]``, then ``## Thin`` and ``## Full``.

Kept from the original (03 A4): above the marker only ``zoom:`` (-> ``zoom: full``) and ``aliases:`` (synced)
change; below it the card body, stripped, between one blank line and one final newline; a missing scaffold
(``klode build`` not run) or marker is refused; no id = every ``cards_content/*.md``; ``  wrote <id>  (<n>
aliases)`` per card.

Changed (03 B2 / G3; 02 §3.3 B2, r2.1 row 1.4):

- **gate 1 is built in**: ``check_cards.check_card`` (policy from the source's shelf) runs on every card before
  anything is written. Any FAIL, or a missing scaffold / marker, -> exit 1 and **nothing** is written: the
  bytes of ``library/cards`` are unchanged (all-or-nothing, so the original's half-written runs cannot happen).
- ``--force`` is for a human only and is never passed by the pipeline. It writes despite gate 1 (not despite a
  missing scaffold), and every use is recorded: ``forced: true``, ``status: failed``, exit 1.
- a write receipt (below) for ``check_published`` (stale / forced cards) and the build receipt's step 8.
- aliases are substituted literally (the original passed them to ``re.sub`` as a replacement template, so an
  alias with a backslash broke or changed the line); writes are atomic (temporary file + ``os.replace``).

    klode-kit write-card KB [ID ...] [--force] [--receipt F] [--project DIR]
    exit 0 written; 1 gate 1 FAIL / refused / forced; 2 usage; 40 configuration

Receipt ``<KB>/write_receipt.json`` (``--receipt`` overrides), ``klode-kit.write_receipt/1`` (``JSON_SCHEMA``;
no wall-clock fields, so an identical rerun gives identical bytes)::

    {"schema", "status": "done"|"failed", "forced", "written": [id], "gate": [{"id", "pass", "problems"}],
     "refused": [{"id", "reason"}], "cards": {id: {"card_sha256", "content_sha256", "forced"}}}

``cards`` is the ledger of every card this command has published, carried forward from the previous receipt;
a refused run leaves it unchanged.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import secrets
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from klode_kit import config as config_mod
from klode_kit.build import check_cards

MARKER = "<!-- scaffold: managed by klode — edit Thin/Full below the marker by hand -->"
RECEIPT_NAME = "write_receipt.json"
SCHEMA_ID = "klode-kit.write_receipt/1"
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
ALIASES_RE = re.compile(r"^aliases: \[(.*?)\]\n(.*)$", re.S)
_SHA = {"type": "string", "pattern": "^[0-9a-f]{64}$"}

JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["schema", "status", "forced", "written", "gate", "refused", "cards"],
    "properties": {
        "schema": {"const": SCHEMA_ID},
        "status": {"enum": ["done", "failed"]},
        "forced": {"type": "boolean"},
        "written": {"type": "array", "items": {"type": "string", "minLength": 1}},
        "gate": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["id", "pass", "problems"],
            "properties": {"id": {"type": "string", "minLength": 1}, "pass": {"type": "boolean"},
                           "problems": {"type": "array", "items": {"type": "string"}}}}},
        "refused": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["id", "reason"],
            "properties": {"id": {"type": "string", "minLength": 1}, "reason": {"type": "string"}}}},
        "cards": {"type": "object", "additionalProperties": {
            "type": "object", "additionalProperties": False, "required": ["card_sha256", "content_sha256", "forced"],
            "properties": {"card_sha256": _SHA, "content_sha256": _SHA, "forced": {"type": "boolean"}}}},
    },
}
_VALIDATOR = Draft202012Validator(JSON_SCHEMA)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def render(card_text: str, aliases: list[str], body: str) -> str:
    """The published card: ``card_text`` above the marker with ``zoom: full`` and the aliases, then ``body``."""
    if MARKER not in card_text:
        raise ValueError("scaffold marker not found")
    head = card_text.split(MARKER)[0]
    head = re.sub(r"^zoom: .*$", lambda _m: "zoom: full", head, count=1, flags=re.M)
    line = "aliases: [" + ", ".join(aliases) + "]"
    head = re.sub(r"^aliases: .*$", lambda _m: line, head, count=1, flags=re.M)
    return head + MARKER + "\n\n" + body.strip() + "\n"


def parse_content(raw: str) -> tuple[list[str], str] | None:
    """``(aliases, body)`` of a ``cards_content`` file, or None when the first line is not ``aliases: [...]``."""
    m = ALIASES_RE.match(raw)
    if not m:
        return None
    return [a.strip() for a in m.group(1).split(",") if a.strip()], m.group(2)


def load_receipt(path: Path) -> dict[str, Any] | None:
    """The write receipt at ``path`` when it exists and conforms to ``JSON_SCHEMA``, else None."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if _VALIDATOR.is_valid(data) else None


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.{secrets.token_hex(8)}.tmp"
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.remove(tmp)


def write_cards(kb: Path, ids: list[str] | None = None, *, force: bool = False, receipt: Path | None = None,
                min_aliases: int | None = None, min_anchors_full: int | None = None) -> tuple[int, dict[str, Any]]:
    """Gate 1, then write. Returns ``(exit code, receipt)``; the receipt is written in every case."""
    kb = Path(kb).resolve()
    receipt = Path(receipt) if receipt is not None else kb / RECEIPT_NAME
    src_dir = kb / "cards_content"
    if ids is None:
        ids = sorted(p.stem for p in src_dir.glob("*.md"))
    previous = load_receipt(receipt)
    ledger: dict[str, Any] = dict(previous["cards"]) if previous else {}
    doc: dict[str, Any] = {"schema": SCHEMA_ID, "status": "failed", "forced": force, "written": [], "gate": [],
                           "refused": [], "cards": ledger}
    if not ids:
        print(f"write-card: no card in {src_dir}", file=sys.stderr)
        _atomic_write(receipt, _dumps(doc))
        return 1, doc
    plans: list[tuple[str, Path, str, bytes]] = []
    for cid in ids:
        res = check_cards.check_card(kb, cid, min_aliases=min_aliases, min_anchors_full=min_anchors_full)
        doc["gate"].append({"id": cid, "pass": res["pass"], "problems": res["problems"]})
        card = kb / "library" / "cards" / f"{cid}.md"
        reason = None
        if not card.is_file():
            reason = f"{cid}: 卡片骨架不存在，先跑 klode build"
        elif MARKER not in card.read_text(encoding="utf-8"):
            reason = f"{cid}: 找不到 scaffold 标记线"
        content_file = src_dir / f"{cid}.md"
        parsed = parse_content(content_file.read_text(encoding="utf-8")) if content_file.is_file() else None
        if reason is None and parsed is None:
            reason = f"{cid}: 内容文件不存在或首行不是 aliases: [...]"
        if reason is not None:
            doc["refused"].append({"id": cid, "reason": reason})
            print(reason, file=sys.stderr)
            continue
        aliases, body = parsed
        plans.append((cid, card, render(card.read_text(encoding="utf-8"), aliases, body),
                      content_file.read_bytes()))
    gate_ok = all(g["pass"] for g in doc["gate"])
    if not gate_ok:
        for g in doc["gate"]:
            print(f"{'PASS' if g['pass'] else 'FAIL'}  {g['id']}")
        for g in doc["gate"]:
            for p in g["problems"]:
                print(f"   {g['id']}: {p}")
    if doc["refused"] or (not gate_ok and not force):
        if not gate_ok and not force:
            print("write-card: gate 1 FAIL - nothing written (library/cards unchanged)", file=sys.stderr)
        _atomic_write(receipt, _dumps(doc))
        return 1, doc
    for cid, card, text, content in plans:
        data = text.encode("utf-8")
        if card.read_bytes() != data:
            _atomic_write(card, data)
        n = len(parse_content(content.decode("utf-8"))[0])
        print(f"  wrote {cid}  ({n} aliases)")
        doc["written"].append(cid)
        ledger[cid] = {"card_sha256": _sha(data), "content_sha256": _sha(content), "forced": force}
    doc["cards"] = dict(sorted(ledger.items()))
    if force:
        print("write-card: --force used - the step is recorded as failed (forced=true)", file=sys.stderr)
    else:
        doc["status"] = "done"
    _atomic_write(receipt, _dumps(doc))
    return (0 if doc["status"] == "done" else 1), doc


def _dumps(doc: dict[str, Any]) -> bytes:
    errors = list(_VALIDATOR.iter_errors(doc))
    if errors:  # pragma: no cover - a bug in this module, not an input problem
        raise ValueError(f"write receipt violates {SCHEMA_ID}: {errors[0].message}")
    return (json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="klode-kit write-card", description=__doc__.splitlines()[0])
    parser.add_argument("kb", type=Path, help="build / library directory (holds cards_content/ and library/)")
    parser.add_argument("ids", nargs="*", metavar="ID", help="card ids (default: every cards_content/*.md)")
    parser.add_argument("--force", action="store_true",
                        help="humans only: write despite gate 1; recorded as forced and the step fails")
    parser.add_argument("--receipt", type=Path, default=None, help=f"write receipt (default KB/{RECEIPT_NAME})")
    parser.add_argument("--project", type=Path, default=None, help="data project with klode-kit.toml overrides")
    try:
        args = parser.parse_args(argv)
        for cid in args.ids:
            if not ID_RE.match(cid):
                parser.error(f"not a card id: {cid!r}")
    except SystemExit as exc:
        return 2 if exc.code else 0
    cfg = config_mod.load(args.project)
    rc, _doc = write_cards(args.kb, args.ids or None, force=args.force, receipt=args.receipt,
                           min_aliases=cfg["cards.min_aliases"], min_anchors_full=cfg["cards.min_anchors_full"])
    return rc


if __name__ == "__main__":  # pragma: no cover - entry is ``klode-kit write-card``
    raise SystemExit(main())
