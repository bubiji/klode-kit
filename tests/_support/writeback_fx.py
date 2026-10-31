"""Helpers for the writeback tests (WI-08c; matrix 08c-*).

A data project is built in process the way the earlier stages leave it: the kb-ocr batch of a fixture
(``confirm_fx.make_batch``, completed here with ``manifest.jsonl``, run ids stamped into the route lines as the
``ocr_line`` contract allows, and the page's ``record_type`` / ``date`` from the fixture scenario), audited by
``kb-audit`` (``stage.run``), its confirm page written by ``confirm_page.write`` and exported by the Python
exporter ``confirm_page.export_choices``. Nothing here writes ``records/``: that is the writeback's alone.

The writeback runs either in process through its public function ``writeback.run`` (fast; property tests) or as
the real CLI ``python -m klode_kit kb-audit --writeback`` (crash, concurrency and seam tests).
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

from _support.confirm_fx import FIXTURES, audit, make_batch, scenario
from _support.kb_ocr import tree, tree_sha
from klode_kit.audit import confirm_page as CP

U8 = FIXTURES / "fx-u8"
LABS3 = FIXTURES / "fx-u1-3labs"
REDO = FIXTURES / "fx-redo"
APPROX = FIXTURES / "fx-approx-only"
ROUTES = ("vision", "mlx", "claude", "codex")
TS = re.compile(rb"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})")

__all__ = ["tree", "tree_sha"]


# --------------------------------------------------------------------------- building the project


def _stamp(path: Path, run_id: str, engine: str | None = None) -> None:
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    for r in rows:
        r["run_id"] = run_id
        if engine is not None:
            r["engine"] = engine
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def batch(project: Path, fx: Path, *, engine: str = "engine", seq: int = 1, tesseract: bool = False) -> tuple[str, Path]:
    """The kb-ocr batch of ``fx`` (attempt ``t<seq>`` read from ``fx/<engine>``) as WI-04b leaves it."""
    b, bdir = make_batch(project, fx, engine=engine, seq=seq)
    pages = {p["key"]: p for p in scenario(fx)["pages"]}
    rec = json.loads((bdir / "receipt.json").read_text(encoding="utf-8"))
    if tesseract:  # a geometry-only route: tesseract reads what vision reads (it never votes)
        rid = f"{b}/tesseract/0123456789ab/t{seq}"
        adir = f"ocr/tesseract/0123456789ab.t{seq}"
        rec["routes"].append("tesseract")
        rec["attempts"]["tesseract"] = {"run_id": rid, "dir": adir, "status": "done"}
        for page in rec["pages"]:
            src = page["routes"]["vision"].get("file")
            if src:
                dst = bdir / adir / f"{page['key']}.jsonl"
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes((bdir / src).read_bytes())
                _stamp(dst, rid, engine="tesseract")
                page["routes"]["tesseract"] = {"status": "done", "run_id": rid, "file": f"{adir}/{page['key']}.jsonl"}
    for page in rec["pages"]:
        page["record_type"] = pages[page["key"]]["record_type"]
        page["date"] = pages[page["key"]]["date"]
        for route, res in page["routes"].items():
            if res.get("file") and route != "tesseract":
                _stamp(bdir / res["file"], res["run_id"])
    (bdir / "receipt.json").write_text(json.dumps(rec), encoding="utf-8")
    derived = [json.loads(x) for x in (bdir / "derived.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    manifest = []
    for d in derived:
        key = next(k for k, p in pages.items() if p["page_id"] == d["page_id"])
        manifest.append({"doc_id": d["page_id"].split("/")[0], "img_sha256": d["parent"], "kind": "image",
                         "path": str(fx / pages[key]["image"])})
    (bdir / "manifest.jsonl").write_text("".join(json.dumps(m) + "\n" for m in manifest), encoding="utf-8")
    return b, bdir


def revisions(bdir: Path) -> list[Path]:
    root = bdir / "audit"
    return sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: int(p.name[1:])) if root.is_dir() else []


def audited(project: Path, fx: Path, **kw) -> tuple[str, Path]:
    """``batch`` + ``kb-audit``; ``(batch_id, newest a<N>)``."""
    b, bdir = batch(project, fx, **kw)
    audit(project, b)
    return b, revisions(bdir)[-1]


def page(project: Path, b: str, adir: Path) -> bytes:
    """The confirm page of ``adir`` (written once on first use)."""
    path = adir / "confirm.html"
    if not path.exists():
        CP.write(project, b, int(adir.name[1:]))
    return path.read_bytes()


def candidates(adir: Path) -> list[dict]:
    return [json.loads(x) for x in (adir / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]


def cells(adir: Path) -> dict[str, dict]:
    rows = [json.loads(x) for x in (adir / "cells.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    return {c["field_id"]: c for c in rows}


def shown(f: Mapping[str, Any]) -> bool:
    """A field to confirm (the confirm page's rule)."""
    return f.get("verdict") != "agree" or bool(f.get("uncertain")) or bool(f.get("missing"))


def to_confirm(adir: Path) -> dict[str, list[str]]:
    """``{rec_id: [field_id to confirm, ...]}`` of the clickable records of ``adir`` (page order)."""
    out = {}
    for c in candidates(adir):
        if any(u.get("scope") == "page" and u.get("reason") != "extra_values" for u in c["uncertain"]):  # D-37
            continue
        fids = [f["field_id"] for f in c["fields"] if shown(f)] + [p["field_id"] for p in c.get("page_items", [])]
        if fids:
            out[c["rec_id"]] = fids
    return out


def route_pick(adir: Path, fid: str, *, avoid: str | None = None) -> str:
    """A voting route with a value for ``fid``: one holding the recommended value when there is one, else the first
    route with a value (``avoid`` skips one value)."""
    cell = cells(adir)[fid]
    field = next(f for c in candidates(adir) for f in c["fields"] if f["field_id"] == fid)
    seen = {e: (cell["candidates"].get(e) or {}).get("value") for e in ROUTES}
    options = [e for e in ROUTES if seen[e] is not None and seen[e] != avoid]
    rec = field.get("recommended")
    best = [e for e in options if seen[e] == rec]
    return (best or options)[0]


def picks(adir: Path, rec_ids: Iterable[str] | None = None) -> list[dict]:
    """Route clicks for every field to confirm of ``rec_ids`` (default: every clickable record)."""
    todo = to_confirm(adir)
    wanted = list(todo) if rec_ids is None else list(rec_ids)
    return [{"field_id": fid, "kind": "route", "route": route_pick(adir, fid)} for r in wanted for fid in todo[r]]


def export(project: Path, b: str, adir: Path, selections: Iterable[Mapping[str, Any]]) -> dict:
    return CP.export_choices(page(project, b, adir), list(selections))


def write_choices(path: Path, choices: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(choices, ensure_ascii=False), encoding="utf-8")
    return path


def forged(choices: Mapping[str, Any]) -> dict:
    return copy.deepcopy(dict(choices))


# --------------------------------------------------------------------------- running the writeback


def submit(project: Path, choices: Mapping[str, Any] | None, where: Path, *, batch_id: str | None = None):
    """In-process ``writeback.run``; ``(exit code, receipt printed on stdout, stderr text)``. The choices file is
    written under ``where`` (outside the data project)."""
    from klode_kit.audit import writeback as WB

    path = write_choices(where / "choices.json", choices) if choices is not None else None
    err, out = io.StringIO(), io.StringIO()
    code = WB.run(project, batch_id, path, out=err, stdout=out)
    lines = [x for x in out.getvalue().splitlines() if x.strip()]
    return code, (json.loads(lines[-1]) if lines else None), err.getvalue()


def cli_argv(project: Path, choices: Path | None = None, batch_id: str | None = None) -> list[str]:
    argv = [sys.executable, "-m", "klode_kit", "kb-audit", "--project", str(project), "--writeback"]
    if choices is not None:
        argv.append(str(choices))
    if batch_id is not None:
        argv += ["--batch", batch_id]
    return argv


def cli(project: Path, choices: Path | None, env: Mapping[str, str], *, batch_id: str | None = None,
        timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cli_argv(project, choices, batch_id), env=dict(env), capture_output=True, timeout=timeout)


# --------------------------------------------------------------------------- reading the products


def _front(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), path
    end = text.find("\n---", 4)
    assert end > 0, path
    return json.loads(text[4:end])


def record_path(project: Path, rec_id: str) -> Path:
    return project / "records" / "_by_id" / f"{rec_id}.md"


def body_path(project: Path, rec_id: str, rev: int) -> Path:
    return project / "records" / "_rev" / f"{rec_id}.r{rev}.md"


def front(project: Path, rec_id: str) -> dict | None:
    path = record_path(project, rec_id)
    return _front(path) if path.exists() else None


def body(project: Path, rec_id: str, rev: int) -> dict:
    return _front(body_path(project, rec_id, rev))


def jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def fixes(adir: Path) -> list[dict]:
    return jsonl(adir / "fixes.jsonl")


def commits(adir: Path) -> list[dict]:
    return jsonl(adir / "commits.jsonl")


def by_id_bytes(project: Path) -> dict[str, bytes]:
    root = project / "records" / "_by_id"
    return {p.name: p.read_bytes() for p in sorted(root.glob("*"))} if root.is_dir() else {}


def normalised(files: Mapping[str, bytes]) -> dict[str, bytes]:
    """Wall-clock stamps replaced, so two runs at different seconds compare byte for byte."""
    return {k: TS.sub(b"<ts>", v) for k, v in files.items()}


def state(project: Path, b: str) -> dict[str, bytes]:
    """Every file the writeback owns (records/** and the audit revisions' fixes/commits), normalised; the per-run
    receipt log is left out (one line per run, by design)."""
    out = {f"records/{k}": v for k, v in tree(project / "records").items()} if (project / "records").is_dir() else {}
    for adir in revisions(project / "work" / b):
        for name in ("fixes.jsonl", "commits.jsonl"):
            p = adir / name
            if p.exists():
                out[f"{adir.name}/{name}"] = p.read_bytes()
    return normalised(out)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
