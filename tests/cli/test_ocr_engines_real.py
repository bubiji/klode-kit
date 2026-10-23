"""WI-04a: the real local engines on every synthetic fx image (matrix 04a-10/11/12; gate G4 approved).

These are the real paths paired with the replayed raw output of tests/contract/test_ocr_engine_replay.py and the
boundary doubles of tests/unit/test_ocr_engines.py. A missing engine, language pack or MLX weights FAILS here
(the lane never skips); ``mlx_weights_status`` is what doctor (WI-10b) reports.
"""

from __future__ import annotations

import hashlib

import pytest

from _support.flows import REPO
from klode_kit.contract import schema
from klode_kit.ocr import engines as E
from klode_kit.ocr import geometry as G

pytestmark = pytest.mark.cli_integration

FIXTURES = REPO / "tests" / "fixtures"
IMAGES = sorted([*FIXTURES.glob("fx-*/images/*.png"), *FIXTURES.glob("fx-*/derived/*.png")])
IDS = [p.relative_to(FIXTURES).as_posix() for p in IMAGES]


def ident(png):
    sha = hashlib.sha256(png.read_bytes()).hexdigest()
    return f"d-{sha[:12]}/p001", sha


def check_page(lines, size, engine):
    assert lines, "every fx image has printed text"
    assert [line["line_idx"] for line in lines] == list(range(len(lines)))
    for line in lines:
        schema.validate("ocr_line", line)
        assert line["engine"] == engine
        boxes = [line["bbox_px"], line.get("block_bbox"), *[t["bbox_px"] for t in line["tokens"] or []]]
        for box in [b for b in boxes if b is not None]:
            assert all(type(v) is int for v in box)
            assert G.in_bounds(box, size), (line["text"], box, size)


def test_fx_images_found():
    """Guard against a vacuous run: the synthetic image set is the WI-00a one."""
    assert len(IMAGES) >= 20


@pytest.fixture(scope="module")
def ocrmac_engine():
    return E.OcrMacEngine()


@pytest.fixture(scope="module")
def tesseract_engine():
    return E.TesseractEngine()


@pytest.mark.macos
@pytest.mark.parametrize("png", IMAGES, ids=IDS)
def test_real_ocrmac_bounds(ocrmac_engine, png):
    """04a-10 (S4 over all fx images): real Vision via ocrmac; every box inside the image; tokens approx."""
    page, sha = ident(png)
    lines = ocrmac_engine.ocr_page(png, page_id=page, der_sha256=sha)
    check_page(lines, G.image_size(png), "vision")
    assert {t["geometry"] for line in lines for t in line["tokens"]} == {"approx"}


@pytest.mark.parametrize("png", IMAGES, ids=IDS)
def test_real_tesseract_tsv_bounds(tesseract_engine, png):
    """04a-11 (S4 over all fx images): real tesseract chi_sim+jpn tsv; every box inside; tokens exact."""
    page, sha = ident(png)
    lines = tesseract_engine.ocr_page(png, page_id=page, der_sha256=sha)
    check_page(lines, G.image_size(png), "tesseract")
    assert {t["geometry"] for line in lines for t in line["tokens"]} == {"exact"}


@pytest.mark.macos
def test_real_ocrmac_reads_lab_page(ocrmac_engine):
    """04a-10b (added): the bounds check is not vacuous - Vision actually reads the fx-labs-1x table."""
    png = FIXTURES / "fx-labs-1x" / "images" / "p001.png"
    page, sha = ident(png)
    text = "\n".join(line["text"] for line in ocrmac_engine.ocr_page(png, page_id=page, der_sha256=sha))
    assert "血红蛋白" in text and "检验报告单" in text


@pytest.mark.macos
@pytest.mark.macos_mlx
def test_real_mlx_smoke():
    """04a-12 (S1): real Unlimited-OCR on fx-labs-1x through the route-B chain: schema-valid MLX lines with
    block boxes, no fallback. Missing weights raise MlxWeightsMissing -> this test FAILS (never skips)."""
    status = E.mlx_weights_status()
    assert status.ok, f"MLX weights missing ({status.problem}); run `klode-kit doctor`"
    png = FIXTURES / "fx-labs-1x" / "images" / "p001.png"
    page, sha = ident(png)
    res = E.ocr_page(png, page_id=page, der_sha256=sha, mlx=E.MlxEngine(), fallback=E.TesseractEngine())
    assert res.status == "ok" and res.engine == "mlx", res.attempts
    check_page(res.lines, G.image_size(png), "mlx")
    assert all(line["tokens"] is None for line in res.lines)
    assert all(line.get("block_bbox") for line in res.lines)
    assert any("血红蛋白" in line["text"] for line in res.lines)
    assert res.attempts[0]["prompt"] == E.MLX_PROMPT and res.attempts[0]["degenerate"] is False
