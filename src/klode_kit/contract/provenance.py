"""Provenance-chain validator (WI-00b3; design 02 r2.2-A, r3.1, r4.1, r4.4 R3-07).

``validate(root, layout=None)`` reads a product directory and returns every violation of the
three-segment chain plus the version binding. It never writes. Each violation names a code, the
record, the field, the route (``engine``) and an artifact location (``<relpath>[:<line>]``).

====================================  ==========================================================
segment / check                       codes
====================================  ==========================================================
inputs                                ``artifact_missing``, ``malformed_artifact``,
                                      ``artifact_sha_mismatch`` (original / derived bytes)
(1) route span -> candidate           ``span_mismatch`` (line text ``[s:e]`` != candidate, span out
                                      of range, OCR line or candidate absent - cloud routes too),
                                      ``der_parent_mismatch`` (derived parent != manifest
                                      img_sha256, or prov der_sha256 not registered),
                                      ``cloud_bbox_not_null``, ``bbox_outside_line``,
                                      ``run_id_mismatch`` (prov vs ``runs.json``, line vs its dir),
                                      ``pdftext_dpi_mismatch`` (bbox_px != bbox_pt * dpi / 72)
(2) decision -> revision value        ``route_value_mismatch``, ``manual_evidence_missing``,
                                      ``missing_effective_decision``,
                                      ``multiple_effective_decisions``,
                                      ``record_value_mismatch``, ``record_missing``
(3) export transform -> export text   ``export_text_mismatch``, ``export_file_mismatch``,
                                      ``value_audited_mismatch``, ``transform_on_numeric``,
                                      ``alias_not_item``, ``dateshift_not_date``,
                                      ``dateshift_without_mode``, ``unknown_slot``
version binding                       ``rev_binding_mismatch`` (export_map.rev or the bound
                                      audit_rev != ``audit_bindings[rec_id]``)
====================================  ==========================================================

Effective decision of a field at revision ``R``: among its decisions with ``rev_new <= R`` those
with the largest ``rev_new``; a field of a verified revision needs exactly one. Verified revisions
are the record's verified candidates, its effective revision when verified, and every revision a
build binds. Export values come from the bound revision (``audit_bindings``), never the current
one (r3.1 version binding; R3-07).

Text-layer rows (WI-04c): a text-layer page's pdftext rows live in ``text/<key>.jsonl`` without a run_id
(``Layout.text_dirs``). Such a line is traced by ``run_id = "text/<key>"`` + ``der_sha256`` + ``line_idx``; a
cell's pdftext prov carries that key, and ``runs.json`` (which freezes OCR attempts) is not consulted for it.
Only pdftext rows may live there (otherwise ``run_id_mismatch``); the dpi check applies as under
``ocr/pdftext``. Both locations are scanned (transition: fx-chain still keeps pdftext under ``ocr/``).

Transforms (r3.1 segment 3): ``apply`` runs ``{kind, field_id, from, to}`` in order, each requiring
the current value to equal ``from``; ``unapply`` runs them backwards. A transform without
``field_id`` is line-level (layout) and leaves field values alone. ``alias`` may only touch item
fields, ``dateshift`` only date fields and only when ``deident_mode == "dateshift"``; nothing may
touch a value or unit field.

Slots (WI-00b3b): a field id is ``<rec_id>/<slot>``. ``slot_kind`` reads both slot formats - kb-audit slots
``r<row:03d>-<kind>-<k>`` and their row-less ``<kind>-<k>`` (``kind`` in ``number date unit flag drug``), and
fx-chain slots ``<group>[<n>].<role>`` / bare ``date`` (``value`` reads as ``number``). Numeric kinds are
``number`` and ``unit``; item kinds (alias targets) are ``item`` and ``drug`` (``drug-0`` is the item of an rx
row). A field of neither format - in a transform or in an export row's ``field_ids`` - is ``unknown_slot``; it is
never skipped in silence. ``value_audited`` is checked against the line's first ``number`` field.

Pipeline products (WI-00b3b): ``Layout.for_project(project, build_dir)`` lists ``work/<batch>/...``, the audit
revisions ``work/<batch>/audit/a<N>/``, ``records/`` and the build. Real kb-ocr page files carry no ``run_id``
(kb-audit stamps it on read): such a line is traced by the run id its attempt directory names, the kb-ocr formula
``<batch>/<engine>/<fp>/t<N>`` for ``<batch>/ocr/<engine>/<fp>.t<N>/<key>.jsonl``; when the directories name no
valid run id the line is a ``run_id_mismatch``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from klode_kit.contract import ids as ids_mod

CODES: frozenset[str] = frozenset({
    "artifact_missing", "malformed_artifact", "artifact_sha_mismatch",
    "span_mismatch", "der_parent_mismatch", "cloud_bbox_not_null", "bbox_outside_line", "run_id_mismatch",
    "pdftext_dpi_mismatch",
    "route_value_mismatch", "manual_evidence_missing", "missing_effective_decision",
    "multiple_effective_decisions", "record_value_mismatch", "record_missing",
    "export_text_mismatch", "export_file_mismatch", "value_audited_mismatch",
    "transform_on_numeric", "alias_not_item", "dateshift_not_date", "dateshift_without_mode",
    "rev_binding_mismatch", "unknown_slot",
})

CLOUD_ROUTES = frozenset({"claude", "codex"})
TEXT_KEY_PREFIX = "text/"
NUMERIC_KINDS = frozenset({"number", "unit"})   # no transform may touch them
ITEM_KINDS = frozenset({"item", "drug"})       # alias may touch them (drug-0 is the item of an rx row)
_PIPE_SLOT = re.compile(r"^(?:r\d{3,}-)?(number|date|unit|flag|drug)-\d+$")
_CHAIN_SLOT = re.compile(r"^[A-Za-z_]\w*\[\d+\]\.(item|value|unit|flag|date)$")
_CHAIN_KIND = {"value": "number"}
DPI_TOLERANCE_PX = 1.0


class TransformError(ValueError):
    """A transform does not apply: the current value differs from its ``from`` (``to`` when undoing)."""


@dataclass(frozen=True)
class Violation:
    code: str
    rec_id: str | None = None
    field_id: str | None = None
    engine: str | None = None
    location: str = ""
    detail: str = ""


@dataclass
class Layout:
    """Where the chain artifacts live. ``for_products`` is the single-directory layout of fx-chain."""

    root: Path
    manifest: list[Path] = field(default_factory=list)
    derived: list[Path] = field(default_factory=list)
    page_map: list[Path] = field(default_factory=list)
    ocr_dirs: list[Path] = field(default_factory=list)
    audit_dir: Path | None = None
    records_dir: Path | None = None
    build_dir: Path | None = None
    artifact_base: Path | None = None
    text_dirs: list[Path] = field(default_factory=list)
    audit_glob: str = "*/a*/"   # audit revision directories under ``audit_dir``

    @classmethod
    def for_products(cls, root: str | os.PathLike) -> "Layout":
        """``text/`` is scanned when present; ``ocr/`` unless the products are text-layer only (``text/`` and no
        ``ocr/``), so a missing ``ocr/`` is still reported for OCR products."""
        r = Path(root)
        page_map = r / "page_map.jsonl"
        text, ocr = r / "text", r / "ocr"
        return cls(root=r, manifest=[r / "manifest.jsonl"], derived=[r / "derived.jsonl"],
                   page_map=[page_map] if page_map.exists() else [],
                   ocr_dirs=[ocr] if ocr.exists() or not text.is_dir() else [],
                   audit_dir=r / "audit", records_dir=r / "records", build_dir=r / "build", artifact_base=r,
                   text_dirs=[text] if text.is_dir() else [])

    @classmethod
    def for_project(cls, project: str | os.PathLike, build_dir: str | os.PathLike) -> "Layout":
        """The data-project layout (WI-00b3b): ``work/<batch>/{manifest,derived,page_map}.jsonl``,
        ``work/<batch>/ocr``, ``work/<batch>/text``, audit revisions ``work/<batch>/audit/a<N>/``, ``records/`` and the
        build directory ``build_dir``."""
        p = Path(project)
        # a symlink is a date alias of a batch (``work/20261022`` -> ``<batch_id>``, WI-13b), not a second batch
        batches = sorted(d for d in (p / "work").glob("*") if d.is_dir() and not d.is_symlink()) \
            if (p / "work").is_dir() else []

        def files(name: str) -> list[Path]:
            return [d / name for d in batches if (d / name).is_file()]

        def dirs(name: str) -> list[Path]:
            return [d / name for d in batches if (d / name).is_dir()]

        return cls(root=p, manifest=files("manifest.jsonl"), derived=files("derived.jsonl"),
                   page_map=files("page_map.jsonl"), ocr_dirs=dirs("ocr"), audit_dir=p / "work",
                   records_dir=p / "records", build_dir=Path(build_dir), artifact_base=p, text_dirs=dirs("text"),
                   audit_glob="*/audit/a*/")


# --------------------------------------------------------------------------- transforms


def slot_kind(field_id: str) -> str | None:
    """The kind of a field's slot, in the pipeline vocabulary (WI-00b3b): kb-audit slots ``r<row:03d>-<kind>-<k>``
    and their row-less form ``<kind>-<k>`` (``r-x/r005-number-0`` -> ``number``); fx-chain slots
    ``<group>[<n>].<role>`` (``value`` -> ``number``) and a bare ``date``. ``None``: neither format (the caller
    reports ``unknown_slot``, it never guesses)."""
    slot = field_id.split("/", 1)[-1]
    if slot == "date":
        return "date"
    m = _PIPE_SLOT.match(slot)
    if m:
        return m.group(1)
    m = _CHAIN_SLOT.match(slot)
    if m:
        return _CHAIN_KIND.get(m.group(1), m.group(1))
    return None


def _run(transforms: Iterable[Mapping[str, Any]], values: Mapping[str, str], src: str, dst: str) -> dict[str, str]:
    out = dict(values)
    for t in transforms:
        fid = t.get("field_id")
        if fid is None:
            continue
        if fid not in out or out[fid] != t[src]:
            raise TransformError(f"{t.get('kind')} on {fid}: expected {t[src]!r}, found {out.get(fid)!r}")
        out[fid] = t[dst]
    return out


def apply(transforms: Sequence[Mapping[str, Any]], values: Mapping[str, str]) -> dict[str, str]:
    return _run(transforms, values, "from", "to")


def unapply(transforms: Sequence[Mapping[str, Any]], values: Mapping[str, str]) -> dict[str, str]:
    return _run(list(reversed(transforms)), values, "to", "from")


def check_transforms(transforms: Sequence[Mapping[str, Any]], *, deident_mode: str | None,
                     location: str = "transforms") -> list[Violation]:
    found = []
    for i, t in enumerate(transforms):
        fid, kind = t.get("field_id"), t.get("kind")
        where = f"{location}#{i}"
        code = None
        sk = slot_kind(fid) if fid is not None else None
        if fid is not None and sk is None:
            code = "unknown_slot"
        elif sk in NUMERIC_KINDS:
            code = "transform_on_numeric"
        elif kind == "alias" and fid is not None and sk not in ITEM_KINDS:
            code = "alias_not_item"
        elif kind == "dateshift" and fid is not None and sk != "date":
            code = "dateshift_not_date"
        elif kind == "dateshift" and deident_mode != "dateshift":
            code = "dateshift_without_mode"
        if code:
            found.append(Violation(code, _rec_of(fid), fid, None, where, f"{kind} {t.get('from')!r}->{t.get('to')!r}"))
    return found


def _rec_of(field_id: str | None) -> str | None:
    return field_id.split("/", 1)[0] if field_id else None


# --------------------------------------------------------------------------- reading


class _Ctx:
    def __init__(self, layout: Layout):
        self.layout = layout
        self.found: list[Violation] = []

    def rel(self, path: Path, base: Path | None = None) -> str:
        try:
            return path.relative_to(base or self.layout.root).as_posix()
        except ValueError:
            return path.as_posix()

    def add(self, code: str, location: str, *, rec: str | None = None, fid: str | None = None,
            engine: str | None = None, detail: str = "") -> None:
        self.found.append(Violation(code, rec if rec is not None else _rec_of(fid), fid, engine, location, detail))

    def missing(self, path: Path) -> None:
        self.add("artifact_missing", self.rel(path), detail="required artifact not found")

    def jsonl(self, path: Path) -> list[tuple[str, dict]]:
        if not path.is_file():
            self.missing(path)
            return []
        rows = []
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            where = f"{self.rel(path)}:{n}"
            try:
                row = json.loads(line)
            except ValueError as exc:
                self.add("malformed_artifact", where, detail=str(exc))
                continue
            if not isinstance(row, dict):
                self.add("malformed_artifact", where, detail="row is not an object")
                continue
            rows.append((where, row))
        return rows

    def json(self, path: Path) -> dict | None:
        if not path.is_file():
            self.missing(path)
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            self.add("malformed_artifact", self.rel(path), detail=str(exc))
            return None
        if not isinstance(data, dict):
            self.add("malformed_artifact", self.rel(path), detail="not an object")
            return None
        return data

    def front_matter(self, path: Path) -> dict | None:
        text = path.read_text(encoding="utf-8")
        end = text.find("\n---", 4)
        try:
            if not text.startswith("---\n") or end < 0:
                raise ValueError("no front matter block")
            data = json.loads(text[4:end])
            if not isinstance(data, dict):
                raise ValueError("front matter is not an object")
        except ValueError as exc:
            self.add("malformed_artifact", self.rel(path), detail=str(exc))
            return None
        return data

    def guarded(self, where: str, fn, *args) -> None:
        """Run one row check; a row of the wrong shape is a malformed artifact, never an exception."""
        try:
            fn(*args)
        except (KeyError, TypeError, ValueError, AttributeError, IndexError) as exc:
            self.add("malformed_artifact", where, detail=f"{type(exc).__name__}: {exc}")


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------------------- the chain


class _Chain:
    def __init__(self, ctx: _Ctx):
        self.ctx = ctx
        self.lay = ctx.layout
        self.manifest: dict[str, str] = {}          # doc_id -> img_sha256
        self.derived: dict[str, dict] = {}          # der_sha256 -> row
        self.dpi: dict[str, float] = {}             # page_id -> dpi
        self.lines: dict[tuple, dict] = {}          # (run_id, der_sha256, line_idx) -> line
        self.runs: dict[str, dict] = {}             # audit dir rel -> {engine: run_id}
        self.cells: dict[str, dict[str, dict]] = {}  # audit dir rel -> field_id -> cell
        self.decisions: dict[str, list[tuple[str, dict]]] = {}  # field_id -> [(where, decision)]
        self.bodies: dict[tuple[str, int], dict] = {}  # (rec_id, rev) -> body
        self.records: dict[str, dict] = {}          # rec_id -> by_id front matter

    # ---- inputs

    def _artifact(self, rel_path: Any, expect_sha: str) -> None:
        base = self.lay.artifact_base or self.lay.root
        path = base / str(rel_path)
        if not path.is_file():
            self.ctx.missing(path)
        elif _sha_file(path) != expect_sha:
            self.ctx.add("artifact_sha_mismatch", self.ctx.rel(path, base), detail=f"expected {expect_sha}")

    def load_inputs(self) -> None:
        for p in self.lay.manifest:
            for where, row in self.ctx.jsonl(p):
                self.ctx.guarded(where, self._manifest_row, row)
        for p in self.lay.page_map:
            for where, row in self.ctx.jsonl(p):
                self.ctx.guarded(where, self._page_map_row, row)
        rows = [(where, row) for p in self.lay.derived for where, row in self.ctx.jsonl(p)]
        for where, row in rows:
            self.derived[row.get("der_sha256")] = row
        for where, row in rows:
            self.ctx.guarded(where, self._derived_row, where, row)

    def _manifest_row(self, row: dict) -> None:
        self.manifest[row["doc_id"]] = row["img_sha256"]
        if row.get("path") is not None:
            self._artifact(row["path"], row["img_sha256"])

    def _page_map_row(self, row: dict) -> None:
        self.dpi[row["page_id"]] = float(row["dpi"])

    def _derived_row(self, where: str, row: dict) -> None:
        doc = row["page_id"].split("/", 1)[0]
        if self.manifest.get(doc) != row["parent"]:
            self.ctx.add("der_parent_mismatch", where,
                         detail=f"parent {row['parent']} != manifest img_sha256 of {doc} ({self.manifest.get(doc)})")
        if row.get("path") is not None:
            self._artifact(row["path"], row["der_sha256"])

    # ---- OCR lines (segment 1 geometry)

    def load_ocr(self) -> None:
        for root in self.lay.ocr_dirs:
            if not root.is_dir():
                self.ctx.missing(root)
                continue
            for page in sorted(root.glob("*/*/*.jsonl")):
                engine, run_dir = page.parent.parent.name, page.parent.name
                fp, _, attempt = run_dir.rpartition(".")
                derived = _attempt_run_id(root.parent.name, engine, fp, attempt)
                for where, line in self.ctx.jsonl(page):
                    self.ctx.guarded(where, self._ocr_line, where, line, engine, f"/{engine}/{fp}/{attempt}", derived)
        for root in self.lay.text_dirs:
            if not root.is_dir():
                self.ctx.missing(root)
                continue
            for page in sorted(root.glob("*.jsonl")):
                for where, line in self.ctx.jsonl(page):
                    self.ctx.guarded(where, self._text_line, where, line, f"{TEXT_KEY_PREFIX}{page.stem}")

    def _text_line(self, where: str, line: dict, key: str) -> None:
        """A text-layer row of ``text/<key>.jsonl`` (WI-04c): traced by ``key`` instead of a run_id."""
        if line.get("engine") != "pdftext":
            self.ctx.add("run_id_mismatch", where, engine=line.get("engine"),
                         detail=f"{key}.jsonl holds pdftext rows only")
            return
        self.lines[(key, line["der_sha256"], int(line["line_idx"]))] = line
        self._pdftext_dpi(where, line)

    def _ocr_line(self, where: str, line: dict, engine: str, suffix: str, derived: str | None) -> None:
        run_id = line.get("run_id", derived)
        if run_id is None:
            self.ctx.add("run_id_mismatch", where, engine=engine,
                         detail=f"line carries no run_id and its directories name none (…{suffix})")
            return
        if line.get("engine", engine) != engine or not run_id.endswith(suffix):
            self.ctx.add("run_id_mismatch", where, engine=engine, detail=f"line run_id {run_id} not under {suffix}")
        self.lines[(run_id, line["der_sha256"], int(line["line_idx"]))] = line
        if engine == "pdftext":
            self._pdftext_dpi(where, line)

    def _pdftext_dpi(self, where: str, line: dict) -> None:
        dpi = self.dpi.get(line["page_id"])
        if dpi is None:
            self.ctx.add("pdftext_dpi_mismatch", where, engine="pdftext", detail="no page_map dpi for page")
            return
        boxes = [(line.get("bbox_px"), line.get("bbox_pt"))]
        boxes += [(t.get("bbox_px"), t.get("bbox_pt")) for t in line.get("tokens") or []]
        for px, pt in boxes:
            if px is None and pt is None:
                continue
            if px is None or pt is None or len(px) != len(pt) or any(
                    abs(a - b * dpi / 72) > DPI_TOLERANCE_PX for a, b in zip(px, pt)):
                self.ctx.add("pdftext_dpi_mismatch", where, engine="pdftext",
                             detail=f"bbox_px {px} is not bbox_pt {pt} at {dpi:g} dpi")
                return

    # ---- audit (segment 1 spans, segment 2 decisions)

    def load_audit(self) -> None:
        audit = self.lay.audit_dir
        revs = sorted(audit.glob(self.lay.audit_glob)) if audit is not None and audit.is_dir() else []
        # a date alias (``work/<yyyymmdd>`` -> the batch, WI-13b) is the same audit revisions under a second name: count
        # each revision once, through the real directory (a decision seen twice would be "2 effective decisions")
        revs = [r for r in revs if not any(a.is_symlink() for a in r.parents if audit in a.parents)]
        if not revs:
            self.ctx.missing(audit if audit is not None else self.lay.root / "audit")
            return
        for rev_dir in revs:
            key = self.ctx.rel(rev_dir)
            self.runs[key] = self.ctx.json(rev_dir / "runs.json") or {}
            self.cells[key] = {}
            for where, cell in self.ctx.jsonl(rev_dir / "cells.jsonl"):
                self.ctx.guarded(where, self._cell, where, key, cell)
            # decisions are appended when a human decides: an audit revision nobody has clicked yet (a redo after a
            # failed run, WI-09b) has no fixes.jsonl, which is a state, not a missing artifact
            if (rev_dir / "fixes.jsonl").is_file():
                for where, d in self.ctx.jsonl(rev_dir / "fixes.jsonl"):
                    self.ctx.guarded(where, self._decision, where, key, d)

    def _cell(self, where: str, key: str, cell: dict) -> None:
        fid = cell["field_id"]
        self.cells[key][fid] = cell
        cands, prov = cell["candidates"], cell["prov"]
        for engine in sorted(set(cands) | set(prov)):
            if engine not in prov or engine not in cands:
                self.ctx.add("span_mismatch", where, fid=fid, engine=engine, detail="candidate without prov or prov without candidate")
                continue
            self._prov(where, key, fid, engine, prov[engine], cands[engine]["value"])

    def _prov(self, where: str, key: str, fid: str, engine: str, p: dict, value: str) -> None:
        ctx = self.ctx
        if not p["run_id"].startswith(TEXT_KEY_PREFIX) and self.runs[key].get(engine) != p["run_id"]:
            ctx.add("run_id_mismatch", where, fid=fid, engine=engine, detail=f"prov run_id {p['run_id']} != runs.json")
        if p["der_sha256"] not in self.derived:
            ctx.add("der_parent_mismatch", where, fid=fid, engine=engine, detail="prov der_sha256 not in derived.jsonl")
        bbox = p.get("bbox_px")
        if engine in CLOUD_ROUTES and bbox is not None:
            ctx.add("cloud_bbox_not_null", where, fid=fid, engine=engine, detail="cloud routes carry no bbox_px")
        line = self.lines.get((p["run_id"], p["der_sha256"], int(p["line_idx"])))
        if line is None:
            ctx.add("span_mismatch", where, fid=fid, engine=engine, detail=f"no OCR line {p['line_idx']} in {p['run_id']}")
            return
        text, span = line["text"], p["span"]
        ok = (isinstance(span, list) and len(span) == 2 and all(isinstance(v, int) for v in span)
              and 0 <= span[0] <= span[1] <= len(text) and text[span[0]:span[1]] == value)
        if not ok:
            ctx.add("span_mismatch", where, fid=fid, engine=engine, detail=f"line text at {span} != candidate {value!r}")
        outer = line.get("bbox_px")
        if bbox is not None and engine not in CLOUD_ROUTES and (outer is None or not _inside(bbox, outer)):
            ctx.add("bbox_outside_line", where, fid=fid, engine=engine, detail=f"{bbox} not inside line box {outer}")

    def _decision(self, where: str, key: str, d: dict) -> None:
        fid = d["field_id"]
        self.decisions.setdefault(fid, []).append((where, d))
        if d["choice"] == "route":
            route = d["route"]
            cand = self.cells[key].get(fid, {}).get("candidates", {}).get(route)
            if cand is None or cand["value"] != d["value"]:
                self.ctx.add("route_value_mismatch", where, fid=fid, engine=route,
                             detail=f"value {d['value']!r} != candidates[{route}] {cand and cand['value']!r}")
        elif d["choice"] == "manual":
            ev = d.get("evidence") or {}
            region = ev.get("region") if isinstance(ev, dict) else None
            good = (isinstance(region, dict) and region.get("der_sha256") in self.derived
                    and _is_box(region.get("bbox_px")) and isinstance(ev.get("seen"), dict))
            if not good:
                self.ctx.add("manual_evidence_missing", where, fid=fid,
                             detail="manual decision needs evidence.region{der_sha256, bbox_px} and evidence.seen")

    # ---- records (segment 2 values)

    def load_records(self) -> None:
        rec_dir = self.lay.records_dir
        by_id = sorted((rec_dir / "_by_id").glob("*.md")) if rec_dir is not None else []
        if not by_id:
            self.ctx.missing((rec_dir if rec_dir is not None else self.lay.root / "records") / "_by_id")
            return
        for path in by_id:
            meta = self.ctx.front_matter(path)
            if meta is not None:
                self.ctx.guarded(self.ctx.rel(path), self._record, meta)

    def _record(self, meta: dict) -> None:
        self.records[meta["rec_id"]] = meta

    def body(self, rec: str, rev: int, where: str) -> dict | None:
        if (rec, rev) not in self.bodies:
            path = self.lay.records_dir / "_rev" / f"{rec}.r{rev}.md"
            meta = self.ctx.front_matter(path) if path.is_file() else None
            if meta is None and not path.is_file():
                self.ctx.add("record_missing", where, rec=rec, detail=f"revision body {self.ctx.rel(path)} not found")
            self.bodies[(rec, rev)] = meta
        return self.bodies[(rec, rev)]

    def check_records(self, bound: Mapping[str, int]) -> None:
        for rec, meta in sorted(self.records.items()):
            where = self.ctx.rel(self.lay.records_dir / "_by_id" / f"{rec}.md")
            self.ctx.guarded(where, self._record_revs, rec, meta, bound, where)
        for rec in sorted(set(bound) - set(self.records)):
            self.ctx.add("record_missing", self.ctx.rel(self.lay.build_dir / "build_receipt.json"), rec=rec,
                         detail="bound record has no _by_id entry")

    def _record_revs(self, rec: str, meta: dict, bound: Mapping[str, int], where: str) -> None:
        revs = {c["rev"] for c in meta.get("candidates", []) if c.get("status") == "verified"}
        eff = meta.get("effective") or {}
        if eff.get("status") == "verified":
            revs.add(eff["rev"])
        if rec in bound:
            revs.add(bound[rec])
        for rev in sorted(revs):
            body = self.body(rec, rev, where)
            if body is not None:
                self._check_rev(rec, rev, body)

    def _check_rev(self, rec: str, rev: int, body: dict) -> None:
        where = self.ctx.rel(self.lay.records_dir / "_rev" / f"{rec}.r{rev}.md")
        fields = body["fields"]
        mine = {f for f in self.decisions if _rec_of(f) == rec}
        for fid in sorted(set(fields) | mine):
            eff = self.effective(fid, rev)
            if not eff:
                if fid in fields:
                    self.ctx.add("missing_effective_decision", where, fid=fid, detail=f"no decision with rev_new <= {rev}")
                continue
            if len(eff) > 1:
                self.ctx.add("multiple_effective_decisions", eff[-1][0], fid=fid,
                             detail=f"{len(eff)} decisions effective at rev {rev}")
                continue
            d = eff[0][1]
            if fid not in fields:
                if d["choice"] != "none":
                    self.ctx.add("record_value_mismatch", where, fid=fid, detail="decided field absent from the revision")
            elif fields[fid] != d["value"]:
                self.ctx.add("record_value_mismatch", where, fid=fid,
                             detail=f"revision value {fields[fid]!r} != decision value {d['value']!r}")

    def effective(self, fid: str, rev: int) -> list[tuple[str, dict]]:
        applicable = [(w, d) for w, d in self.decisions.get(fid, []) if d["rev_new"] <= rev]
        if not applicable:
            return []
        top = max(d["rev_new"] for _, d in applicable)
        return [(w, d) for w, d in applicable if d["rev_new"] == top]

    # ---- build (segment 3 + version binding)

    def load_bindings(self) -> tuple[dict[str, dict], str | None]:
        build = self.lay.build_dir if self.lay.build_dir is not None else self.lay.root / "build"
        receipt = self.ctx.json(build / "build_receipt.json")
        if receipt is None:
            return {}, None
        bindings = receipt.get("audit_bindings")
        if not isinstance(bindings, dict):
            self.ctx.add("malformed_artifact", self.ctx.rel(build / "build_receipt.json"), detail="no audit_bindings")
            return {}, None
        return bindings, receipt.get("deident_mode")

    def check_bindings(self, bindings: Mapping[str, dict]) -> None:
        where = self.ctx.rel(self.lay.build_dir / "build_receipt.json")
        for rec, b in sorted(bindings.items()):
            meta = self.records.get(rec) or {}
            cand = next((c for c in meta.get("candidates", []) if c.get("rev") == b.get("rev")), None)
            if cand is not None and cand.get("audit_rev") != b.get("audit_rev"):
                self.ctx.add("rev_binding_mismatch", where, rec=rec,
                             detail=f"bound audit_rev {b.get('audit_rev')} != rev {b.get('rev')} audit_rev {cand.get('audit_rev')}")

    def check_export(self, bindings: Mapping[str, dict], deident_mode: str | None) -> None:
        build = self.lay.build_dir
        for where, row in self.ctx.jsonl(build / "src" / "export_map.jsonl"):
            self.ctx.guarded(where, self._export_row, where, row, bindings, deident_mode)

    def _export_row(self, where: str, row: dict, bindings: Mapping[str, dict], deident_mode: str | None) -> None:
        rec, text = row["rec_id"], row["text"]
        b = bindings.get(rec)
        if b is None or row["rev"] != b.get("rev"):
            self.ctx.add("rev_binding_mismatch", where, rec=rec,
                         detail=f"export_map.rev {row['rev']} != audit_bindings rev {b and b.get('rev')}")
        for v in check_transforms(row["transforms"], deident_mode=deident_mode, location=f"{where} transforms"):
            self.ctx.found.append(Violation(v.code, rec, v.field_id, None, v.location, v.detail))
        self._export_file(where, row)
        for fid in row["field_ids"]:
            if slot_kind(fid) is None:
                self.ctx.add("unknown_slot", f"{where} field_ids", fid=fid, detail="slot of neither known format")
        body = self.body(rec, b["rev"], where) if b is not None else None
        if body is None:
            return
        fields = body["fields"]
        ids = row["field_ids"]
        values = {f: fields[f] for f in ids if f in fields}
        try:
            applied = apply(row["transforms"], values)
        except TransformError as exc:
            self.ctx.add("export_text_mismatch", where, rec=rec, detail=str(exc))
            return
        positions = row.get("positions") or {}
        for fid in ids:
            pos = positions.get(fid)
            if fid not in applied or not _is_span(pos, len(text)) or text[pos[0]:pos[1]] != applied[fid]:
                self.ctx.add("export_text_mismatch", where, fid=fid,
                             detail=f"text at {pos} != apply(transforms, {values.get(fid)!r}) = {applied.get(fid)!r}")
        value_fids = [f for f in ids if slot_kind(f) == "number"]
        if value_fids and row["value_audited"] != fields.get(value_fids[0]):
            self.ctx.add("value_audited_mismatch", where, fid=value_fids[0],
                         detail=f"value_audited {row['value_audited']!r} != bound revision value {fields.get(value_fids[0])!r}")

    def _export_file(self, where: str, row: dict) -> None:
        path = self.lay.build_dir / row["source_file"]
        if not path.is_file():
            self.ctx.missing(path)
            return
        lines = path.read_text(encoding="utf-8").splitlines()
        n = row["line_no"]
        if not (1 <= n <= len(lines)) or lines[n - 1] != row["text"]:
            self.ctx.add("export_file_mismatch", where, rec=row["rec_id"],
                         detail=f"{row['source_file']} line {n} differs from export_map text")


def _attempt_run_id(batch: str, engine: str, fp: str, attempt: str) -> str | None:
    """The run id kb-OCR gives attempt ``ocr/<engine>/<fp>.<attempt>`` of batch ``batch`` (``None``: not one)."""
    try:
        return ids_mod.run_id(batch, engine, fp, attempt)
    except ValueError:
        return None


def _is_box(box: Any) -> bool:
    return isinstance(box, list) and len(box) == 4 and all(isinstance(v, (int, float)) for v in box)


def _inside(inner: Any, outer: Any) -> bool:
    return (_is_box(inner) and _is_box(outer) and outer[0] <= inner[0] <= inner[2] <= outer[2]
            and outer[1] <= inner[1] <= inner[3] <= outer[3])


def _is_span(pos: Any, length: int) -> bool:
    return (isinstance(pos, list) and len(pos) == 2 and all(isinstance(v, int) for v in pos)
            and 0 <= pos[0] <= pos[1] <= length)


def _unique(found: Iterable[Violation]) -> list[Violation]:
    seen, out = set(), []
    for v in found:
        key = (v.code, v.rec_id, v.field_id, v.engine, v.location)
        if key not in seen:
            seen.add(key)
            out.append(v)
    return sorted(out, key=lambda v: (v.code, v.rec_id or "", v.field_id or "", v.engine or "", v.location))


def validate(root: str | os.PathLike, layout: Layout | None = None) -> list[Violation]:
    """All provenance violations of the products under ``root`` (``[]`` = the chain holds). Read-only."""
    lay = layout or Layout.for_products(root)
    ctx = _Ctx(lay)
    if not lay.root.is_dir():
        ctx.missing(lay.root)
        return _unique(ctx.found)
    chain = _Chain(ctx)
    chain.load_inputs()
    chain.load_ocr()
    chain.load_audit()
    chain.load_records()
    bindings, deident_mode = chain.load_bindings()
    bound = {rec: b["rev"] for rec, b in bindings.items() if isinstance(b, dict) and isinstance(b.get("rev"), int)}
    if chain.records:
        chain.check_records(bound)
    if bindings:
        chain.check_bindings(bindings)
        chain.check_export(bindings, deident_mode)
    return _unique(ctx.found)
