"""WI-05b (WI-05a pending item): masked images are registered in ``work/<batch>/derived.jsonl``.

``mask.mask_batch(project, batch)`` adds, for every masked page, one derived line (contract ``derived``):
``der_sha256`` = the ``.m.png`` sha (= its MANIFEST row), ``parent`` = the original image sha (as the upright
line), same ``page_id``, ``path`` = ``_masked/<doc>/<page>.m.png`` (project-relative, like the upright lines),
``transform`` = the upright line's ``rotate`` (and ``render_dpi`` when present), ``crop: null`` and the MANIFEST
``mask_boxes``. The coordinates of the masked image are those of the upright image, so the cloud-route OCR lines
(``der_sha256`` of the masked image) trace back through provenance.

Laws: registration is idempotent (S5: reruns change no byte of derived.jsonl and add no line), and a masked
line is never fed back into masking (only upright lines are masked).

Vision boundary: ``_support.mask_replay.ReplayVision``; paired real path tests/cli/test_mask_real.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from _support.mask_replay import FIXTURES, ReplayVision, fixture_images, make_batch, sha256
from klode_kit.audit import mask
from klode_kit.contract.schema import issues

pytestmark = pytest.mark.unit

RX = FIXTURES / "fx-rx-stamp" / "images" / "p001.png"


def lines(project: Path, batch: str) -> list[dict]:
    return [json.loads(x) for x in (project / "work" / batch / "derived.jsonl").read_text().splitlines()]


def _engine_for(project: Path, batch: str) -> ReplayVision:
    return ReplayVision({row["der_sha256"]: [] for row in lines(project, batch)})


@pytest.mark.parametrize("images", [[RX], fixture_images("fx-rotated90"), fixture_images("fx-u1-3labs")[:2]])
def test_masked_lines_registered(tmp_path, images):
    project = tmp_path / "proj"
    project.mkdir()
    batch = make_batch(project, images)
    upright = lines(project, batch)
    engine = ReplayVision({r["der_sha256"]: [] for r in upright})

    rows = mask.mask_batch(project, batch, engine=engine)

    after = lines(project, batch)
    assert after[: len(upright)] == upright
    added = after[len(upright):]
    assert len(added) == len(rows)
    by_page = {r["page_id"]: r for r in upright}
    for line, row in zip(added, rows):
        assert issues("derived", line) == []
        up = by_page[row["page_id"]]
        assert line["der_sha256"] == row["der_sha256"]
        assert line["parent"] == row["parent"] == up["parent"]
        assert line["page_id"] == up["page_id"]
        assert line["path"] == f"_masked/{row['path']}"
        assert sha256((project / line["path"]).read_bytes()) == line["der_sha256"]
        assert line["transform"] == {"rotate": up["transform"]["rotate"], "crop": None,
                                     "mask_boxes": row["mask_boxes"]}


def test_rotated_page_keeps_rotate(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    batch = make_batch(project, fixture_images("fx-rotated90"))
    mask.mask_batch(project, batch, engine=_engine_for(project, batch))
    masked = [x for x in lines(project, batch) if x["path"].startswith("_masked/")]
    assert masked and all(x["transform"]["rotate"] == 90 for x in masked)


def test_render_dpi_carried_over(tmp_path):
    """A rendered PDF page keeps its ``render_dpi`` on the masked line (the pixels are the rendered ones)."""
    project = tmp_path / "proj"
    project.mkdir()
    batch = make_batch(project, [RX])
    path = project / "work" / batch / "derived.jsonl"
    row = json.loads(path.read_text())
    row["transform"]["render_dpi"] = 300
    path.write_text(json.dumps(row, sort_keys=True) + "\n")

    mask.mask_batch(project, batch, engine=_engine_for(project, batch))

    masked = lines(project, batch)[1]
    assert masked["transform"]["render_dpi"] == 300
    assert issues("derived", masked) == []


def test_registration_idempotent(tmp_path):
    """S5: a second and third run leave derived.jsonl byte-identical and do not mask the masked image."""
    project = tmp_path / "proj"
    project.mkdir()
    batch = make_batch(project, fixture_images("fx-u1-3labs")[:2])
    engine = _engine_for(project, batch)
    mask.mask_batch(project, batch, engine=engine)
    first = (project / "work" / batch / "derived.jsonl").read_bytes()
    upright_shas = {x["der_sha256"] for x in lines(project, batch) if not x["path"].startswith("_masked/")}

    for _ in range(2):
        engine.calls.clear()
        mask.mask_batch(project, batch, engine=engine)
        assert set(engine.calls) <= upright_shas
        assert (project / "work" / batch / "derived.jsonl").read_bytes() == first
    assert len(mask.read_manifest(project)) == 2


def test_registration_needs_no_batch_lock_holder(tmp_path):
    """The registration takes the batch lock ``work/.lock`` and releases it: a later lock attempt succeeds."""
    import fcntl
    import os

    project = tmp_path / "proj"
    project.mkdir()
    batch = make_batch(project, [RX])
    mask.mask_batch(project, batch, engine=_engine_for(project, batch))
    assert (project / "work" / ".lock").is_file(), "registration did not go through the batch lock"
    fd = os.open(project / "work" / ".lock", os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(fd)


def test_masked_lines_skipped_as_input(tmp_path):
    """A derived.jsonl that already lists masked images: only the upright images reach the engine."""
    project = tmp_path / "proj"
    project.mkdir()
    batch = make_batch(project, [RX])
    engine = _engine_for(project, batch)
    mask.mask_batch(project, batch, engine=engine)
    engine.calls.clear()
    rows = mask.mask_batch(project, batch, engine=engine)
    assert len(rows) == 1
    assert engine.calls == [lines(project, batch)[0]["der_sha256"]]
