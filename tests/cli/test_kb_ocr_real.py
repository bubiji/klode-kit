"""WI-04b: kb-ocr with the real local engines (no replay seam) - the real path paired with the replayed
run_batch cases of tests/contract/test_run_batch.py. Missing engines FAIL (the lane never skips).

Cloud routes in this WI are placeholders (WI-06a/b): they must be route_failed with zero calls, never silent.
"""

from __future__ import annotations

import pytest

from _support.flows import base_env
from _support.kb_ocr import FIXTURES, jsonl, log_calls, make_inbox, only_batch, receipt, run_kb_ocr, sha256
from klode_kit.contract import schema
from klode_kit.ocr import geometry as G

pytestmark = pytest.mark.cli_integration


def real_env(log):
    env = base_env({"KLODE_KIT_FAKE_LOG": str(log)})
    env.pop("KLODE_KIT_ENGINE_REPLAY", None)
    return env


def check_route(project, engine, allowed):
    rec = receipt(project)
    batch = only_batch(project)
    for page in rec["pages"]:
        route = page["routes"][engine]
        assert route["status"] == "done", route
        size = G.image_size(project / page["derived"] if not page["derived"].startswith("/") else page["derived"])
        rows = jsonl(batch / route["file"])
        assert rows
        for row in rows:
            schema.validate("ocr_line", row)
            assert row["engine"] in allowed
            assert (row["page_id"], row["der_sha256"]) == (page["page_id"], page["der_sha256"])
            if row["bbox_px"] is not None:
                assert G.in_bounds(row["bbox_px"], size)
    return rec


@pytest.mark.macos
def test_real_vision_route_on_rotated_page(tmp_path):
    """fx-rotated90 through real Vision: the derived upright page is what the engine reads (rotate 90, boxes
    inside the upright page, rows bound to its der_sha256); the cloud placeholders make zero calls."""
    project = tmp_path / "proj"
    project.mkdir()
    inbox = make_inbox(project / "_inbox", [FIXTURES / "fx-rotated90" / "images" / "p001.png"])
    log = tmp_path / "calls.jsonl"
    res = run_kb_ocr(project, inbox, real_env(log), "--routes", "vision,claude,codex", timeout=300)
    assert res.returncode == 0, res.stderr.decode()
    rec = check_route(project, "vision", {"vision"})
    page = rec["pages"][0]
    assert page["rotate"] == 90
    assert page["der_sha256"] == sha256((FIXTURES / "fx-rotated90" / "derived" / "p001.upright.png").read_bytes())
    text = "\n".join(r["text"] for r in jsonl(only_batch(project) / page["routes"]["vision"]["file"]))
    assert "血红蛋白" in text
    for engine in ("claude", "codex"):
        assert page["routes"][engine]["status"] == "route_failed"
        assert page["routes"][engine]["error"].startswith(("route_unavailable", "route_disabled"))
    assert log_calls(log) == []


@pytest.mark.macos
@pytest.mark.macos_mlx
def test_real_mlx_route(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    inbox = make_inbox(project / "_inbox", [FIXTURES / "fx-labs-1x" / "images" / "p001.png"])
    res = run_kb_ocr(project, inbox, real_env(tmp_path / "calls.jsonl"), "--routes", "mlx", timeout=900)
    assert res.returncode == 0, res.stderr.decode()
    check_route(project, "mlx", {"mlx", "tesseract"})
