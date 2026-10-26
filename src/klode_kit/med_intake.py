"""``klode-kit med-intake``: register an inbox, run ocr -> audit, stop at the human (WI-13b; design 02 §2.3 /med-intake,
risk R-3, 02 r5 "同一日期第二批"; deviations WI-09a pipeline kernel, WI-08b confirm page, WI-08d record_meta).

Usage::

    klode-kit med-intake --inbox DIR [--project DIR] [--type lab|rx] [--routes a,b] [--launcher L]

What it does, in order (the orchestration is the pipeline kernel's - ``klode_kit.pipeline.run`` - this module adds the
registration, the date aliases and the two terminal lines; see ``docs/med-intake.md``):

1. scan the inbox (usage error, exit 2, before anything is created: not a directory, no images);
2. take ``work/.med-intake.lock`` (second process: exit 40);
3. ``pipeline`` up to ``ocr`` (preflight, kb-ocr); print ``ocr: 3 页 × 4 路 OK``;
4. register the type (``--type``, else the title keyword the routes read, ``record_meta.record_type``; no or conflicting
   keywords -> exit 2 asking for ``--type``) and the date (inbox directory, else the page, else today);
5. allocate the date alias ``work/<yyyymmdd>`` -> ``<batch_id>`` (``-2``, ``-3`` ... when the day is taken by another
   batch; an existing name is never overwritten, an alias of this batch is reused);
6. ``pipeline`` up to ``audit`` (reuses the stages already done); print ``audit: 5 条分歧 → confirm/<alias>.html``;
7. link ``confirm/<alias>.html`` -> the newest audit revision's confirm page; write ``work/<batch_id>/med_intake.json``.

Exit codes: 10 awaiting the human's choices, 0 nothing to confirm, 2 usage / type required, 30 contract, 40 environment
or lock, and the pipeline kernel's own for a failed stage (50, 60, 61).

Crash points (all after the lock, so a killed run is rerun by running it again): ``med_intake.lock`` (lock taken),
``med_intake.aliased`` (``work/`` link made), ``med_intake.linked`` (``confirm/`` link made), ``med_intake.receipt``
(receipt written to its temporary name, not yet renamed). Every step is idempotent: the aliases are looked up before they
are made, the pipeline reuses done stages, the receipt is replaced whole.
"""

from __future__ import annotations

import argparse
import fcntl
import io
import itertools
import json
import os
import re
import sys
from collections import Counter
from datetime import date as _date
from pathlib import Path
from typing import Any, Mapping, Sequence, TextIO

from klode_kit import pipeline as P
from klode_kit import receipts
from klode_kit.audit import confirm_page
from klode_kit.audit import gate as G
from klode_kit.audit import record_meta as RM
from klode_kit.ocr import manifest as manifest_mod
from klode_kit.seams import crashpoint

SCHEMA = "klode-kit.med_intake/1"
TYPES = ("lab", "rx")
LOCK_NAME = ".med-intake.lock"
RECEIPT_NAME = "med_intake.json"
CRASHPOINTS = ("med_intake.lock", "med_intake.aliased", "med_intake.linked", "med_intake.receipt")
USAGE_EXIT = 2
AWAITING_EXIT = 10
_COMPACT = re.compile(r"\d{8}")


class TypeRequired(Exception):
    """No (single) document type could be decided from the titles; the caller must give ``--type``."""


class AliasConflict(Exception):
    """A name wanted for a date alias is taken by something that is not this batch's link."""


# --------------------------------------------------------------------------- type and date

def header_type(lines_by_route: Mapping[str, Sequence[str]]) -> str | None:
    """The type the title keyword gives (``处方`` -> rx, ``检验`` / ``化验`` -> lab), voted across the routes; ``None``
    without a keyword. The same rule the record views use, so the registered type is the type of the view file."""
    return RM.record_type(lines_by_route)


def resolve_type(page_types: Sequence[str | None], explicit: str | None) -> tuple[str, str]:
    """``(type, source)`` of a batch: the explicit ``--type`` always wins (``explicit``); else every page must have the
    same header type (``header``). No pages, a page without keyword or pages that disagree -> ``TypeRequired``."""
    if explicit is not None:
        if explicit not in TYPES:
            raise ValueError(f"unknown document type {explicit!r}; types: {', '.join(TYPES)}")
        return explicit, "explicit"
    known = set(page_types)
    if not page_types or None in known:
        raise TypeRequired("no title keyword (检验 / 化验 / 处方) was read on every page: give --type lab or --type rx")
    if len(known) > 1:
        raise TypeRequired(f"the pages disagree on the document type ({', '.join(sorted(known))}): split the inbox "
                           f"or give --type lab / --type rx")
    return page_types[0], "header"


def alias_date(inbox: str | os.PathLike) -> str | None:
    """``yyyymmdd`` of the inbox directory (``2026-10-22`` or ``20261022``, nearest dated directory of the path, a real
    calendar day), else ``None``."""
    iso = RM.dir_date(str(Path(inbox) / "x"))
    return iso.replace("-", "") if iso else None


def _iso(compact: str) -> str:
    return f"{compact[:4]}-{compact[4:6]}-{compact[6:]}"


# --------------------------------------------------------------------------- aliases

def _alias_name(date: str, k: int) -> str:
    return date if k == 1 else f"{date}-{k}"


def allocate_alias(project: str | os.PathLike, date: str, batch_id: str) -> str:
    """The alias name of ``batch_id`` for ``date`` (``yyyymmdd``), made as the relative link ``work/<name>`` ->
    ``<batch_id>`` when it does not exist yet. The first name of ``date``, ``date-2``, ``date-3`` ... that is either this
    batch's own link (reused) or free (made) is used; a name taken by anything else - another batch's link, a real
    directory, a file, a stray ``confirm/<name>.html`` - is skipped, never touched. The same batch gets the same name
    every time."""
    project = Path(project)
    work = project / "work"
    work.mkdir(parents=True, exist_ok=True)
    for k in itertools.count(1):
        name = _alias_name(date, k)
        link = work / name
        if os.path.lexists(link):
            if link.is_symlink() and os.readlink(link) == batch_id:
                return name
            continue
        if os.path.lexists(project / "confirm" / f"{name}.html"):
            continue
        os.symlink(batch_id, link)  # one writer at a time (``work/.med-intake.lock``): the name is still free
        return name
    raise AssertionError("unreachable")  # pragma: no cover - itertools.count never ends


def link_confirm(project: str | os.PathLike, alias: str, batch_id: str, rev: int) -> Path:
    """``confirm/<alias>.html`` -> ``../work/<batch_id>/audit/a<rev>/confirm.html`` (relative). A link that already has
    this target is left alone (same inode); a link of this batch at an older revision is replaced in one rename; anything
    else at that name -> ``AliasConflict`` and nothing is touched."""
    confirm = Path(project) / "confirm"
    confirm.mkdir(parents=True, exist_ok=True)
    page = confirm / f"{alias}.html"
    target = f"../work/{batch_id}/audit/a{rev}/confirm.html"
    if os.path.lexists(page):
        if not page.is_symlink() or not os.readlink(page).startswith(f"../work/{batch_id}/audit/"):
            raise AliasConflict(f"{page} exists and is not a link to a confirm page of batch {batch_id}")
        if os.readlink(page) == target:
            return page
    tmp = confirm / f".{alias}.html.tmp"
    if os.path.lexists(tmp):
        os.unlink(tmp)  # the leftover link of a killed run (we hold the lock): a link, not data
    os.symlink(target, tmp)
    os.replace(tmp, page)
    return page


# --------------------------------------------------------------------------- the two lines

def ocr_failures(receipt: Mapping[str, Any]) -> list[str]:
    """``route:page`` of every page route that is not ``done``, sorted."""
    out = []
    for page in receipt.get("pages", []):
        for route, res in (page.get("routes") or {}).items():
            if (res or {}).get("status") != "done":
                out.append(f"{route}:{page.get('key')}")
    return sorted(out)


def _routes(receipt: Mapping[str, Any]) -> list[str]:
    names = set(receipt.get("attempts") or {})
    if not names:
        for page in receipt.get("pages", []):
            names |= set(page.get("routes") or {})
    return [e for e in G.ENGINES if e in names] + sorted(names - set(G.ENGINES))


def ocr_line(receipt: Mapping[str, Any]) -> str:
    pages, routes, failed = len(receipt.get("pages", [])), len(_routes(receipt)), ocr_failures(receipt)
    head = f"ocr: {pages} 页 × {routes} 路"
    return f"{head}，{len(failed)} 处失败（{', '.join(failed)}）" if failed else f"{head} OK"


def audit_line(n: int, alias: str | None) -> str:
    if n == 0 or alias is None:
        return f"audit: {n} 条分歧，无需确认"
    return f"audit: {n} 条分歧 → confirm/{alias}.html"


# --------------------------------------------------------------------------- the run

class _LockBusy(Exception):
    pass


def _take_lock(work: Path) -> int:
    fd = os.open(work / LOCK_NAME, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        holder = os.pread(fd, 64, 0).decode("utf-8", "replace").split()
        os.close(fd)
        raise _LockBusy(holder[0] if holder else "unknown") from None
    os.pwrite(fd, f"{os.getpid()}".ljust(63).encode("utf-8") + b"\n", 0)
    return fd


def _json_line(text: str) -> dict:
    """The pipeline's one-line JSON summary (its stdout); ``{}`` when there is none."""
    lines = text.strip().splitlines()
    try:
        data = json.loads(lines[-1]) if lines else None
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _write_receipt(path: Path, data: Mapping[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.tmp")  # one writer at a time (the lock); a killed run's leftover is overwritten
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.write(fd, receipts.dumps(data))
        os.fsync(fd)
    finally:
        os.close(fd)
    crashpoint("med_intake.receipt")
    os.replace(tmp, path)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _page_facts(batch_dir: Path, receipt: Mapping[str, Any]) -> list[dict]:
    return [RM.derive(batch_dir, page, G.ENGINES) for page in receipt.get("pages", [])]


def _register_date(inbox: Path, facts: Sequence[Mapping[str, Any]], today: _date) -> tuple[str, str]:
    """``(yyyymmdd, source)``: the inbox directory, else the date most pages read, else today."""
    compact = alias_date(inbox)
    if compact:
        return compact, "inbox_dir"
    counts = Counter(f["date"] for f in facts if f.get("date_source") == "page" and f.get("date"))
    if counts:
        best = max(counts.values())
        return min(d for d, n in counts.items() if n == best).replace("-", ""), "page"
    return today.strftime("%Y%m%d"), "today"


def run(inbox: str | os.PathLike, *, project: str | os.PathLike, record_type: str | None = None,
        routes: str | None = None, launcher: str | os.PathLike | None = None,
        env: Mapping[str, str] | None = None, out: TextIO | None = None, stdout: TextIO | None = None,
        today: _date | None = None) -> int:
    out = sys.stderr if out is None else out
    stdout = sys.stdout if stdout is None else stdout
    project, inbox = Path(project), Path(inbox)
    if record_type is not None and record_type not in TYPES:
        print(f"med-intake: unknown document type {record_type!r}; types: {', '.join(TYPES)}", file=out)
        return USAGE_EXIT
    if not project.is_dir():
        print(f"med-intake: no such project directory: {project}", file=out)
        return USAGE_EXIT
    try:
        images, skipped = manifest_mod.scan(inbox)
    except manifest_mod.InboxError as exc:
        print(f"med-intake: {exc}", file=out)
        return USAGE_EXIT
    if not images:
        print(f"med-intake: no images in {inbox}", file=out)
        return USAGE_EXIT

    work = project / "work"
    work.mkdir(exist_ok=True)
    try:
        lock = _take_lock(work)
    except _LockBusy as exc:
        print(f"med-intake: another med-intake holds the project lock {work / LOCK_NAME} (pid {exc})", file=out)
        return P.EXIT_CODE[P.Reason.lock_busy]
    try:
        crashpoint("med_intake.lock")
        return _locked(inbox, project, images, skipped, record_type, routes, launcher, env, out, stdout,
                       today or _date.today())
    finally:
        os.close(lock)


def _stopped(buf: io.StringIO, code: int, out: TextIO) -> int:
    summary = _json_line(buf.getvalue())
    stages = summary.get("stages") or []
    where = stages[-1]["stage"] if stages else "start"
    print(f"med-intake: stopped at {where}: {summary.get('reason', 'failed')} (exit {code})", file=out)
    return code


def _locked(inbox: Path, project: Path, images: Sequence[Any], skipped: Sequence[Mapping[str, Any]],
            record_type: str | None, routes: str | None, launcher: str | os.PathLike | None,
            env: Mapping[str, str] | None, out: TextIO, stdout: TextIO, today: _date) -> int:
    first = io.StringIO()
    code = P.run(project, inbox, stage="ocr", routes=routes, launcher=launcher, env=env, out=out, stdout=first)
    if code != 0:
        return _stopped(first, code, out)
    batch_id = _json_line(first.getvalue()).get("batch_id")
    batch_dir = project / "work" / str(batch_id)
    ocr = _read_json(batch_dir / "receipt.json")
    if not isinstance(batch_id, str) or not isinstance(ocr, dict):  # pragma: no cover - the ocr stage checks its products
        print("med-intake: the pipeline left no OCR batch receipt", file=out)
        return P.EXIT_CODE[P.Reason.contract_violation]
    print(ocr_line(ocr), file=stdout, flush=True)

    facts = _page_facts(batch_dir, ocr)
    try:
        rtype, type_source = resolve_type([f.get("record_type") for f in facts], record_type)
    except TypeRequired as exc:
        print(f"med-intake: {exc}", file=out)
        return USAGE_EXIT
    compact, date_source = _register_date(inbox, facts, today)
    alias = allocate_alias(project, compact, batch_id)
    crashpoint("med_intake.aliased")

    second = io.StringIO()
    code = P.run(project, inbox, stage="audit", routes=routes, launcher=launcher, env=env, out=out, stdout=second)
    if code not in (0, AWAITING_EXIT):
        return _stopped(second, code, out)
    summary = _json_line(second.getvalue())

    disputes, confirm = 0, None
    if code == AWAITING_EXIT:
        adir = P.newest_audit_rev(batch_dir)
        page = None if adir is None else adir / "confirm.html"
        if adir is None or page is None or not page.is_file():  # pragma: no cover - the audit stage checks its products
            print("med-intake: kb-audit awaits the human but wrote no confirm page", file=out)
            return P.EXIT_CODE[P.Reason.contract_violation]
        data = confirm_page.page_data(page.read_bytes())
        disputes = sum(len(r.get("fields", [])) for r in data.get("records", []))
        try:
            link_confirm(project, alias, batch_id, int(adir.name[1:]))
        except AliasConflict as exc:
            print(f"med-intake: {exc}", file=out)
            return P.EXIT_CODE[P.Reason.contract_violation]
        crashpoint("med_intake.linked")
        confirm = f"confirm/{alias}.html"
        print(f"confirm page: {page}", file=out)
    status = "awaiting_confirm" if code == AWAITING_EXIT else "nothing_to_confirm"

    source: dict[str, Any] = {"inbox": inbox.resolve().name, "files": [s.name for s in images]}
    if skipped:
        source["skipped"] = [{"name": Path(s["path"]).name, "reason": s["reason"]} for s in skipped]
    receipt = {
        "schema": SCHEMA, "batch_id": batch_id, "alias": alias, "confirm": confirm,
        "record_type": rtype, "type_source": type_source,
        "date": _iso(compact), "date_source": date_source, "source": source,
        "pages": len(ocr.get("pages", [])), "routes": _routes(ocr), "ocr_failures": ocr_failures(ocr),
        "disputes": disputes, "status": status, "exit_code": code,
        "stages": [{"stage": s["stage"], "status": s["status"], "exit_code": s["exit_code"]}
                   for s in summary.get("stages", [])],
    }
    _write_receipt(batch_dir / RECEIPT_NAME, receipt)

    print(audit_line(disputes, alias if confirm else None), file=stdout)
    print(f"status: {status}", file=stdout, flush=True)
    return code


# --------------------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="klode-kit med-intake",
                                     description="Register an inbox, run ocr -> audit and stop at the confirm page.")
    parser.add_argument("--inbox", required=True, type=Path, help="inbox directory with the images/PDFs, or one PDF")
    parser.add_argument("--project", type=Path, default=None, help="data project directory (default: cwd)")
    parser.add_argument("--type", dest="record_type", choices=TYPES, default=None,
                        help="document type (default: decided by the title keyword)")
    parser.add_argument("--routes", default=None, help="comma-separated subset of the OCR routes")
    parser.add_argument("--launcher", type=Path, default=None,
                        help="command launcher (default: this repo's bin/klode-kit)")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return USAGE_EXIT if exc.code else 0
    project = Path.cwd() if args.project is None else args.project
    return run(args.inbox, project=project, record_type=args.record_type, routes=args.routes, launcher=args.launcher)


if __name__ == "__main__":  # pragma: no cover - entry is ``klode-kit med-intake``
    sys.exit(main())
