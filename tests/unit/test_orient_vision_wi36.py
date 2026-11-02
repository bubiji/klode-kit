"""WI-36 (real-data finding P19, D-47): a 180 proposed by OSD is confirmed with a tesseract recognition score.

Law (S4): the cross-check never rotates a page OSD left at 0, never changes a 90/270 proposal, and never picks
anything but 0 or OSD's proposal. Scoring is injected (``score(image, rotate) -> float | None``).
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from PIL import Image

from _support.kb_ocr import FIXTURES
from klode_kit.ocr import orient

pytestmark = pytest.mark.unit

LABS = FIXTURES / "fx-labs-1x" / "images" / "p001.png"


def osd(n, conf="2.57"):
    return f"Rotate: {n}\nOrientation confidence: {conf}\n"


def page():
    return Image.open(LABS).convert("RGB")


def scorer(table, calls=None):
    def score(image, rotate):
        if calls is not None:
            calls.append(rotate)
        return table[rotate]
    return score


def test_s0_better_rejects_osd_180():
    info = orient.detect_info(page(), run_osd=lambda p: osd(180), score=scorer({0: 167.0, 180: 118.0}))
    assert info["rotate"] == 0 and info["source"] == "osd-rejected" and info["confidence"] == 2.57


def test_s180_better_applies_it():
    info = orient.detect_info(page(), run_osd=lambda p: osd(180), score=scorer({0: 30.0, 180: 53.0}))
    assert info["rotate"] == 180 and info["source"] == "osd" and info["check"] == "osd+tess"


def test_equal_scores_keep_zero():
    info = orient.detect_info(page(), run_osd=lambda p: osd(180), score=scorer({0: 50.0, 180: 50.0}))
    assert info["rotate"] == 0 and info["source"] == "osd-rejected"


def test_both_zero_keep_zero():
    assert orient.detect_info(page(), run_osd=lambda p: osd(180), score=scorer({0: 0.0, 180: 0.0}))["rotate"] == 0


@pytest.mark.parametrize("n,ccw", [(90, 270), (270, 90)])
def test_quarter_turn_proposals_are_not_cross_checked(n, ccw):
    calls: list[int] = []
    info = orient.detect_info(page(), run_osd=lambda p: osd(n), score=scorer({0: 999.0}, calls))
    assert info["rotate"] == ccw and info["source"] == "osd" and calls == []


def test_scorer_unavailable_keeps_osd_180():
    info = orient.detect_info(page(), run_osd=lambda p: osd(180), score=lambda image, rotate: None)
    assert info["rotate"] == 180 and info["source"] == "osd" and info.get("check") is None


def test_score_not_asked_when_osd_says_zero():
    calls: list[int] = []
    info = orient.detect_info(page(), run_osd=lambda p: osd(0), score=scorer({0: 1.0}, calls))
    assert info["rotate"] == 0 and info["source"] == "osd" and calls == []


def test_low_confidence_osd_skips_cross_check():
    calls: list[int] = []
    info = orient.detect_info(page(), run_osd=lambda p: osd(180, "0.1"), score=scorer({0: 1.0, 180: 1.0}, calls))
    assert info["source"] == "heuristic" and calls == []


def test_scores_both_candidates():
    calls: list[int] = []
    orient.detect_info(page(), run_osd=lambda p: osd(180), score=scorer({0: 1.0, 180: 2.0}, calls))
    assert sorted(calls) == [0, 180]


TSV = ("level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
       "4\t1\t1\t1\t1\t0\t0\t0\t9\t9\t-1\t\n"
       "5\t1\t1\t1\t1\t1\t0\t0\t9\t9\t95.5\tHello\n"
       "5\t1\t1\t1\t1\t2\t0\t0\t9\t9\t59.9\tlowconf\n"
       "5\t1\t1\t1\t1\t3\t0\t0\t9\t9\t60\t日本語\n"
       "5\t1\t1\t1\t1\t4\t0\t0\t9\t9\tabc\tbad\n")


def test_parse_tsv_score_counts_confident_word_characters():
    assert orient.parse_tsv_score(TSV) == 5 + 3
    assert orient.parse_tsv_score("") == 0.0


def test_tess_score_downscales_and_rotates_before_scoring():
    seen = {}

    def run_tsv(path):
        seen["size"] = Image.open(path).size
        return TSV

    big = Image.new("RGB", (3200, 1000), "white")
    assert orient.tess_score(big, 90, run_tsv=run_tsv) == 8.0
    assert max(seen["size"]) <= orient.TESS_MAX_SIDE == 1600 and seen["size"][1] > seen["size"][0]


def test_tess_score_none_when_tesseract_fails():
    def run_tsv(path):
        raise FileNotFoundError("tesseract")
    assert orient.tess_score(page(), 0, run_tsv=run_tsv) is None


@settings(max_examples=60, deadline=None)
@given(n=st.sampled_from([0, 90, 180, 270]),
       s0=st.floats(0, 1e5, allow_nan=False), sp=st.floats(0, 1e5, allow_nan=False),
       available=st.booleans())
def test_property_cross_check_only_ever_picks_zero_or_the_proposal(n, s0, sp, available):
    proposal = (-n) % 360
    img = Image.new("L", (40, 40), 255)
    img.paste(0, (5, 5, 30, 12))
    table = {0: s0, proposal: sp}
    info = orient.detect_info(img, run_osd=lambda p: osd(n),
                              score=(lambda image, rotate: table[rotate]) if available else (lambda i, r: None))
    assert info["rotate"] in {0, proposal}
    if proposal != 90 and proposal != 270 and proposal != 0 and available and info["rotate"] != 0:
        assert sp > s0
    if proposal in (90, 270, 0):
        assert info["rotate"] == proposal
