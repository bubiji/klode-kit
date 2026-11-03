"""WI-39: reviewer attribution - which decisions an AI reviewer (D-43) made, and the backfill for the ones written as
``human`` before ``choices.json`` could say so (spec ``.claude/tdd-guardian/wi-39-spec.md``).

Usage::

    klode-kit kb-audit --attribute --batch <batch_id> --reviewer-model <model> --reviewer-rule <rule> [--project DIR]

A reviewer who confirmed a batch's fields before ``choices.json`` carried ``reviewer: {model, rule}`` left decisions
written ``by: "human"``. ``--attribute`` marks them without rewriting anything: it appends one line to
``work/<batch>/audit/attributions.jsonl``::

    {op: "attribute", batch_id, commit_ids: [...], by: "reviewer", reviewer: {model, rule}, at}

``commit_ids`` are the ended commits of every ``a<N>`` of the batch that hold at least one ``by: "human"`` decision and
are not attributed yet. ``fixes.jsonl``, ``commits.jsonl``, the record bodies and ``_rev/`` are never touched.

**One reader.** ``decided_by(project, batch_id, decision)`` is ``"reviewer"`` for a ``human`` decision whose
``commit_id`` is attributed, else the decision's own ``by`` (an ``auto`` decision stays ``auto``).
``record_verified_by(project, rec_id)`` is the same rule for a record: ``None`` unless its ``effective`` revision is
verified; ``"reviewer"`` when the effective revision was written ``reviewer`` or any decision it rests on (the
committed decisions on its audit revision up to its rev, the last one on a field standing) is a reviewer's;
otherwise the revision's own ``verified_by`` (``human`` / ``auto``).

**Receipt** (stdout, one JSON line, ``klode-kit.attribute_receipt/1``): ``{schema, result: attributed|nothing|refused,
exit_code, batch_id, commits, records}`` (refused: ``{schema, result, exit_code, error}``). Nothing to attribute: no
line written. Exit codes: 0 attributed / nothing; 30 refused (bad or missing batch id, no ``work/``, no audit
revision, a missing or over-long model / rule); 40 the project lock is held.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping, TextIO

RECEIPT_SCHEMA = "klode-kit.attribute_receipt/1"
LOG = "attributions.jsonl"
OK_EXIT, CONTRACT_EXIT, ENV_EXIT = 0, 30, 40
DECIDERS = ("human", "reviewer")   # the `by` values of a decision a person (or a reviewer for the owner) made
LIMIT = 64

_BATCH = re.compile(r"[0-9a-f]{12}")
_REV_DIR = re.compile(r"a([1-9][0-9]*)")


def _jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _front(path: Path) -> dict | None:
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    end = text.find("\n---", 4)
    if not text.startswith("---\n") or end < 0:
        return None
    data = json.loads(text[4:end])
    return data if isinstance(data, dict) else None


def attributed(project: Path, batch_id: str) -> dict[str, dict]:
    """``{commit_id: reviewer}`` of the batch's ``attributions.jsonl`` (empty when there is none)."""
    out: dict[str, dict] = {}
    for row in _jsonl(Path(project) / "work" / batch_id / "audit" / LOG):
        if row.get("op") == "attribute":
            for cid in row.get("commit_ids", []):
                out.setdefault(cid, row.get("reviewer") or {})
    return out


def decided_by(project: Path, batch_id: str, decision: Mapping[str, Any]) -> str:
    """Who made ``decision``: ``"reviewer"`` for a human decision of an attributed commit, else its own ``by``."""
    if decision.get("by") == "human" and decision.get("commit_id") in attributed(project, batch_id):
        return "reviewer"
    return str(decision["by"])


def settled(decision: Mapping[str, Any]) -> bool:
    """A decision that decides its field: not a plain ``none`` (which leaves it open)."""
    reason = (decision.get("evidence") or {}).get("reason")
    return not (decision["choice"] == "none" and reason not in ("not_a_field", "acknowledged"))


def standing(adir: Path, rec_id: str, *, upto: int | None = None) -> dict[str, dict]:
    """The last committed person-made decision per field of ``rec_id`` on audit revision directory ``adir`` (with
    ``rev_new <= upto``), settled ones only: what the record's revisions rest on."""
    ended = {m["commit_id"] for m in _jsonl(adir / "commits.jsonl") if m.get("op") == "end"}
    last: dict[str, dict] = {}
    for d in _jsonl(adir / "fixes.jsonl"):
        if d.get("by") in DECIDERS and d.get("commit_id") in ended and d["field_id"].split("/", 1)[0] == rec_id \
                and (upto is None or d["rev_new"] <= upto):
            last[d["field_id"]] = d
    return {f: d for f, d in last.items() if settled(d)}


def _effective(project: Path, rec_id: str) -> dict | None:
    front = _front(Path(project) / "records" / "_by_id" / f"{rec_id}.md")
    eff = (front or {}).get("effective")
    ok = isinstance(eff, dict) and eff.get("status") == "verified" and isinstance(eff.get("audit_rev"), str) \
        and "/a" in eff["audit_rev"]
    return eff if ok else None   # a hand-made record without an audit revision has no decisions to read


def _decisions_of(project: Path, eff: Mapping[str, Any], rec_id: str) -> list[dict]:
    batch_id, n = str(eff["audit_rev"]).split("/a")
    adir = Path(project) / "work" / batch_id / "audit" / f"a{n}"
    return list(standing(adir, rec_id, upto=eff["rev"]).values())


def record_verified_by(project: Path, rec_id: str) -> str | None:
    """``"auto" | "human" | "reviewer"`` for the verified effective revision of ``rec_id``, else ``None``."""
    eff = _effective(project, rec_id)
    if eff is None:
        return None
    batch_id = str(eff["audit_rev"]).split("/a")[0]
    by = eff.get("verified_by") or "auto"
    for d in _decisions_of(project, eff, rec_id):
        if decided_by(project, batch_id, d) == "reviewer":
            return "reviewer"
    return str(by)


def reviewer_model(project: Path, rec_id: str) -> str | None:
    """The model of the newest reviewer decision of ``rec_id``'s effective revision (``evidence.reviewer`` of the
    decision, or the attribution's reviewer for an attributed commit); ``None`` when there is none."""
    eff = _effective(project, rec_id)
    if eff is None:
        return None
    batch_id = str(eff["audit_rev"]).split("/a")[0]
    att = attributed(project, batch_id)
    found = None
    for d in sorted(_decisions_of(project, eff, rec_id), key=lambda x: x["decision_id"]):
        rv = (d.get("evidence") or {}).get("reviewer") if d["by"] == "reviewer" else \
            att.get(d["commit_id"]) if d["by"] == "human" else None
        if rv and rv.get("model"):
            found = rv["model"]
    return found


# --------------------------------------------------------------------------- the backfill


class Refused(Exception):
    def __init__(self, message: str, exit_code: int = CONTRACT_EXIT):
        super().__init__(message)
        self.exit_code = exit_code


def _attribute(project: Path, batch_id: Any, model: Any, rule: Any) -> dict:
    from klode_kit.audit import writeback as WB

    if not (isinstance(batch_id, str) and _BATCH.fullmatch(batch_id)):
        raise Refused(f"--batch must be a batch id (12 hex characters): {batch_id!r}")
    for name, value in (("--reviewer-model", model), ("--reviewer-rule", rule)):
        if not (isinstance(value, str) and 1 <= len(value) <= LIMIT):
            raise Refused(f"{name} is required: 1..{LIMIT} characters, got {value!r}")
    if not (project / "work").is_dir():
        raise Refused(f"{project} has no work/ directory: nothing to attribute")
    bdir = project / "work" / batch_id
    revs = WB._revisions(bdir)
    if not revs:
        raise Refused(f"batch {batch_id} has no audit revision; nothing to attribute")
    with WB._locked(project / "work"):
        WB._recover(project, batch_id)
        done = attributed(project, batch_id)
        commits: set[str] = set()
        records: set[str] = set()
        for n in revs:
            adir = bdir / "audit" / f"a{n}"
            ended = {m["commit_id"] for m in WB._jsonl(adir / WB.COMMITS) if m.get("op") == "end"}
            for d in WB._jsonl(adir / WB.FIXES):
                if d.get("by") == "human" and d.get("commit_id") in ended and d["commit_id"] not in done:
                    commits.add(d["commit_id"])
                    records.add(d["field_id"].split("/", 1)[0])
        receipt = {"schema": RECEIPT_SCHEMA, "result": "nothing", "exit_code": OK_EXIT, "batch_id": batch_id,
                   "commits": 0, "records": 0}
        if commits:
            row = {"op": "attribute", "batch_id": batch_id, "commit_ids": sorted(commits), "by": "reviewer",
                   "reviewer": {"model": model, "rule": rule}, "at": WB._now()}
            WB._append(bdir / "audit" / LOG, [row], "attribute.appended")
            receipt.update(result="attributed", commits=len(commits), records=len(records))
    return receipt


def run(project: str | os.PathLike, *, batch_id: str | None = None, model: str | None = None,
        rule: str | None = None, out: TextIO | None = None, stdout: TextIO | None = None) -> int:
    """The backfill (module docstring); prints the receipt on ``stdout`` and returns the exit code."""
    from klode_kit.audit import writeback as WB

    out = sys.stderr if out is None else out
    stdout = sys.stdout if stdout is None else stdout
    try:
        receipt = _attribute(Path(project), batch_id, model, rule)
    except WB.LockBusy as exc:
        code, message = ENV_EXIT, str(exc)
    except Refused as exc:
        code, message = exc.exit_code, str(exc)
    except (WB.WritebackError, ValueError) as exc:
        code, message = getattr(exc, "exit_code", CONTRACT_EXIT), str(exc)
    else:
        print(f"kb-audit --attribute: {batch_id}: {receipt['result']} ({receipt['commits']} commit(s), "
              f"{receipt['records']} record(s))", file=out)
        print(json.dumps(receipt, ensure_ascii=False, sort_keys=True), file=stdout)
        return receipt["exit_code"]
    print(f"kb-audit --attribute: {batch_id}: {message}", file=out)
    print(json.dumps({"schema": RECEIPT_SCHEMA, "result": "refused", "exit_code": code, "error": message},
                     ensure_ascii=False, sort_keys=True), file=stdout)
    return code
