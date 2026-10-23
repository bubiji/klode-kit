"""WI-04c edges added during implementation: malformed pdftotext output and poppler tools that answer
wrongly are ``PdfUnreadable`` (never a silent empty page).

Boundary: the poppler tools are replaced by tiny shell scripts in ``tmp_path`` (child-process boundary);
the real tools are paired in tests/cli/test_kb_ocr_pdf.py (04c-04..07).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from klode_kit.ocr import textlayer

pytestmark = pytest.mark.unit

HEAD = '<html xmlns="http://www.w3.org/1999/xhtml"><body><doc>'
TAIL = "</doc></body></html>"


def test_word_without_coordinates_is_unreadable():
    xml = HEAD + '<page width="10" height="10"><line xMin="0" yMin="0" xMax="1" yMax="1"><word>x</word></line></page>' + TAIL
    with pytest.raises(textlayer.PdfUnreadable, match="coordinates"):
        textlayer.parse_bbox_layout(xml)


def test_page_without_size_is_unreadable():
    with pytest.raises(textlayer.PdfUnreadable, match="width/height"):
        textlayer.parse_bbox_layout(HEAD + "<page></page>" + TAIL)


def test_blank_words_are_dropped_and_wordless_lines_vanish():
    xml = (HEAD + '<page width="10" height="10"><line xMin="0" yMin="0" xMax="1" yMax="1">'
           '<word xMin="0" yMin="0" xMax="1" yMax="1"> </word></line></page>' + TAIL)
    assert textlayer.parse_bbox_layout(xml).lines == ()


def _tools(tmp_path: Path, scripts: dict[str, str]) -> dict[str, str]:
    out = {}
    for name in textlayer.TOOLS:
        p = tmp_path / name
        p.write_text("#!/bin/sh\n" + scripts.get(name, "exit 0\n"))
        p.chmod(0o755)
        out[name] = str(p)
    return out


def test_pdfinfo_without_page_count_is_unreadable(tmp_path):
    tools = _tools(tmp_path, {"pdfinfo": "echo 'Title: x'\n"})
    with pytest.raises(textlayer.PdfUnreadable, match="no pages"):
        textlayer.read_pdf(tmp_path / "x.pdf", tools=tools)


def test_tool_failure_names_tool_and_last_stderr_line(tmp_path):
    tools = _tools(tmp_path, {"pdfinfo": "echo 'Syntax Error: first' >&2; echo 'Syntax Error: last' >&2; exit 1\n"})
    with pytest.raises(textlayer.PdfUnreadable) as exc:
        textlayer.read_pdf(tmp_path / "x.pdf", tools=tools)
    assert "pdfinfo exit 1" in str(exc.value) and "Syntax Error: last" in str(exc.value)


def test_renderer_without_png_is_unreadable(tmp_path):
    xml = HEAD + '<page width="10" height="10"></page>' + TAIL
    tools = _tools(tmp_path, {"pdfinfo": "echo 'Pages:          1'\n", "pdftotext": f"echo '{xml}'\n",
                              "pdftoppm": "echo 'not a png'\n"})
    with pytest.raises(textlayer.PdfUnreadable, match="no PNG"):
        textlayer.read_pdf(tmp_path / "x.pdf", tools=tools)


def test_tool_timeout_is_unreadable(tmp_path, monkeypatch):
    monkeypatch.setattr(textlayer, "TIMEOUT_S", 0.2)
    tools = _tools(tmp_path, {"pdfinfo": "sleep 5\n"})
    with pytest.raises(textlayer.PdfUnreadable, match="timed out"):
        textlayer.read_pdf(tmp_path / "x.pdf", tools=tools)
