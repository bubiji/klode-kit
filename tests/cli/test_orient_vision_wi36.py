"""WI-36 (cli_integration, real tesseract): with the 180 recognition cross-check on by default, registered
synthetic pages are still restored upright at four rotations (no regression against WI-33c)."""

from __future__ import annotations

import json

import pytest
from PIL import Image

from _support.kb_ocr import FIXTURES
from klode_kit.ocr import orient

pytestmark = pytest.mark.cli_integration

_manifest = json.loads((FIXTURES / "MANIFEST.json").read_text(encoding="utf-8"))
_paths = sorted({e["path"] for e in _manifest["files"] if e["path"].endswith(".png") and "/images/" in e["path"]
                 and "fx-rotated90" not in e["path"]})[:12]


def test_tess_score_is_positive_on_a_text_page():
    assert orient.tess_score(Image.open(FIXTURES / "fx-labs-1x" / "images" / "p001.png"), 0) > 0


@pytest.mark.parametrize("r", [0, 90, 180, 270])
@pytest.mark.parametrize("path", _paths)
def test_detect_restores_upright_with_cross_check(path, r):
    upright = Image.open(FIXTURES / path)
    stored = upright.rotate(-r, expand=True)
    info = orient.detect_info(stored)
    assert info["rotate"] == r, (path, r, info)
    assert orient.apply(stored, info["rotate"]).convert("L").tobytes() == upright.convert("L").tobytes()
