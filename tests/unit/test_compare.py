"""WI-07c: N-route vote per cell (matrix 07c-05..12, K1).

``compare(alignment, routes=..., routes_enabled=..., whitelist=...)`` turns the WI-07b cells into votes.
The law under test (K1, design 02 r2.2-D):

    a cell agrees  <=>  every voting route has a candidate in it, all their ``norm`` are equal, the cell carries
                        no uncertain code from the alignment, and every cell of its (row, kind) has the same set of
                        voting routes (no route's fields of that kind shifted on the row).

Spec change D-37 (WI-33f): a voting route's value-bearing line left unplaced on the page is no longer a code on
every cell; it is one page item per engine (``Comparison.page_items``), see ``test_extra_values_wi33f.py``.

Grids are built directly from ``align.Cell`` / ``align.Candidate`` so the generator controls every input the law
names; the cross-layer cases run the real ``align`` on fixture routes.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from klode_kit.audit import align as A
from klode_kit.audit import compare as C
from klode_kit.audit import whitelist as W

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures"
ROUTES4 = ("vision", "mlx", "claude", "codex")
ALIGN_CODES = ("no_geometry", "unaligned:mlx", "merged_rows:codex", "unaligned:claude")
VALS = ("1", "2", "10")


def cand(norm: str, line_idx: int = 0) -> A.Candidate:
    return A.Candidate(norm, norm, line_idx, (0, len(norm)), None, None)


def cell(row: int, kind: str, k: int, reads: dict, uncertain: tuple = (), label: str | None = None) -> A.Cell:
    order = [e for e in A.ENGINE_ORDER if e in reads]
    return A.Cell(row, kind, k, None, None, 0, 1, label,
                  tuple((e, cand(reads[e])) for e in order if reads[e] is not None), tuple(uncertain))


def alignment(cells, extra_values=None) -> A.Alignment:
    return A.Alignment(None, (), {}, tuple(cells), (), {}, (), extra_values=extra_values or {})


def fixture_routes(name: str, key: str = "p001") -> dict[str, list[dict]]:
    out = {}
    for route in ROUTES4:
        path = FIXTURES / name / "engine" / route / f"{key}.jsonl"
        out[route] = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    return out


# --------------------------------------------------------------------------- generator


@st.composite
def grids(draw):
    nrows = draw(st.integers(1, 3))
    cells = []
    for r in range(nrows):
        row_unc = tuple(sorted(draw(st.sets(st.sampled_from(ALIGN_CODES), max_size=2))))
        for kind in sorted(draw(st.sets(st.sampled_from(("number", "unit")), min_size=1))):
            for k in range(draw(st.integers(1, 3))):
                base = draw(st.sampled_from(VALS))
                reads = {}
                for e in ROUTES4:
                    how = draw(st.sampled_from(("same", "same", "same", "same", "other", "none")))
                    reads[e] = base if how == "same" else (None if how == "none" else draw(st.sampled_from(VALS)))
                if draw(st.booleans()):
                    reads["tesseract"] = draw(st.sampled_from(VALS))   # not a voting route: never counts
                cells.append(cell(r, kind, k, reads, row_unc))
    extra = draw(st.sampled_from(({}, {}, {}, {}, {"claude": (3,)}, {"tesseract": (1,)})))
    return alignment(cells, extra)


def voting(c: A.Cell) -> dict[str, str]:
    return {e: x.norm for e, x in c.candidates if e in ROUTES4}


def law_agree(c: A.Cell, al: A.Alignment) -> bool:
    reads = voting(c)
    return (set(reads) == set(ROUTES4) and len(set(reads.values())) == 1 and all(u == "no_geometry" for u in c.uncertain))
    # WI-32b: no_geometry / ordinal_shift are advisory; D-37: page extras no longer matter to a cell


# --------------------------------------------------------------------------- 07c-05 / 06 / 07 properties


@settings(max_examples=300, deadline=None)
@given(grids())
def test_auto_agree_iff_all_present_equal(al):
    """07c-05 (L1-S4, K1): on random four-route grids (None cells, row-level uncertain, a non-voting tesseract
    candidate, page extras) a cell agrees exactly when the law above holds - both directions. Every cell that
    does not agree says why: an uncertain code or more than one value."""
    res = C.compare(al, routes=ROUTES4, whitelist=W.EMPTY)
    assert len(res.cells) == len(al.cells)
    for c in al.cells:
        v = res.cell(c.row, c.kind, c.k)
        assert v is not None
        assert v.agree == law_agree(c, al), (c, v)
        if v.agree:
            assert v.uncertain == ()
            assert v.recommended == next(iter(voting(c).values()))
            assert v.votes == ((v.recommended, tuple(e for e in A.ENGINE_ORDER if e in ROUTES4)),)
        else:
            assert v.uncertain != () or len(v.votes) > 1, v


@settings(max_examples=200, deadline=None)
@given(grids(), st.permutations(ROUTES4), st.randoms(use_true_random=False))
def test_vote_engine_permutation(al, perm, rnd):
    """07c-06 (L1-S6): the order of ``routes`` and the order of each cell's candidates change nothing."""
    shuffled = []
    for c in al.cells:
        cands = list(c.candidates)
        rnd.shuffle(cands)
        shuffled.append(A.Cell(c.row, c.kind, c.k, c.col, c.geom, c.ordinal, c.count, c.item_label,
                               tuple(cands), c.uncertain))
    rnd.shuffle(shuffled)
    a = C.compare(al, routes=ROUTES4, whitelist=W.EMPTY)
    b = C.compare(alignment(shuffled, dict(al.extra_values)), routes=list(perm), whitelist=W.EMPTY)
    key = lambda v: (v.row, v.kind, v.k)  # noqa: E731
    assert sorted(a.cells, key=key) == sorted(b.cells, key=key)
    assert a.uncertain == b.uncertain
    assert a.routes == b.routes == tuple(e for e in A.ENGINE_ORDER if e in ROUTES4)


@settings(max_examples=300, deadline=None)
@given(grids())
def test_recommended_in_candidates(al):
    """07c-07 (L1-S4): a recommended value is one of the voting candidates and strictly the most common one;
    no recommendation exactly on tie / all_differ / none. The vote never rewrites a reading: ``readings`` are
    the voting candidates' norms as aligned (a majority is only a recommendation)."""
    res = C.compare(al, routes=ROUTES4, whitelist=W.EMPTY)
    for c in al.cells:
        v = res.cell(c.row, c.kind, c.k)
        reads = voting(c)
        assert dict(v.readings) == reads
        assert set(v.missing) == set(ROUTES4) - set(reads)
        tally = Counter(reads.values())
        if v.recommended is None:
            assert v.verdict in (C.TIE, C.ALL_DIFFER, C.NONE)
        else:
            assert v.verdict in (C.AGREE, C.MAJORITY)
            assert v.recommended in reads.values()
            assert all(tally[v.recommended] > n for val, n in tally.items() if val != v.recommended)
        if not reads:
            assert v.verdict == C.NONE
        elif len(tally) == len(reads) and len(reads) >= 2:
            assert v.verdict == C.ALL_DIFFER
        elif list(tally.values()).count(max(tally.values())) > 1:
            assert v.verdict == C.TIE


# --------------------------------------------------------------------------- 07c-08 .. 12 examples


def test_tie_no_majority_recommendation():
    """07c-08 (L1-S2): 2:2 is a tie - no recommended value, uncertain ``tie``, not agree."""
    al = alignment([cell(0, "number", 0, {"vision": "1", "mlx": "1", "claude": "2", "codex": "2"})])
    v = C.compare(al, routes=ROUTES4, whitelist=W.EMPTY).cells[0]
    assert v.verdict == C.TIE
    assert v.recommended is None
    assert "tie" in v.uncertain
    assert not v.agree
    assert sorted(v.votes) == [("1", ("vision", "mlx")), ("2", ("claude", "codex"))]


def test_all_differ_no_recommendation():
    """Reason table r2.3 (L1-S2): four different values -> ``all_differ``, no recommendation."""
    al = alignment([cell(0, "number", 0, {"vision": "1", "mlx": "2", "claude": "3", "codex": "4"})])
    v = C.compare(al, routes=ROUTES4, whitelist=W.EMPTY).cells[0]
    assert (v.verdict, v.recommended, v.agree) == (C.ALL_DIFFER, None, False)
    assert "all_differ" in v.uncertain


def test_three_vs_one_not_agree():
    """07c-09 (L1-S2): 3:1 is a disagreement whose recommended value is the majority value; never agree, and the
    minority reading stays as read."""
    al = alignment([cell(0, "number", 0, {"vision": "4.35", "mlx": "4.35", "claude": "4.35", "codex": "210"})])
    v = C.compare(al, routes=ROUTES4, whitelist=W.EMPTY).cells[0]
    assert v.verdict == C.MAJORITY
    assert not v.agree
    assert v.recommended == "4.35"
    assert v.votes == (("4.35", ("vision", "mlx", "claude")), ("210", ("codex",)))
    assert v.reading("codex") == "210"
    assert "tie" not in v.uncertain


def wl(engine: str, cls: str, pairs: list[tuple[str, str]]) -> W.Whitelist:
    body = ", ".join(f'["{a}", "{b}"]' for a, b in pairs)
    return W.parse(f'[[weakness]]\nengine = "{engine}"\nclass = "{cls}"\npairs = [{body}]\n')


def test_whitelist_not_applied_to_numeric():
    """07c-10 (L1-S3): a whitelist entry that matches a numeric difference exactly (vision ``≤`` for ``<``)
    leaves number and date cells disputed; the same entry does fire on a text cell, so it is the kind that
    keeps it off."""
    rule = wl("vision", "comparator", [("≤", "<")])
    al = alignment([
        cell(0, "number", 0, {"vision": "≤0.1", "mlx": "<0.1", "claude": "<0.1", "codex": "<0.1"}),
        cell(1, "date", 0, {"vision": "≤2026", "mlx": "<2026", "claude": "<2026", "codex": "<2026"}),
        cell(2, "flag", 0, {"vision": "≤", "mlx": "<", "claude": "<", "codex": "<"}),
    ])
    res = C.compare(al, routes=ROUTES4, whitelist=rule)
    assert res.cell(0, "number", 0).verdict == C.MAJORITY
    assert res.cell(1, "date", 0).verdict == C.MAJORITY
    assert not res.cell(0, "number", 0).agree and not res.cell(1, "date", 0).agree
    assert res.cell(0, "number", 0).recommended == "<0.1"
    assert res.cell(2, "flag", 0).agree


def test_whitelist_applied_to_text():
    """07c-11 (L1-S1): a known engine weakness on unit text (vision reading ``μ`` as ``u``) is ignored: the cell
    agrees, the recommended value is the majority spelling, and vision's reading is kept as read. Without the
    entry - or with the entry filed under an engine that did not vote - the same cell is a disagreement."""
    reads = {"vision": "umol/L", "mlx": "μmol/L", "claude": "μmol/L", "codex": "μmol/L"}
    al = alignment([cell(0, "unit", 0, reads)])
    v = C.compare(al, routes=ROUTES4, whitelist=wl("vision", "micro-sign", [("u", "μ")])).cells[0]
    assert v.agree
    assert v.recommended == "μmol/L"
    assert v.reading("vision") == "umol/L"
    assert v.uncertain == ()
    assert not C.compare(al, routes=ROUTES4, whitelist=W.EMPTY).cells[0].agree
    assert not C.compare(al, routes=ROUTES4, whitelist=wl("tesseract", "micro-sign", [("u", "μ")])).cells[0].agree


def test_route_disabled_never_agree():
    """07c-12 (L1-S3): ``routes_enabled.codex = false`` - every cell is ``missing_cell:codex`` and nothing agrees,
    whether codex produced no lines or stale lines are still around (they are not counted)."""
    routes = fixture_routes("fx-dup10")
    for given_routes in (routes, {e: v for e, v in routes.items() if e != "codex"}):
        res = C.compare(A.align(given_routes), routes=ROUTES4, routes_enabled={"claude": True, "codex": False},
                        whitelist=W.EMPTY)
        assert res.cells
        for v in res.cells:
            assert "missing_cell:codex" in v.uncertain
            assert "codex" in v.missing
            assert v.reading("codex") is None
            assert not v.agree


# --------------------------------------------------------------------------- cross-layer pass-through


def test_alignment_uncertain_passes_through():
    """v2 gap 2 (L1-S3): ``no_geometry`` from the real alignment (fx-approx-only: four routes identical on a row
    with approx geometry only) reaches the vote; those cells do not agree although all four values are equal."""
    al = A.align(fixture_routes("fx-approx-only"))
    res = C.compare(al, routes=ROUTES4, whitelist=W.EMPTY)
    flagged = [c for c in al.cells if "no_geometry" in c.uncertain]
    assert flagged
    for c in flagged:
        v = res.cell(c.row, c.kind, c.k)
        assert "no_geometry" in v.notes     # WI-32b: kept as a note, not a reason to confirm
        assert v.agree and v.uncertain == ()
        assert len(set(voting(c).values())) == 1 and len(voting(c)) == 4


def test_extra_value_line_is_a_page_item_not_a_cell_block():
    """impl-deviations WI-07b risk 1 (L1-S3), spec change D-37: a voting route's line that pairs with no row and
    holds a value (``血糖 5.6 mmol/L`` only claude read) is a page-level ``extra_values:claude`` item; the cells of
    the page keep agreeing. An unplaced line without values (a stamp) changes nothing."""
    routes = fixture_routes("fx-dup10")
    base = C.compare(A.align(routes), routes=ROUTES4, whitelist=W.EMPTY)
    assert all(v.agree for v in base.cells) and base.uncertain == ()

    def with_claude_line(text: str) -> dict:
        r = {e: list(v) for e, v in routes.items()}
        last = dict(r["claude"][-1])
        last.update(line_idx=last["line_idx"] + 1, text=text)
        r["claude"].append(last)
        return r

    stamp = C.compare(A.align(with_claude_line("示例专用章")), routes=ROUTES4, whitelist=W.EMPTY)
    assert stamp.uncertain == () and all(v.agree for v in stamp.cells)

    al = A.align(with_claude_line("血糖 5.6 mmol/L"))
    assert al.extra_values.get("claude")
    res = C.compare(al, routes=ROUTES4, whitelist=W.EMPTY)
    assert [code for code, _ in res.uncertain] == ["extra_values:claude"]
    assert res.cells and all(v.agree and v.uncertain == () for v in res.cells)
    assert [i.code for i in res.page_items] == ["extra_values:claude"]


def test_ordinal_shift_marks_row_kind():
    """impl-deviations WI-07b risk 2 (L1-S3): mlx missed the first number of a row, so its later numbers moved one
    place forward; every number cell of that row is ``ordinal_shift:mlx`` and none agrees - even the cell where
    mlx's shifted value happens to equal the others."""
    al = alignment([
        cell(0, "number", 0, {"vision": "5", "mlx": "5", "claude": "5", "codex": "5"}),
        cell(0, "number", 1, {"vision": "5", "claude": "5", "codex": "5"}),
        cell(0, "unit", 0, {"vision": "g/L", "mlx": "g/L", "claude": "g/L", "codex": "g/L"}),
        cell(1, "number", 0, {"vision": "7", "mlx": "7", "claude": "7", "codex": "7"}),
    ])
    res = C.compare(al, routes=ROUTES4, whitelist=W.EMPTY)
    first = res.cell(0, "number", 0)       # WI-32b: all four read 5 -> agrees, the shift is a note
    assert first.agree and first.notes == ("ordinal_shift:mlx",) and first.uncertain == ()
    last = res.cell(0, "number", 1)
    assert not last.agree and {"ordinal_shift:mlx", "missing_cell:mlx"} <= set(last.uncertain)
    assert res.cell(0, "unit", 0).agree
    assert res.cell(1, "number", 0).agree


def test_non_voting_engine_ignored():
    """A candidate of an engine outside ``routes`` (tesseract, the geometry route) neither votes nor counts as a
    present route."""
    al = alignment([
        cell(0, "number", 0, {"vision": "1", "mlx": "1", "claude": "1", "codex": "1", "tesseract": "9"}),
        cell(1, "number", 0, {"tesseract": "3"}),
    ])
    res = C.compare(al, routes=ROUTES4, whitelist=W.EMPTY)
    assert res.cell(0, "number", 0).agree
    assert res.cell(0, "number", 0).reading("tesseract") is None
    lone = res.cell(1, "number", 0)
    assert (lone.verdict, lone.recommended, lone.agree) == (C.NONE, None, False)
    assert set(lone.missing) == set(ROUTES4)


def test_compare_does_not_touch_alignment():
    """Determinism (L1-S1): comparing twice gives the same result and leaves the alignment's candidates as they
    were."""
    al = A.align(fixture_routes("fx-swap"))
    before = [(c.row, c.kind, c.k, c.candidates, c.uncertain) for c in al.cells]
    a = C.compare(al, routes=ROUTES4)
    b = C.compare(al, routes=ROUTES4)
    assert a == b
    assert [(c.row, c.kind, c.k, c.candidates, c.uncertain) for c in al.cells] == before


@pytest.mark.parametrize("routes", [(), ("vision", "vision", "mlx")])
def test_routes_guard(routes):
    """Guard (L1-S3): no voting route, or a route named twice, is a ``ValueError``."""
    with pytest.raises(ValueError):
        C.compare(alignment([]), routes=routes)
