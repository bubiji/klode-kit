"""WI-34: adopt an existing (pre-klode-kit) klode library into a klode-kit build (``klode-kit build --adopt OLD``).

The old libraries were made by older scripts with klode 0.5.0 alone: ``<old>/library.toml`` (``[library]``
id / name / description / dir / cards / shelves, ``[frameworks]``, ``[copyright]``, ``[bibliography]``), the sources
``<old>/<dir>/<shelf>/*.txt``, the cards ``<old>/<dir>/<cards>/<id>.md`` (klode scaffold; Thin / Full below the marker),
optionally ``<old>/cards_content/<id>.md`` (the ``aliases:`` / ``## Thin`` / ``## Full`` format klode-kit uses),
``<old>/src/desk.json`` + a holdout file and ``<old>/<dir>/<frameworks>/<syntheses>/*.md``.

This module only **reads** that tree (``read_old``) and holds the pure pieces the build needs:

- ``extract_card(text)``: a published klode card -> the ``cards_content`` text (the aliases of the front matter, then
  the body below the scaffold marker, stripped); ``None`` for a card never written (stub placeholder), without a marker
  or with an empty body. ``write_card.render`` of the result onto the same scaffold gives the card back (round trip).
- ``card_source(old, id)``: where the adopted card comes from - ``cards_content`` of the old library first, else the
  part below the marker of ``<dir>/<cards>/<id>.md``.
- ``patch_toml(text, old)``: ``extra_guard_dirs`` and ``[bibliography] enabled`` are carried into the new
  library.toml; the library / cards directory names stay klode-kit's, ``[copyright] guard`` stays true.
- ``inputs(old)``: the part of ``build_inputs.json`` that records what was adopted (sha of every source and card).

Nothing here writes, and nothing calls a model.
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Sequence

from klode_kit.build.init_kb import SHELF_RE, _section_span

MARKER_RE = re.compile(r"^<!-- scaffold: managed by klode.*?-->[ \t]*$", re.M)
ALIASES_LINE_RE = re.compile(r"^aliases: \[(.*)\][ \t]*$", re.M)
UNWRITTEN_RE = re.compile(r"_\(L[12] owed")
PLAIN_NAME_RE = re.compile(r"[A-Za-z0-9._-]+")


class AdoptError(Exception):
    """The directory is not an old klode library this build can read."""


@dataclass
class OldKb:
    root: Path
    kb_id: str
    name: str
    description: str
    shelves: tuple[str, ...]
    dropped_shelves: tuple[str, ...] = ()  # shelves of the old library left out by ``select`` (--adopt-shelves)
    library_dir: str = "library"
    cards_dir: str = "cards"
    guard: bool = True
    extra_guard_dirs: list[str] = field(default_factory=list)
    bibliography_enabled: bool = True
    frameworks_enabled: bool = False
    sources: dict[str, Path] = field(default_factory=dict)         # "<shelf>/<file>.txt" -> path
    cards_content: dict[str, Path] = field(default_factory=dict)   # id -> cards_content/<id>.md
    old_cards: dict[str, Path] = field(default_factory=dict)       # id -> <dir>/<cards>/<id>.md
    desk: Path | None = None
    holdout: Path | None = None
    syntheses_dir: Path | None = None
    bibliography: Path | None = None
    library_toml: Path | None = None


def sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _plain(value: Any, what: str) -> str:
    if not isinstance(value, str) or value in (".", "..") or not PLAIN_NAME_RE.fullmatch(value):
        raise AdoptError(f"library.toml: {what} must be a plain directory name: {value!r}")
    return value


def read_old(root: str | Path) -> OldKb:
    """Read the old library at ``root`` (no writes); ``AdoptError`` when it is not a readable klode library."""
    root = Path(root)
    if not root.is_dir():
        raise AdoptError(f"no such directory: {root}")
    toml_path = root / "library.toml"
    try:
        cfg = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    except OSError:
        raise AdoptError(f"no library.toml in {root} (not a klode library)") from None
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise AdoptError(f"library.toml is not valid TOML: {exc}") from None
    lib = cfg.get("library")
    if not isinstance(lib, dict):
        raise AdoptError("library.toml has no [library] table")
    kb_id = lib.get("id")
    if not isinstance(kb_id, str) or not kb_id.strip():
        raise AdoptError("library.toml: [library].id is missing")
    shelves = lib.get("shelves")
    if (not isinstance(shelves, list) or not shelves
            or not all(isinstance(s, str) and s not in (".", "..") and SHELF_RE.fullmatch(s) for s in shelves)):
        raise AdoptError(f"library.toml: [library].shelves must be a non-empty list of plain names: {shelves!r}")
    lib_dir = _plain(lib.get("dir", "library"), "[library].dir")
    cards_dir = _plain(lib.get("cards", "cards"), "[library].cards")
    fw = cfg.get("frameworks") if isinstance(cfg.get("frameworks"), dict) else {}
    cp = cfg.get("copyright") if isinstance(cfg.get("copyright"), dict) else {}
    bib = cfg.get("bibliography") if isinstance(cfg.get("bibliography"), dict) else {}
    extra = cp.get("extra_guard_dirs", [])
    old = OldKb(
        root=root, kb_id=kb_id, name=lib.get("name") if isinstance(lib.get("name"), str) else kb_id,
        description=lib.get("description") if isinstance(lib.get("description"), str) else "",
        shelves=tuple(dict.fromkeys(shelves)), library_dir=lib_dir, cards_dir=cards_dir,
        guard=cp.get("guard", True) is not False,
        extra_guard_dirs=[str(x) for x in extra] if isinstance(extra, list) else [],
        bibliography_enabled=bib.get("enabled", True) is not False, frameworks_enabled=fw.get("enabled", False) is True,
        library_toml=toml_path)
    library = root / lib_dir
    for shelf in old.shelves:
        for p in sorted((library / shelf).glob("*.txt")):
            if p.is_file():
                old.sources[f"{shelf}/{p.name}"] = p
    old.cards_content = {p.stem: p for p in sorted((root / "cards_content").glob("*.md")) if p.is_file()}
    old.old_cards = {p.stem: p for p in sorted((library / cards_dir).glob("*.md"))
                     if p.is_file() and p.stem != "INDEX"}
    if (root / "src" / "desk.json").is_file():
        old.desk = root / "src" / "desk.json"
    if old.desk is not None:
        candidates = [root / "src" / "holdout.jsonl", *sorted((root / "src").glob("*holdout*.jsonl")),
                      *sorted((root / "eval").glob("*holdout*.jsonl"))]
        old.holdout = next((c for c in candidates if c.is_file()), None)
    syn = library / str(fw.get("dir", "frameworks")) / str(fw.get("syntheses", "_syntheses"))
    if syn.is_dir():
        old.syntheses_dir = syn
    bib_path = library / str(bib.get("path", "BIBLIOGRAPHY.md"))
    if bib_path.is_file():
        old.bibliography = bib_path
    return old


def _desk_cards(desk: Path) -> set[str] | None:
    try:
        spec = json.loads(desk.read_text(encoding="utf-8"))
        return {str(c) for d in spec["dimensions"] for c in d["cards"]}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def select(old: OldKb, shelves: Sequence[str] | None) -> OldKb:
    """Only ``shelves`` of the old library (``--adopt-shelves``): their sources, the cards of those sources, the old
    shelf order. Cards of the other shelves are neither adopted nor reported missing. The old desk (and its holdout
    and syntheses) is kept only when every card it names is among the chosen ones; the bibliography loses the rows of
    the dropped shelves (``bibliography_text``). ``None`` or every shelf: ``old`` itself."""
    if shelves is None:
        return old
    chosen = list(dict.fromkeys(shelves))
    if not chosen:
        raise AdoptError("--adopt-shelves: no shelf named")
    unknown = [s for s in chosen if s not in old.shelves]
    if unknown:
        raise AdoptError(f"--adopt-shelves: no such shelf in {old.root}: {', '.join(unknown)} "
                         f"(shelves: {', '.join(old.shelves)})")
    keep = tuple(s for s in old.shelves if s in chosen)
    if keep == old.shelves:
        return old
    sources = {rel: p for rel, p in old.sources.items() if rel.split("/", 1)[0] in keep}
    ids = {Path(rel).stem for rel in sources}
    out = replace(old, shelves=keep, dropped_shelves=tuple(s for s in old.shelves if s not in keep), sources=sources,
                  cards_content={c: p for c, p in old.cards_content.items() if c in ids},
                  old_cards={c: p for c, p in old.old_cards.items() if c in ids})
    named = _desk_cards(old.desk) if old.desk is not None else None
    if old.desk is not None and (named is None or not named <= ids):
        out.desk = out.holdout = out.syntheses_dir = None
    return out


def bibliography_text(old: OldKb) -> str | None:
    """The old BIBLIOGRAPHY.md; after ``select`` without the table rows that name a source of a dropped shelf."""
    if old.bibliography is None:
        return None
    text = old.bibliography.read_bytes().decode("utf-8")  # bytes: CRLF stays
    if not old.dropped_shelves:
        return text
    gone = tuple(f"{old.library_dir}/{s}/" for s in old.dropped_shelves)
    return "".join(ln for ln in text.splitlines(keepends=True) if not (ln.lstrip().startswith("|") and any(g in ln for g in gone)))


def extract_card(text: str) -> str | None:
    """The ``cards_content`` text of a published klode card, or ``None`` when it holds no written card."""
    m = MARKER_RE.search(text)
    if m is None:
        return None
    body = text[m.end():].strip()
    if not body or UNWRITTEN_RE.search(body):
        return None
    found = ALIASES_LINE_RE.search(text[:m.start()])
    raw = found.group(1) if found else ""
    return f"aliases: [{raw}]\n\n{body}\n"


def card_source(old: OldKb, cid: str) -> tuple[str, bytes] | None:
    """``(where, bytes)`` of the card ``cid`` of the old library, or ``None`` when it was never written."""
    src = old.cards_content.get(cid)
    if src is not None:
        return "cards_content", src.read_bytes()
    card = old.old_cards.get(cid)
    if card is not None:
        try:
            text = extract_card(card.read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            return None
        if text is not None:
            return "library_cards", text.encode("utf-8")
    return None


def _set_key(text: str, section: str, key: str, literal: str) -> str:
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    span = _section_span([ln.rstrip("\n") for ln in lines], section)
    if span is None:
        return "".join(lines) + f"\n[{section}]\n{key} = {literal}\n"
    idx = next((i for i in range(span[0] + 1, span[1]) if re.match(rf"^\s*{re.escape(key)}\s*=", lines[i])), None)
    new = f"{key} = {literal}\n"
    if idx is None:
        lines.insert(span[0] + 1, new)
    else:
        lines[idx] = new
    return "".join(lines)


def patch_toml(text: str, old: OldKb) -> str:
    """The new library.toml with the old library's guard dirs and bibliography switch; guard stays on."""
    cfg = tomllib.loads(text)
    out = text
    if cfg.get("copyright", {}).get("extra_guard_dirs") != old.extra_guard_dirs:
        out = _set_key(out, "copyright", "extra_guard_dirs", json.dumps(old.extra_guard_dirs, ensure_ascii=False))
    if cfg.get("copyright", {}).get("guard") is not True:
        out = _set_key(out, "copyright", "guard", "true")
    if cfg.get("bibliography", {}).get("enabled") != old.bibliography_enabled:
        out = _set_key(out, "bibliography", "enabled", "true" if old.bibliography_enabled else "false")
    tomllib.loads(out)  # what klode will read
    return out


def inputs(old: OldKb) -> dict[str, Any]:
    """What is adopted, for ``build_inputs.json`` (hashes only: the build id follows the content, not the path)."""
    cards: dict[str, dict[str, str]] = {}
    for cid in sorted(set(old.cards_content) | set(old.old_cards)):
        got = card_source(old, cid)
        if got is not None:
            cards[cid] = {"from": got[0], "sha256": hashlib.sha256(got[1]).hexdigest()}
    doc = {
        "old_id": old.kb_id, "shelves": list(old.shelves),
        "settings": sha_file(old.library_toml) if old.library_toml else None,
        "sources": {rel: sha_file(p) for rel, p in sorted(old.sources.items())},
        "cards": cards,
        "bibliography": (hashlib.sha256((bibliography_text(old) or "").encode("utf-8")).hexdigest()
                         if old.bibliography else None),
        "frameworks_enabled": old.frameworks_enabled,
    }
    if old.dropped_shelves:
        doc["selected"] = list(old.shelves)
    return doc
