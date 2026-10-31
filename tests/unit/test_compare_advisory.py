"""WI-32b: alignment / display doubts do not, by themselves, send an all-routes-equal field to the confirm page.

Law (K1, refined; design 02 r2.2-D, impl-deviations WI-07c "agree only when all voting routes are present and
equal", WI-31 user decision of 2026-10-27):

    a cell agrees  <=>  every voting route has a candidate in it, all their ``norm`` are equal, and none of the
                        *blocking* doubts applies: ``unaligned`` / ``merged_rows`` (the row pairing itself is in
                        doubt), ``swap_suspect`` (spec change D-37: page ``extra_values`` is a page item, no longer
                        a cell blocker).

``no_geometry`` (no exact box to crop with) and ``ordinal_shift`` (a route's k-th value of the row may sit one place
off - the equal reading is still what every route read at that place) are *advisory*: they never make an
all-present-equal cell disagree; they stay on the cell as ``notes`` (trace) while ``uncertain`` stays empty.
When the cell does not agree they remain ordinary ``uncertain`` codes (a missing or different reading is a real
dispute and all its doubts are listed). The old behaviour is pinned by ``test_compare.py`` (replaced rows are
listed in the WI-32b deviation).
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from klode_kit.audit import align as A
from klode_kit.audit import compare as C
from klode_kit.audit import whitelist as W

pytestmark = pytest.mark.unit

ROUTES4 = ("vision", "mlx", "claude", "codex")
ADVISORY = ("no_geometry",)
BLOCKING = ("unaligned:mlx", "merged_rows:codex", "unaligned:claude")
VALS = ("1", "2", "10")


def cand(norm: str, line_idx: int = 0) -> A.Candidate:
    return A.Candidate(norm, norm, line_idx, (0, len(norm)), None, None)


def cell(row, kind, k, reads, uncertain=()):
    order = [e for e in A.ENGINE_ORDER if e in reads]
    return A.Cell(row, kind, k, None, None, 0, 1, None,
                  tuple((e, cand(reads[e])) for e in order if reads[e] is not None), tuple(uncertain))


def alignment(cells, extra_values=None):
    return A.Alignment(None, (), {}, tuple(cells), (), {}, (), extra_values=extra_values or {})


def same(v="5"):
    return {e: v for e in ROUTES4}


def run(cells, extra=None):
    return C.compare(alignment(cells, extra), routes=ROUTES4, whitelist=W.EMPTY)


# --------------------------------------------------------------------------- examples


def test_equal_four_without_geometry_agrees_and_keeps_the_trace():
    """S1: four equal readings on a cell with ``no_geometry`` agree; the doubt is a note, not an uncertain code."""
    v = run([cell(0, "number", 0, same(), ("no_geometry",))]).cell(0, "number", 0)
    assert v.agree and v.verdict == C.AGREE
    assert v.uncertain == ()
    assert v.notes == ("no_geometry",)
    assert v.recommended == "5" and v.missing == ()


def test_equal_four_in_a_shifted_row_agrees_but_the_gap_cell_does_not():
    """S1: mlx has no second number: the cell where all four read ``5`` agrees (note ``ordinal_shift:mlx``), the
    cell mlx lacks stays a dispute carrying ``missing_cell:mlx`` and ``ordinal_shift:mlx`` as uncertain codes."""
    res = run([cell(0, "number", 0, same()),
               cell(0, "number", 1, {"vision": "5", "claude": "5", "codex": "5"})])
    first, second = res.cell(0, "number", 0), res.cell(0, "number", 1)
    assert first.agree and first.uncertain == () and first.notes == ("ordinal_shift:mlx",)
    assert not second.agree and second.notes == ()
    assert set(second.uncertain) == {"missing_cell:mlx", "ordinal_shift:mlx"}


@pytest.mark.parametrize("code", BLOCKING)
def test_pairing_doubts_still_block_equal_readings(code):
    """S3: ``unaligned`` / ``merged_rows`` mean the row pairing itself is not trustworthy: equal values are not
    enough, with or without ``no_geometry`` next to them."""
    v = run([cell(0, "number", 0, same(), (code, "no_geometry"))]).cell(0, "number", 0)
    assert not v.agree and v.verdict == C.MAJORITY
    assert code in v.uncertain and "no_geometry" in v.uncertain and v.notes == ()


def test_extra_values_no_longer_block_equal_readings():
    """S3, spec change D-37: a value line nobody placed (page-level) is one page item; it does not touch the cells,
    equal cells agree (with their advisory note) and stay off the confirm page."""
    res = run([cell(0, "number", 0, same(), ("no_geometry",))], {"claude": (3,)})
    v = res.cell(0, "number", 0)
    assert v.agree and v.uncertain == () and v.notes == ("no_geometry",)
    assert [i.code for i in res.page_items] == ["extra_values:claude"]


def test_swap_suspect_still_blocks_equal_readings():
    """S3: codex reads in each of two consecutive rows the value the other routes read in the other; the cell
    where... both are non-agree already by value - swap_suspect is listed and neither agrees."""
    res = run([cell(0, "number", 0, {"vision": "1", "mlx": "1", "claude": "1", "codex": "2"}, ("no_geometry",)),
               cell(1, "number", 0, {"vision": "2", "mlx": "2", "claude": "2", "codex": "1"}, ("no_geometry",))])
    for r in (0, 1):
        v = res.cell(r, "number", 0)
        assert not v.agree and any(c.startswith("swap_suspect") for c in v.uncertain) and "no_geometry" in v.uncertain


@pytest.mark.parametrize("reads", [
    {"vision": "5", "mlx": "5", "claude": "5"},                       # one route absent
    {"vision": "5", "mlx": "5", "claude": "5", "codex": "6"},         # one route differs
    {"vision": "5", "mlx": "6", "claude": "7", "codex": "8"},         # all differ
    {"vision": "5", "mlx": "5", "claude": "6", "codex": "6"},         # tie
])
def test_real_disputes_stay_disputes_with_all_their_codes(reads):
    """S2: absent / different readings are never forgiven, and the advisory doubts stay visible as uncertain."""
    v = run([cell(0, "number", 0, reads, ("no_geometry",))]).cell(0, "number", 0)
    assert not v.agree and "no_geometry" in v.uncertain and v.notes == ()


def test_whitelist_folded_text_still_agrees_without_geometry():
    """S2: the weakness whitelist (text cells only) still folds a vision kana slip; no geometry does not matter."""
    reads = {"vision": "ト", "mlx": "ト", "claude": "ト", "codex": "ト"}
    v = C.compare(alignment([cell(0, "text", 0, reads, ("no_geometry",))]), routes=ROUTES4).cell(0, "text", 0)
    assert v.agree and v.notes == ("no_geometry",)


# --------------------------------------------------------------------------- properties (S4)


@st.composite
def grids(draw):
    cells = []
    for r in range(draw(st.integers(1, 3))):
        row_unc = tuple(sorted(draw(st.sets(st.sampled_from(ADVISORY + BLOCKING), max_size=2))))
        for kind in sorted(draw(st.sets(st.sampled_from(("number", "unit")), min_size=1))):
            for k in range(draw(st.integers(1, 3))):
                base = draw(st.sampled_from(VALS))
                reads = {}
                for e in ROUTES4:
                    how = draw(st.sampled_from(("same", "same", "same", "same", "other", "none")))
                    reads[e] = base if how == "same" else (None if how == "none" else draw(st.sampled_from(VALS)))
                cells.append(cell(r, kind, k, reads, row_unc))
    extra = draw(st.sampled_from(({}, {}, {}, {"claude": (3,)})))
    return alignment(cells, extra)


def voting(c):
    return {e: x.norm for e, x in c.candidates if e in ROUTES4}


@settings(max_examples=300, deadline=None)
@given(grids())
def test_agree_iff_all_present_equal_and_no_blocking_doubt(al):
    """S4, both directions: agree <=> all four present and equal and no blocking code (``unaligned``,
    ``merged_rows`` from the alignment; D-37: page ``extra_values`` no longer); ``no_geometry`` /
    ``ordinal_shift`` never matter.
    Whatever is not agree has a reason (uncertain code or more than one value) and no notes; every agree cell has
    no uncertain code and its notes are exactly the advisory doubts it carried (nothing silently dropped)."""
    res = C.compare(al, routes=ROUTES4, whitelist=W.EMPTY)
    for c in al.cells:
        v = res.cell(c.row, c.kind, c.k)
        reads = voting(c)
        blocking = any(not u.startswith("no_geometry") for u in c.uncertain)
        want = set(reads) == set(ROUTES4) and len(set(reads.values())) == 1 and not blocking
        assert v.agree == want, (c, v)
        if v.agree:
            assert v.uncertain == ()
            raised = {u for u in c.uncertain if u == "no_geometry"}
            shifted = {u for u in v.notes if u.startswith("ordinal_shift:")}
            assert set(v.notes) == raised | shifted
            assert v.notes == tuple(sorted(v.notes))
        else:
            assert v.notes == ()
            assert v.uncertain != () or len(v.votes) > 1


@settings(max_examples=300, deadline=None)
@given(grids())
def test_any_route_different_or_absent_is_never_agree(al):
    """S4 (the safety half, stated alone): if any voting route is absent or reads a different value, the cell is
    not agree - whatever advisory doubts it carries."""
    res = C.compare(al, routes=ROUTES4, whitelist=W.EMPTY)
    for c in al.cells:
        reads = voting(c)
        if set(reads) != set(ROUTES4) or len(set(reads.values())) > 1:
            assert not res.cell(c.row, c.kind, c.k).agree


@settings(max_examples=200, deadline=None)
@given(grids(), st.randoms(use_true_random=False))
def test_advisory_codes_do_not_change_the_verdict_of_a_cell_without_them(al, rnd):
    """S6 (metamorphic): adding ``no_geometry`` to every cell never turns an agree cell into a dispute, and never
    turns a dispute into agree; the verdicts are the same cell by cell."""
    plain = C.compare(al, routes=ROUTES4, whitelist=W.EMPTY)
    flagged_cells = [A.Cell(c.row, c.kind, c.k, c.col, c.geom, c.ordinal, c.count, c.item_label, c.candidates,
                            tuple(sorted(set(c.uncertain) | {"no_geometry"}))) for c in al.cells]
    flagged = C.compare(alignment(flagged_cells, dict(al.extra_values)), routes=ROUTES4, whitelist=W.EMPTY)
    for c in al.cells:
        a, b = plain.cell(c.row, c.kind, c.k), flagged.cell(c.row, c.kind, c.k)
        assert a.agree == b.agree and a.votes == b.votes and a.missing == b.missing
