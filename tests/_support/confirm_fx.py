"""Helpers for the confirm-page tests (WI-08b): a kb-ocr batch built in process from a fixture's
``scenario.json`` and engine files, then audited in process by ``klode_kit.audit.stage.run``.

The batch is laid out as WI-04b leaves it (receipt, ``derived.jsonl``, one page file per done route), with the
fixture's own images as the derived images (identity transform). No model or engine runs: the engine outputs
are the fixture's recorded files, exactly as the WI-08a unit edges do.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
from pathlib import Path
from typing import Mapping

from _support.flows import REPO
from klode_kit.audit import stage
from klode_kit.contract import ids

FIXTURES = REPO / "tests" / "fixtures"
ROUTES = ("vision", "mlx", "claude", "codex")
CFG = "0123456789ab"


def scenario(fx: Path) -> dict:
    return json.loads((fx / "scenario.json").read_text(encoding="utf-8"))


def batch_of(fx: Path) -> str:
    return ids.batch_id(p["der_sha256"] for p in scenario(fx)["pages"])


def make_batch(project: Path, fx: Path, *, engine: str = "engine", seq: int = 1,
               keys: tuple[str, ...] | None = None) -> tuple[str, Path]:
    """Write (or rewrite) the kb-ocr receipt of ``fx`` for attempt ``t<seq>`` read from ``fx/<engine>``.

    A route whose page file is missing in ``fx/<engine>`` (or whose whole directory is missing, as codex in
    ``fx-redo/engine_t2``) gets ``route_missing_page`` for that page."""
    sc = scenario(fx)
    pages = [p for p in sc["pages"] if keys is None or p["key"] in keys]
    batch = batch_of(fx)
    bdir = project / "work" / batch
    bdir.mkdir(parents=True, exist_ok=True)
    derived = []
    for p in pages:
        img = fx / p["image"]
        parent = hashlib.sha256(img.read_bytes()).hexdigest()
        rotate = 0
        if parent != p["der_sha256"]:  # fx-rotated90: the routes read the upright derivative
            img = fx / "derived" / f"{p['key']}.upright.png"
            assert hashlib.sha256(img.read_bytes()).hexdigest() == p["der_sha256"]
            rotate = 90
        derived.append({"der_sha256": p["der_sha256"], "page_id": p["page_id"], "parent": parent,
                        "path": str(img), "transform": {"crop": None, "mask_boxes": [], "rotate": rotate}})
    (bdir / "derived.jsonl").write_text("".join(json.dumps(d) + "\n" for d in derived), encoding="utf-8")
    attempts, out_pages = {}, []
    for p in pages:
        out_pages.append({"key": p["key"], "doc_id": p["doc_id"], "page_id": p["page_id"], "routes": {},
                          "uncertain": []})
    for r in ROUTES:
        src_dir = fx / engine / r
        rid = f"{batch}/{r}/{CFG}/t{seq}"
        adir = f"ocr/{r}/{CFG}.t{seq}"
        attempts[r] = {"run_id": rid, "dir": adir, "status": "done"}
        for op in out_pages:
            src = src_dir / f"{op['key']}.jsonl"
            if src.is_file():
                rel = f"{adir}/{op['key']}.jsonl"
                (bdir / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, bdir / rel)
                op["routes"][r] = {"status": "done", "run_id": rid, "file": rel}
            else:
                op["routes"][r] = {"status": "route_missing_page", "run_id": rid, "tries": 2,
                                   "error": "replay: no page"}
    rec = {"schema": "klode-kit.ocr_batch/1", "batch_id": batch, "routes": list(ROUTES), "pages": out_pages,
           "attempts": attempts}
    (bdir / "receipt.json").write_text(json.dumps(rec), encoding="utf-8")
    return batch, bdir


def audit(project: Path, batch: str) -> int:
    code = stage.run(project, batch, out=io.StringIO())
    assert code in (0, 10), code
    return code


def audited(project: Path, fx: Path, *, engine: str = "engine", seq: int = 1,
            keys: tuple[str, ...] | None = None) -> tuple[str, Path]:
    """``make_batch`` + ``kb-audit``; returns ``(batch, a<N> directory)`` of the newest revision."""
    batch, bdir = make_batch(project, fx, engine=engine, seq=seq, keys=keys)
    audit(project, batch)
    revs = sorted((p for p in (bdir / "audit").iterdir() if p.is_dir()), key=lambda p: int(p.name[1:]))
    return batch, revs[-1]


def candidates(adir: Path) -> list[dict]:
    return [json.loads(x) for x in (adir / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]


def write_record(project: Path, front: Mapping) -> Path:
    """A ``records/_by_id/<rec_id>.md`` as WI-08c leaves it (JSON front matter between ``---`` lines)."""
    path = project / "records" / "_by_id" / f"{front['rec_id']}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\n" + json.dumps(front, ensure_ascii=False, indent=1) + "\n---\n", encoding="utf-8")
    return path
