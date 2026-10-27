"""WI-05b: a kb-ocr rerun keeps the masked-image registrations in ``work/<batch>/derived.jsonl``.

kb-ocr (WI-04b) rewrites derived.jsonl from its own plan on every run; the masked lines added by
``mask.mask_batch`` (WI-05b) must survive that, unchanged and after the upright lines, or the cloud-route OCR
lines lose their provenance. Engines replayed (``KLODE_KIT_ENGINE_REPLAY``), as in the 04b flow.
"""

from __future__ import annotations

import json

import pytest

from _support.flows import REPO, base_env, run_module
from _support.mask_replay import ReplayVision
from klode_kit.audit import mask

pytestmark = pytest.mark.contract_replay

FIXTURE = REPO / "tests" / "fixtures" / "fx-labs-1x"


def kb_ocr(project, tmp_path, routes="vision,mlx"):  # WI-32a: local routes only, so kb-ocr itself does not mask
    env = base_env({"KLODE_KIT_ENGINE_REPLAY": str(FIXTURE / "engine"), "KLODE_KIT_TEST_SEAMS": "1",
                    "KLODE_KIT_FAKE_LOG": str(tmp_path / "calls.jsonl")})
    env.pop("KLODE_KIT_REAL", None)
    res = run_module("klode_kit", ["kb-ocr", "--batch", str(FIXTURE / "images"), "--project", str(project), "--routes", routes],
                     env,
                     timeout=120)
    assert res.returncode == 0, res.stderr.decode()
    [batch] = [p.name for p in (project / "work").iterdir() if p.is_dir()]
    return batch


def test_kb_ocr_rerun_keeps_masked_lines(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    batch = kb_ocr(project, tmp_path)
    path = project / "work" / batch / "derived.jsonl"
    upright = path.read_bytes()
    engine = ReplayVision({json.loads(x)["der_sha256"]: [] for x in upright.decode().splitlines()})
    mask.mask_batch(project, batch, engine=engine)
    registered = path.read_bytes()
    assert registered.startswith(upright) and len(registered) > len(upright)

    assert kb_ocr(project, tmp_path) == batch

    assert path.read_bytes() == registered
    masked = [json.loads(x) for x in registered[len(upright):].decode().splitlines()]
    assert {m["der_sha256"] for m in masked} == {r["der_sha256"] for r in mask.read_manifest(project)}


def test_kb_ocr_rerun_drops_garbage_keeps_masked(tmp_path):
    """A non-JSON line in derived.jsonl is dropped by the rerun; the masked lines are kept."""
    project = tmp_path / "proj"
    project.mkdir()
    batch = kb_ocr(project, tmp_path)
    path = project / "work" / batch / "derived.jsonl"
    engine = ReplayVision({json.loads(x)["der_sha256"]: [] for x in path.read_text().splitlines()})
    mask.mask_batch(project, batch, engine=engine)
    registered = path.read_bytes()
    path.write_bytes(registered + b"{garbage\n")

    kb_ocr(project, tmp_path)

    assert path.read_bytes() == registered
