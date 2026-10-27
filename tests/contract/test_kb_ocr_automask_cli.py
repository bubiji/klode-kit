"""WI-32a: the user entries mask by themselves - process level (engines replayed, no real Vision, no cloud CLI).

- 32a-C1 (S1) ``kb-ocr`` (default routes) leaves ``_masked/`` and ``_mask_review/``; ``--routes vision,mlx`` leaves
  neither; the batch receipt carries ``mask`` only in the first case.
- 32a-C2 (S5) SIGKILL at each durable point of the masking, then a clean rerun: the final ``_masked/``,
  ``_mask_review/``, ``derived.jsonl`` and MANIFEST equal those of an uninterrupted run, no temporary file remains.
- 32a-C3 (S1) ``pipeline --stage ocr`` and ``med-intake`` (their ocr stage runs ``kb-ocr``) mask all pages.
- 32a-C4 (S3) a second ``kb-ocr`` of the same inbox writes nothing new (idempotent through the user entry).
"""

from __future__ import annotations

import json
import signal
from pathlib import Path

import pytest

from _support import kb_ocr as K
from _support.intake_fx import IntakeRig
from klode_kit.audit import mask

pytestmark = pytest.mark.contract_replay

FX = K.FIXTURES / "fx-labs-1x"


def _kb_ocr(project: Path, inbox: Path, tmp: Path, *args: str, crash: str | None = None):
    extra = {"KLODE_KIT_CRASH_AT": crash} if crash else None
    env = K.kb_ocr_env(FX / "engine", tmp / "calls.jsonl", extra)
    return K.run_kb_ocr(project, inbox, env, *args)


def _state(project: Path, inbox: Path) -> dict[str, bytes]:
    out = {}
    for sub in ("_masked", "_mask_review"):
        root = project / sub
        if root.is_dir():
            out.update({p.relative_to(project).as_posix(): p.read_bytes() for p in sorted(root.rglob("*"))
                        if p.is_file()})
    for p in sorted((project / "work").glob("*/derived.jsonl")):
        out[p.relative_to(project).as_posix()] = p.read_bytes().replace(str(inbox).encode(), b"<inbox>")
    return out


def _fresh(tmp_path: Path, name: str) -> tuple[Path, Path]:
    root = tmp_path / name
    project = root / "project"
    project.mkdir(parents=True)
    return project, K.make_inbox(root / "_inbox" / "day", [FX / "images" / "p001.png"])


def test_kb_ocr_masks_when_a_cloud_route_is_requested_only(tmp_path):
    """32a-C1 (L2 S1/S3)."""
    project, inbox = _fresh(tmp_path, "a")
    res = _kb_ocr(project, inbox, tmp_path)
    assert res.returncode == 0, res.stderr.decode()
    assert list((project / "_masked").rglob("*.m.png")) and list((project / "_mask_review").rglob("*.review.png"))
    assert mask.read_manifest(project)
    assert K.receipt(project)["mask"]["status"] == "done"

    local, inbox2 = _fresh(tmp_path, "b")
    res = _kb_ocr(local, inbox2, tmp_path, "--routes", "vision,mlx")
    assert res.returncode == 0, res.stderr.decode()
    assert not (local / "_masked").exists() and not (local / "_mask_review").exists()
    assert "mask" not in K.receipt(local)


POINTS = ["mask.mkdir", "mask.image_written", "mask.review_written", "mask.manifest_appended",
          "mask.derived_registered"]


@pytest.mark.parametrize("point", POINTS)
def test_killed_masking_resumes_to_the_uninterrupted_state(tmp_path, point):
    """32a-C2 (L2 S5): crash safety and idempotence."""
    ref, ref_inbox = _fresh(tmp_path, "ref")
    assert _kb_ocr(ref, ref_inbox, tmp_path).returncode == 0
    want = _state(ref, ref_inbox)

    project, inbox = _fresh(tmp_path, "crash")
    killed = _kb_ocr(project, inbox, tmp_path, crash=point)
    assert killed.returncode == -signal.SIGKILL, killed.stderr.decode()
    rerun = _kb_ocr(project, inbox, tmp_path)
    assert rerun.returncode == 0, rerun.stderr.decode()
    assert _state(project, inbox) == want
    leftovers = [p for sub in ("_masked", "_mask_review", "work") for p in (project / sub).rglob(".*.tmp")]
    assert leftovers == []
    manifest = {r["der_sha256"] for r in mask.read_manifest(project)}
    registered = {json.loads(x)["der_sha256"] for x in (K.only_batch(project) / "derived.jsonl").read_text()
                  .splitlines() if json.loads(x)["path"].startswith("_masked/")}
    assert manifest == registered and len(manifest) == 1


def test_rerun_through_the_user_entry_writes_nothing_new(tmp_path):
    """32a-C4 (L2 S5)."""
    project, inbox = _fresh(tmp_path, "r")
    assert _kb_ocr(project, inbox, tmp_path).returncode == 0
    first = _state(project, inbox)
    assert _kb_ocr(project, inbox, tmp_path).returncode == 0
    assert _state(project, inbox) == first


def test_pipeline_ocr_stage_masks_every_page(tmp_path):
    """32a-C3 (L2 S1): the ocr stage of ``pipeline`` and ``med-intake`` run ``kb-ocr``; three pages, three masks."""
    rig = IntakeRig(tmp_path, "fx-u1-3labs")
    res = rig.run()
    assert res.returncode == 10, res.stderr.decode()
    rows = mask.read_manifest(rig.project)
    assert len(rows) == 3
    assert len(list((rig.project / "_masked").rglob("*.m.png"))) == 3
    assert len(list((rig.project / "_mask_review").rglob("*.review.png"))) == 3
    receipt = json.loads((rig.batch_dir / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["mask"]["status"] == "done" and receipt["mask"]["pages"] == 3
