"""WI-08e: ``kb-audit --reopen <field_id>`` - put an already verified field back on a confirm page (design 02 r3.1
"四路 10.5、人工改 1.05", r4.1 two-layer record, 04 U5; spec ``.claude/tdd-guardian/wi-08e-spec.md``).

Usage::

    klode-kit kb-audit --reopen <field_id> [--project <data project>] [--batch <batch_id>] [--reason <text>]

Four routes can read the same wrong value; they then agree, the record is ``verified(auto)`` and no confirm page ever
lists the field. A reopen is the public way back: it records that a human doubts ``<field_id>`` and writes a small
confirm page for that one field. It changes nothing the records say - the old revision stays effective until the human
decides. The decision goes through the ordinary writeback::

    kb-audit --reopen <field_id>                      -> work/<batch>/audit/a<N>/reopen/o<NNNN>.html
    node templates/confirm_export.js <that page> ...  -> choices.json     (or ``confirm_page.export_choices``)
    kb-audit --writeback choices.json                 -> r<current+1> verified (human), the old rev kept
    klode-kit views                                   -> the compatibility links follow ``effective``

**Files** (all inside the newest audit revision ``a<N>`` of the record's batch, both only ever added to):

- ``reopen.jsonl``: append-only; one line per reopen ``{op: "reopen", reopen_id: "<audit_rev>/o<NNNN>", field_id,
  rec_id, audit_rev, rev_base, effective_rev, value, dropped, reason, by: "human", at, page}``. ``rev_base`` is the
  record's current rev when the field was reopened; ``value`` the field's current value (``null`` for a field a human
  dropped, ``dropped: true``).
- ``reopen/o<NNNN>.html``: the page, written once (temporary file, rename, read-only); the ordinary confirm page's
  template and seal, one record, one field (``confirm_page.render(reopen=...)``).

**Refused** (exit 30, nothing written): no such record or field; the record has no ``effective`` revision (it is not
verified - confirm it on the ordinary page); a newer candidate than ``effective`` waits for confirmation; the batch's
newest audit revision is newer than the record's last candidate (``kb-audit --writeback --batch`` first, else the
correction would be stamped over an audit revision nobody wrote back); the field is not in the effective revision
(``fields`` or ``dropped``) or not in the audit revision's cells; ``--batch`` is not the record's batch.

**Idempotent**: a field that is already reopened and not yet decided (an event whose ``rev_base`` is the record's current
rev) is not reopened again; the receipt says ``unchanged`` and names the same page. After the correction is committed (the
rev moved on) a new reopen is a new event, ``o<NNNN+1>``.

**Under the project lock** ``work/.lock`` shared with kb-ocr / kb-audit / writeback; each run first rolls forward
unfinished writeback commits, as every writeback does. Crash points (one durable write between two of them):
``writeback.lock`` (the shared lock), ``reopen.logged``, ``reopen.mkdir``, ``reopen.page_tmp``, ``reopen.page_renamed``,
``reopen.page_written``. A run killed after the log line and before the page is completed by the next reopen.

**Receipt** (stdout, one JSON line, ``klode-kit.reopen_receipt/1``): ``{schema, result: opened|unchanged, exit_code,
field_id, rec_id, batch_id, audit_rev, reopen_id, rev_base, effective_rev, page, confirm_sha256}`` or, refused,
``{schema, result: "refused", exit_code, field_id, error}``.

Exit codes: 10 the field awaits the human (opened or unchanged); 2 usage (malformed field id or batch id); 30 refused;
40 the project lock is held.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable, TextIO

from klode_kit.audit import confirm_page as CP
from klode_kit.audit import writeback as WB
from klode_kit.contract import schema
from klode_kit.seams import crashpoint

OK_EXIT, AWAITING_EXIT, USAGE_EXIT, CONTRACT_EXIT, ENV_EXIT = 0, 10, 2, 30, 40
RECEIPT_SCHEMA = "klode-kit.reopen_receipt/1"
LOG, PAGE_DIR = WB.REOPEN_LOG, WB.REOPEN_DIR
CRASHPOINTS = ("reopen.logged", "reopen.mkdir", "reopen.page_tmp", "reopen.page_renamed", "reopen.page_written")

_FIELD = re.compile(r"r-[0-9a-f]{12}/[^/\s]+")
_BATCH = re.compile(r"[0-9a-f]{12}")


class Refused(Exception):
    def __init__(self, message: str, exit_code: int = CONTRACT_EXIT):
        super().__init__(message)
        self.exit_code = exit_code


def reopen_id(audit_rev: str, seq: int) -> str:
    return f"{audit_rev}/o{seq:04d}"


def _page_name(seq: int) -> str:
    return f"o{seq:04d}.html"


# --------------------------------------------------------------------------- the checks


def _recover_all(project: Path) -> None:
    """Complete every unfinished writeback commit of every batch (a reopen reads records a crash may have cut)."""
    work = project / "work"
    for d in sorted(work.iterdir()):
        if _BATCH.fullmatch(d.name) and (d / "audit").is_dir():
            WB._recover(project, d.name)


def _check(project: Path, field_id: str, batch_id: str | None) -> tuple[WB.Revision, dict, dict, Any]:
    """``(audit revision, record front matter, effective body, candidate)`` of a reopenable field, or ``Refused``."""
    rec_id = field_id.split("/", 1)[0]
    front = WB._front(WB._record_path(project, rec_id))
    if front is None:
        raise Refused(f"no record {rec_id}: nothing to reopen")
    eff = front.get("effective")
    if not eff or eff.get("status") != "verified":
        raise Refused(f"record {rec_id} is not verified (status {front.get('status')}): confirm its fields on the "
                      f"ordinary confirm page; only a verified field is reopened")
    current = WB._current(front)
    if eff["rev"] != current:
        newer = next(c for c in front["candidates"] if c["rev"] == current)
        raise Refused(f"record {rec_id} has a newer candidate r{current} ({newer['status']}) than its effective "
                      f"r{eff['rev']}: confirm it first, a reopen would bury it")
    batch = eff["audit_rev"].rsplit("/a", 1)[0]
    if batch_id is not None and batch_id != batch:
        raise Refused(f"{rec_id} belongs to batch {batch}, not {batch_id}")
    revs = WB._revisions(project / "work" / batch)
    newest = revs[-1] if revs else 0
    if WB._audit_no(eff["audit_rev"]) != newest:
        raise Refused(f"audit revision a{newest} of batch {batch} is not the one {rec_id} r{eff['rev']} came from "
                      f"({eff['audit_rev']}): run kb-audit --writeback --batch {batch} first")
    body = WB._front(WB._body_path(project, rec_id, eff["rev"]))
    if body is None:
        raise Refused(f"revision body of {rec_id} r{eff['rev']} is missing")
    if field_id not in body.get("fields", {}) and field_id not in body.get("dropped", []):
        raise Refused(f"{field_id} is not a field of {rec_id} r{eff['rev']}")
    rev = WB._load(project, batch, newest)
    cand = rev.cands.get(rec_id)
    if field_id not in rev.cells or cand is None or field_id not in [f["field_id"] for f in cand.get("fields", [])]:
        raise Refused(f"{field_id} is not a cell of audit revision {rev.audit_rev}")
    return rev, front, body, cand


# --------------------------------------------------------------------------- the page


def _write_page(adir: Path, seq: int, render: Callable[[], bytes]) -> None:
    """Write ``reopen/o<seq>.html`` once: temporary file, rename, read-only. A page that already exists is kept (a run
    killed half way is completed, never rewritten)."""
    directory = adir / PAGE_DIR
    if not directory.is_dir():
        os.mkdir(directory)
        WB._fsync_dir(adir)
        crashpoint("reopen.mkdir")
    target = directory / _page_name(seq)
    if not target.exists():
        tmp = target.with_name(f".{target.name}.tmp")
        WB._tmp_write(tmp, render(), 0o600, "reopen.page_tmp")
        os.rename(tmp, target)
        WB._fsync_dir(directory)
        crashpoint("reopen.page_renamed")
    if os.stat(target).st_mode & 0o222:
        os.chmod(target, 0o444)
        crashpoint("reopen.page_written")


def _open_event(adir: Path, field_id: str, current: int) -> dict | None:
    """The reopen of ``field_id`` nobody has decided yet: the newest event on it made at the record's current rev."""
    return next((e for e in reversed(WB._jsonl(adir / LOG))
                 if e["field_id"] == field_id and e["rev_base"] == current), None)


def _reopen(project: Path, field_id: str, batch_id: str | None, reason: str | None) -> dict:
    if batch_id is not None and not _BATCH.fullmatch(batch_id):
        raise Refused(f"--batch must be a batch id (12 hex characters): {batch_id!r}", USAGE_EXIT)
    if not _FIELD.fullmatch(field_id):
        raise Refused(f"not a field id (r-<12 hex>/<slot>): {field_id!r}", USAGE_EXIT)
    if not (project / "work").is_dir():
        raise Refused(f"{project} has no work/ directory: nothing to reopen")
    with WB._locked(project / "work"):
        _recover_all(project)
        rev, front, body, _ = _check(project, field_id, batch_id)
        adir, rec_id, current = rev.dir, front["rec_id"], WB._current(front)
        event = _open_event(adir, field_id, current)
        result = "unchanged"
        if event is None:
            seq = len(WB._jsonl(adir / LOG)) + 1
            event = {"op": "reopen", "reopen_id": reopen_id(rev.audit_rev, seq), "field_id": field_id,
                     "rec_id": rec_id, "audit_rev": rev.audit_rev, "rev_base": current, "effective_rev": current,
                     "value": body.get("fields", {}).get(field_id), "dropped": field_id not in body.get("fields", {}),
                     "reason": reason, "by": "human", "at": WB._now(), "page": f"{PAGE_DIR}/{_page_name(seq)}"}
            WB._append(adir / LOG, [event], "reopen.logged")
            result = "opened"
        seq = int(event["reopen_id"].rsplit("/o", 1)[1])
        target = adir / PAGE_DIR / _page_name(seq)
        _write_page(adir, seq, lambda: CP.render(project, rev.batch_id, rev.n, reopen=event))
        page = target.read_bytes()
    return {"schema": RECEIPT_SCHEMA, "result": result, "exit_code": AWAITING_EXIT, "field_id": field_id,
            "rec_id": rec_id, "batch_id": rev.batch_id, "audit_rev": rev.audit_rev,
            "reopen_id": event["reopen_id"], "rev_base": event["rev_base"], "effective_rev": event["effective_rev"],
            "page": target.relative_to(project).as_posix(), "confirm_sha256": CP.confirm_sha256(page)}


def run(project: str | os.PathLike, field_id: str, *, batch_id: str | None = None, reason: str | None = None,
        out: TextIO | None = None, stdout: TextIO | None = None) -> int:
    """Reopen ``field_id`` of ``project`` (module docstring); prints the receipt on ``stdout``, returns the exit code."""
    out = sys.stderr if out is None else out
    stdout = sys.stdout if stdout is None else stdout
    try:
        receipt = _reopen(Path(project), field_id, batch_id, reason)
    except WB.LockBusy as exc:
        code, message = ENV_EXIT, str(exc)
    except Refused as exc:
        code, message = exc.exit_code, str(exc)
    except (WB.WritebackError, schema.ContractError, CP.ConfirmError) as exc:
        code, message = getattr(exc, "exit_code", CONTRACT_EXIT), str(exc)
    else:
        print(f"kb-audit --reopen: {field_id}: {receipt['result']} {receipt['reopen_id']} (r{receipt['rev_base']}); "
              f"confirm page {receipt['page']}", file=out)
        print(json.dumps(receipt, ensure_ascii=False, sort_keys=True), file=stdout)
        return AWAITING_EXIT
    print(f"kb-audit --reopen: {field_id}: {message}", file=out)
    print(json.dumps({"schema": RECEIPT_SCHEMA, "result": "refused", "exit_code": code, "field_id": field_id,
                      "error": message}, ensure_ascii=False, sort_keys=True), file=stdout)
    return code
