"""WI-08d: ``views.uncertain_ids`` - the id list a revision body shows for its uncertain entries (plan risk R-14;
04 U8 ``uncertain: [p002-l07]``)."""

from __future__ import annotations

import pytest

from klode_kit.audit import gate as G
from klode_kit.audit import views as V

pytestmark = pytest.mark.unit

PAGE = "d-823f9af4750e/p001"


def test_cell_row_and_page_entries():
    cell = G.entry("tie", page_id=PAGE, field_id="r-b07990ab5816/r007-number-0")
    row = G.entry("unaligned:codex", page_id=PAGE, row=12)
    page = G.entry("route_missing_page:codex", page_id=PAGE)
    assert V.uncertain_ids([cell, row, page], "p002") == ["p002-l07", "p002-l12", "p002:codex:route_missing_page"]


def test_repeats_dropped_order_kept():
    a = G.entry("tie", page_id=PAGE, field_id="r-b07990ab5816/r007-number-0")
    b = G.entry("missing_cell:codex", page_id=PAGE, field_id="r-b07990ab5816/r007-unit-0")
    c = G.entry("tie", page_id=PAGE, field_id="r-b07990ab5816/r003-date-0")
    assert V.uncertain_ids([a, b, c, a], "p002") == ["p002-l07", "p002-l03"]
    assert V.uncertain_ids([], "p002") == []


def test_wide_row_and_unknown_shape():
    wide = G.entry("tie", page_id=PAGE, field_id="r-b07990ab5816/r123-number-0")
    assert V.uncertain_ids([wide], "p002") == ["p002-l123"]
    assert V.uncertain_ids([{"id": "something-else", "scope": "cell"}], "p002") == ["something-else"]
    assert V.uncertain_ids([{"scope": "page", "reason": "route_failed"}], "p002") == ["p002:route_failed"]
