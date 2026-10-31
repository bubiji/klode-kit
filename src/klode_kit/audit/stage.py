"""``klode-kit kb-audit``: the audit stage (WI-08a; design 02 r2.3, r3.2 ``audit_rev``, r3.3, r4.1; matrix 08a-*).

Usage::

    klode-kit kb-audit --batch <batch_id> [--project <data project>] [--type lab|rx]
    klode-kit kb-audit --writeback [<choices.json>] ...        (WI-08c, audit/writeback.py)
    klode-kit kb-audit --reopen <field_id> [--reason ...] ...  (WI-08e, audit/reopen.py)

It reads the kb-ocr batch receipt ``work/<batch>/receipt.json`` (WI-04b) and, for every page the routes read, aligns the routes' lines (WI-07b), votes every cell (WI-07c), crops the
cells that do not agree (WI-07d) and gates the page's record (``audit.gate``). Layout::

    work/.lock                                  the project lock kb-ocr also takes (fcntl, pid + batch)
    work/<batch>/audit/a<N>/runs.json           {route: run_id | null} - the run set, frozen
    work/<batch>/audit/a<N>/crops/...           WI-07d crops and per-page crop index
    work/<batch>/audit/a<N>/cells.jsonl         one ``cells`` contract line per aligned cell
    work/<batch>/audit/a<N>/candidates.jsonl    one candidate per record: {rec_id, doc_id, page_id, record_idx,
                                                audit_rev, status, verified_by, uncertain, fields}
    work/<batch>/audit/a<N>/confirm.html        written once by WI-08b
    work/<batch>/audit/uncertain_log.jsonl      append-only history of uncertain entries (all revisions)

Rules

- **Text-layer pages** (WI-33d, real-data pilot P8). A PDF page with a text layer makes no OCR route call; its
  ``pdftext`` rows (``text/<key>.jsonl``, exact word boxes, design R2-08) are the page's one voting route. They are
  audited only when the batch has a record type (``--type`` or the receipt's ``record_type``: a lab report or a
  prescription, whose fields are records). Without a type the pages are book / guide text (U4) and are not audited:
  "every page has a text layer; nothing to audit", exit 0, no audit revision. Trust (decision D-35): the gate is
  unchanged - a page is ``verified`` (auto) when it has a cell, every cell agrees and nothing is uncertain, and
  the voting route (pdftext, the PDF's own characters: no recognition error) is present on every cell. The gate asks
  for every voting route, not for two; a rule "at least two routes" would leave a pending record with no field the
  confirm page lists (it lists disagreeing cells only), a state nobody can resolve. What one route cannot see is the
  cross-route checks (swap, shift, extra value); only the row alignment's own doubts (``unaligned``, ``merged_rows``)
  can still send a text-layer record to the human. ``runs.json`` then names the route: ``"pdftext": "text-layer"``.
- **Audit revision** (r3.2): the run set is ``{route: run_id}`` of the receipt's ``attempts`` (``null`` for a
  route that made no attempt, e.g. disabled). It is compared with the latest ``a<N>``: equal -> ``a<N>`` is
  reused (only artifacts a killed run left missing are written; a complete ``a<N>`` is not written at all);
  different -> a new ``a<N+1>``; a revision whose records do not cover the pages now to audit (a text-layer
  page that a record type has just made auditable) is not reused. ``a<N>`` is never rewritten: ``runs.json``, ``cells.jsonl``,
  ``candidates.jsonl`` and ``confirm.html`` go through ``write_once`` (a second write raises
  ``WriteOnceViolation``; the files are made read-only).
- **Candidates only** (r4.1): kb-audit writes candidate records under ``a<N>``; it never writes ``records/`` and
  never moves ``effective`` - that is WI-08c writeback's alone.
- **uncertain_log** (r2.3): ``UncertainLog`` only appends. A new revision appends an ``add`` line for every entry
  that is not already open and a ``clear`` line (``cleared_by: auto``, ``cleared_at``, ``cleared_evidence``)
  for every open entry it no longer raises. Old lines are never touched.
- Exit codes (r2.6): 0 every candidate verified; 10 some candidate awaits confirmation (pending_confirm or
  disputed); 2 usage (bad batch id, no kb-ocr receipt); 30 the batch's inputs do not match (receipt of another
  batch, run file missing, derived image changed); 40 the project lock is held.

Crash points: ``audit.lock``, ``audit.mkdir``, ``audit.written`` (after each write-once file),
``audit.log_appended``.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, TextIO

from klode_kit.audit import align as A
from klode_kit.audit import compare as C
from klode_kit.audit import crop as CR
from klode_kit.audit import gate as G
from klode_kit.contract import ids, schema
from klode_kit.seams import crashpoint

RECORD_TYPES = ("lab", "rx")
TEXT_RUN = "text-layer"  # runs.json value of the pdftext route: its rows have no run of their own (``text/<key>``)
WRITE_ONCE = ("runs.json", "cells.jsonl", "candidates.jsonl", "confirm.html")
LOG_NAME = "uncertain_log.jsonl"
LOCK_NAME = ".lock"
BATCH_RECEIPT = "klode-kit.ocr_batch/1"
USAGE_EXIT, CONTRACT_EXIT, ENV_EXIT, AWAITING_EXIT = 2, 30, 40, 10
CRASHPOINTS = ("audit.lock", "audit.mkdir", "audit.written", "audit.log_appended")

_BATCH = re.compile(r"[0-9a-f]{12}")
_REV_DIR = re.compile(r"a([1-9][0-9]*)")


class WriteOnceViolation(Exception):
    """A write-once artifact of an audit revision already exists."""


class AuditError(Exception):
    def __init__(self, message: str, exit_code: int = CONTRACT_EXIT):
        super().__init__(message)
        self.exit_code = exit_code


class LockBusy(Exception):
    pass


# --------------------------------------------------------------------------- files


def _fsync_dir(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _ensure_dir(directory: Path) -> None:
    missing = []
    d = directory
    while not d.exists():
        missing.append(d)
        d = d.parent
    for d in reversed(missing):
        try:
            os.mkdir(d)
        except FileExistsError:
            continue
        _fsync_dir(d.parent)
        crashpoint("audit.mkdir")


def audit_dir(project: str | os.PathLike, batch_id: str, n: int) -> Path:
    """``<project>/work/<batch>/audit/a<n>`` (checks the batch id and revision number)."""
    ids.audit_rev(batch_id, n)
    return Path(project) / "work" / batch_id / "audit" / f"a{n}"


def write_once(directory: str | os.PathLike, name: str, data: bytes) -> Path:
    """Write ``directory/name`` once (temporary file + hard link, which fails if the name exists); the file is
    made read-only. A second write raises ``WriteOnceViolation`` and changes nothing."""
    if name not in WRITE_ONCE:
        raise ValueError(f"{name!r} is not a write-once audit artifact ({', '.join(WRITE_ONCE)})")
    directory = Path(directory)
    target = directory / name
    if target.exists():
        raise WriteOnceViolation(f"{target} already exists: an audit revision is written once")
    tmp = directory / f".{name}.{secrets.token_hex(4)}.tmp"
    with open(tmp, "xb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    try:
        os.chmod(tmp, 0o444)
        os.link(tmp, target)
    except FileExistsError:
        raise WriteOnceViolation(f"{target} already exists: an audit revision is written once") from None
    finally:
        os.remove(tmp)
    _fsync_dir(directory)
    crashpoint("audit.written")
    return target


def _jsonl(rows: Iterable[Mapping]) -> bytes:
    return b"".join((json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8") for r in rows)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- uncertain log


class UncertainLog:
    """Append-only history of uncertain entries (r2.3). Lines: ``{op: "add", audit_rev, at, **entry}`` and
    ``{op: "clear", id, audit_rev, cleared_by, cleared_at, cleared_evidence}``."""

    def __init__(self, path: str | os.PathLike):
        self.path = Path(path)

    def lines(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(x) for x in self.path.read_text(encoding="utf-8").splitlines() if x.strip()]

    def open_entries(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for line in self.lines():
            if line.get("op") == "clear":
                out.pop(line["id"], None)
            elif line["id"] not in out:
                out[line["id"]] = {k: v for k, v in line.items() if k not in ("op", "audit_rev", "at")}
        return out

    def _append(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        _ensure_dir(self.path.parent)
        with open(self.path, "ab") as fh:
            fh.write(_jsonl(rows))
            fh.flush()
            os.fsync(fh.fileno())
        crashpoint("audit.log_appended")
        return len(rows)

    def add(self, entries: Iterable[Mapping], *, audit_rev: str, at: str) -> int:
        """Append the entries that are not open yet (an open entry raised again writes nothing)."""
        open_ids = set(self.open_entries())
        rows = []
        for e in entries:
            if not e.get("id"):
                raise ValueError(f"uncertain entry without id: {dict(e)!r}")
            if e["id"] in open_ids:
                continue
            open_ids.add(e["id"])
            rows.append({**dict(e), "op": "add", "audit_rev": audit_rev, "at": at})
        return self._append(rows)

    def clear(self, ids_: Iterable[str], *, by: str, at: str, evidence: str, audit_rev: str | None = None) -> int:
        """Append one clear line per id; every id must be open (else ``ValueError``, nothing written)."""
        if by not in ("auto", "human"):
            raise ValueError(f"cleared_by must be auto or human: {by!r}")
        if not evidence:
            raise ValueError("clearing an uncertain entry needs evidence")
        wanted = list(dict.fromkeys(ids_))
        open_ids = self.open_entries()
        unknown = [i for i in wanted if i not in open_ids]
        if unknown:
            raise ValueError(f"not open in {self.path.name}: {unknown}")
        rows = [{"op": "clear", "id": i, "audit_rev": audit_rev, "cleared_by": by, "cleared_at": at,
                 "cleared_evidence": evidence} for i in wanted]
        return self._append(rows)

    def sync(self, current: Iterable[Mapping], *, audit_rev: str, at: str) -> None:
        """Make the open set equal ``current``: add the new entries, auto-clear the ones no longer raised."""
        current = list(current)
        now = {e["id"] for e in current}
        self.add(current, audit_rev=audit_rev, at=at)
        gone = [i for i in self.open_entries() if i not in now]
        if gone:
            self.clear(gone, by="auto", at=at, audit_rev=audit_rev,
                       evidence=f"{audit_rev}: not raised again by this audit revision's runs")


# --------------------------------------------------------------------------- lock


def _lock(work: Path, batch: str) -> int:
    path = work / LOCK_NAME
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        holder = os.pread(fd, 128, 0).decode("utf-8", "replace").split()
        os.close(fd)
        who = f"pid {holder[0]}, batch {holder[1]}" if len(holder) >= 2 else "unknown holder"
        raise LockBusy(f"another kb-ocr / kb-audit holds {path} ({who})") from None
    stamp = f"{os.getpid()} {batch}".ljust(63)[:63] + "\n"
    os.pwrite(fd, stamp.encode("utf-8"), 0)
    crashpoint("audit.lock")
    return fd


# --------------------------------------------------------------------------- the stage


def _covers(adir: Path, page_ids: Iterable[str]) -> bool:
    """False when the revision's records (if it has them yet) are not exactly the pages to audit."""
    path = adir / "candidates.jsonl"
    if not path.exists():
        return True
    done = {json.loads(x)["page_id"] for x in path.read_text(encoding="utf-8").splitlines() if x.strip()}
    return done == set(page_ids)


def _claim(root: Path, runs: Mapping[str, str | None], page_ids: Iterable[str] | None = None) -> tuple[int, bool]:
    """``(n, new)``: the latest revision when its run set is ``runs`` and it covers ``page_ids`` (or it was claimed
    by a killed run before its runs.json was written), else a new ``a<N+1>``."""
    found = sorted(int(m.group(1)) for p in root.iterdir() if p.is_dir() and (m := _REV_DIR.fullmatch(p.name))) \
        if root.is_dir() else []
    if found:
        last = found[-1]
        rp = root / f"a{last}" / "runs.json"
        if not rp.exists() or (json.loads(rp.read_text(encoding="utf-8")) == dict(runs)
                               and (page_ids is None or _covers(root / f"a{last}", page_ids))):
            return last, False
    n = (found[-1] + 1) if found else 1
    _ensure_dir(root)
    os.mkdir(root / f"a{n}")
    _fsync_dir(root)
    crashpoint("audit.mkdir")
    return n, True


def _rows(batch_dir: Path, page: Mapping[str, Any]) -> dict[str, list[dict]]:
    """``{engine: rows}`` of the page's done routes, each row stamped with its route's ``run_id``."""
    out: dict[str, list[dict]] = {}
    for route, res in page["routes"].items():
        if res.get("status") != "done":
            continue
        path = batch_dir / str(res.get("file", ""))
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise AuditError(f"{page['page_id']}: run file of route {route} is missing: {exc}") from exc
        for line in text.splitlines():
            if line.strip():
                row = json.loads(line)
                row["run_id"] = res.get("run_id")
                out.setdefault(row["engine"], []).append(row)
    return out


def _text_rows(batch_dir: Path, page: Mapping[str, Any]) -> dict[str, list[dict]]:
    """``{"pdftext": rows}`` of a text-layer page: the rows of ``text/<key>.jsonl``, traced by ``run_id`` =
    ``text/<key>`` (contract: a text-layer line has no run of its own)."""
    path = batch_dir / str(page.get("text_file") or f"text/{page.get('key')}.jsonl")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AuditError(f"{page['page_id']}: text-layer file is missing: {exc}") from exc
    rows = []
    for line in text.splitlines():
        if line.strip():
            row = json.loads(line)
            row["run_id"] = f"text/{path.stem}"
            rows.append(row)
    return {"pdftext": rows}


def _cell_line(rec_id: str, vote: C.CellVote, cell: A.Cell) -> dict:
    return {"field_id": f"{rec_id}/{G.slot(vote.row, vote.kind, vote.k)}", "rec_id": rec_id,
            "slot": G.slot(vote.row, vote.kind, vote.k),
            "cell": {"row": vote.row, "col": vote.col or f"{vote.kind}-{vote.k}"},
            "candidates": {e: {"value": c.value} for e, c in cell.candidates},
            "prov": {e: {"run_id": c.run_id, "line_idx": c.line_idx, "span": list(c.span),
                         "bbox_px": list(c.bbox_px) if c.bbox_px is not None else None,
                         "der_sha256": c.der_sha256} for e, c in cell.candidates}}


def _audit_page(project: Path, batch_id: str, n: int, batch_dir: Path, page: Mapping[str, Any],
                routes: list[str], write_crops: bool) -> tuple[list[dict], dict]:
    if page.get("text_class") == "text":
        rows, routes = _text_rows(batch_dir, page), ["pdftext"]  # WI-33d: the text layer is the page's one route
    else:
        rows = _rows(batch_dir, page)
    al = A.align(rows) if rows else A.Alignment(None, (), {}, (), ())
    cmp = C.compare(al, routes=routes)
    crops: dict[tuple[int, str, int], dict] = {}
    if write_crops and any(v.verdict != C.AGREE for v in cmp.cells):
        try:
            records = CR.write_crops(project, batch_id, n, page_id=page["page_id"], alignment=al, comparison=cmp)
        except CR.CropError as exc:
            raise AuditError(f"{page['page_id']}: {exc}") from exc
        crops = {(r["cell"][0], r["cell"][1], r["cell"][2]): r for r in records}
    rec_id = ids.rec_id({"doc_id": page["doc_id"], "page_id": page["page_id"], "record_idx": 0})
    verdict = G.judge(cmp, rec_id=rec_id, page_id=page["page_id"], page_uncertain=G.page_entries(page),
                      cell_codes={k: r["uncertain"] for k, r in crops.items()})
    cells = {(c.row, c.kind, c.k): c for c in al.cells}
    lines = []
    fields = []
    for vote, f in zip(cmp.cells, verdict.fields):
        key = (vote.row, vote.kind, vote.k)
        line = _cell_line(rec_id, vote, cells[key])
        problems = schema.issues("cells", line)
        if problems:
            raise AuditError(f"{line['field_id']}: cell violates the cells contract: {problems[0].message}")
        lines.append(line)
        fields.append({**f, "item_label": vote.item_label,
                       "votes": [[value, list(engines)] for value, engines in vote.votes],
                       "crop": crops[key]["path"] if key in crops else None})
    candidate = {"rec_id": rec_id, "doc_id": page["doc_id"], "page_id": page["page_id"], "record_idx": 0,
                 "audit_rev": ids.audit_rev(batch_id, n), "status": verdict.status,
                 "verified_by": verdict.verified_by, "uncertain": list(verdict.uncertain), "fields": fields}
    return lines, candidate


def run(project: Path, batch_id: str, *, out: TextIO | None = None, record_type: str | None = None) -> int:
    out = sys.stderr if out is None else out
    work = project / "work"
    batch_dir = work / batch_id
    receipt_path = batch_dir / "receipt.json"
    if not receipt_path.is_file():
        print(f"kb-audit: no kb-ocr receipt for batch {batch_id} ({receipt_path}); run kb-ocr first", file=out)
        return USAGE_EXIT
    try:
        lock = _lock(work, batch_id)
    except LockBusy as exc:
        print(f"kb-audit: {exc}", file=out)
        return ENV_EXIT
    try:
        rec = json.loads(receipt_path.read_text(encoding="utf-8"))
        if rec.get("schema") != BATCH_RECEIPT or rec.get("batch_id") != batch_id:
            raise AuditError(f"{receipt_path} is not the kb-ocr receipt of batch {batch_id}")
        routes = list(rec["routes"])
        typed = record_type or rec.get("record_type")  # WI-33d: a lab / rx batch audits its text-layer pages too
        pages = [p for p in rec["pages"] if typed or p.get("text_class") != "text"]
        if not pages:
            print(f"kb-audit: batch {batch_id}: every page has a text layer; nothing to audit", file=out)
            return 0
        runs = {r: (rec.get("attempts", {}).get(r) or {}).get("run_id") for r in routes}
        if any(p.get("text_class") == "text" for p in pages):
            runs["pdftext"] = TEXT_RUN  # a route of this revision (the writeback and the confirm page read it here)
        root = batch_dir / "audit"
        n, new = _claim(root, runs, [p["page_id"] for p in pages])
        adir = root / f"a{n}"
        audit_rev = ids.audit_rev(batch_id, n)
        if not (adir / "runs.json").exists():
            write_once(adir, "runs.json", (json.dumps(runs, sort_keys=True, indent=1) + "\n").encode("utf-8"))
        complete = (adir / "cells.jsonl").exists() and (adir / "candidates.jsonl").exists()
        if complete:
            candidates = [json.loads(x) for x in (adir / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
        else:
            all_lines, candidates = [], []
            for page in pages:
                lines, cand = _audit_page(project, batch_id, n, batch_dir, page, routes, write_crops=True)
                all_lines += lines
                candidates.append(cand)
            if not (adir / "cells.jsonl").exists():
                write_once(adir, "cells.jsonl", _jsonl(all_lines))
            write_once(adir, "candidates.jsonl", _jsonl(candidates))
        UncertainLog(root / LOG_NAME).sync([u for c in candidates for u in c["uncertain"]], audit_rev=audit_rev,
                                           at=_now())
    except AuditError as exc:
        print(f"kb-audit: {exc}", file=out)
        return exc.exit_code
    finally:
        os.close(lock)
    for c in candidates:
        print(f"kb-audit: {c['page_id']}: {c['status']} ({len(c['uncertain'])} uncertain)", file=out)
    waiting = sum(1 for c in candidates if c["status"] != G.VERIFIED)
    print(f"kb-audit: batch {batch_id}: audit revision a{n} ({'new' if new else 'reused'}), "
          f"{len(candidates)} record(s), {waiting} awaiting confirmation", file=out)
    return AWAITING_EXIT if waiting else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="klode-kit kb-audit",
                                     description="Align, vote and gate a kb-ocr batch into audit revision a<N>; "
                                                 "with --writeback, commit its candidates or the human choices "
                                                 "(WI-08c).")
    parser.add_argument("--batch", default=None, help="batch id (12 hex characters, the work/<batch> directory)")
    parser.add_argument("--project", type=Path, default=None, help="data project directory (default: cwd)")
    parser.add_argument("--type", dest="record_type", choices=RECORD_TYPES, default=None,
                        help="document type of the batch (lab / rx): its text-layer pages are audited as records "
                             "(WI-33d); default: the type kb-ocr recorded, else text-layer pages are not audited")
    parser.add_argument("--writeback", nargs="?", const="", default=None, metavar="CHOICES",
                        help="writeback (audit.writeback): without CHOICES ingest the newest audit revision's "
                             "candidates of --batch; with a choices.json commit the human choices")
    parser.add_argument("--reopen", default=None, metavar="FIELD_ID",
                        help="reopen (audit.reopen, WI-08e): put an already verified field back on a one-field confirm "
                             "page; --batch (optional) is checked against the record's batch")
    parser.add_argument("--reason", default=None, help="with --reopen: the human's note on the reopen event")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return USAGE_EXIT if exc.code else 0
    project = Path.cwd() if args.project is None else args.project
    if args.reopen is not None:
        if args.writeback is not None:
            print("kb-audit: --reopen and --writeback are two steps: reopen first, then write back the choices",
                  file=sys.stderr)
            return USAGE_EXIT
        from klode_kit.audit import reopen

        return reopen.run(project, args.reopen, batch_id=args.batch, reason=args.reason)
    if args.reason is not None:
        print("kb-audit: --reason belongs to --reopen", file=sys.stderr)
        return USAGE_EXIT
    if args.writeback is not None:
        from klode_kit.audit import writeback

        return writeback.run(project, args.batch, Path(args.writeback) if args.writeback else None)
    if args.batch is None:
        print("kb-audit: --batch is required (or --writeback <choices.json>)", file=sys.stderr)
        return USAGE_EXIT
    if not _BATCH.fullmatch(args.batch):
        print(f"kb-audit: --batch must be a batch id (12 hex characters): {args.batch!r}", file=sys.stderr)
        return USAGE_EXIT
    return run(project, args.batch, record_type=args.record_type)


if __name__ == "__main__":  # pragma: no cover - entry is ``klode-kit kb-audit``
    sys.exit(main())
