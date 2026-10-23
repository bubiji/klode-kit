"""WI-08a edges: kb-audit in process on hand-built kb-ocr batches (guards, resume after a killed run, the
uncertain log across revisions) and the gate's guard clauses. Added while implementing; the matrix rows are in
tests/unit/test_audit_gate.py and tests/contract/test_audit_stage.py.
"""

from __future__ import annotations

import io
import json
import shutil
import stat
from pathlib import Path

import pytest

from _support.flows import REPO
from klode_kit.audit import align as A
from klode_kit.audit import compare as C
from klode_kit.audit import gate as G
from klode_kit.audit import stage

pytestmark = pytest.mark.unit

FX = REPO / "tests" / "fixtures" / "fx-redo"
BATCH = "eb2b28a29018"
ROUTES = ("vision", "mlx", "claude", "codex")
PAGE = {"key": "p001", "doc_id": "d-e9f04e9657b8", "page_id": "d-e9f04e9657b8/p001"}
DER = "e9f04e9657b807e822e220b1cb33ab5fa6052e3ac53fcce4b27fd950449a1fdc"


def run_id(route: str, seq: int = 1) -> str:
    return f"{BATCH}/{route}/0123456789ab/t{seq}"


def make_batch(project: Path, *, states: dict | None = None, seq: dict | None = None,
               engine: str = "engine_t3", text_page: bool = False) -> Path:
    """A kb-ocr batch as WI-04b leaves it: receipt, derived.jsonl and one page file per done route."""
    states, seq = states or {}, seq or {}
    bdir = project / "work" / BATCH
    bdir.mkdir(parents=True, exist_ok=True)
    (bdir / "derived.jsonl").write_text(json.dumps(
        {"der_sha256": DER, "page_id": PAGE["page_id"], "parent": DER, "path": str(FX / "images" / "p001.png"),
         "transform": {"crop": None, "mask_boxes": [], "rotate": 0}}) + "\n", encoding="utf-8")
    routes, attempts = {}, {}
    for r in ROUTES:
        state = states.get(r, "done")
        if state == "disabled":
            routes[r] = {"status": "route_failed", "error": f"route_disabled: {r} is disabled"}
            continue
        rid = run_id(r, seq.get(r, 1))
        attempts[r] = {"run_id": rid, "dir": f"ocr/{r}/0123456789ab.t{seq.get(r, 1)}", "status": "done"}
        if state == "done":
            rel = f"ocr/{r}/0123456789ab.t{seq.get(r, 1)}/p001.jsonl"
            (bdir / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(FX / engine / r / "p001.jsonl", bdir / rel)
            routes[r] = {"status": "done", "run_id": rid, "file": rel}
        else:
            routes[r] = {"status": state, "run_id": rid, "tries": 2, "error": "replay: no page"}
    page = {**PAGE, "routes": routes, "uncertain": []}
    if text_page:
        page = {**PAGE, "text_class": "text", "routes": {r: {"status": "skipped", "reason": "text_layer"}
                                                         for r in ROUTES}, "uncertain": []}
    rec = {"schema": "klode-kit.ocr_batch/1", "batch_id": BATCH, "routes": list(ROUTES), "pages": [page],
           "attempts": attempts}
    (bdir / "receipt.json").write_text(json.dumps(rec), encoding="utf-8")
    return bdir


def audit(project: Path) -> tuple[int, str]:
    buf = io.StringIO()
    return stage.run(project, BATCH, out=buf), buf.getvalue()


def lines(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]


def test_all_agree_verified_and_frozen(tmp_path):
    bdir = make_batch(tmp_path)
    code, out = audit(tmp_path)
    assert code == 0, out
    a1 = bdir / "audit" / "a1"
    (cand,) = lines(a1 / "candidates.jsonl")
    assert (cand["status"], cand["verified_by"]) == ("verified", "auto")
    assert json.loads((a1 / "runs.json").read_text()) == {r: run_id(r) for r in ROUTES}
    for name in ("runs.json", "cells.jsonl", "candidates.jsonl"):
        assert not (a1 / name).stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH), name
    assert not (bdir / "audit" / "uncertain_log.jsonl").exists()
    assert not (tmp_path / "records").exists()


def test_main_guards(tmp_path, capsys):
    assert stage.main(["--batch", "not-a-batch", "--project", str(tmp_path)]) == 2
    assert stage.main(["--project", str(tmp_path)]) == 2
    assert stage.main(["--batch", BATCH, "--project", str(tmp_path)]) == 2
    assert "run kb-ocr first" in capsys.readouterr().err
    make_batch(tmp_path)
    assert stage.main(["--batch", BATCH, "--project", str(tmp_path)]) == 0


def test_receipt_of_another_batch_exit30(tmp_path):
    bdir = make_batch(tmp_path)
    rec = json.loads((bdir / "receipt.json").read_text())
    rec["batch_id"] = "000000000000"
    (bdir / "receipt.json").write_text(json.dumps(rec))
    code, out = audit(tmp_path)
    assert code == 30 and "not the kb-ocr receipt" in out
    assert not (bdir / "audit").exists()


def test_missing_run_file_exit30(tmp_path):
    bdir = make_batch(tmp_path)
    shutil.move(bdir / "ocr" / "codex" / "0123456789ab.t1" / "p001.jsonl", tmp_path / "moved.jsonl")
    code, out = audit(tmp_path)
    assert code == 30 and "run file of route codex is missing" in out
    assert not (bdir / "audit" / "a1" / "cells.jsonl").exists()


def test_text_layer_only_nothing_to_audit(tmp_path):
    bdir = make_batch(tmp_path, text_page=True)
    code, out = audit(tmp_path)
    assert code == 0 and "nothing to audit" in out
    assert not (bdir / "audit").exists()


def test_resume_claimed_revision_without_runs(tmp_path):
    """A run killed right after mkdir a1 left it empty: the next run continues a1, no a2."""
    bdir = make_batch(tmp_path)
    (bdir / "audit" / "a1").mkdir(parents=True)
    assert audit(tmp_path)[0] == 0
    assert sorted(p.name for p in (bdir / "audit").iterdir() if p.is_dir()) == ["a1"]
    assert (bdir / "audit" / "a1" / "candidates.jsonl").is_file()


def test_resume_after_cells_written(tmp_path):
    """A run killed after cells.jsonl: the rerun writes candidates.jsonl and leaves cells.jsonl's bytes."""
    bdir = make_batch(tmp_path)
    assert audit(tmp_path)[0] == 0
    a1 = bdir / "audit" / "a1"
    cells = (a1 / "cells.jsonl").read_bytes()
    shutil.move(a1 / "candidates.jsonl", tmp_path / "cand.jsonl")
    assert audit(tmp_path)[0] == 0
    assert (a1 / "cells.jsonl").read_bytes() == cells
    assert (a1 / "candidates.jsonl").read_bytes() == (tmp_path / "cand.jsonl").read_bytes()


def test_disabled_route_null_run_and_disputed(tmp_path):
    bdir = make_batch(tmp_path, states={"codex": "disabled"})
    code, _ = audit(tmp_path)
    assert code == 10
    a1 = bdir / "audit" / "a1"
    assert json.loads((a1 / "runs.json").read_text())["codex"] is None
    (cand,) = lines(a1 / "candidates.jsonl")
    assert cand["status"] == "disputed"
    assert {(u["reason"], u.get("engine")) for u in cand["uncertain"] if u["scope"] == "page"} == {
        ("route_disabled", "codex")}
    assert (a1 / "crops").is_dir()
    assert any(f["crop"] for f in cand["fields"])
    assert audit(tmp_path)[0] == 10
    assert sorted(p.name for p in (bdir / "audit").iterdir() if p.is_dir()) == ["a1"]


def test_log_clears_entries_a_new_revision_no_longer_raises(tmp_path):
    bdir = make_batch(tmp_path, states={"codex": "route_missing_page"})
    assert audit(tmp_path)[0] == 10
    log = stage.UncertainLog(bdir / "audit" / "uncertain_log.jsonl")
    first = log.path.read_bytes()
    missing_id = f"{PAGE['page_id']}:codex:route_missing_page"
    assert missing_id in log.open_entries()

    make_batch(tmp_path, seq={"codex": 2})
    assert audit(tmp_path)[0] == 0
    assert log.path.read_bytes().startswith(first)
    assert log.open_entries() == {}
    clears = [x for x in log.lines() if x["op"] == "clear"]
    assert missing_id in {x["id"] for x in clears}
    assert all(x["cleared_by"] == "auto" and x["audit_rev"] == f"{BATCH}/a2" for x in clears)


def test_log_guards(tmp_path):
    log = stage.UncertainLog(tmp_path / "log.jsonl")
    with pytest.raises(ValueError):
        log.add([{"reason": "tie"}], audit_rev=f"{BATCH}/a1", at="2026-10-23T00:00:00Z")
    log.add([{"id": "x", "reason": "tie"}], audit_rev=f"{BATCH}/a1", at="2026-10-23T00:00:00Z")
    before = log.path.read_bytes()
    with pytest.raises(ValueError):
        log.clear(["x"], by="robot", at="2026-10-23T00:00:01Z", evidence="e")
    with pytest.raises(ValueError):
        log.clear(["x"], by="human", at="2026-10-23T00:00:01Z", evidence="")
    assert log.path.read_bytes() == before


def test_audit_dir(tmp_path):
    assert stage.audit_dir(tmp_path, BATCH, 3) == tmp_path / "work" / BATCH / "audit" / "a3"
    with pytest.raises(ValueError):
        stage.audit_dir(tmp_path, "nope", 1)


# --------------------------------------------------------------------------- gate guards


def test_gate_guards():
    pid = PAGE["page_id"]
    with pytest.raises(ValueError):
        G.entry(None, page_id=pid)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        G.entry("missing_cell:scanner", page_id=pid, field_id="r-0123456789ab/x")
    with pytest.raises(ValueError):
        G.entry("unaligned:codex", page_id=pid)
    with pytest.raises(ValueError):
        G.entry("tie", page_id=pid)
    empty = C.compare(A.Alignment(None, (), {}, (), ()), routes=ROUTES)
    with pytest.raises(ValueError):
        G.judge(empty, rec_id="r-0123456789ab", page_id=pid,
                page_uncertain=[G.entry("tie", page_id=pid, field_id="r-0123456789ab/x")])
    cleared = {**G.entry("route_missing_page:codex", page_id=pid), "cleared_by": "auto"}
    v = G.judge(empty, rec_id="r-0123456789ab", page_id=pid, page_uncertain=[cleared])
    assert v.status == G.PENDING and v.uncertain == ()
