"""WI-33c (cli_integration): real tesseract OSD restores every registered synthetic page at four rotations.

Pages with too little text for OSD (fx-lowres) go through the heuristic fallback and are restored too.
"""

from __future__ import annotations

import json
import shutil

import pytest
from PIL import Image

from _support.kb_ocr import FIXTURES
from klode_kit.ocr import orient

pytestmark = pytest.mark.cli_integration

_manifest = json.loads((FIXTURES / "MANIFEST.json").read_text(encoding="utf-8"))
_paths = sorted({e["path"] for e in _manifest["files"] if e["path"].endswith(".png") and "/images/" in e["path"]
                 and "fx-rotated90" not in e["path"]}) if isinstance(_manifest, dict) and "files" in _manifest else []


def test_tesseract_present_and_pages_registered():
    assert shutil.which("tesseract"), "tesseract is a hard dependency"
    assert len(_paths) >= 20


@pytest.mark.parametrize("r", [0, 90, 180, 270])
@pytest.mark.parametrize("path", _paths)
def test_real_osd_restores_upright(path, r):
    upright = Image.open(FIXTURES / path)
    stored = upright.rotate(-r, expand=True)
    info = orient.detect_info(stored)
    assert info["rotate"] == r, (path, r, info)
    assert info["source"] in {"osd", "heuristic"}
    assert orient.apply(stored, info["rotate"]).convert("L").tobytes() == upright.convert("L").tobytes()


def test_real_osd_is_the_source_on_a_text_page():
    assert orient.detect_info(Image.open(FIXTURES / "fx-labs-1x" / "images" / "p001.png"))["source"] == "osd"
