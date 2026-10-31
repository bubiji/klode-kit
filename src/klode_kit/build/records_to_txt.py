"""WI-01a: ``records_to_txt`` - verified records -> deterministic text sources + ``export_map`` (design 02 r2.2-A
export line, r2.2-C 书架/行格式, r3.1 segment 3 + version binding, R2-02, R3-07; plan WI-01a; matrix 01a-*).

``export(project, out=None, *, kb_id=None, bindings=None, deident=None) -> dict``

**What is exported.** ``bindings`` is a build's snapshot ``{rec_id: {rev, audit_rev}}``; without it the snapshot is
taken here from ``records/_by_id/<rec_id>.md``: every record whose ``effective`` revision is ``verified`` binds
``effective.{rev, audit_rev}``; any other record is ``excluded`` (``not_verified``, with its status). Every value
of a record is read from the bound revision body ``records/_rev/<rec_id>.r<rev>.md`` (r3.1: the bound revision,
not the current one; R3-07: never ``cells.jsonl``, never a newer candidate). A bound body that is not verified is
``not_verified`` too; a body without fields is ``empty_record``; a value holding a line break (or not a string) is
``malformed_value``; a record without any date is ``no_date``. An excluded record yields no line.

**Rows.** A body's ``fields`` are grouped into rows by slot:

- kb-audit slots ``r<row>-<kind>-<k>`` (WI-07b/08a): ``number-0`` is the value, ``unit-0`` the unit, ``flag-0`` the
  flag, ``drug-0`` the item (rx rows), ``date-*`` a date; other slots (``number-1``: a reference range, ...) are
  extra. The item of a row without an item field is the ``item_label`` kb-audit gave that row in the bound
  revision's own audit revision (``work/<batch>/audit/a<N>/candidates.jsonl``; only the label is read there).
- fx-chain slots ``<group>[<n>].<role>`` (roles ``item value unit flag date``) and a bare ``date``: the record
  date field, consumed as the observation date and not printed on its own.

The record date is the body's ``date``, else its first date field; the field whose value equals it (bare ``date``
first) is the date field of the record's observation lines.

**Lines.** A row with an item and a value is an **observation**::

    <item> <value>[ <unit>] <date>[ <flag>] ⟨<rec_id>⟩

Any other row is a **source** line: ``[<label> ]<values in role order> ⟨<rec_id>⟩``. ``<item>`` is the item's
display name: the first alias of its ``items.yaml`` entry (an alias written on the page, ``HGB``, is shown as
``血红蛋白`` and the export map row carries an ``alias`` transform on the item field); an unknown item keeps its
own text and is listed in ``new_items``. Values and units are written as audited, with no transform. Extra fields
of an observation row are not exported. When one record would print the same text twice, the second copy's
anchor is ``⟨<rec_id>#2⟩`` (and so on): every line is unique by construction (the rec_id separates records).

**Shelves.** ``library/items/<item_slug>.txt``: the observation lines of the item, ordered by (date, rec_id, row);
``library/records/<rec_id>.txt``: every line of the record, in row order. ``export_map`` has one row per line
(contract ``export_map``: ``{kb_id, source_file, line_no, rec_id, field_ids, rev, text, value_audited, positions,
transforms, line_kind}``, ``line_kind`` = ``observation`` | ``source``; ``value_audited`` = the value of an
observation, the first printed value of a source line), ordered by file then line. ``items_index``: ``{slug:
[{rec_id, rev, date}]}``.

**Result** (always returned, plain JSON data): ``{kb_id, audit_bindings, files: {relpath: text}, export_map,
items_index, excluded: [{rec_id, reason, status}], new_items}``. With ``out`` the files are also written:
``out/library/{items,records}/*.txt``, ``out/src/export_map.jsonl``, ``out/src/items_index.json``. The export never
overwrites: any target already present is an ``ExportError`` before anything is written. Output depends only on
the bound bodies, the item table and the labels: the same input gives the same bytes.

**De-identified view** (WI-01c; design r2.1 1.2, r3.1 segment 3): ``deident="pseudonym" | "dateshift"`` exports
the view of that mode with the project key (``deident.load_key``: ``<project>/_private/deident_key.json``,
created on first use; an unknown mode or a malformed key is a ``deident.DeidentError`` before anything is
written). Without ``deident`` the export is the plain one above. Under a mode:

- a row **label** (not a field) is ``deident.scrub``-bed; a change is registered as a line-level ``layout``
  transform ``{kind, from, to}`` (no field_id);
- under ``dateshift`` every **date** shown - the observation date, a date field of a source line - moves by the
  key's single offset (``deident.shift_date``, spelling kept) and is registered as ``{kind: dateshift, field_id,
  from, to}`` (without field_id when the record date comes from the body alone); under ``pseudonym`` dates are
  shown as audited and no ``dateshift`` transform exists;
- every other shown **field** (item, value, unit, flag, extra) stays as audited - values and units never get a
  transform, an item only its alias. A field holding an identifier (``deident.find_identifiers``), under
  ``dateshift`` one holding a date outside a date field, or a date that cannot be moved, would leak through: the
  record is excluded as ``deident_unsafe`` instead.

``value_audited`` and ``transforms[].from`` stay the audited values (``unapply`` must give them back), so
``src/export_map.jsonl`` holds originals and belongs with the private side of a build; ``library/`` and the cards
built from it are the de-identified view.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from klode_kit import items as items_mod
from klode_kit.build import deident as deident_mod

ITEMS_SHELF = "library/items"
RECORDS_SHELF = "library/records"
EXPORT_MAP = "src/export_map.jsonl"
ITEMS_INDEX = "src/items_index.json"

_PIPE = re.compile(r"^r(\d+)-([a-z]+)-(\d+)$")
_CHAIN = re.compile(r"^([A-Za-z_]\w*)\[(\d+)\]\.([A-Za-z_]\w*)$")
_PIPE_ROLE = {"number": "value", "unit": "unit", "flag": "flag", "drug": "item"}
_CHAIN_ROLES = ("item", "value", "unit", "flag", "date")
_ROLE_ORDER = {"item": 0, "value": 1, "unit": 2, "date": 3, "flag": 4, "extra": 5}
_BARE = 2


class ExportError(RuntimeError):
    """The export cannot run (a target file exists, a record file is missing or malformed)."""


# --------------------------------------------------------------------------- reading


def _front(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ExportError(f"{path}: cannot read: {exc}") from exc
    end = text.find("\n---", 4)
    try:
        if not text.startswith("---\n") or end < 0:
            raise ValueError("no front matter block")
        data = json.loads(text[4:end])
    except ValueError as exc:
        raise ExportError(f"{path}: malformed record file: {exc}") from exc
    if not isinstance(data, dict):
        raise ExportError(f"{path}: malformed record file: front matter is not an object")
    return data


def _snapshot(project: Path) -> tuple[dict[str, dict], list[dict]]:
    """``(bindings, excluded)`` from ``records/_by_id``: effective verified records bind ``effective``."""
    bindings: dict[str, dict] = {}
    excluded: list[dict] = []
    root = project / "records" / "_by_id"
    for path in sorted(root.glob("*.md")) if root.is_dir() else []:
        front = _front(path)
        rec = front.get("rec_id", path.stem)
        eff = front.get("effective") or {}
        if eff.get("status") == "verified":
            bindings[rec] = {"rev": eff["rev"], "audit_rev": eff["audit_rev"]}
        else:
            excluded.append({"rec_id": rec, "reason": "not_verified", "status": front.get("status")})
    return bindings, excluded


snapshot = _snapshot   # public: the build takes the snapshot once at its start (WI-09b)
read_front = _front    # public: the JSON front matter of a record file (WI-09b)


def _candidate_status(project: Path, rec: str, rev: Any) -> str | None:
    """The status ``records/_by_id/<rec>.md`` gives revision ``rev`` (bodies written before WI-08c carry none)."""
    path = project / "records" / "_by_id" / f"{rec}.md"
    if not path.is_file():
        return None
    return next((c.get("status") for c in _front(path).get("candidates", []) if c.get("rev") == rev), None)


def _labels(project: Path, audit_rev: Any, cache: dict[str, dict[str, str]]) -> dict[str, str]:
    """``{field_id: item_label}`` of ``audit_rev``'s ``candidates.jsonl`` (empty when there is none)."""
    if not isinstance(audit_rev, str) or "/" not in audit_rev:
        return {}
    if audit_rev not in cache:
        batch, n = audit_rev.split("/", 1)
        path = project / "work" / batch / "audit" / n / "candidates.jsonl"
        out: dict[str, str] = {}
        if path.is_file():
            for raw in path.read_text(encoding="utf-8").splitlines():
                if not raw.strip():
                    continue
                for f in json.loads(raw).get("fields", []):
                    if isinstance(f.get("item_label"), str) and f["item_label"]:
                        out.setdefault(f["field_id"], f["item_label"])
        cache[audit_rev] = out
    return cache[audit_rev]


# --------------------------------------------------------------------------- rows


def _slot(fid: str) -> tuple[tuple[int, str, int], str]:
    """``(row key, role)`` of a field id."""
    slot = fid.split("/", 1)[1]
    m = _PIPE.match(slot)
    if m:
        kind, k = m.group(2), int(m.group(3))
        role = "date" if kind == "date" else (_PIPE_ROLE.get(kind, "extra") if k == 0 else "extra")
        return (0, "", int(m.group(1))), role
    m = _CHAIN.match(slot)
    if m:
        role = m.group(3) if m.group(3) in _CHAIN_ROLES else "extra"
        return (1, m.group(1), int(m.group(2))), role
    return (_BARE, slot, 0), "date" if slot == "date" else "extra"


@dataclass
class _Row:
    key: tuple[int, str, int]
    roles: dict[str, tuple[str, str]] = field(default_factory=dict)
    fields: list[tuple[str, str, str]] = field(default_factory=list)  # (role, fid, value)


@dataclass
class _Line:
    rec_id: str
    base: str
    kind: str
    parts: list[tuple[str, str | None]]  # (text, field_id)
    transforms: list[dict]
    value_audited: str
    slug: str | None = None
    date: str | None = None
    order: int = 0
    text: str = ""
    positions: dict[str, list[int]] = field(default_factory=dict)


class _Items:
    def __init__(self, table: items_mod.Items):
        self.table = table
        self.by_slug = {it.slug: it for it in table.items}

    def display(self, label: str) -> tuple[str, str, bool]:
        """``(slug, display name, known)`` of a label or slug."""
        slug, known = self.table.resolve(label)
        if not known:
            return slug, label, False
        item = self.by_slug[slug]
        return slug, (item.aliases[0] if item.aliases else slug), True


def _rows(fields: Mapping[str, str]) -> list[_Row]:
    rows: dict[tuple[int, str, int], _Row] = {}
    for fid in sorted(fields):
        key, role = _slot(fid)
        row = rows.setdefault(key, _Row(key))
        row.fields.append((role, fid, fields[fid]))
        if role != "extra":
            row.roles.setdefault(role, (fid, fields[fid]))
    return [rows[k] for k in sorted(rows)]


def _record_date(body: Mapping[str, Any], rows: list[_Row]) -> tuple[str | None, str | None]:
    """``(date, date field id)``."""
    dated = sorted(((0 if r.key[0] == _BARE else 1, r.key, fid, v) for r in rows for role, fid, v in r.fields
                    if role == "date"))
    date = body.get("date") if isinstance(body.get("date"), str) and body.get("date") else None
    if date is None and dated:
        date = dated[0][3]
    fid = next((f for _, _, f, v in dated if v == date), None)
    return date, fid


class _Unsafe(Exception):
    """The record cannot be shown under the deident mode without touching a field no transform may touch."""


@dataclass
class _Deident:
    """The export view under a deident mode (``deident`` module; r3.1 segment 3 transforms)."""

    mode: str
    key: deident_mod.Key

    def check(self, text: str) -> None:
        """A field shown as audited (no transform) must hold no identifier, nor a date under ``dateshift``."""
        if deident_mod.find_identifiers(text) or (self.mode == "dateshift" and deident_mod.find_dates(text)):
            raise _Unsafe

    def date(self, value: str, fid: str | None, transforms: list[dict]) -> str:
        """A date as shown; under ``dateshift`` moved by the key offset and registered as a transform."""
        if self.mode != "dateshift":
            self.check(value)
            return value
        try:
            moved = deident_mod.shift_date(value, self.key.offset_days)
        except deident_mod.DeidentError:
            raise _Unsafe from None
        t = {"kind": "dateshift", "field_id": fid, "from": value, "to": moved}
        transforms.append(t if fid is not None else {k: v for k, v in t.items() if k != "field_id"})
        return moved

    def label(self, label: str, transforms: list[dict]) -> str:
        """A row label (not a field): identifiers scrubbed, the change registered as a ``layout`` transform."""
        shown = deident_mod.scrub(label, self.key, self.mode)
        if shown != label:
            transforms.append({"kind": "layout", "from": label, "to": shown})
        return shown


def _build_lines(rec: str, rows: list[_Row], date: str, date_fid: str | None, labels: Mapping[str, str],
                 items: _Items, new_items: set[str], deid: _Deident | None) -> list[_Line]:
    """The lines of a record's rows, before anchors (``_Unsafe`` when the deident view cannot show them)."""

    def shown(text: str, fid: str | None, role: str, transforms: list[dict]) -> str:
        if deid is None:
            return text
        if role == "date":
            return deid.date(text, fid, transforms)
        deid.check(text)
        return text

    out: list[_Line] = []
    record_moves: list[dict] = []
    shown_date = shown(date, date_fid, "date", record_moves)
    for idx, row in enumerate(rows):
        if row.key[0] == _BARE and all(role == "date" for role, _, _ in row.fields):
            continue
        item_fid, item_raw = row.roles.get("item", (None, None))
        transforms: list[dict] = []
        slug = display = None
        known = True
        if item_raw is not None:
            shown(item_raw, item_fid, "item", transforms)
            slug, display, known = items.display(item_raw)
            if display != item_raw:
                transforms.append({"kind": "alias", "field_id": item_fid, "from": item_raw, "to": display})
        else:
            order = [row.roles["value"][0]] if "value" in row.roles else []
            label = next((labels[f] for f in order + [f for _, f, _ in row.fields] if f in labels), None)
            if label is not None:
                if deid is not None:
                    label = deid.label(label, transforms)
                slug, display, known = items.display(label)
        head = [(display, item_fid)] if display is not None else []
        if display is not None and "value" in row.roles:
            if not known:
                new_items.add(slug)
            value_fid, value = row.roles["value"]
            parts = head + [(shown(value, value_fid, "value", transforms), value_fid)]
            if "unit" in row.roles:
                parts.append((shown(row.roles["unit"][1], row.roles["unit"][0], "unit", transforms),
                              row.roles["unit"][0]))
            parts.append((shown_date, date_fid))
            transforms += [dict(t) for t in record_moves]
            if "flag" in row.roles:
                parts.append((shown(row.roles["flag"][1], row.roles["flag"][0], "flag", transforms),
                              row.roles["flag"][0]))
            out.append(_Line(rec, "", "observation", parts, transforms, value, slug, shown_date, idx))
        else:
            rest = sorted(((_ROLE_ORDER[role], role, fid, v) for role, fid, v in row.fields if fid != item_fid))
            parts = head + [(shown(v, fid, role, transforms), fid) for _, role, fid, v in rest]
            value_audited = rest[0][3] if rest else item_raw
            out.append(_Line(rec, "", "source", parts, transforms, value_audited, None, None, idx))
    return out


def _record_lines(rec: str, body: Mapping[str, Any], labels: Mapping[str, str], items: _Items,
                  new_items: set[str], deid: _Deident | None = None) -> list[_Line] | str:
    """The lines of one bound body, or the exclusion reason."""
    fields = body.get("fields") or {}
    if not isinstance(fields, dict) or not fields:
        return "empty_record"
    if any(not isinstance(v, str) or "\n" in v or "\r" in v for v in fields.values()):
        return "malformed_value"
    rows = _rows(fields)
    date, date_fid = _record_date(body, rows)
    if date is None:
        return "no_date"
    try:
        out = _build_lines(rec, rows, date, date_fid, labels, items, new_items, deid)
    except _Unsafe:
        return "deident_unsafe"
    seen: dict[str, int] = {}
    for line in out:
        base = " ".join(text for text, _ in line.parts)
        seen[base] = seen.get(base, 0) + 1
        anchor = f"⟨{rec}⟩" if seen[base] == 1 else f"⟨{rec}#{seen[base]}⟩"
        line.base, pos = base, 0
        for text, fid in line.parts:
            if fid is not None:
                line.positions[fid] = [pos, pos + len(text)]
            pos += len(text) + 1
        line.text = f"{base} {anchor}"
    return out


# --------------------------------------------------------------------------- export


def _map_row(kb_id: str, rel: str, n: int, line: _Line, rev: int) -> dict:
    ids = list(dict.fromkeys(fid for _, fid in line.parts if fid is not None))
    return {"kb_id": kb_id, "source_file": rel, "line_no": n, "rec_id": line.rec_id, "field_ids": ids, "rev": rev,
            "text": line.text, "value_audited": line.value_audited, "positions": dict(line.positions),
            "transforms": [dict(t) for t in line.transforms], "line_kind": line.kind}


def export(project: str | os.PathLike, out: str | os.PathLike | None = None, *, kb_id: str | None = None,
           bindings: Mapping[str, Mapping[str, Any]] | None = None, deident: str | None = None) -> dict:
    """Export the bound verified records of ``project`` (see the module docstring); write them under ``out``."""
    project = Path(project)
    deid = None
    if deident is not None:
        deid = _Deident(deident_mod.check_mode(deident), deident_mod.load_key(project))
    kb = kb_id or project.resolve().name
    if bindings is None:
        bound, excluded = _snapshot(project)
    else:
        bound, excluded = {r: {"rev": b["rev"], "audit_rev": b["audit_rev"]} for r, b in bindings.items()}, []
    items = _Items(items_mod.default())
    cache: dict[str, dict[str, str]] = {}
    new_items: set[str] = set()
    per_record: dict[str, list[_Line]] = {}
    audit_bindings: dict[str, dict] = {}
    for rec in sorted(bound):
        b = bound[rec]
        path = project / "records" / "_rev" / f"{rec}.r{b['rev']}.md"
        if not path.is_file():
            raise ExportError(f"{path}: bound revision body not found")
        body = _front(path)
        status = body.get("status") or _candidate_status(project, rec, b["rev"])
        if status != "verified":
            excluded.append({"rec_id": rec, "reason": "not_verified", "status": status})
            continue
        lines = _record_lines(rec, body, _labels(project, body.get("audit_rev"), cache), items, new_items, deid)
        if isinstance(lines, str):
            excluded.append({"rec_id": rec, "reason": lines, "status": body.get("status")})
            continue
        per_record[rec] = lines
        audit_bindings[rec] = dict(b)

    files: dict[str, list[_Line]] = {}
    owner: dict[str, str] = {}  # file stem -> the slug / record id it names (D-38: stems are ASCII-safe)

    def stem(name: str) -> str:
        s = items_mod.file_stem(name)
        if owner.setdefault(s, name) != name:
            raise ExportError(f"file name collision: {owner[s]!r} and {name!r} both map to {s!r}")
        return s

    for rec, lines in per_record.items():
        files[f"{RECORDS_SHELF}/{stem(rec)}.txt"] = list(lines)
        for line in lines:
            if line.kind == "observation":
                files.setdefault(f"{ITEMS_SHELF}/{stem(line.slug)}.txt", []).append(line)
    index: dict[str, list[dict]] = {}
    for rel in files:
        if rel.startswith(ITEMS_SHELF + "/"):
            files[rel].sort(key=lambda ln: (ln.date, ln.rec_id, ln.order))
            slug = rel[len(ITEMS_SHELF) + 1:-len(".txt")]  # the file stem: the key of items_index
            entries = {(ln.date, ln.rec_id): {"rec_id": ln.rec_id, "rev": audit_bindings[ln.rec_id]["rev"],
                                               "date": ln.date} for ln in files[rel]}
            index[slug] = [entries[k] for k in sorted(entries)]
    export_map = [_map_row(kb, rel, n, line, audit_bindings[line.rec_id]["rev"])
                  for rel in sorted(files) for n, line in enumerate(files[rel], start=1)]
    result = {
        "kb_id": kb,
        "audit_bindings": audit_bindings,
        "files": {rel: "".join(ln.text + "\n" for ln in files[rel]) for rel in sorted(files)},
        "export_map": export_map,
        "items_index": {k: index[k] for k in sorted(index)},
        "excluded": sorted(excluded, key=lambda e: e["rec_id"]),
        "new_items": sorted(new_items),
    }
    if out is not None:
        _write(Path(out), result)
    return result


def _write(out: Path, result: Mapping[str, Any]) -> None:
    payload = dict(result["files"])
    payload[EXPORT_MAP] = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n"
                                  for r in result["export_map"])
    payload[ITEMS_INDEX] = json.dumps(result["items_index"], ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    present = [rel for rel in payload if (out / rel).exists() or (out / rel).is_symlink()]
    if present:
        raise ExportError(f"{out}: refusing to overwrite {', '.join(sorted(present))}")
    for rel in sorted(payload):
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as fh:
            fh.write(payload[rel])


def main(argv: list[str] | None = None) -> int:
    """``klode-kit records-to-txt --project P --out DIR [--kb-id ID] [--bindings F] [--deident MODE]``: export the
    bound verified records under ``DIR`` and print one JSON line ``{kb_id, audit_bindings, excluded, new_items,
    files}``. Exit 0; 1 the export refused (a target exists, a record file is missing / malformed); 2 usage; 40 the
    de-identification key or mode is unusable."""
    parser = argparse.ArgumentParser(prog="klode-kit records-to-txt", description=main.__doc__.splitlines()[0])
    parser.add_argument("--project", type=Path, required=True, help="data project directory")
    parser.add_argument("--out", type=Path, required=True, help="build directory to write library/ and src/ into")
    parser.add_argument("--kb-id", default=None)
    parser.add_argument("--bindings", type=Path, default=None,
                        help="JSON {rec_id: {rev, audit_rev}}: export exactly this snapshot (default: take it now)")
    parser.add_argument("--deident", choices=sorted(deident_mod.MODES), default=None)
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    try:
        bindings = json.loads(args.bindings.read_text(encoding="utf-8")) if args.bindings is not None else None
        if bindings is not None and not (isinstance(bindings, dict) and all(
                isinstance(b, dict) and isinstance(b.get("rev"), int) and isinstance(b.get("audit_rev"), str)
                for b in bindings.values())):
            raise ValueError("not an object of {rev, audit_rev} entries")
        res = export(args.project, args.out, kb_id=args.kb_id, bindings=bindings, deident=args.deident)
    except (OSError, ValueError) as exc:
        print(f"records-to-txt: cannot read the bindings: {exc}", file=sys.stderr)
        return 2
    except ExportError as exc:
        print(f"records-to-txt: {exc}", file=sys.stderr)
        return 1
    except deident_mod.DeidentError as exc:
        print(f"records-to-txt: de-identification: {exc}", file=sys.stderr)
        return 40
    print(json.dumps({"kb_id": res["kb_id"], "audit_bindings": res["audit_bindings"], "excluded": res["excluded"],
                      "new_items": res["new_items"], "files": sorted(res["files"])}, ensure_ascii=False,
                     sort_keys=True))
    return 0
