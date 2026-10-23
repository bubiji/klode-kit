"""WI-04c: text-layer PDF split - page classification, pt -> px, pdftext rows (matrix 04c-01, 04c-02).

Laws: ``px = pt * dpi / 72`` with round-half-up to an integer pixel (S4, monotone in pt). Classification has
no conservation law; its boundaries are pinned (S2): fewer than ``min_chars`` non-whitespace characters is
``empty``; a garbage ratio strictly above ``garbage_max`` is ``garbled``; otherwise ``text``.

Boundary: ``parse_bbox_layout`` is fed a recorded ``pdftotext -bbox-layout`` page (fx-mixed-pdf page 1, plus an
escaped ampersand); the real pdftotext path is paired in tests/cli/test_kb_ocr_pdf.py (04c-04/05).
"""

from __future__ import annotations

import io
import math
import shutil

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from _support.kb_ocr import FIXTURES, make_inbox
from klode_kit.contract import schema
from klode_kit.ocr import run_batch, textlayer

pytestmark = pytest.mark.unit

PAGE_ID = "d-0123456789ab/p001"
DER = "a" * 64

# fx-mixed-pdf page 1 as printed by `pdftotext -f 1 -l 1 -bbox-layout` (poppler 26.08), shortened, plus a
# second block with an escaped ampersand and a box that starts slightly left of / above the page.
XML = """<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd"><html xmlns="http://www.w3.org/1999/xhtml">
<head>
<title>synthetic</title>
</head>
<body>
<doc>
  <page width="595.275600" height="841.889800">
    <flow>
      <block xMin="72.000000" yMin="58.353800" xMax="199.494000" yMax="76.767800">
        <line xMin="72.000000" yMin="58.353800" xMax="199.494000" yMax="76.767800">
          <word xMin="72.000000" yMin="58.353800" xMax="144.000000" yMax="76.767800">混排示例</word>
          <word xMin="147.726000" yMin="58.353800" xMax="165.726000" yMax="76.767800">第</word>
          <word xMin="169.452000" yMin="58.353800" xMax="177.768000" yMax="76.767800">1</word>
          <word xMin="181.494000" yMin="58.353800" xMax="199.494000" yMax="76.767800">页</word>
        </line>
      </block>
      <block xMin="72.000000" yMin="92.865800" xMax="504.000000" yMax="105.141800">
        <line xMin="72.000000" yMin="92.865800" xMax="504.000000" yMax="105.141800">
          <word xMin="72.000000" yMin="92.865800" xMax="504.000000" yMax="105.141800">混排文档第一页为正常文字层。本页内容为合成文本，用于验证按页分流：文字层</word>
        </line>
      </block>
    </flow>
    <flow>
      <block xMin="-0.500000" yMin="-1.250000" xMax="60.000000" yMax="10.000000">
        <line xMin="-0.500000" yMin="-1.250000" xMax="60.000000" yMax="10.000000">
          <word xMin="-0.500000" yMin="-1.250000" xMax="20.000000" yMax="10.000000">A&amp;B</word>
          <word xMin="24.000000" yMin="-1.000000" xMax="60.000000" yMax="10.000000">C</word>
        </line>
      </block>
    </flow>
  </page>
</doc>
</body>
</html>
"""

EMPTY_XML = """<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "x"><html xmlns="http://www.w3.org/1999/xhtml">
<head><title>synthetic</title></head>
<body>
<doc>
  <page width="595.275600" height="841.889800">
  </page>
</doc>
</body>
</html>
"""


# --------------------------------------------------------------------------- 04c-01 classification


def test_classify_boundaries():
    """04c-01 (S2): 49 characters -> empty, 50 -> text; garbage ratio 0.05 -> text, 0.0501 -> garbled."""
    assert textlayer.classify_page("字" * 49).kind == "empty"
    assert textlayer.classify_page("字" * 50).kind == "text"
    at = textlayer.classify_page("字" * 9500 + "ÿ" * 500)
    assert at.garbage_ratio == 0.05 and at.kind == "text"
    over = textlayer.classify_page("字" * 9499 + "ÿ" * 501)
    assert over.garbage_ratio == pytest.approx(0.0501) and over.kind == "garbled"


def test_classify_counts_non_whitespace_only():
    """Whitespace (spaces, newlines, ideographic space) is not a character: 49 + whitespace stays empty."""
    padded = " \n".join("字" * 49) + "　\t"
    c = textlayer.classify_page(padded)
    assert (c.kind, c.chars) == ("empty", 49)
    assert textlayer.classify_page("\n".join("字" * 50)).kind == "text"


def test_classify_empty_page():
    c = textlayer.classify_page("")
    assert (c.kind, c.chars, c.garbage, c.garbage_ratio) == ("empty", 0, 0, 0.0)


def test_classify_thresholds_are_parameters():
    """The config values (text_layer.min_chars / garbage_max) are honoured, not hard-coded."""
    assert textlayer.classify_page("字" * 10, min_chars=10).kind == "text"
    assert textlayer.classify_page("字" * 10, min_chars=11).kind == "empty"
    assert textlayer.classify_page("字" * 90 + "ÿ" * 10, garbage_max=0.1).kind == "text"
    assert textlayer.classify_page("字" * 90 + "ÿ" * 10, garbage_max=0.09).kind == "garbled"


def test_classify_fixture_garbled_and_text():
    """fx-mixed-pdf page 3 noise (Latin-1 symbols) is garbled; Chinese prose with ASCII digits, fullwidth and
    CJK punctuation and common unit symbols (°, ×, µ) is text."""
    noise = "ÿþ¤¦§¨©ª«¬®¯°±²³´µ¶·¸¹º»¼½¾¿×÷ÐÞßðþ" * 8
    assert textlayer.classify_page(noise).kind == "garbled"
    prose = ("本指南是为测试而编写的合成文本，不对应任何真实机构或个人。白细胞 6.5×10^9/L，体温 36.5°C，"
             "肌酐 80 µmol/L（参考 44–133）。「示例」第 1 章：Ａ４ 纸张。カタカナ もじ")
    c = textlayer.classify_page(prose)
    assert c.kind == "text" and c.garbage == 0


def test_classify_replacement_and_private_use_are_garbage():
    c = textlayer.classify_page("字" * 50 + "�" * 3 + "" * 2)
    assert c.garbage == 5 and c.chars == 55


# --------------------------------------------------------------------------- 04c-02 pt -> px


@settings(max_examples=300, deadline=None)
@given(pt=st.floats(min_value=0, max_value=20000, allow_nan=False, allow_infinity=False),
       dpi=st.integers(min_value=1, max_value=1200))
def test_bbox_pt_to_px(pt, dpi):
    """04c-02 (S4): px = pt * dpi / 72, rounded half up to an integer pixel; never off by more than half a pixel."""
    exact = pt * dpi / 72
    px = textlayer.pt_to_px(pt, dpi)
    assert isinstance(px, int)
    assert px == math.floor(exact + 0.5)
    assert abs(px - exact) <= 0.5


@settings(max_examples=200, deadline=None)
@given(a=st.floats(min_value=0, max_value=5000, allow_nan=False), b=st.floats(min_value=0, max_value=5000,
                                                                                allow_nan=False),
       dpi=st.integers(min_value=1, max_value=1200))
def test_pt_to_px_monotone(a, b, dpi):
    """Monotone: a <= b implies px(a) <= px(b), so a box never turns inside out."""
    lo, hi = sorted((a, b))
    assert textlayer.pt_to_px(lo, dpi) <= textlayer.pt_to_px(hi, dpi)


def test_pt_to_px_half_pixel_rounds_up():
    assert textlayer.pt_to_px(36, 1) == 1  # 0.5 px exactly
    assert textlayer.pt_to_px(35.9, 1) == 0
    assert textlayer.pt_to_px(72, 300) == 300
    assert textlayer.pt_to_px(0, 300) == 0


# --------------------------------------------------------------------------- pdftext rows


def test_pdftext_rows_conform_and_scale():
    """Rows of a recorded page pass ocr_line; engine pdftext; line text is the words joined by one space;
    every token span slices its word out of the line; tokens are exact; bbox_px = pt_to_px(bbox_pt)."""
    page = textlayer.parse_bbox_layout(XML)
    assert (page.width_pt, page.height_pt) == pytest.approx((595.2756, 841.8898))
    rows = textlayer.pdftext_rows(page, page_id=PAGE_ID, der_sha256=DER, dpi=300)
    assert [r["text"] for r in rows] == [
        "混排示例 第 1 页",
        "混排文档第一页为正常文字层。本页内容为合成文本，用于验证按页分流：文字层",
        "A&B C",
    ]
    assert [r["line_idx"] for r in rows] == [0, 1, 2]
    for row in rows:
        schema.validate("ocr_line", row)
        assert (row["engine"], row["page_id"], row["der_sha256"]) == ("pdftext", PAGE_ID, DER)
        assert row["bbox_px"] == [textlayer.pt_to_px(v, 300) for v in row["bbox_pt"]]
        assert row["tokens"]
        for tok in row["tokens"]:
            s, e = tok["span"]
            assert row["text"][s:e] == tok["text"]
            assert tok["geometry"] == "exact"
            assert tok["bbox_px"] == [textlayer.pt_to_px(v, 300) for v in tok["bbox_pt"]]
    first = rows[0]
    assert first["bbox_pt"] == pytest.approx([72.0, 58.3538, 199.494, 76.7678])
    assert first["bbox_px"] == [300, 243, 831, 320]
    assert [t["text"] for t in first["tokens"]] == ["混排示例", "第", "1", "页"]


def test_pdftext_rows_clamp_negative_coordinates():
    """pdftotext may report a glyph box a little outside the page; coordinates are clamped to 0 (contract
    minimum), never dropped."""
    rows = textlayer.pdftext_rows(textlayer.parse_bbox_layout(XML), page_id=PAGE_ID, der_sha256=DER, dpi=150)
    last = rows[-1]
    assert last["bbox_pt"][:2] == [0, 0]
    assert last["tokens"][0]["bbox_px"][:2] == [0, 0]
    schema.validate("ocr_line", last)


def test_page_without_text_layer():
    page = textlayer.parse_bbox_layout(EMPTY_XML)
    assert page.text == ""
    assert textlayer.pdftext_rows(page, page_id=PAGE_ID, der_sha256=DER, dpi=300) == []
    assert textlayer.classify_page(page.text).kind == "empty"


def test_page_text_is_lines_joined():
    page = textlayer.parse_bbox_layout(XML)
    assert page.text.split("\n") == ["混排示例 第 1 页",
                                      "混排文档第一页为正常文字层。本页内容为合成文本，用于验证按页分流：文字层",
                                      "A&B C"]


def test_parse_rejects_non_pdftotext_output():
    with pytest.raises(textlayer.PdfUnreadable):
        textlayer.parse_bbox_layout("not xml at all")
    with pytest.raises(textlayer.PdfUnreadable):
        textlayer.parse_bbox_layout('<html xmlns="http://www.w3.org/1999/xhtml"><body><doc></doc></body></html>')


# --------------------------------------------------------------------------- missing poppler


def test_require_tools_names_every_missing_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(textlayer.PopplerMissing) as exc:
        textlayer.require_tools()
    for tool in ("pdfinfo", "pdftotext", "pdftoppm"):
        assert tool in str(exc.value)


def test_kb_ocr_pdf_without_poppler_is_exit_40(tmp_path, monkeypatch):
    """A PDF inbox on a machine without poppler: exit 40 naming the missing tools; nothing is written."""
    project = tmp_path / "proj"
    project.mkdir()
    inbox = make_inbox(tmp_path / "inbox", [FIXTURES / "fx-text-pdf" / "guide-2026.pdf"])
    empty = tmp_path / "emptybin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    out = io.StringIO()
    assert run_batch.run(project, inbox, ["vision"], out=out) == 40
    msg = out.getvalue()
    assert "pdftotext" in msg and "pdftoppm" in msg and "pdfinfo" in msg
    assert not (project / "work").exists()


def test_require_tools_found_returns_paths(tmp_path, monkeypatch):
    for tool in ("pdfinfo", "pdftotext", "pdftoppm"):
        p = tmp_path / tool
        p.write_text("#!/bin/sh\nexit 0\n")
        p.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    found = textlayer.require_tools()
    assert found == {t: shutil.which(t) for t in ("pdfinfo", "pdftotext", "pdftoppm")}
