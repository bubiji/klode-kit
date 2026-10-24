"""WI-08d: the compatibility views (design 02 R2-10, r4.1 "视图", R4-01; 04 U3 / U6 / U8; plan risk R-14).

Usage::

    klode-kit views [--project <data project>]

The record files ``records/_by_id/<rec_id>.md`` (written by the writeback alone, WI-08c) are the source; the views
are symbolic links to immutable revision bodies ``records/_rev/<rec_id>.r<N>.md``:

- ``records/<date>-<type>.md`` -> ``_rev/<rec_id>.r<N>.md`` with ``N = effective.rev``; while a record has no
  effective revision (first ingest, R4-01) ``N`` is its newest candidate, so the view shows that candidate and its
  status (``pending_confirm`` / ``disputed``);
- ``verified/<date>-<type>.md`` -> ``../records/_rev/<rec_id>.r<effective.rev>.md`` only when
  ``effective.status == verified``; it carries the same name as the record's ``records/`` view.

Names: ``<date>-<type>.md`` for the first record of a date and type, then ``-2``, ``-3``, ... in the order ``(batch
of the record's first candidate, page key in that batch's receipt, record_idx, rec_id)``. The order depends only on
the records, never on the order they were written or listed. A record without a date is ``undated-<type>.md``.

Rebuild: deterministic and idempotent (a link already right is not rewritten); every link is written to a
temporary name and renamed into place, so a reader never sees a missing or dangling view; rebuilds are serialised
by ``records/.views.lock`` (blocking). A view link no record names any more is removed (only links pointing at
``records/_rev/``; any other file or link is left alone). Views never write ``records/_by_id`` or ``records/_rev``:
the status of a record is the writeback's alone (08c-16).

``uncertain_ids`` renders uncertain entries as the id list a revision body shows (R-14): a cell or row entry is
``<page key>-l<NN>`` (``NN`` = the aligned row, at least two digits: ``p002-l07``), a page entry
``<page key>:<engine>:<reason>``; repeats are dropped, order kept.

Exit codes: 0 rebuilt; 2 usage (bad arguments, no such project directory); 30 refused (a regular file where a view
link goes, a malformed record file, a record whose revision body is missing).
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence, TextIO

OK_EXIT, USAGE_EXIT, CONTRACT_EXIT = 0, 2, 30
SCHEMA = "klode-kit.views/1"
LOCK = ".views.lock"
UNDATED = "undated"

_FIELD_ROW = re.compile(r"[^/]*/r(\d+)-")
_ID_ROW = re.compile(r":r(\d+):")
_TMP = re.compile(r"\..+\.\d+\.tmp")
_UNSAFE = re.compile(r"[^\w.-]")


class ViewsError(Exception):
    pass


# --------------------------------------------------------------------------- rendering


def _row_id(key: str, row: str) -> str:
    return f"{key}-l{int(row):02d}"


def uncertain_ids(entries: Sequence[Mapping[str, Any]], key: str) -> list[str]:
    """The id list view of ``entries`` on the page keyed ``key`` (module docstring)."""
    out: list[str] = []
    for u in entries:
        uid = str(u.get("id", ""))
        m = _FIELD_ROW.match(str(u.get("field_id") or "")) or _ID_ROW.search(uid)
        if m:
            rid = _row_id(key, m.group(1))
        elif u.get("scope") == "page":
            rid = ":".join(x for x in (key, u.get("engine"), u.get("reason")) if x)
        else:
            rid = uid
        if rid not in out:
            out.append(rid)
    return out


# --------------------------------------------------------------------------- reading the records


def _front(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    end = text.find("\n---", 4)
    try:
        if not text.startswith("---\n") or end < 0:
            raise ValueError("no front matter block")
        data = json.loads(text[4:end])
        if not isinstance(data, dict) or not isinstance(data.get("rec_id"), str):
            raise ValueError("front matter is not a record")
        return data
    except ValueError as exc:
        raise ViewsError(f"{path}: {exc}") from None


def _page_keys(project: Path, batch: str, cache: dict[str, dict[str, str]]) -> dict[str, str]:
    if batch not in cache:
        receipt = project / "work" / batch / "receipt.json"
        pages = json.loads(receipt.read_text(encoding="utf-8")).get("pages", []) if receipt.is_file() else []
        cache[batch] = {p["page_id"]: p["key"] for p in pages if p.get("page_id") and p.get("key")}
    return cache[batch]


def _plan(project: Path) -> tuple[dict[str, str], dict[str, str], list[str]]:
    """``(records/ links, verified/ links, problems)``: name -> link target."""
    rows, problems = [], []
    keys: dict[str, dict[str, str]] = {}
    for path in sorted((project / "records" / "_by_id").glob("*.md")):
        front = _front(path)
        rec = front["rec_id"]
        cands = sorted(front.get("candidates") or [], key=lambda c: c["rev"])
        eff = front.get("effective")
        if not cands and not eff:
            continue
        rev = eff["rev"] if eff else cands[-1]["rev"]
        if not (project / "records" / "_rev" / f"{rec}.r{rev}.md").is_file():
            problems.append(f"{rec}: revision body _rev/{rec}.r{rev}.md is missing; no view written for it")
            continue
        batch = str(cands[0]["audit_rev"]).rsplit("/a", 1)[0] if cands else ""
        key = _page_keys(project, batch, keys).get(front.get("page_id"), str(front.get("page_id"))) if batch else ""
        base = f"{front.get('date') or UNDATED}-{_UNSAFE.sub('_', str(front.get('type') or 'doc'))}"
        rows.append(((batch, key, int(front.get("record_idx") or 0), rec), base, rec, rev,
                     eff is not None and eff.get("status") == "verified"))
    records, verified, seen = {}, {}, {}
    for _, base, rec, rev, ok in sorted(rows, key=lambda r: r[0]):
        seen[base] = seen.get(base, 0) + 1
        name = f"{base}.md" if seen[base] == 1 else f"{base}-{seen[base]}.md"
        records[name] = f"_rev/{rec}.r{rev}.md"
        if ok:
            verified[name] = f"../records/_rev/{rec}.r{rev}.md"
    return records, verified, problems


# --------------------------------------------------------------------------- writing the links


def _is_view(path: Path, prefix: str) -> bool:
    return path.is_symlink() and path.name.endswith(".md") and os.readlink(path).startswith(prefix)


def _link(directory: Path, name: str, target: str) -> None:
    path = directory / name
    if path.is_symlink() and os.readlink(path) == target:
        return
    tmp = directory / f".{name}.{os.getpid()}.tmp"
    if os.path.lexists(tmp):
        os.unlink(tmp)
    os.symlink(target, tmp)
    os.replace(tmp, path)


def _sync(directory: Path, want: Mapping[str, str], prefix: str) -> None:
    for name, target in want.items():
        _link(directory, name, target)
    for path in sorted(directory.iterdir()):
        if path.is_symlink() and _TMP.fullmatch(path.name):  # left by a rebuild that died
            os.unlink(path)
        elif path.name not in want and _is_view(path, prefix):
            os.unlink(path)


def rebuild(project: str | os.PathLike) -> dict:
    """Rebuild the views of ``project`` (module docstring); returns ``{schema, records, verified, problems}``."""
    project = Path(project)
    out: dict[str, Any] = {"schema": SCHEMA, "records": {}, "verified": {}, "problems": []}
    records_dir, verified_dir = project / "records", project / "verified"
    if not (records_dir / "_by_id").is_dir():
        return out
    fd = os.open(records_dir / LOCK, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        records, verified, problems = _plan(project)
        for d, names in ((records_dir, records), (verified_dir, verified)):
            for name in names:
                if os.path.lexists(d / name) and not (d / name).is_symlink():
                    raise ViewsError(f"{d.name}/{name} is a regular file, not a view link: refusing to replace it")
        verified_dir.mkdir(exist_ok=True)
        _sync(records_dir, records, "_rev/")
        _sync(verified_dir, verified, "../records/_rev/")
    finally:
        os.close(fd)
    out.update(records=records, verified=verified, problems=problems)
    return out


# --------------------------------------------------------------------------- CLI


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        raise ViewsError(f"usage: {message}")


def main(argv: list[str] | None = None, *, out: TextIO | None = None, stdout: TextIO | None = None) -> int:
    out = sys.stderr if out is None else out
    stdout = sys.stdout if stdout is None else stdout
    parser = _Parser(prog="klode-kit views", description="rebuild records/<date>-<type>.md and verified/ views")
    parser.add_argument("--project", type=Path, default=Path("."), help="data project (default: current directory)")
    try:
        args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    except ViewsError as exc:
        print(f"views: {exc}", file=out)
        return USAGE_EXIT
    if not args.project.is_dir():
        print(f"views: no such project directory: {args.project}", file=out)
        return USAGE_EXIT
    try:
        result = rebuild(args.project)
    except ViewsError as exc:
        print(f"views: {exc}", file=out)
        return CONTRACT_EXIT
    for p in result["problems"]:
        print(f"views: {p}", file=out)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), file=stdout)
    return CONTRACT_EXIT if result["problems"] else OK_EXIT
