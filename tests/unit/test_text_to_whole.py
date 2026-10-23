"""WI-04c: ``text_to_whole`` - whole-document text plus a page map (matrix 04c-03).

Law (S5, round trip): every line of the whole text, looked up through the page map, is the original line of
its page; and the lookup is a bijection between whole-text lines and (page, line) pairs - nothing is lost,
duplicated or reordered.

Format: ``whole`` is the lines of all pages in order, each followed by ``\\n``; a page map row is the page's
input metadata (everything but ``lines``) plus ``first_line`` (1-based line number in ``whole``) and ``n_lines``.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from klode_kit.ocr.text_to_whole import locate, text_to_whole

pytestmark = pytest.mark.unit

LINE = st.text(alphabet=st.characters(blacklist_categories=("Cc", "Cs", "Zl", "Zp")), max_size=30)
PAGES = st.lists(st.lists(LINE, max_size=6), min_size=1, max_size=8)


def _pages(texts):
    return [{"page_id": f"d-0123456789ab/p{i + 1:03d}", "key": f"p{i + 1:03d}", "dpi": 300, "lines": lines}
            for i, lines in enumerate(texts)]


@settings(max_examples=200, deadline=None)
@given(texts=PAGES)
def test_whole_pagemap_roundtrip(texts):
    """04c-03 (S5): every whole-text line goes back through page_map to the very line of its page; the
    mapping covers every (page, line) exactly once and keeps page and line order."""
    pages = _pages(texts)
    whole, page_map = text_to_whole(pages)
    whole_lines = whole.split("\n")
    assert whole_lines[-1] == ""
    whole_lines = whole_lines[:-1]
    assert len(whole_lines) == sum(len(t) for t in texts)
    by_id = {p["page_id"]: p for p in pages}
    seen = []
    for n, line in enumerate(whole_lines, start=1):
        pid, idx = locate(page_map, n)
        assert by_id[pid]["lines"][idx] == line
        seen.append((pid, idx))
    assert seen == [(p["page_id"], i) for p in pages for i in range(len(p["lines"]))]


@settings(max_examples=100, deadline=None)
@given(texts=PAGES)
def test_page_map_rows_cover_pages_in_order(texts):
    pages = _pages(texts)
    _whole, page_map = text_to_whole(pages)
    assert [r["page_id"] for r in page_map] == [p["page_id"] for p in pages]
    nxt = 1
    for row, page in zip(page_map, pages):
        assert row["first_line"] == nxt and row["n_lines"] == len(page["lines"])
        assert (row["key"], row["dpi"]) == (page["key"], 300)
        assert "lines" not in row
        nxt += row["n_lines"]


def test_no_lines_gives_empty_whole():
    whole, page_map = text_to_whole(_pages([[], []]))
    assert whole == ""
    assert [(r["first_line"], r["n_lines"]) for r in page_map] == [(1, 0), (1, 0)]
    with pytest.raises(LookupError):
        locate(page_map, 1)


def test_locate_out_of_range():
    _whole, page_map = text_to_whole(_pages([["a", "b"], ["c"]]))
    assert locate(page_map, 3) == ("d-0123456789ab/p002", 0)
    for bad in (0, -1, 4):
        with pytest.raises(LookupError):
            locate(page_map, bad)


@pytest.mark.parametrize("bad", ["a\nb", "a\rb", "a b", "a\x0cb", "a\x85b"])
def test_line_with_a_line_break_is_rejected(bad):
    """A line that would split into two whole-text lines would break the round trip: rejected, not split."""
    with pytest.raises(ValueError):
        text_to_whole(_pages([["ok", bad]]))


def test_duplicate_page_id_is_rejected():
    pages = _pages([["a"], ["b"]])
    pages[1]["page_id"] = pages[0]["page_id"]
    with pytest.raises(ValueError):
        text_to_whole(pages)


def test_input_is_not_mutated():
    pages = _pages([["a"], ["b", "c"]])
    before = [dict(p, lines=list(p["lines"])) for p in pages]
    text_to_whole(pages)
    assert pages == before
