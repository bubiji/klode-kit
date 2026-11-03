"""WI-38: ``kb-audit --again --batch <batch_id>`` - a second confirm pass on the records a first pass left pending or
disputed (P33; spec ``.claude/tdd-guardian/wi-38-spec.md``).

Usage::

    klode-kit kb-audit --again --batch <batch_id> [--project <data project>]

The confirm page ``a<N>/confirm.html`` is written once, with each record's rev at that moment. A first writeback that
leaves fields as "都不对" moves the record to ``r<cur+1>`` (``disputed``) and every later choice from that page is
refused as stale; ``--reopen`` only takes a verified record. ``--again`` gives such records a new page that lists only
what is still open, at the records' current revs; the decision goes through the ordinary writeback::

    kb-audit --again --batch B                        -> work/B/audit/a<N>/again/g<NNNN>.html
    node templates/confirm_export.js <that page> ...  -> choices.json     (or ``confirm_page.export_choices``)
    kb-audit --writeback choices.json                 -> r<cur+1> verified (human), or disputed if fields stay open

**Which records** (of the batch's newest audit revision ``a<N>``): every record whose current rev is a candidate of
``a<N>`` with status ``pending_confirm`` or ``disputed`` and no page-level blocker (a missing route: rerun kb-audit
first, the page could not take a choice). Its fields are the ones still undecided (``writeback._undecided``): the
fields to confirm, cells then page items, that no committed human decision on ``a<N>`` has settled ("都不对" settles
nothing). A verified record is never listed (that is ``--reopen``).

**Files** (inside ``a<N>``, both only ever added to):

- ``again.jsonl``: one line per new page ``{op: "again", again_id: "<audit_rev>/g<NNNN>", batch_id, audit_rev,
  records: [{rec_id, rev_base, fields: [field_id, ...]}], by: "human", at, page}``;
- ``again/g<NNNN>.html``: the page, written once (temporary file, rename, read-only); the ordinary template and seal
  (``confirm_page.render(again=...)``), its data block carrying ``again: {again_id}``.

**Idempotent**: a run whose records (rec_id, rev_base, fields) equal the newest event's is ``unchanged`` and names the
same page; once a writeback moved a record on, the next run is a new event ``g<NNNN+1>``. A batch with no such record
is ``nothing`` (no file written).

**Refused** (exit 30, nothing written): no ``work/``; a malformed batch id; a batch with no audit revision; a record
of ``a<N>`` whose candidates are older than ``a<N>`` (``kb-audit --writeback --batch`` first).

**Under the project lock** ``work/.lock``; each run first rolls forward the batch's unfinished writeback commits.
Crash points (one durable write between two of them): ``writeback.lock``, ``again.logged``, ``again.mkdir``,
``again.page_tmp``, ``again.page_renamed``, ``again.page_written``. A run killed after the log line is completed by the
next run (same ``again_id``).

**Receipt** (stdout, one JSON line, ``klode-kit.again_receipt/1``): ``{schema, result: opened|unchanged|nothing,
exit_code, batch_id, audit_rev, again_id, records, fields, page, confirm_sha256}`` or, refused, ``{schema, result:
"refused", exit_code, error}``.

Exit codes: 10 records await the human (opened or unchanged); 0 nothing to do; 2 the audit revision is incomplete
(as the writeback); 30 refused; 40 the project lock is held.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Callable, TextIO

from klode_kit.audit import confirm_page as CP
from klode_kit.audit import gate as G
from klode_kit.audit import writeback as WB
from klode_kit.contract import schema
from klode_kit.seams import crashpoint

OK_EXIT, AWAITING_EXIT, CONTRACT_EXIT, ENV_EXIT = 0, 10, 30, 40
RECEIPT_SCHEMA = "klode-kit.again_receipt/1"
LOG, PAGE_DIR = WB.AGAIN_LOG, WB.AGAIN_DIR
CRASHPOINTS = ("again.logged", "again.mkdir", "again.page_tmp", "again.page_renamed", "again.page_written")

_BATCH = re.compile(r"[0-9a-f]{12}")


class Refused(Exception):
    def __init__(self, message: str, exit_code: int = CONTRACT_EXIT):
        super().__init__(message)
        self.exit_code = exit_code


def again_id(audit_rev: str, seq: int) -> str:
    return f"{audit_rev}/g{seq:04d}"


def _page_name(seq: int) -> str:
    return f"g{seq:04d}.html"


# --------------------------------------------------------------------------- which records


def _open_records(project: Path, rev: WB.Revision) -> tuple[list[dict], int]:
    """``(records still awaiting a human, number of verified records)`` of ``rev`` (module docstring), or ``Refused``."""
    records, verified = [], 0
    for rec_id, cand in rev.cands.items():
        front = WB._front(WB._record_path(project, rec_id))
        current = WB._current(front)
        cur = next((c for c in (front or {}).get("candidates", []) if c["rev"] == current), None)
        if cur is None or WB._audit_no(cur["audit_rev"]) < rev.n:
            raise Refused(f"audit revision {rev.audit_rev} is newer than the candidates of {rec_id}: run "
                          f"kb-audit --writeback --batch {rev.batch_id} first")
        if cur["status"] == G.VERIFIED:
            verified += 1
            continue
        if cur["status"] not in (G.PENDING, G.DISPUTED) or WB._blocked(cand):
            continue
        fields = WB._undecided(rev, cand)
        if fields:
            records.append({"rec_id": rec_id, "rev_base": current, "fields": fields})
    return records, verified


# --------------------------------------------------------------------------- the page


def _write_page(adir: Path, seq: int, render: Callable[[], bytes]) -> None:
    """Write ``again/g<seq>.html`` once: temporary file, rename, read-only. A page that already exists is kept (a run
    killed half way is completed, never rewritten)."""
    directory = adir / PAGE_DIR
    if not directory.is_dir():
        os.mkdir(directory)
        WB._fsync_dir(adir)
        crashpoint("again.mkdir")
    target = directory / _page_name(seq)
    if not target.exists():
        tmp = target.with_name(f".{target.name}.tmp")
        WB._tmp_write(tmp, render(), 0o600, "again.page_tmp")
        os.rename(tmp, target)
        WB._fsync_dir(directory)
        crashpoint("again.page_renamed")
    if os.stat(target).st_mode & 0o222:
        os.chmod(target, 0o444)
        crashpoint("again.page_written")


def _again(project: Path, batch_id: str) -> dict:
    if not (isinstance(batch_id, str) and _BATCH.fullmatch(batch_id)):
        raise Refused(f"--batch must be a batch id (12 hex characters): {batch_id!r}")
    if not (project / "work").is_dir():
        raise Refused(f"{project} has no work/ directory: nothing to confirm")
    with WB._locked(project / "work"):
        WB._recover(project, batch_id)
        revs = WB._revisions(project / "work" / batch_id)
        if not revs:
            raise Refused(f"batch {batch_id} has no audit revision; run kb-audit first")
        rev = WB._load(project, batch_id, revs[-1])
        records, verified = _open_records(project, rev)
        receipt = {"schema": RECEIPT_SCHEMA, "result": "nothing", "exit_code": OK_EXIT, "batch_id": batch_id,
                   "audit_rev": rev.audit_rev, "again_id": None, "records": 0, "fields": 0, "page": None,
                   "confirm_sha256": None, "verified": verified}
        if not records:
            return receipt
        adir = rev.dir
        log = WB._jsonl(adir / LOG)
        event = log[-1] if log and log[-1]["records"] == records else None
        result = "unchanged"
        if event is None:
            seq = len(log) + 1
            event = {"op": "again", "again_id": again_id(rev.audit_rev, seq), "batch_id": batch_id,
                     "audit_rev": rev.audit_rev, "records": records, "by": "human", "at": WB._now(),
                     "page": f"{PAGE_DIR}/{_page_name(seq)}"}
            WB._append(adir / LOG, [event], "again.logged")
            result = "opened"
        seq = int(event["again_id"].rsplit("/g", 1)[1])
        target = adir / PAGE_DIR / _page_name(seq)
        _write_page(adir, seq, lambda: CP.render(project, batch_id, rev.n, again=event))
        page = target.read_bytes()
    receipt.update(result=result, exit_code=AWAITING_EXIT, again_id=event["again_id"], records=len(records),
                   fields=sum(len(r["fields"]) for r in records), page=target.relative_to(project).as_posix(),
                   confirm_sha256=CP.confirm_sha256(page))
    return receipt


def run(project: str | os.PathLike, *, batch_id: str | None = None, out: TextIO | None = None,
        stdout: TextIO | None = None) -> int:
    """The second pass of ``batch_id`` in ``project`` (module docstring); prints the receipt on ``stdout``, returns
    the exit code."""
    out = sys.stderr if out is None else out
    stdout = sys.stdout if stdout is None else stdout
    try:
        receipt = _again(Path(project), batch_id)
    except WB.LockBusy as exc:
        code, message = ENV_EXIT, str(exc)
    except Refused as exc:
        code, message = exc.exit_code, str(exc)
    except (WB.WritebackError, schema.ContractError, CP.ConfirmError) as exc:
        code, message = getattr(exc, "exit_code", CONTRACT_EXIT), str(exc)
    else:
        verified = receipt.pop("verified")
        if receipt["result"] == "nothing":
            hint = f"; {verified} verified record(s): a wrong verified field is kb-audit --reopen <field_id>" \
                if verified else ""
            print(f"kb-audit --again: {batch_id}: nothing awaits a human in {receipt['audit_rev']}{hint}", file=out)
        else:
            print(f"kb-audit --again: {batch_id}: {receipt['result']} {receipt['again_id']} ({receipt['records']} "
                  f"record(s), {receipt['fields']} field(s)); confirm page {receipt['page']}", file=out)
        print(json.dumps(receipt, ensure_ascii=False, sort_keys=True), file=stdout)
        return receipt["exit_code"]
    print(f"kb-audit --again: {batch_id}: {message}", file=out)
    print(json.dumps({"schema": RECEIPT_SCHEMA, "result": "refused", "exit_code": code, "error": message},
                     ensure_ascii=False, sort_keys=True), file=stdout)
    return code
