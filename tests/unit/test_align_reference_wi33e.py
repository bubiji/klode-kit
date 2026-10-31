"""WI-33e: the tesseract geometry reference in the alignment (real-data pilot P9, decision D-36; design R2-08, R3-05).

Law (what ``align(routes, reference=lines)`` promises):

    the reference only corroborates geometry. It is exact word boxes of the same page image that make approx
    (Vision) token boxes count on the rows it overlaps (R3-05) and add its own x0 values to the column clusters.
    It is not a route: it never adds a row, never changes the pairing, never becomes a candidate, never changes a
    value or an agreement verdict. The only cell doubt it can remove is ``no_geometry``.

Matrix (lane: unit, no collaborators are mocked - pure functions on plain rows):

- effect: vision (approx) + mlx/claude (no boxes) agree; before, no cell has geometry; with the reference every cell
  on a corroborated row has ``col`` / ``geom='vision'`` and no ``no_geometry`` (S1/S2);
- per-row: a reference line on one row does not give geometry to the other rows (S2);
- nothing to corroborate with: a reference of approx tokens, of lines without tokens, of an empty list, ``None``
  (S2/S3);
- not a voter: no candidate of engine ``tesseract``, ``compare`` votes and verdicts are the same with a reference that
  misreads every value (S3/S5);
- ``tesseract`` already a route (MLX fallback rows): the reference is not counted a second time (S2);
- laws over generated references (S4/S6): candidates, pairs, rows, ``extra`` and every code except ``no_geometry`` are
  equal with and without the reference; the compare verdict, votes, recommended value are equal; repeated runs are
  equal.
"""

from __future__ import annotations

import re

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from klode_kit.audit import align as A
from klode_kit.audit import compare as C

pytestmark = pytest.mark.unit

DER = "e" * 64
PAGE = "d-000000000033/p001"
COLS = (40, 330, 470, 620, 690)
ROWS = ("血红蛋白 128 g/L", "尿素 7.3 mmol/L", "白细胞 6.2 10^9/L")
VOTERS = ("vision", "mlx")


def geo(engine: str, idx: int, text: str, row: int, *, geometry: str | None, y0: int | None = None,
        dx: int = 0, tokens: bool = True) -> dict:
    top = 100 + 50 * row if y0 is None else y0
    toks = []
    for i, m in enumerate(re.finditer(r"\S+", text)):
        x0 = (COLS[i] if i < len(COLS) else COLS[-1] + 60 * (i - len(COLS) + 1)) + dx
        toks.append({"text": m.group(), "bbox_px": [x0, top, x0 + 12 * len(m.group()), top + 24],
                     "span": [m.start(), m.end()], "geometry": geometry})
    box = [min(t["bbox_px"][0] for t in toks), top, max(t["bbox_px"][2] for t in toks), top + 24]
    return {"engine": engine, "page_id": PAGE, "der_sha256": DER, "line_idx": idx, "text": text, "bbox_px": box,
            "tokens": toks if tokens else None}


def plain(engine: str, idx: int, text: str) -> dict:
    return {"engine": engine, "page_id": PAGE, "der_sha256": DER, "line_idx": idx, "text": text, "bbox_px": None,
            "tokens": None}


def vision(texts=ROWS) -> list[dict]:
    return [geo("vision", i, t, i, geometry="approx") for i, t in enumerate(texts)]


def mlx(texts=ROWS) -> list[dict]:
    return [plain("mlx", i, t) for i, t in enumerate(texts)]


def reference(texts=ROWS, rows=None, dx: int = 0) -> list[dict]:
    rows = range(len(texts)) if rows is None else rows
    return [geo("tesseract", n, texts[r], r, geometry="exact", dx=dx) for n, r in enumerate(rows)]


def routes() -> dict[str, list[dict]]:
    return {"vision": vision(), "mlx": mlx()}


def verdicts(al: A.Alignment) -> list[tuple]:
    cmp = C.compare(al, routes=VOTERS)
    return [(v.row, v.kind, v.k, v.verdict, v.recommended, v.votes, v.readings, v.missing) for v in cmp.cells]


def candidates(al: A.Alignment) -> list[tuple]:
    return [(c.row, c.kind, c.k, [(e, cd.value, cd.norm, cd.line_idx, cd.span, cd.unit, cd.dose)
                                  for e, cd in c.candidates], c.item_label, c.ordinal, c.count) for c in al.cells]


def non_geometry_codes(al: A.Alignment) -> list[tuple]:
    return [(c.row, c.kind, c.k, tuple(x for x in c.uncertain if x != A.NO_GEOMETRY)) for c in al.cells]


# --------------------------------------------------------------------------- effect


def test_without_reference_no_cell_has_geometry():
    """The pilot's state (P9): approx boxes alone are no evidence, every cell is no_geometry."""
    al = A.align(routes())
    assert al.cells
    assert all(c.col is None and c.geom is None and A.NO_GEOMETRY in c.uncertain for c in al.cells)
    assert not any(r.exact for r in al.rows)


def test_reference_makes_approx_vision_boxes_count():
    al = A.align(routes(), reference=reference())
    assert al.cells
    for c in al.cells:
        assert A.NO_GEOMETRY not in c.uncertain
        assert c.geom == "vision" and c.col is not None
    assert all(r.exact for r in al.rows)
    # the three numbers sit in one column; the columns are the reference's x0 clusters, not one per row
    numbers = [c for c in al.cells if c.kind == "number"]
    assert len({c.col for c in numbers}) == 1


def test_agreeing_routes_agree_with_geometry_and_the_note_goes():
    cmp = C.compare(A.align(routes(), reference=reference()), routes=VOTERS)
    assert cmp.cells and all(v.verdict == C.AGREE and v.uncertain == () and v.notes == () for v in cmp.cells)
    before = C.compare(A.align(routes()), routes=VOTERS)
    assert all(v.verdict == C.AGREE and A.NO_GEOMETRY in v.notes for v in before.cells)


def test_reference_geometry_is_judged_per_row():
    al = A.align(routes(), reference=reference(rows=[1]))
    by_row = {}
    for c in al.cells:
        by_row.setdefault(c.row, []).append(c)
    assert all(A.NO_GEOMETRY not in c.uncertain and c.geom == "vision" for c in by_row[1])
    for r in (0, 2):
        assert all(A.NO_GEOMETRY in c.uncertain and c.geom is None and c.col is None for c in by_row[r])
    assert [r.exact for r in al.rows] == [False, True, False]


def test_reference_off_by_a_row_gives_nothing():
    """A reference line that overlaps no vision row corroborates nothing and does not create a row."""
    far = [geo("tesseract", 0, "血红蛋白 128 g/L", 0, geometry="exact", y0=900)]
    al = A.align(routes(), reference=far)
    assert len(al.rows) == 3 and not any(r.exact for r in al.rows)
    assert all(A.NO_GEOMETRY in c.uncertain for c in al.cells)


@pytest.mark.parametrize("ref", [None, [], [plain("tesseract", 0, "血红蛋白 128 g/L")],
                                 [geo("tesseract", 0, "血红蛋白 128 g/L", 0, geometry="approx")],
                                 [geo("tesseract", 0, "血红蛋白 128 g/L", 0, geometry="exact", tokens=False)]],
                         ids=["none", "empty", "no-box", "approx-only", "no-tokens"])
def test_a_reference_without_exact_tokens_changes_nothing(ref):
    assert A.align(routes(), reference=ref) == A.align(routes())


def test_reference_is_not_counted_twice_when_tesseract_is_already_a_route():
    """MLX's fallback leaves rows of engine ``tesseract`` among the routes: they already are the geometry."""
    with_route = {**routes(), "tesseract": reference()}
    assert A.align(with_route, reference=reference(dx=300)) == A.align(with_route)


def test_reference_does_not_move_the_base_or_the_rows():
    plain_al = A.align(routes())
    al = A.align(routes(), reference=reference())
    assert [(r.row, r.label, r.lines) for r in al.rows] == [(r.row, r.label, r.lines) for r in plain_al.rows]
    assert al.pairs == plain_al.pairs and al.extra == plain_al.extra and al.base == plain_al.base


# --------------------------------------------------------------------------- not a voter


def test_the_reference_never_becomes_a_candidate_or_a_vote():
    """tesseract misreads every value; no candidate, no vote, same verdicts."""
    wrong = ["血红蛋白 728 g/L", "尿素 1.3 mmol/L", "白细胞 8.2 10^9/L"]
    al = A.align(routes(), reference=reference(wrong))
    assert all(e != "tesseract" for c in al.cells for e, _ in c.candidates)
    assert candidates(al) == candidates(A.align(routes()))
    assert verdicts(al) == verdicts(A.align(routes()))
    cmp = C.compare(al, routes=VOTERS)
    assert all(v.verdict == C.AGREE for v in cmp.cells)
    assert not any("728" in str(v.votes) for v in cmp.cells)


def test_a_dispute_stays_a_dispute_with_geometry():
    disputed = {"vision": vision(), "mlx": mlx(["血红蛋白 120 g/L", ROWS[1], ROWS[2]])}
    plain_v = verdicts(A.align(disputed))
    with_ref = A.align(disputed, reference=reference())
    assert verdicts(with_ref) == plain_v
    hb = next(v for v in C.compare(with_ref, routes=VOTERS).cells if (v.row, v.kind) == (0, "number"))
    assert hb.verdict != C.AGREE and A.NO_GEOMETRY not in hb.uncertain and hb.col is not None


def test_a_missing_voting_route_row_is_not_filled_by_the_reference():
    """The reference has a row nobody else read: no row, no cell, no value."""
    ref = reference() + [geo("tesseract", 3, "肌酐 64 pg", 3, geometry="exact")]
    al = A.align(routes(), reference=ref)
    assert len(al.rows) == 3 and len(al.cells) == len(A.align(routes()).cells)


# --------------------------------------------------------------------------- laws


NOISE = st.text(alphabet="0123456789.abc 血红蛋白尿素", min_size=1, max_size=14)


@st.composite
def references(draw):
    n = draw(st.integers(0, 6))
    out = []
    for i in range(n):
        row = draw(st.integers(0, 5))
        text = draw(NOISE)
        if not text.split():
            text = "x"
        geometry = draw(st.sampled_from(["exact", "exact", "approx"]))
        out.append(geo("tesseract", i, text, row, geometry=geometry, dx=draw(st.integers(-30, 30)),
                       y0=draw(st.integers(80, 330)) if draw(st.booleans()) else None))
    return out


@settings(max_examples=120, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(ref=references(), disputed=st.booleans())
def test_law_reference_changes_geometry_and_nothing_else(ref, disputed):
    """S6 metamorphic: adding any reference leaves candidates, pairing, rows and verdicts as they were; the
    only code that can disappear is ``no_geometry``, and a cell with geometry afterwards names the vision route."""
    base_routes = {"vision": vision(), "mlx": mlx(["血红蛋白 120 g/L", ROWS[1], ROWS[2]] if disputed else ROWS)}
    without = A.align(base_routes)
    with_ref = A.align(base_routes, reference=ref)
    assert candidates(with_ref) == candidates(without)
    assert non_geometry_codes(with_ref) == non_geometry_codes(without)
    assert with_ref.pairs == without.pairs and with_ref.extra == without.extra
    assert with_ref.extra_values == without.extra_values and with_ref.uncertain == without.uncertain
    assert [(r.row, r.label, r.lines, r.bbox_px) for r in with_ref.rows] == [(r.row, r.label, r.lines, r.bbox_px)
                                                                              for r in without.rows]
    assert verdicts(with_ref) == verdicts(without)
    for a, b in zip(with_ref.cells, without.cells):
        assert set(a.uncertain) <= set(b.uncertain)
        assert a.geom in (None, "vision") and (a.geom is None) == (a.col is None)
    assert A.align(base_routes, reference=ref) == with_ref


@settings(max_examples=60, deadline=None)
@given(ref=references())
def test_law_reference_is_order_independent_of_the_other_routes(ref):
    forward = A.align({"vision": vision(), "mlx": mlx()}, reference=ref)
    backward = A.align({"mlx": mlx(), "vision": vision()}, reference=ref)
    assert forward == backward
