"""WI-08c: writeback, the sole committer of record revisions (design 02 r2.3, r3.1 segment 2, r3.2, r4.1, 1st-round
4.2 remainder; matrix 08c-*; K2, K3).

Usage::

    klode-kit kb-audit --batch <batch_id> --writeback [--project <data project>]          # ingest
    klode-kit kb-audit --writeback <choices.json> [--batch <batch_id>] [--project ...]      # human choices

**Two layers** (r4.1). ``records/_by_id/<rec_id>.md`` holds JSON front matter (contract ``record``)::

    {rec_id, doc_id, page_id, record_idx, type, date, status, effective?: {rev, audit_rev, status, verified_by},
     candidates: [{rev, audit_rev, status, verified_by, uncertain, created, superseded_by?}]}

``records/_rev/<rec_id>.r<N>.md`` is the body of revision N, written once and made read-only: ``{rec_id, rev,
audit_rev, commit_id, status, verified_by, type, date, fields: {field_id: value}, dropped: [...], open: [...],
uncertain: [...]}`` (``uncertain``: the candidate's entries as the id list the views show, ``views.uncertain_ids``;
``type`` / ``date``: the batch receipt's, else the record's own, else derived from the page, ``record_meta``).
``fields`` holds values only for a verified revision (every field but the dropped ones); a revision that is not
verified claims no value and lists the fields still ``open``. Top-level ``status`` is ``effective.status``, or the
newest candidate's status while there is no effective revision (R4-01).

**Ingest** (no choices): every candidate of the batch's newest audit revision ``a<N>`` (WI-08a ``candidates.jsonl``)
becomes candidate revision ``r<current+1>`` of its record unless the record already has a candidate of ``a<N>``
(idempotent) or of a newer audit revision (an old revision never overrides a newer one). A verified(auto)
candidate is written with one ``auto`` decision per field (route = first voting route, r3.1 segment 2).

**Choices** (``klode-kit.choices/1``, WI-08b export). Whole file refused with exit 30 and nothing written when the
file is not legal choices (manual blank is first turned into ``none``, r2.3; a repeated field_id is left to the
record check, F17), names another batch, or ``audit_rev`` / ``fingerprints`` (= the newest ``runs.json``) /
``confirm_sha256`` (= ``confirm_page.confirm_sha256`` of ``a<N>/confirm.html``) do not match. Then per record, all
checks before any write (record-atomic): the record is a candidate of ``a<N>`` without a page-level uncertain
entry (r3.3: blocked); its items cover every field to confirm exactly once (no missing, F14; no duplicate, F17; no
other field); each item's ``cell`` and ``seen`` equal ``cells.jsonl``, ``rev_base`` equals the record's current
rev; a ``route`` choice names a voting route holding a value (tesseract never votes; a route without a value is
refused); ``evidence.reason`` is only ``not_a_field`` and only with ``none``; a ``manual`` value gets
``evidence.region`` (the cell's box on a registered derived image, else its crop box). Any failure rejects that
record (listed with its field_ids; exit 30) and the others go on. A record whose every item matches a committed
decision (idempotency key ``(field_id, rev_base, choice)``) is ``unchanged``. Otherwise one commit writes revision
``r<current+1>``: ``verified`` (``verified_by: human``) when no field is left as plain ``none`` (``none`` +
``not_a_field`` drops the field), else ``disputed`` keeping the undecided fields' uncertain entries.

**Promotion** (r4.1): only a verified candidate moves ``effective``; it then equals that candidate on ``{rev,
audit_rev, status, verified_by}`` and every earlier candidate gets ``superseded_by``. A pending or disputed
candidate never touches ``effective``.

**One commit** (one record) under the project lock ``work/.lock`` (shared with kb-ocr / kb-audit):
``a<N>/commits.jsonl`` ``begin`` line holding the whole planned commit -> decisions appended to ``a<N>/fixes.jsonl``
(contract ``fixes``, one ``commit_id``) -> body (temporary file, rename, read-only) -> front matter (temporary file,
replace) -> ``end`` line. Every run first completes each ``begin`` without ``end`` from its stored plan (roll
forward), so a crash leaves a commit all done or not begun. ``fixes.jsonl`` and ``commits.jsonl`` are only ever
appended. The run's receipt ``{schema, batch_id, audit_rev, mode, choices_sha256, exit_code, per_record,
recovered, at}`` is printed on stdout and, when the run wrote anything, appended to ``a<N>/writeback_receipts.jsonl``.

Exit codes: 0 every record in scope verified; 10 some record awaits confirmation; 2 usage (no batch, bad batch id,
batch not audited, choices file unreadable); 30 refused (see above); 40 the project lock is held.

Crash points (one durable write between two of them; matrix 00s-16 / 08c-13): ``writeback.lock``,
``writeback.begin``, ``writeback.fixes_appended``, ``writeback.mkdir``, ``writeback.rev_tmp``,
``writeback.rev_renamed``, ``writeback.rev_written``, ``writeback.byid_tmp``, ``writeback.byid_written``,
``writeback.committed``, ``writeback.receipt``.
"""

from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, TextIO

from klode_kit.audit import confirm_page as CP
from klode_kit.audit import gate as G
from klode_kit.audit import record_meta as RM
from klode_kit.audit import views as V
from klode_kit.contract import ids, schema
from klode_kit.seams import crashpoint

OK_EXIT, AWAITING_EXIT, USAGE_EXIT, CONTRACT_EXIT, ENV_EXIT = 0, 10, 2, 30, 40
RECEIPT_SCHEMA = "klode-kit.writeback_receipt/1"
CHOICES_SCHEMA = "klode-kit.choices/1"
FIXES, COMMITS, RECEIPTS, LOCK = "fixes.jsonl", "commits.jsonl", "writeback_receipts.jsonl", ".lock"
NON_VOTING = frozenset({"tesseract"})
DEFAULT_TYPE = "doc"
CRASHPOINTS = ("writeback.lock", "writeback.begin", "writeback.fixes_appended", "writeback.mkdir",
               "writeback.rev_tmp", "writeback.rev_renamed", "writeback.rev_written", "writeback.byid_tmp",
               "writeback.byid_written", "writeback.committed", "writeback.receipt")

_BATCH = re.compile(r"[0-9a-f]{12}")
_REV_DIR = re.compile(r"a([1-9][0-9]*)")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
# blank = only white space as str.isspace() defines it (the confirm page's exporter uses the same set)
_BLANK = re.compile(r"[\t\n\x0b\x0c\r\x1c-\x1f \x85\xa0  -     　]*")


class WritebackError(Exception):
    def __init__(self, message: str, exit_code: int = CONTRACT_EXIT):
        super().__init__(message)
        self.exit_code = exit_code


class LockBusy(Exception):
    pass


# --------------------------------------------------------------------------- small file helpers


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _dumps(rows: Iterable[Mapping]) -> bytes:
    return b"".join((json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8") for r in rows)


def _md(front: Mapping) -> bytes:
    return ("---\n" + json.dumps(front, ensure_ascii=False, indent=1, sort_keys=True) + "\n---\n").encode("utf-8")


def _front(path: Path) -> dict | None:
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    end = text.find("\n---", 4)
    if not text.startswith("---\n") or end < 0:
        raise WritebackError(f"{path}: no front matter block")
    data = json.loads(text[4:end])
    if not isinstance(data, dict):
        raise WritebackError(f"{path}: front matter is not an object")
    return data


def _fsync_dir(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_fd(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]
    os.fsync(fd)


def _append(path: Path, rows: list[Mapping], point: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        _write_fd(fd, _dumps(rows))
    finally:
        os.close(fd)
    crashpoint(point)


def _tmp_write(path: Path, data: bytes, mode: int, point: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        _write_fd(fd, data)
    finally:
        os.close(fd)
    crashpoint(point)


# --------------------------------------------------------------------------- the audit revision


@dataclass
class Revision:
    project: Path
    batch_id: str
    n: int
    dir: Path
    runs: dict[str, str | None]
    cells: dict[str, dict]
    cands: dict[str, dict]
    pages: dict[str, dict] = field(default_factory=dict)
    meta: dict[str, dict] = field(default_factory=dict)

    @property
    def audit_rev(self) -> str:
        return ids.audit_rev(self.batch_id, self.n)

    @property
    def voting(self) -> list[str]:
        return [e for e in self.runs if e not in NON_VOTING]

    def seen(self, fid: str) -> dict[str, str | None]:
        cands = self.cells[fid].get("candidates", {})
        return {e: (cands.get(e) or {}).get("value") for e in self.runs}


def _revisions(bdir: Path) -> list[int]:
    root = bdir / "audit"
    if not root.is_dir():
        return []
    return sorted(int(m.group(1)) for p in root.iterdir() if p.is_dir() and (m := _REV_DIR.fullmatch(p.name)))


def _load(project: Path, batch_id: str, n: int) -> Revision:
    adir = project / "work" / batch_id / "audit" / f"a{n}"
    need = [adir / x for x in ("runs.json", "cells.jsonl", "candidates.jsonl")]
    if not all(p.is_file() for p in need):
        raise WritebackError(f"{adir} is incomplete (runs.json / cells.jsonl / candidates.jsonl); rerun kb-audit",
                             USAGE_EXIT)
    runs = json.loads(need[0].read_text(encoding="utf-8"))
    cells = {c["field_id"]: c for c in _jsonl(need[1])}
    cands = {c["rec_id"]: c for c in _jsonl(need[2])}
    receipt = project / "work" / batch_id / "receipt.json"
    pages = {}
    if receipt.is_file():
        pages = {p.get("page_id"): p for p in json.loads(receipt.read_text(encoding="utf-8")).get("pages", [])}
    return Revision(project, batch_id, n, adir, runs, cells, cands, pages)


def _shown(f: Mapping[str, Any]) -> bool:
    return f.get("verdict") != G.AGREE or bool(f.get("uncertain")) or bool(f.get("missing"))


def _blocked(cand: Mapping[str, Any]) -> list[dict]:
    return [u for u in cand.get("uncertain", []) if u.get("scope") == G.PAGE]


# --------------------------------------------------------------------------- records


def _record_path(project: Path, rec_id: str) -> Path:
    return project / "records" / "_by_id" / f"{rec_id}.md"


def _body_path(project: Path, rec_id: str, rev: int) -> Path:
    return project / "records" / "_rev" / f"{rec_id}.r{rev}.md"


def _current(front: Mapping | None) -> int:
    return max((c["rev"] for c in (front or {}).get("candidates", [])), default=0)


def _audit_no(audit_rev: str) -> int:
    return int(audit_rev.rsplit("/a", 1)[1])


def _choice_key(fid: str, rev_base: int, choice: Mapping[str, Any]) -> str:
    return ids.idempotency_key(fid, rev_base, {k: v for k, v in choice.items() if v is not None})


def _decision_choice(d: Mapping[str, Any]) -> dict:
    out: dict[str, Any] = {"kind": d["choice"]}
    if d["choice"] == "route":
        out["route"] = d["route"]
    elif d["choice"] == "manual":
        out["value"] = d["value"]
    if (d.get("evidence") or {}).get("reason"):
        out["reason"] = d["evidence"]["reason"]
    return out


def _committed_keys(adir: Path) -> set[str]:
    ended = {m["commit_id"] for m in _jsonl(adir / COMMITS) if m.get("op") == "end"}
    return {_choice_key(d["field_id"], d["rev_base"], _decision_choice(d)) for d in _jsonl(adir / FIXES)
            if d.get("by") == "human" and d.get("commit_id") in ended}


# --------------------------------------------------------------------------- planning one commit


def _first_voting(rev: Revision, fid: str) -> str:
    """The route an auto decision names: the first voting route (``gate.ENGINES`` order) holding the agreed value
    (an agreed field has a value on every voting route)."""
    seen = rev.seen(fid)
    return next(e for e in G.ENGINES if e in rev.voting and seen.get(e) is not None)


def _region(rev: Revision, cand: Mapping[str, Any], fid: str) -> dict | None:
    prov = rev.cells[fid].get("prov", {})
    for e in G.ENGINES:
        p = prov.get(e) or {}
        if p.get("bbox_px") and p.get("der_sha256"):
            return {"der_sha256": p["der_sha256"], "bbox_px": list(p["bbox_px"])}
    # no geometric route read the cell (a cloud-only page): the box of its WI-07d crop (row band or half page)
    crop = next(f.get("crop") for f in cand.get("fields", []) if f["field_id"] == fid)
    index = {r.get("path"): r for r in _jsonl(rev.dir / "crops" / f"{Path(str(crop)).name.rsplit('-r', 1)[0]}.jsonl")}
    row = index.get(crop)
    return {"der_sha256": row["source_der_sha256"], "bbox_px": list(row["box_px"])} if row else None


def _page_meta(rev: Revision, cand: Mapping[str, Any], front: Mapping | None) -> tuple[str, str | None]:
    """``(type, date)``: the batch receipt's ``record_type`` / ``date`` when it has them, else what the record
    already has (a redo never renames its views), else derived from the page (``record_meta``, WI-08d)."""
    page = rev.pages.get(cand["page_id"], {})
    if page and cand["page_id"] not in rev.meta:
        rev.meta[cand["page_id"]] = RM.derive(rev.project / "work" / rev.batch_id, page, rev.voting)
    derived = rev.meta.get(cand["page_id"], {})
    known = (front or {}).get("type")
    rtype = page.get("record_type") or (known if known != DEFAULT_TYPE else None) or derived.get("record_type")
    date = page.get("date") if isinstance(page.get("date"), str) and _DATE.fullmatch(page["date"]) else None
    return str(rtype or DEFAULT_TYPE), date or (front or {}).get("date") or derived.get("date")


def _plan(rev: Revision, cand: Mapping[str, Any], front: Mapping | None, *, status: str, verified_by: str | None,
          uncertain: list[dict], human: Mapping[str, dict], dropped: list[str], at: str,
          confirm_sha: str | None = None) -> dict:
    """The complete commit of one record: decisions, body and front matter (``human``: field_id -> normalised
    choice; fields not in it are agreed and get an auto decision when the revision is verified)."""
    base = _current(front)
    rev_new = base + 1
    marks = _jsonl(rev.dir / COMMITS)
    commit_id = ids.commit_id(rev.audit_rev, 1 + sum(1 for m in marks if m.get("op") == "begin"))
    seq = len(_jsonl(rev.dir / FIXES))
    decisions, values, open_ = [], {}, []
    for f in cand.get("fields", []):
        fid = f["field_id"]
        seen = rev.seen(fid)
        evidence: dict[str, Any] = {"seen": seen}
        if fid in human:
            ch = human[fid]
            evidence["confirm_sha256"] = confirm_sha
            route, value, kind = None, None, ch["kind"]
            if kind == "route":
                route, value = ch["route"], seen[ch["route"]]
            elif kind == "manual":
                value = ch["value"]
                evidence["region"] = ch["region"]
            elif ch.get("reason"):
                evidence["reason"] = ch["reason"]
            by = "human"
        elif status == G.VERIFIED:
            kind, route = "route", _first_voting(rev, fid)
            value, by = seen[route], "auto"
        else:
            open_.append(fid)
            continue
        if kind == "none" and fid not in dropped:
            open_.append(fid)
        if value is not None and status == G.VERIFIED:
            values[fid] = value
        seq += 1
        decisions.append({"decision_id": ids.decision_id(rev.audit_rev, seq), "commit_id": commit_id,
                          "audit_rev": rev.audit_rev, "field_id": fid, "rev_base": base, "rev_new": rev_new,
                          "by": by, "choice": kind, "route": route, "value": value, "evidence": evidence, "at": at})
    rtype, date = _page_meta(rev, cand, front)
    key = rev.pages.get(cand["page_id"], {}).get("key") or cand["page_id"]
    body = {"rec_id": cand["rec_id"], "rev": rev_new, "audit_rev": rev.audit_rev, "commit_id": commit_id,
            "status": status, "verified_by": verified_by, "type": rtype, "date": date, "fields": values,
            "dropped": sorted(dropped), "open": [] if status == G.VERIFIED else open_,
            "uncertain": V.uncertain_ids(uncertain, key)}
    new = {"rev": rev_new, "audit_rev": rev.audit_rev, "status": status, "verified_by": verified_by,
           "uncertain": uncertain, "created": at}
    olds = copy.deepcopy(list((front or {}).get("candidates", [])))
    effective = copy.deepcopy((front or {}).get("effective"))
    if status == G.VERIFIED:
        effective = {k: new[k] for k in ("rev", "audit_rev", "status", "verified_by")}
        for c in olds:
            c.setdefault("superseded_by", rev_new)
    out = {"rec_id": cand["rec_id"], "doc_id": cand["doc_id"], "page_id": cand["page_id"],
           "record_idx": cand.get("record_idx", 0), "type": rtype, "date": date,
           "status": effective["status"] if effective else status, "candidates": [*olds, new]}
    if effective:
        out["effective"] = effective
    schema.validate("record", out)  # a planned commit that breaks its contract is a bug: never written
    for d in decisions:
        schema.validate("fixes", d)
    return {"commit_id": commit_id, "rec_id": cand["rec_id"], "rev_new": rev_new, "audit_rev": rev.audit_rev,
            "at": at, "decisions": decisions, "body": body, "front": out}


# --------------------------------------------------------------------------- applying one commit


def _dirs(project: Path) -> None:
    for d in (project / "records", project / "records" / "_rev", project / "records" / "_by_id"):
        if d.is_dir():
            continue
        os.mkdir(d)  # under the project lock: nobody else creates records/
        _fsync_dir(d.parent)
        crashpoint("writeback.mkdir")


def _write_body(project: Path, plan: Mapping[str, Any]) -> None:
    target = _body_path(project, plan["rec_id"], plan["rev_new"])
    data = _md(plan["body"])
    if not target.exists():
        tmp = target.with_name(f".{target.name}.tmp")
        _tmp_write(tmp, data, 0o600, "writeback.rev_tmp")
        os.rename(tmp, target)
        _fsync_dir(target.parent)
        crashpoint("writeback.rev_renamed")
    if target.read_bytes() != data:
        raise WritebackError(f"{target} exists with other content: a revision body is written once")
    if os.stat(target).st_mode & 0o222:
        os.chmod(target, 0o444)
        crashpoint("writeback.rev_written")


def _write_front(project: Path, plan: Mapping[str, Any]) -> None:
    target = _record_path(project, plan["rec_id"])
    data = _md(plan["front"])
    if target.is_file() and target.read_bytes() == data:
        return
    tmp = target.with_name(f".{target.name}.tmp")
    _tmp_write(tmp, data, 0o644, "writeback.byid_tmp")
    os.replace(tmp, target)
    _fsync_dir(target.parent)
    crashpoint("writeback.byid_written")


def _apply(project: Path, adir: Path, plan: Mapping[str, Any]) -> None:
    if plan["decisions"]:
        have = {d.get("decision_id") for d in _jsonl(adir / FIXES)}
        if plan["decisions"][0]["decision_id"] not in have:
            _append(adir / FIXES, list(plan["decisions"]), "writeback.fixes_appended")
    _dirs(project)
    _write_body(project, plan)
    _write_front(project, plan)
    _append(adir / COMMITS, [{"op": "end", "commit_id": plan["commit_id"], "at": plan["at"]}], "writeback.committed")


def _commit(rev: Revision, plan: dict) -> None:
    _append(rev.dir / COMMITS, [{"op": "begin", **plan}], "writeback.begin")
    _apply(rev.project, rev.dir, plan)


def _recover(project: Path, batch_id: str) -> list[str]:
    """Complete every commit whose begin marker has no end marker (roll forward from its stored plan)."""
    done = []
    for n in _revisions(project / "work" / batch_id):
        adir = project / "work" / batch_id / "audit" / f"a{n}"
        marks = _jsonl(adir / COMMITS)
        ended = {m["commit_id"] for m in marks if m.get("op") == "end"}
        for m in marks:
            if m.get("op") == "begin" and m["commit_id"] not in ended:
                plan = {k: v for k, v in m.items() if k != "op"}
                _apply(project, adir, plan)
                done.append(plan["commit_id"])
    return done


# --------------------------------------------------------------------------- the two modes


def _ingest(rev: Revision, at: str) -> list[dict]:
    results = []
    for rec_id, cand in rev.cands.items():
        front = _front(_record_path(rev.project, rec_id))
        known = [_audit_no(c["audit_rev"]) for c in (front or {}).get("candidates", [])]
        if known and max(known) >= rev.n:
            results.append({"rec_id": rec_id, "result": "unchanged"})
            continue
        status = cand["status"]
        plan = _plan(rev, cand, front, status=status, verified_by=cand.get("verified_by"),
                     uncertain=list(cand.get("uncertain", [])), human={}, dropped=[], at=at)
        _commit(rev, plan)
        results.append({"rec_id": rec_id, "result": "committed", "rev_new": plan["rev_new"], "status": status,
                        "commit_id": plan["commit_id"]})
    return results


def _normalise(choices: Any) -> Any:
    """A blank manual value is ``none`` (r2.3)."""
    if not isinstance(choices, dict) or not isinstance(choices.get("items"), list):
        return choices
    out = copy.deepcopy(choices)
    for it in out["items"]:
        ch = it.get("choice") if isinstance(it, dict) else None
        if isinstance(ch, dict) and ch.get("kind") == "manual" and isinstance(ch.get("value"), str) \
                and _BLANK.fullmatch(ch["value"]):
            it["choice"] = {"kind": "none"}
    return out


def _check_record(rev: Revision, rec_id: str, items: list[dict], committed: set[str]) -> tuple[dict, dict | None]:
    """``(result, human choices or None)`` for one record's items (all checks, no write)."""
    cand = rev.cands.get(rec_id)
    fids = [it["field_id"] for it in items]
    if cand is None:
        return {"rec_id": rec_id, "result": "rejected",
                "errors": [{"field_id": f, "reason": f"record not in audit revision {rev.audit_rev}"} for f in fids],
                "missing": [], "duplicate": []}, None
    blocked = _blocked(cand)
    if blocked:
        why = ", ".join(f"{u.get('engine')}:{u['reason']}" for u in blocked)
        return {"rec_id": rec_id, "result": "rejected",
                "errors": [{"field_id": f, "reason": f"page-level uncertain ({why}): not selectable"} for f in fids],
                "missing": [], "duplicate": []}, None
    shown = [f["field_id"] for f in cand.get("fields", []) if _shown(f)]
    counts: dict[str, int] = {}
    for f in fids:
        counts[f] = counts.get(f, 0) + 1
    duplicate = [f for f in dict.fromkeys(fids) if counts[f] > 1]
    missing = [f for f in shown if f not in counts]
    errors = [{"field_id": f, "reason": "not a field to confirm of this record"}
              for f in dict.fromkeys(fids) if f not in shown]
    if not duplicate and not missing and not errors and all(
            _choice_key(it["field_id"], it["rev_base"], _item_choice(it)) in committed for it in items):
        return {"rec_id": rec_id, "result": "unchanged"}, None
    current = _current(_front(_record_path(rev.project, rec_id)))
    human: dict[str, dict] = {}
    for it in items:
        fid = it["field_id"]
        if fid not in shown or fid in human:
            continue
        reason = _item_problem(rev, cand, it, current)
        if reason is not None:
            errors.append({"field_id": fid, "reason": reason})
            continue
        ch = _item_choice(it)
        if ch["kind"] == "manual":
            ch["region"] = _region(rev, cand, fid)
        human[fid] = ch
    if duplicate or missing or errors:
        return {"rec_id": rec_id, "result": "rejected", "errors": errors, "missing": missing,
                "duplicate": duplicate}, None
    return {"rec_id": rec_id, "result": "ok"}, human


def _item_choice(it: Mapping[str, Any]) -> dict:
    ch = dict(it["choice"])
    reason = (it.get("evidence") or {}).get("reason")
    if reason:
        ch["reason"] = reason
    return ch


def _item_problem(rev: Revision, cand: Mapping[str, Any], it: Mapping[str, Any], current: int) -> str | None:
    fid = it["field_id"]
    if it["cell"] != rev.cells[fid].get("cell"):
        return f"cell {it['cell']} is not the cell of {fid} ({rev.cells[fid].get('cell')})"
    if it["seen"] != rev.seen(fid):
        return "seen differs from the readings of this audit revision (stale page)"
    if it["rev_base"] != current:
        return f"rev_base {it['rev_base']} is not the record's current rev {current} (stale page)"
    ch, reason = it["choice"], (it.get("evidence") or {}).get("reason")
    if reason is not None and (reason != "not_a_field" or ch["kind"] != "none"):
        return f"evidence.reason {reason!r}: the only reason is not_a_field, with a 'none' choice"
    if ch["kind"] == "route":
        route = ch["route"]
        if route in NON_VOTING:
            return f"{route} supplies geometry only and never votes: its reading cannot be chosen"
        if route not in rev.runs:
            return f"route {route} is not a route of audit revision {rev.audit_rev}"
        if rev.seen(fid).get(route) is None:
            return f"route {route} has no value on this cell (missing route): not selectable"
    if ch["kind"] == "manual" and _region(rev, cand, fid) is None:
        return "a typed value needs a region (no box for this cell)"
    return None


def _choices_mode(project: Path, batch_arg: str | None, path: Path, at: str, out: TextIO) -> tuple[int, dict]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise WritebackError(f"cannot read the choices file {path}: {exc}", USAGE_EXIT) from exc
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise WritebackError(f"{path} is not JSON: {exc}") from exc
    data = _normalise(data)
    problems = [i for i in schema.issues("choices", data) if i.keyword != "x-uniqueBy"]
    if problems:
        raise WritebackError(f"{path} is not klode-kit.choices/1: {problems[0].path or '<root>'}: "
                             f"{problems[0].message}")
    batch_id = data["batch_id"]
    if batch_arg is not None and batch_arg != batch_id:
        raise WritebackError(f"the choices are for batch {batch_id}, not {batch_arg}")
    revs = _revisions(project / "work" / batch_id)
    if not revs:
        raise WritebackError(f"batch {batch_id} of the choices has no audit revision in this project")
    receipt = {"schema": RECEIPT_SCHEMA, "batch_id": batch_id, "audit_rev": None, "mode": "choices",
               "choices_sha256": hashlib.sha256(raw).hexdigest(), "per_record": [], "recovered": []}
    with _locked(project / "work"):
        receipt["recovered"] = _recover(project, batch_id)
        rev = _load(project, batch_id, revs[-1])
        receipt["audit_rev"] = rev.audit_rev
        _check_page(rev, data)
        groups: dict[str, list[dict]] = {}
        for it in data["items"]:
            groups.setdefault(it["rec_id"], []).append(it)
        committed = _committed_keys(rev.dir)
        rejected = False
        for rec_id, items in groups.items():
            res, human = _check_record(rev, rec_id, items, committed)
            if human is not None:
                res = _commit_human(rev, rec_id, human, at, data["confirm_sha256"])
            rejected |= res["result"] == "rejected"
            receipt["per_record"].append(res)
            if res["result"] == "rejected":
                parts = [f"{e['field_id']} ({e['reason']})" for e in res["errors"]]
                parts += [f"missing field_id {f}" for f in res["missing"]]
                parts += [f"duplicate field_id {f}" for f in res["duplicate"]]
                print(f"kb-audit --writeback: {rec_id}: rejected, nothing written: {'; '.join(parts)}", file=out)
        code = CONTRACT_EXIT if rejected else _awaiting(project, groups)
        _finish(rev, receipt, code, at)
    return code, receipt


def _commit_human(rev: Revision, rec_id: str, human: dict[str, dict], at: str, confirm_sha: str) -> dict:
    cand = rev.cands[rec_id]
    front = _front(_record_path(rev.project, rec_id))
    undecided = [f for f, ch in human.items() if ch["kind"] == "none" and ch.get("reason") != "not_a_field"]
    dropped = [f for f, ch in human.items() if ch["kind"] == "none" and ch.get("reason") == "not_a_field"]
    if undecided:
        status, by = G.DISPUTED, None
        keep = set(undecided)
        uncertain = [u for u in cand.get("uncertain", [])
                     if u.get("field_id") in keep or (u.get("scope") == G.ROW)]
    else:
        status, by, uncertain = G.VERIFIED, "human", []
    plan = _plan(rev, cand, front, status=status, verified_by=by, uncertain=uncertain, human=human,
                 dropped=dropped, at=at, confirm_sha=confirm_sha)
    _commit(rev, plan)
    return {"rec_id": rec_id, "result": "committed", "rev_new": plan["rev_new"], "status": status,
            "commit_id": plan["commit_id"]}


def _check_page(rev: Revision, data: Mapping[str, Any]) -> None:
    if data.get("audit_rev") is not None and data["audit_rev"] != rev.audit_rev:
        raise WritebackError(f"the choices are for {data['audit_rev']}; the newest audit revision is "
                             f"{rev.audit_rev} (stale page)")
    fingerprints = {e: r for e, r in rev.runs.items() if r}
    if data["fingerprints"] != fingerprints:
        raise WritebackError(f"fingerprints {data['fingerprints']} are not the runs of {rev.audit_rev} "
                             f"({fingerprints}): stale page")
    page = rev.dir / "confirm.html"
    if not page.is_file():
        raise WritebackError(f"{rev.audit_rev} has no confirm page; the choices cannot come from it")
    if CP.confirm_sha256(page.read_bytes()) != data["confirm_sha256"]:
        raise WritebackError(f"confirm_sha256 is not the sealed sha of {rev.audit_rev}/confirm.html")


def _awaiting(project: Path, rec_ids: Iterable[str]) -> int:
    for rec_id in rec_ids:
        front = _front(_record_path(project, rec_id))
        if front is None or front.get("status") != G.VERIFIED:
            return AWAITING_EXIT
    return OK_EXIT


def _finish(rev: Revision, receipt: dict, code: int, at: str) -> None:
    receipt["exit_code"] = code
    receipt["at"] = at
    if receipt["recovered"] or any(r["result"] == "committed" for r in receipt["per_record"]):
        _append(rev.dir / RECEIPTS, [receipt], "writeback.receipt")


class _locked:
    def __init__(self, work: Path):
        self.path = work / LOCK
        self.fd: int | None = None

    def __enter__(self) -> "_locked":
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            raise LockBusy(f"another kb-ocr / kb-audit / writeback holds {self.path}") from None
        self.fd = fd
        crashpoint("writeback.lock")
        return self

    def __exit__(self, *exc: object) -> None:
        assert self.fd is not None
        os.close(self.fd)


def _auto_mode(project: Path, batch_id: str | None, at: str) -> tuple[int, dict]:
    if batch_id is None:
        raise WritebackError("--writeback without a choices file needs --batch <batch_id>", USAGE_EXIT)
    revs = _revisions(project / "work" / batch_id)
    if not revs:
        raise WritebackError(f"batch {batch_id} has no audit revision; run kb-audit first", USAGE_EXIT)
    receipt = {"schema": RECEIPT_SCHEMA, "batch_id": batch_id, "audit_rev": None, "mode": "ingest",
               "choices_sha256": None, "per_record": [], "recovered": []}
    with _locked(project / "work"):
        receipt["recovered"] = _recover(project, batch_id)
        rev = _load(project, batch_id, revs[-1])
        receipt["audit_rev"] = rev.audit_rev
        receipt["per_record"] = _ingest(rev, at)
        code = _awaiting(project, rev.cands)
        _finish(rev, receipt, code, at)
    return code, receipt


def run(project: str | os.PathLike, batch_id: str | None = None, choices: str | os.PathLike | None = None, *,
        out: TextIO | None = None, stdout: TextIO | None = None) -> int:
    """The writeback of ``project`` (module docstring); prints the receipt on ``stdout`` and returns the exit code."""
    out = sys.stderr if out is None else out
    stdout = sys.stdout if stdout is None else stdout
    project = Path(project)
    at = _now()
    if batch_id is not None and not _BATCH.fullmatch(batch_id):
        print(f"kb-audit --writeback: --batch must be a batch id (12 hex characters): {batch_id!r}", file=out)
        return USAGE_EXIT
    try:
        if choices is None:
            code, receipt = _auto_mode(project, batch_id, at)
        else:
            code, receipt = _choices_mode(project, batch_id, Path(choices), at, out)
    except LockBusy as exc:
        print(f"kb-audit --writeback: {exc}", file=out)
        return ENV_EXIT
    except (WritebackError, schema.ContractError) as exc:
        exc_code = getattr(exc, "exit_code", CONTRACT_EXIT)
        print(f"kb-audit --writeback: {exc}", file=out)
        print(json.dumps({"schema": RECEIPT_SCHEMA, "exit_code": exc_code, "error": str(exc),
                          "per_record": []}, ensure_ascii=False), file=stdout)
        return exc_code
    for r in receipt["per_record"]:
        if r["result"] == "committed":
            print(f"kb-audit --writeback: {r['rec_id']}: r{r['rev_new']} {r['status']} ({r['commit_id']})", file=out)
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True), file=stdout)
    return code
