"""WI-33c: page orientation from tesseract OSD (real-data pilot P7), heuristic only as fallback.

``parse_osd`` turns ``tesseract --psm 0`` text into ``(rotate_ccw, confidence)``; tesseract's ``Rotate: N`` is the
clockwise turn that corrects the page, ``orient`` uses counter-clockwise, so ``rotate_ccw == (-N) % 360``.
``detect_info`` uses OSD when it answers with confidence >= ``OSD_MIN_CONFIDENCE``, else the ink heuristic.
Law (S4): for every stored rotation r, an OSD runner that reports the matching ``Rotate`` makes detect return r.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from PIL import Image

from _support.kb_ocr import FIXTURES
from klode_kit.ocr import orient

pytestmark = pytest.mark.unit

LABS = FIXTURES / "fx-labs-1x" / "images" / "p001.png"


def osd(rotate, conf="1.94"):
    return (f"Page number: 0\nOrientation in degrees: {(360 - rotate) % 360}\nRotate: {rotate}\n"
            f"Orientation confidence: {conf}\nScript: Han\nScript confidence: 0.05\n")


@pytest.mark.parametrize("n,ccw", [(0, 0), (90, 270), (180, 180), (270, 90)])
def test_parse_osd_converts_clockwise_to_ccw(n, ccw):
    assert orient.parse_osd(osd(n, "13.60")) == (ccw, 13.6)


@pytest.mark.parametrize("text,expected", [
    ("Rotate: 90\n", (270, None)),
    ("Orientation confidence: 2.5\n", (None, 2.5)),
    ("", (None, None)),
    ("Too few characters. Skipping this page\nError during processing.\n", (None, None)),
    ("Rotate: abc\nOrientation confidence: x\n", (None, None)),
    ("Rotate: 45\nOrientation confidence: 3\n", (None, 3.0)),
    ("Rotate: -90\n", (None, None)),
])
def test_parse_osd_missing_or_garbage(text, expected):
    assert orient.parse_osd(text) == expected


def test_threshold_constant():
    assert 0 < orient.OSD_MIN_CONFIDENCE <= 1.0


@pytest.mark.parametrize("r", [0, 90, 180, 270])
def test_detect_uses_osd_first(r):
    stored = Image.open(LABS).rotate(-r, expand=True)
    # the runner says the clockwise turn that corrects the page
    info = orient.detect_info(stored, run_osd=lambda png: osd((360 - r) % 360))
    assert info["rotate"] == r and info["source"] == "osd" and info["confidence"] == 1.94
    assert orient.detect(stored, run_osd=lambda png: osd((360 - r) % 360)) == r


def test_osd_wins_over_heuristic():
    # upright page, OSD (wrongly or not) says it needs 90 ccw: OSD is the source of truth
    assert orient.detect(Image.open(LABS), run_osd=lambda png: osd(270)) == 90


def test_runner_receives_a_readable_png_path():
    seen = {}

    def runner(png):
        with Image.open(png) as im:
            seen["size"] = im.size
        return osd(0)

    img = Image.open(LABS)
    orient.detect(img, run_osd=runner)
    assert seen["size"] == img.size


@pytest.mark.parametrize("bad", [
    lambda png: (_ for _ in ()).throw(RuntimeError("boom")),
    lambda png: "Too few characters. Skipping this page\n",
    lambda png: "Orientation confidence: 5.0\n",
    lambda png: osd(90, "0.4"),
])
@pytest.mark.parametrize("r", [0, 90, 180, 270])
def test_fallback_is_heuristic(bad, r):
    stored = Image.open(LABS).rotate(-r, expand=True)
    info = orient.detect_info(stored, run_osd=bad)
    assert info["rotate"] == r and info["source"] == "heuristic"


def test_confidence_at_threshold_is_accepted():
    info = orient.detect_info(Image.open(LABS), run_osd=lambda png: osd(90, str(orient.OSD_MIN_CONFIDENCE)))
    assert info["source"] == "osd" and info["rotate"] == 270


def test_blank_page_is_none_source_without_osd():
    def runner(png):
        raise AssertionError("OSD must not run on an empty page")

    info = orient.detect_info(Image.new("L", (64, 32), 255), run_osd=runner)
    assert info == {"rotate": 0, "source": "none", "confidence": None}


@given(r=st.sampled_from([0, 90, 180, 270]), conf=st.floats(min_value=0.5, max_value=20))
def test_law_osd_rotation_roundtrip(r, conf):
    stored = Image.open(LABS).rotate(-r, expand=True)
    assert orient.detect(stored, run_osd=lambda png: osd((360 - r) % 360, f"{conf:.2f}")) == r
