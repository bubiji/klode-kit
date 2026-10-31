"""WI-33a: ``group_rows`` must not let a tall observation or a growing band swallow rows (pilot finding P4).

Law: every observation lands in exactly one row; rows are ordered top to bottom and items left to right; k well
separated text rows give exactly k rows (hypothesis property). Synthetic observations only.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings, strategies as st

from klode_kit.ocr.engines import group_rows

pytestmark = pytest.mark.unit


def ob(text, x0, y0, x1, y1):
    return {"text": text, "bbox_px": [x0, y0, x1, y1]}


def texts(rows):
    return [[o["text"] for o in r] for r in rows]


def test_tall_observation_does_not_absorb_three_short_rows():
    tall = ob("TALL", 0, 28, 50, 609)
    shorts = [ob(f"s{i}", 100, y, 200, y + 20) for i, y in enumerate((100, 300, 500))]
    rows = group_rows([tall, *shorts])
    for s in shorts:
        row = next(r for r in rows if s in r)
        assert [o["text"] for o in row if o["text"].startswith("s")] == [s["text"]]
    assert len(rows) >= 3
    assert sum(len(r) for r in rows) == 4


def test_staircase_does_not_chain_into_one_row():
    # each box overlaps the next by 5 of 20 px (0.25 < 0.5); chained band growth would merge all of them
    stairs = [ob(f"t{i}", i * 30, i * 15, i * 30 + 25, i * 15 + 20) for i in range(8)]
    rows = group_rows(stairs)
    assert len(rows) == 8
    assert texts(rows) == [[f"t{i}"] for i in range(8)]


def test_gradual_drift_does_not_grow_band_past_seed():
    # each neighbour overlaps 0.75 of its height with the previous one, but the first and last are disjoint
    drift = [ob(f"d{i}", i * 30, i * 5, i * 30 + 25, i * 5 + 20) for i in range(8)]
    rows = group_rows(drift)
    assert len(rows) > 1
    assert sum(len(r) for r in rows) == 8


def test_same_line_items_with_skew_merge_left_to_right_rows_top_to_bottom():
    obs = [
        ob("c", 200, 104, 260, 126),
        ob("a", 0, 100, 60, 120),
        ob("b", 100, 102, 160, 122),
        ob("z", 90, 300, 150, 318),
        ob("y", 0, 298, 60, 320),
    ]
    assert texts(group_rows(obs)) == [["a", "b", "c"], ["y", "z"]]


def test_overlap_exactly_half_still_merges():
    assert texts(group_rows([ob("a", 0, 0, 50, 20), ob("b", 60, 10, 110, 30)])) == [["a", "b"]]


def test_empty_input():
    assert group_rows([]) == []


@st.composite
def page(draw):
    k = draw(st.integers(1, 8))
    obs, expected = [], []
    for r in range(k):
        top = r * 60
        n = draw(st.integers(1, 6))
        h = draw(st.integers(20, 28))
        row = []
        for i in range(n):
            j = draw(st.integers(-2, 2))
            hh = h + draw(st.integers(-2, 2))
            o = ob(f"r{r}i{i}", i * 100, top + j, i * 100 + 80, top + j + hh)
            row.append(o["text"])
            obs.append(o)
        expected.append(row)
    perm = draw(st.permutations(obs))
    return list(perm), expected


@settings(max_examples=200, deadline=None)
@given(page())
def test_k_separated_rows_give_exactly_k_rows(case):
    obs, expected = case
    assert texts(group_rows(obs)) == expected
