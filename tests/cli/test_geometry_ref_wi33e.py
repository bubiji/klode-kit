"""WI-33e: the real path of the tesseract geometry reference - paired with the replayed cases of
tests/contract/test_geometry_ref_wi33e.py. Real tesseract and (macOS) real Vision on the MANIFEST-registered synthetic
page ``fx-labs-1x``; missing engines FAIL (the lane never skips).

- kb-ocr stores real tesseract word boxes of the page (exact, inside the image) beside the Vision route, and the batch
  stays ``done``;
- kb-audit then gives the Vision cells a column and the Vision route as geometry, no cell has tesseract as a reading;
- tesseract not on PATH: the batch is still ``done`` (exit 0) and the page carries the failure note.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from _support.flows import base_env
from _support.kb_ocr import FIXTURES, attempt_dirs, jsonl, make_inbox, only_batch, receipt, run_kb_ocr
from klode_kit.contract import schema
from klode_kit.ocr import geometry as G

pytestmark = [pytest.mark.cli_integration, pytest.mark.macos]

PAGE = FIXTURES / "fx-labs-1x" / "images" / "p001.png"


def real_env(log, **extra):
    env = base_env({"KLODE_KIT_FAKE_LOG": str(log), **extra})
    env.pop("KLODE_KIT_ENGINE_REPLAY", None)
    return env


def test_real_tesseract_reference_and_audit_geometry(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    inbox = make_inbox(project / "_inbox", [PAGE])
    env = real_env(tmp_path / "calls.jsonl")
    res = run_kb_ocr(project, inbox, env, "--routes", "vision", timeout=300)
    assert res.returncode == 0, res.stderr.decode()
    rec, batch = receipt(project), only_batch(project)
    ref = rec["pages"][0]["geometry_ref"]
    assert ref["status"] == "done" and rec["status"] == "done" and rec["routes"] == ["vision"]
    rows = jsonl(batch / ref["file"])
    size = G.image_size(project / rec["pages"][0]["derived"])
    assert rows and {r["engine"] for r in rows} == {"tesseract"}
    for row in rows:
        schema.validate("ocr_line", row)
        assert G.in_bounds(row["bbox_px"], size) and row["tokens"]
        assert all(t["geometry"] == "exact" for t in row["tokens"])
    assert "血红蛋白" in "".join(r["text"] for r in rows) or "128" in " ".join(r["text"] for r in rows)
    assert len(attempt_dirs(batch, "tesseract")) == 1

    audit = subprocess.run([sys.executable, "-m", "klode_kit", "kb-audit", "--batch", batch.name, "--project",
                            str(project)], env=env, capture_output=True, timeout=300, cwd=str(project))
    assert audit.returncode in (0, 10), audit.stderr.decode()
    adir = batch / "audit" / "a1"
    cells = jsonl(adir / "cells.jsonl")
    assert cells and all(set(c["candidates"]) == {"vision"} and set(c["prov"]) == {"vision"} for c in cells)
    assert "tesseract" not in json.loads((adir / "runs.json").read_text(encoding="utf-8"))
    assert sum(1 for c in cells if c["cell"]["col"].startswith("c")) >= len(cells) // 2
    fields = [f for cand in jsonl(adir / "candidates.jsonl") for f in cand["fields"]]
    free = [f for f in fields if "no_geometry" not in set(f["uncertain"]) | set(f["notes"])]
    assert len(free) >= len(fields) // 2


def test_tesseract_missing_only_adds_a_note(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    inbox = make_inbox(project / "_inbox", [PAGE])
    env = real_env(tmp_path / "calls.jsonl", PATH="/nonexistent-klode-kit-bin")
    res = run_kb_ocr(project, inbox, env, "--routes", "vision", timeout=300)
    assert res.returncode == 0, res.stderr.decode()
    rec = receipt(project)
    page = rec["pages"][0]
    assert rec["status"] == "done" and rec["issues"] == [] and page["uncertain"] == []
    assert page["geometry_ref"]["status"] in ("route_failed", "route_missing_page") and page["geometry_ref"]["error"]
    assert "file" not in page["geometry_ref"]
