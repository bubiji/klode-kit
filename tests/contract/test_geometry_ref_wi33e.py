"""WI-33e: kb-ocr runs tesseract on every image page as a geometry-only reference; kb-audit uses it (real-data pilot
P9, decision D-36; design R2-08, R3-05).

Engine boundary: the routes and the reference are replayed through ``KLODE_KIT_ENGINE_REPLAY`` (the reference reads
``<replay>/tesseract/<key>.jsonl`` like a route does; no ``tesseract`` directory = the reference is not run, so every
older recording keeps its exact output). Paired real path: ``tests/cli/test_geometry_ref_wi33e.py`` (real tesseract on
a generated image).

Matrix (lane: contract_replay; real processes on a temporary data project, synthetic page ``fx-labs-1x`` whose vision
boxes are re-labelled ``approx`` - the production state - and whose MLX reads one platelet value differently):

- kb-ocr stores the reference per page under ``ocr/tesseract/<fp>.t<n>/`` with its own attempt receipt, records it in
  the batch receipt as ``geometry_ref`` (page and batch level) and nowhere among the routes (S1);
- a rerun reuses the done attempt, no second call (S5 idempotence); ``--redo`` claims a new attempt (S5);
- it is not a route: ``--routes tesseract`` is a usage error; ``routes``, ``attempts``, ``route_stats``, ``issues``
  and the page ``uncertain`` do not mention it (S3);
- a failing reference never fails the batch: exit 0, status ``done``, a note on the page (S3);
- no ``tesseract`` directory in the replay: no reference call, no ``geometry_ref`` key, no ``ocr/tesseract`` (S2);
- kb-audit: with the reference every field of the corroborated page has a column and no geometry doubt; without it
  every field has none (the pilot); values, votes, verdicts and the record status are the same in both (S6); the
  reference is in neither ``runs.json`` nor ``cells.jsonl`` candidates (S3).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from _support.kb_ocr import (FIXTURES, attempt_dirs, jsonl, kb_ocr_env, log_calls, make_inbox, only_batch, receipt,
                             run_kb_ocr)
from klode_kit.contract import schema

pytestmark = pytest.mark.contract_replay

LABS = FIXTURES / "fx-labs-1x"
GEO_REASONS = {"no_geometry", "no_bbox_crop"}


def write_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def replay_dir(dest: Path, *, reference: bool, fail: str | None = None) -> Path:
    """fx-labs-1x engine rows, vision boxes approx (as Vision gives them), MLX reads one platelet value differently;
    ``reference``: the vision boxes as exact tesseract word boxes."""
    shutil.copytree(LABS / "engine", dest)
    vision = [json.loads(x) for x in (dest / "vision" / "p001.jsonl").read_text(encoding="utf-8").splitlines()]
    exact = json.loads(json.dumps(vision))
    for line in vision:
        for tok in line["tokens"] or ():
            tok["geometry"] = "approx"
    write_rows(dest / "vision" / "p001.jsonl", vision)
    mlx = [json.loads(x) for x in (dest / "mlx" / "p001.jsonl").read_text(encoding="utf-8").splitlines()]
    for line in mlx:
        if line["text"].startswith("血小板"):
            line["text"] = line["text"].replace("210", "270")
    write_rows(dest / "mlx" / "p001.jsonl", mlx)
    if reference:
        for line in exact:
            line["engine"] = "tesseract"
        write_rows(dest / "tesseract" / "p001.jsonl", exact)
    if fail is not None:
        (dest / "tesseract").mkdir(exist_ok=True)
        (dest / "tesseract" / "p001.fail").write_text(fail, encoding="utf-8")
    return dest


def kb_ocr(tmp_path: Path, replay: Path, *args: str, project: Path | None = None):
    project = project or tmp_path / "proj"
    project.mkdir(exist_ok=True)
    inbox = make_inbox(tmp_path / "inbox", [LABS / "images" / "p001.png"])
    log = tmp_path / "calls.jsonl"
    res = run_kb_ocr(project, inbox, kb_ocr_env(replay, log), *args)
    return project, res, log


def audit(project: Path, replay: Path, tmp_path: Path) -> dict:
    batch = only_batch(project)
    res = subprocess.run([sys.executable, "-m", "klode_kit", "kb-audit", "--batch", batch.name, "--project",
                          str(project)], env=kb_ocr_env(replay, tmp_path / "calls.jsonl"), capture_output=True,
                         timeout=120, cwd=str(project))
    assert res.returncode == 10, res.stderr.decode()
    adir = batch / "audit" / "a1"
    return {"candidates": jsonl(adir / "candidates.jsonl"), "cells": jsonl(adir / "cells.jsonl"),
            "runs": json.loads((adir / "runs.json").read_text(encoding="utf-8"))}


# --------------------------------------------------------------------------- kb-ocr


def test_reference_is_stored_per_page_with_its_own_receipt(tmp_path):
    project, res, log = kb_ocr(tmp_path, replay_dir(tmp_path / "replay", reference=True))
    assert res.returncode == 0, res.stderr.decode()
    batch, rec = only_batch(project), receipt(project)
    (attempt,) = attempt_dirs(batch, "tesseract")
    rows = jsonl(attempt / "p001.jsonl")
    assert rows and {r["engine"] for r in rows} == {"tesseract"}
    assert all(schema.issues("ocr_line", r) == [] for r in rows)
    assert all(t["geometry"] == "exact" for r in rows for t in r["tokens"])
    step = json.loads((attempt / "receipt.json").read_text(encoding="utf-8"))
    schema.validate("receipt", step)
    assert step["status"] == "done" and step["step"] == "ocr_tesseract"
    page = rec["pages"][0]
    assert page["geometry_ref"] == {"status": "done", "run_id": f"{batch.name}/tesseract/"
                                    f"{attempt.name.split('.')[0]}/t1", "file": f"ocr/tesseract/{attempt.name}/p001.jsonl"}
    assert rec["geometry_ref"]["status"] == "done" and rec["geometry_ref"]["dir"] == f"ocr/tesseract/{attempt.name}"
    assert [c["tool"] for c in log_calls(log) if c["tool"] == "tesseract"] == ["tesseract"]


def test_reference_is_not_a_route(tmp_path):
    project, res, _ = kb_ocr(tmp_path, replay_dir(tmp_path / "replay", reference=True))
    assert res.returncode == 0
    rec = receipt(project)
    assert rec["routes"] == ["vision", "mlx", "claude", "codex"]
    assert "tesseract" not in rec["attempts"] and "tesseract" not in rec["route_stats"]
    assert "tesseract" not in rec["pages"][0]["routes"]
    assert rec["issues"] == [] and rec["status"] == "done" and rec["pages"][0]["uncertain"] == []
    outputs = {o["path"] for o in json.loads((only_batch(project) / "receipts" / "ocr.json")
                                             .read_text(encoding="utf-8"))["outputs"]}
    assert any(p.startswith("ocr/tesseract/") for p in outputs)


def test_routes_option_refuses_tesseract(tmp_path):
    project, res, _ = kb_ocr(tmp_path, replay_dir(tmp_path / "replay", reference=True), "--routes", "vision,tesseract")
    assert res.returncode == 2 and b"tesseract" in res.stderr
    assert not (project / "work").exists()


def test_rerun_reuses_the_attempt_and_redo_claims_a_new_one(tmp_path):
    replay = replay_dir(tmp_path / "replay", reference=True)
    project, res, log = kb_ocr(tmp_path, replay)
    assert res.returncode == 0
    first = receipt(project)["pages"][0]["geometry_ref"]
    _, res, _ = kb_ocr(tmp_path, replay, project=project)
    assert res.returncode == 0 and receipt(project)["pages"][0]["geometry_ref"] == first
    assert len([c for c in log_calls(log) if c["tool"] == "tesseract"]) == 1
    _, res, _ = kb_ocr(tmp_path, replay, "--redo", project=project)
    assert res.returncode == 0
    names = [p.name for p in attempt_dirs(only_batch(project), "tesseract")]
    assert len(names) == 2 and receipt(project)["pages"][0]["geometry_ref"]["run_id"].endswith("/t2")


def test_a_failing_reference_never_fails_the_batch(tmp_path):
    project, res, _ = kb_ocr(tmp_path, replay_dir(tmp_path / "replay", reference=True, fail=""))
    assert res.returncode == 0, res.stderr.decode()
    rec = receipt(project)
    assert rec["status"] == "done" and rec["issues"] == [] and rec["pages"][0]["uncertain"] == []
    ref = rec["pages"][0]["geometry_ref"]
    assert ref["status"] == "route_failed" and ref["error"] and "file" not in ref
    assert b"tesseract" in res.stderr


def test_no_replay_directory_means_no_reference(tmp_path):
    """Older recordings have no ``tesseract`` directory: nothing is called, nothing in the receipt changes."""
    project, res, log = kb_ocr(tmp_path, replay_dir(tmp_path / "replay", reference=False))
    assert res.returncode == 0
    rec = receipt(project)
    assert "geometry_ref" not in rec and "geometry_ref" not in rec["pages"][0]
    assert attempt_dirs(only_batch(project), "tesseract") == []
    assert [c for c in log_calls(log) if c["tool"] == "tesseract"] == []


# --------------------------------------------------------------------------- kb-audit


def field_view(fields: list[dict]) -> list[tuple]:
    return [(f["field_id"], f["verdict"], f["recommended"], f["votes"], f["missing"]) for f in fields]


def reasons(f: dict) -> set[str]:
    return set(f["uncertain"]) | set(f["notes"])


def test_audit_without_the_reference_has_no_geometry_anywhere(tmp_path):
    """The pilot (P9): approx Vision boxes alone, every field is a geometry doubt."""
    replay = replay_dir(tmp_path / "replay", reference=False)
    project, res, _ = kb_ocr(tmp_path, replay)
    assert res.returncode == 0
    out = audit(project, replay, tmp_path)
    fields = out["candidates"][0]["fields"]
    assert fields and all("no_geometry" in reasons(f) for f in fields)
    assert all(re.fullmatch(r"[a-z]+-\d+", c["cell"]["col"]) for c in out["cells"])


def test_audit_with_the_reference_gives_geometry_and_changes_nothing_else(tmp_path):
    without_replay = replay_dir(tmp_path / "r0", reference=False)
    with_replay = replay_dir(tmp_path / "r1", reference=True)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    p0, res0, _ = kb_ocr(tmp_path / "a", without_replay)
    p1, res1, _ = kb_ocr(tmp_path / "b", with_replay)
    assert res0.returncode == 0 and res1.returncode == 0
    a0 = audit(p0, without_replay, tmp_path / "a")
    a1 = audit(p1, with_replay, tmp_path / "b")
    f0, f1 = a0["candidates"][0]["fields"], a1["candidates"][0]["fields"]
    # nothing but geometry changed: ids, verdicts, votes, recommended values, record status
    assert field_view(f1) == field_view(f0)
    assert a1["candidates"][0]["status"] == a0["candidates"][0]["status"] == "pending_confirm"
    assert {f["verdict"] for f in f1} >= {"agree", "majority"}
    # geometry reasons are gone, and only they
    for g1, g0 in zip(f1, f0):
        assert reasons(g1) <= reasons(g0)
        assert reasons(g0) - reasons(g1) <= GEO_REASONS
        assert not reasons(g1) & GEO_REASONS
    assert any(reasons(g0) & GEO_REASONS for g0 in f0)
    # the cells carry a real column; the reference is nowhere a candidate or a run
    assert all(c["cell"]["col"].startswith("c") for c in a1["cells"])
    assert all("tesseract" not in c["candidates"] and "tesseract" not in c["prov"] for c in a1["cells"])
    assert "tesseract" not in a1["runs"] and a1["runs"].keys() == a0["runs"].keys()
    # the disputed platelet cell gets a crop around the cell, not the whole row
    platelet = next(f for f in f1 if f["verdict"] == "majority")
    assert "210" in str(platelet["votes"]) and "270" in str(platelet["votes"]) and platelet["crop"]
