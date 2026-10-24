"""Helpers for the records_to_txt tests (WI-01a; matrix 01a-*).

Two ways to get a data project whose ``records/`` the export reads:

- ``pipeline(project, fx)``: the real stages - the kb-ocr batch of a fixture, ``kb-audit``, then the writeback
  (route clicks on every field to confirm, or a plain ingest when nothing awaits confirmation). Nothing here
  writes ``records/``.
- ``put_record(...)``: a record written by hand in the writeback's on-disk format (``records/_by_id/<rec>.md`` and
  ``records/_rev/<rec>.r<N>.md``, JSON front matter), for states the public stages cannot reach on demand (a
  candidate body that holds values, an empty verified record, a build snapshot older than ``effective``). Item
  labels of an audit revision go to ``work/<batch>/audit/a<N>/candidates.jsonl`` the way kb-audit writes them.

Readers: ``lines(result, shelf)`` and ``tree(out)``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from _support import writeback_fx as W

FIXTURES = W.FIXTURES


def pipeline(project: Path, fx: Path) -> tuple[str, Path]:
    """Audit ``fx`` into ``project`` and write back every record; ``(batch_id, newest a<N>)``."""
    b, adir = W.audited(project, fx)
    if W.to_confirm(adir):
        code, _, err = W.submit(project, W.export(project, b, adir, W.picks(adir)), project.parent)
    else:
        code, _, err = W.submit(project, None, project.parent, batch_id=b)
    assert code == 0, err
    return b, adir


def _md(front: Mapping[str, Any]) -> str:
    return "---\n" + json.dumps(front, ensure_ascii=False, indent=1, sort_keys=True) + "\n---\n"


def put_record(project: Path, rec_id: str, revs: Mapping[int, Mapping[str, Any]], *, effective: int | None,
               date: str | None = None, rtype: str = "lab") -> None:
    """Write record ``rec_id``: ``revs = {N: {status, audit_rev, fields, date?, verified_by?}}``; ``effective``
    names the revision ``effective`` points at (``None``: no effective revision)."""
    rec_dir, rev_dir = project / "records" / "_by_id", project / "records" / "_rev"
    rec_dir.mkdir(parents=True, exist_ok=True)
    rev_dir.mkdir(parents=True, exist_ok=True)
    cands = []
    for n in sorted(revs):
        r = revs[n]
        vb = r.get("verified_by", "human" if r["status"] == "verified" else None)
        body = {"rec_id": rec_id, "rev": n, "audit_rev": r["audit_rev"], "commit_id": f"{r['audit_rev']}/c{n:04d}",
                "status": r["status"], "verified_by": vb, "type": rtype, "date": r.get("date", date),
                "fields": dict(r["fields"]), "dropped": list(r.get("dropped", [])), "open": [], "uncertain": []}
        (rev_dir / f"{rec_id}.r{n}.md").write_text(_md(body), encoding="utf-8")
        cands.append({"rev": n, "audit_rev": r["audit_rev"], "status": r["status"], "verified_by": vb,
                      "uncertain": [], "created": "2026-10-22T00:00:00Z"})
    front: dict[str, Any] = {"rec_id": rec_id, "doc_id": "d-" + rec_id[2:], "page_id": f"d-{rec_id[2:]}/p001",
                             "record_idx": 0, "type": rtype, "date": date, "candidates": cands}
    if effective is not None:
        e = next(c for c in cands if c["rev"] == effective)
        front["effective"] = {k: e[k] for k in ("rev", "audit_rev", "status", "verified_by")}
        front["status"] = e["status"]
    else:
        front["status"] = cands[-1]["status"]
    (rec_dir / f"{rec_id}.md").write_text(_md(front), encoding="utf-8")


def put_labels(project: Path, audit_rev: str, labels: Mapping[str, str]) -> None:
    """``candidates.jsonl`` of ``audit_rev`` naming ``item_label`` per field_id (other columns as kb-audit)."""
    batch, n = audit_rev.split("/")
    path = project / "work" / batch / "audit" / n / "candidates.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    by_rec: dict[str, list[dict]] = {}
    for fid, label in labels.items():
        by_rec.setdefault(fid.split("/")[0], []).append(
            {"field_id": fid, "item_label": label, "verdict": "agree", "recommended": "WRONG-IF-READ",
             "votes": [["WRONG-IF-READ", ["vision"]]], "uncertain": [], "missing": [], "crop": None})
    rows = [{"rec_id": r, "audit_rev": audit_rev, "fields": f, "uncertain": [], "status": "verified"}
            for r, f in by_rec.items()]
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def lines(result: Mapping[str, Any], shelf: str | None = None) -> list[str]:
    """Every line of every exported file (``shelf``: ``items`` / ``records`` only), file order then line order."""
    out = []
    for rel in sorted(result["files"]):
        if shelf is None or rel.startswith(f"library/{shelf}/"):
            out += result["files"][rel].splitlines()
    return out


def tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def recs_in(texts: Iterable[str]) -> set[str]:
    import re

    return set(re.findall(r"r-[0-9a-f]{12}", "\n".join(texts)))
