"""WI-04a real-process edge added after green: a tesseract language pack that does not exist is a loud
EngineUnavailable (non-zero exit), never an empty page."""

from __future__ import annotations

import pytest

from _support.flows import REPO
from klode_kit.ocr import engines as E

pytestmark = pytest.mark.cli_integration


def test_real_tesseract_missing_language_pack():
    png = REPO / "tests" / "fixtures" / "fx-labs-1x" / "images" / "p001.png"
    with pytest.raises(E.EngineUnavailable, match="tesseract exit"):
        E.TesseractEngine(lang="zz_nonexistent").ocr_page(png, page_id="d-000000000000/p001", der_sha256="0" * 64)
