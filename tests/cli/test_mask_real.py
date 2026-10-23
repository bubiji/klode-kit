"""WI-05a: header masking with the real Vision engine (matrix 05a-07; gate G4 approved in WI-04a).

Paired real path for the Vision replay used by tests/unit/test_mask.py. Real ocrmac finds the header lines, the
page is masked, and real ocrmac reads the masked image again: the synthetic patient name and number must be gone,
while the rest of the page is still readable (the masking is not vacuous by blacking out everything).
"""

from __future__ import annotations

import pytest

from _support.mask_replay import fixture_images, make_batch
from klode_kit.audit import mask
from klode_kit.ocr import engines as E

pytestmark = [pytest.mark.cli_integration, pytest.mark.macos]

SECRETS = ("测试患者甲", "T-0000001")


@pytest.mark.parametrize("fixture", ["fx-u1-3labs", "fx-rx-stamp"])
def test_masked_text_absent(tmp_path, fixture):
    """05a-07 (L3·S3): real Vision on every masked page reads neither 「测试患者甲」 nor 「T-0000001」."""
    images = fixture_images(fixture)
    project = tmp_path / "proj"
    project.mkdir()
    batch = make_batch(project, images)
    engine = E.OcrMacEngine()
    before = []
    for img in images:
        before += [ln["text"] for ln in engine.ocr_page(img, page_id="d-000000000000/p001", der_sha256="0" * 64)]
    assert any("测试患者甲" in t for t in before), "precondition: Vision reads the name on the unmasked page"
    rows = mask.mask_batch(project, batch, engine=engine)
    assert len(rows) == len(images)
    for row in rows:
        doc, page = row["page_id"].split("/")
        png = project / "_masked" / doc / f"{page}.m.png"
        lines = engine.ocr_page(png, page_id=row["page_id"], der_sha256=row["der_sha256"])
        text = "\n".join(ln["text"] for ln in lines)
        for secret in SECRETS:
            assert secret not in text.replace(" ", ""), (row["page_id"], secret, text)
        assert len(lines) >= 5, f"the body of the page must still be readable: {text!r}"
