"""WI-40: ``kb-audit --redate`` - give the records whose date is a date of birth their visit date (P36; spec
``.claude/tdd-guardian/wi-40-spec.md``).

Usage::

    klode-kit kb-audit --redate --batch <batch_id> [--apply] [--project DIR]

``record_meta.page_date`` used to take the earliest date of a page when no line said ``日期``, which on a Japanese lab
report is the date of birth. A record's date, once set, is kept by every later writeback, so fixing the rule does not
repair the records already written. ``--redate`` re-derives the date of every record of the batch's newest audit
revision (any status) with the writeback's own inputs (the receipt page's ``date``, else ``record_meta.derive``) and
lists the records whose stored date (the front ``date`` of ``records/_by_id/<rec_id>.md``) differs.

Without ``--apply`` nothing is written. With ``--apply``, per changed record: one line is appended to
``work/<batch>/audit/redate.jsonl`` ``{op: "redate", rec_id, old, new, source: page|inbox_dir|batch, at}``, the front
``date`` is set to ``new`` and the views are rebuilt (``records/`` and ``verified/`` names follow the date). The
revision bodies under ``records/_rev/`` are never rewritten (append-only store); the next writeback writes the front
date into its new revision body, and ``records_to_txt`` exports the front date of a record that has a ``redate`` event
(``redated``). Idempotent: a second run finds nothing. Under the writeback's project lock ``work/.lock``.

**Receipt** (stdout, one JSON line, ``klode-kit.redate_receipt/1``): ``{schema, result: planned|applied|nothing|
refused, exit_code, batch_id, records: [{rec_id, old, new, source}]}`` (refused: ``{schema, result, exit_code,
error}``). Exit codes: 0 planned / applied / nothing; 30 refused (bad batch id, no ``work/``, no audit revision);
40 the project lock is held.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, TextIO

RECEIPT_SCHEMA = "klode-kit.redate_receipt/1"
LOG = "redate.jsonl"
OK_EXIT, CONTRACT_EXIT, ENV_EXIT = 0, 30, 40

_BATCH = re.compile(r"[0-9a-f]{12}")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class Refused(Exception):
    def __init__(self, message: str, exit_code: int = CONTRACT_EXIT):
        super().__init__(message)
        self.exit_code = exit_code


def redated(project: Path) -> set[str]:
    """The ``rec_id`` of every record with a ``redate`` event in any batch of ``project``."""
    out: set[str] = set()
    for log in sorted((Path(project) / "work").glob(f"*/audit/{LOG}")):
        for raw in log.read_text(encoding="utf-8").splitlines():
            if raw.strip():
                row = json.loads(raw)
                if row.get("op") == "redate":
                    out.add(row["rec_id"])
    return out


def _plan(project: Path, rev: Any) -> list[dict]:
    from klode_kit.audit import record_meta as RM
    from klode_kit.audit import writeback as WB

    out = []
    for rec_id in sorted(rev.cands):
        front = WB._front(WB._record_path(project, rec_id))
        if front is None:
            continue
        page = rev.pages.get(rev.cands[rec_id]["page_id"], {})
        if page.get("date") and _DATE.fullmatch(str(page["date"])):
            new, source = page["date"], "page"
        else:
            d = RM.derive(project / "work" / rev.batch_id, page, rev.voting)
            new, source = d["date"], d["date_source"]
        if new is not None and new != front.get("date"):
            out.append({"rec_id": rec_id, "old": front.get("date"), "new": new, "source": source})
    return out


def _redate(project: Path, batch_id: Any, apply: bool) -> dict:
    from klode_kit.audit import views
    from klode_kit.audit import writeback as WB

    if not (isinstance(batch_id, str) and _BATCH.fullmatch(batch_id)):
        raise Refused(f"--batch must be a batch id (12 hex characters): {batch_id!r}")
    if not (project / "work").is_dir():
        raise Refused(f"{project} has no work/ directory: nothing to re-date")
    bdir = project / "work" / batch_id
    revs = WB._revisions(bdir)
    if not revs:
        raise Refused(f"batch {batch_id} has no audit revision; nothing to re-date")
    with WB._locked(project / "work"):
        WB._recover(project, batch_id)
        changes = _plan(project, WB._load(project, batch_id, revs[-1]))
        receipt = {"schema": RECEIPT_SCHEMA, "result": "planned" if changes else "nothing", "exit_code": OK_EXIT,
                   "batch_id": batch_id, "records": changes}
        if apply and changes:
            at = WB._now()
            WB._append(bdir / "audit" / LOG, [{"op": "redate", **c, "at": at} for c in changes], "redate.appended")
            for c in changes:
                path = WB._record_path(project, c["rec_id"])
                front = WB._front(path)
                front["date"] = c["new"]
                tmp = path.with_name(f".{path.name}.tmp")
                WB._tmp_write(tmp, WB._md(front), 0o644, "redate.byid_tmp")
                os.replace(tmp, path)
                WB._fsync_dir(path.parent)
            views.rebuild(project)
            receipt["result"] = "applied"
    return receipt


def run(project: str | os.PathLike, *, batch_id: str | None = None, apply: bool = False,
        out: TextIO | None = None, stdout: TextIO | None = None) -> int:
    """The command (module docstring); prints the receipt on ``stdout`` and returns the exit code."""
    from klode_kit.audit import views
    from klode_kit.audit import writeback as WB

    out = sys.stderr if out is None else out
    stdout = sys.stdout if stdout is None else stdout
    try:
        receipt = _redate(Path(project), batch_id, apply)
    except WB.LockBusy as exc:
        code, message = ENV_EXIT, str(exc)
    except Refused as exc:
        code, message = exc.exit_code, str(exc)
    except (WB.WritebackError, views.ViewsError, ValueError) as exc:
        code, message = getattr(exc, "exit_code", CONTRACT_EXIT), str(exc)
    else:
        print(f"kb-audit --redate: {batch_id}: {receipt['result']} ({len(receipt['records'])} record(s))", file=out)
        print(json.dumps(receipt, ensure_ascii=False, sort_keys=True), file=stdout)
        return receipt["exit_code"]
    print(f"kb-audit --redate: {batch_id}: {message}", file=out)
    print(json.dumps({"schema": RECEIPT_SCHEMA, "result": "refused", "exit_code": code, "error": message},
                     ensure_ascii=False, sort_keys=True), file=stdout)
    return code
