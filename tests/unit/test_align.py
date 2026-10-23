"""WI-07b: cell alignment (matrix 07b-01..09, 07b-12; plan WI-07b; design 02 r2.2-D, R2-08, R3-05).

Contract under test (``klode_kit.audit.align``):

- ``align(routes, *, items=None, tau=0.6) -> Alignment``; ``routes`` maps an engine name to that route's
  ``ocr_line`` rows for one page (``{engine, page_id, der_sha256, line_idx, text, bbox_px, tokens}``).
- **Base route** = the first engine of ``GEOMETRY_ORDER`` (vision, tesseract, pdftext) that has at least one
  ``exact`` token on the page; ``None`` when no route has exact geometry.
- **Rows**: lines of the token-bearing routes, grouped by vertical overlap >= 50 % of the smaller height,
  top to bottom (``Alignment.rows``; ``Row.exact`` = some exact token lies on the row). A page without any
  token-bearing route takes its rows from the route with the most lines (ties: ``ENGINE_ORDER``).
- **Pairs**: every route is aligned monotonically to the rows: a line with a ``bbox_px`` by vertical overlap,
  a line without one (cloud routes, MLX multi-line blocks) by text similarity >= ``tau``; ``Pair(rows, seq,
  lines)`` - ``rows`` is one row, or two consecutive rows for a line that is their concatenation
  (``merged_rows``); ``seq`` indexes the route's own row sequence; ``lines`` are its ``line_idx`` values.
- **Cells**: the fields (``audit.fields.extract``) of each route's aligned text, keyed
  ``(row, kind, k)`` - ``k`` is the in-row index among fields of that kind. ``col`` is the token-``x0``
  column cluster, ``geom`` the engine whose token placed it (``None`` on a row without exact geometry);
  ``ordinal``/``count``: occurrence index / number of cells of the same kind with the same value on the page.
  ``candidates`` are per engine (``value``, ``norm``, ``line_idx``, ``span``, ``bbox_px``...), with
  ``line[span] == value`` in the route's own line.
- **Uncertain**: ``Alignment.uncertain`` holds ``(code, row)``: ``unaligned:<engine>`` (the route has no line
  for the row), ``merged_rows:<engine>`` (both rows of a merged pair); a cell additionally carries
  ``no_geometry`` when its row has no exact geometry (approx boxes count only with exact corroboration on the
  same row - R3-05). ``Alignment.extra[engine]`` lists route rows that match no row.

Laws (matrix WI-07b):
- alignment recovers a known correspondence up to value equivalence (07b-12, S4); index pairs are strictly
  increasing (07b-06, S4);
- approx corroboration is judged per row, not per page (07b-04, S4 + ``@example``);
- the order of the non-base routes does not change the result (07b-09, S6).
"""

from __future__ import annotations

import difflib
import json
import re
from collections import Counter
from pathlib import Path

import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from klode_kit import items as items_mod
from klode_kit.audit import align as A
from klode_kit.audit.fields import fold

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures"
ROUTES4 = ("vision", "mlx", "claude", "codex")
DER = "d" * 64
PAGE = "d-000000000000/p001"
COLS = (40, 330, 470, 620, 690)

# Row texts for generated pages: distinct items, pairwise dissimilar (precondition checked below), so a line can
# only be confused with an identical line. NOISE lines share no character with them (stamps, page footers).
POOL = (
    "血红蛋白 128 g/L",
    "尿素 7.3 mmol/L",
    "白细胞 6.2 10^9/L",
    "肌酐 64 pg",
    "谷丙转氨酶 31 IU",
    "红细胞沉降率 15 mm/h",
    "淋巴细胞百分比 22.4 %",
    "示例指标 0.05 fL",
)
NOISE = ("专用章", "第页共页", "检查者签名", "核对")


def _sim(a: str, b: str) -> float:
    a, b = re.sub(r"\s+", "", fold(a)), re.sub(r"\s+", "", fold(b))
    return difflib.SequenceMatcher(None, a, b).ratio()


# generator precondition, not a property of the implementation
assert all(_sim(a, b) < 0.5 for i, a in enumerate(POOL) for b in POOL[i + 1:])
assert all(_sim(n, p) < 0.3 for n in NOISE for p in POOL)


# --------------------------------------------------------------------------- line builders


def geo_line(engine: str, idx: int, text: str, row: int, *, geometry: str | None = "exact", tokens: bool = True,
             y0: int | None = None, height: int = 24) -> dict:
    """A geometric line: pieces of ``text`` placed in fixed table columns; ``row`` sets the y position."""
    top = 100 + 50 * row if y0 is None else y0
    toks = []
    for i, m in enumerate(re.finditer(r"\S+", text)):
        x0 = COLS[i] if i < len(COLS) else COLS[-1] + 60 * (i - len(COLS) + 1)
        toks.append({"text": m.group(), "bbox_px": [x0, top, x0 + 12 * len(m.group()), top + height],
                     "span": [m.start(), m.end()], "geometry": geometry})
    bbox = [min(t["bbox_px"][0] for t in toks), top, max(t["bbox_px"][2] for t in toks), top + height]
    return {"engine": engine, "page_id": PAGE, "der_sha256": DER, "line_idx": idx, "text": text, "bbox_px": bbox,
            "tokens": toks if tokens and geometry is not None else None}


def cloud_line(engine: str, idx: int, text: str) -> dict:
    return {"engine": engine, "page_id": PAGE, "der_sha256": DER, "line_idx": idx, "text": text, "bbox_px": None,
            "tokens": None}


def cloud(engine: str, texts: list[str]) -> list[dict]:
    return [cloud_line(engine, i, t) for i, t in enumerate(texts)]


def fixture_routes(name: str, key: str = "p001") -> dict[str, list[dict]]:
    out = {}
    for route in ROUTES4:
        path = FIXTURES / name / "engine" / route / f"{key}.jsonl"
        out[route] = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    return out


def truth(name: str) -> dict:
    return json.loads((FIXTURES / name / "truth.json").read_text(encoding="utf-8"))


def cell_at(al: A.Alignment, row: int, kind: str, k: int = 0) -> A.Cell | None:
    hits = [c for c in al.cells if (c.row, c.kind, c.k) == (row, kind, k)]
    assert len(hits) <= 1, hits
    return hits[0] if hits else None


def truth_cell(al: A.Alignment, row: int, col: str) -> A.Cell:
    """value -> first number of the row; unit -> first unit field of the row."""
    c = cell_at(al, row, "number" if col == "value" else "unit")
    assert c is not None, (row, col)
    return c


def codes(al: A.Alignment, prefix: str) -> set[int]:
    return {row for code, row in al.uncertain if code == prefix}


# --------------------------------------------------------------------------- 07b-01 base route


def test_base_route_first_exact():
    """07b-01 (L1-S2): vision has only approx tokens, tesseract has exact ones -> the base is tesseract;
    boundaries: one exact vision token makes vision the base; no exact token anywhere -> no base and every cell
    is no_geometry; a pdftext-only page uses pdftext."""
    texts = POOL[:3]
    vision_approx = [geo_line("vision", i, t, i, geometry="approx") for i, t in enumerate(texts)]
    tess = [geo_line("tesseract", i, t, i) for i, t in enumerate(texts)]
    al = A.align({"vision": vision_approx, "tesseract": tess, "claude": cloud("claude", list(texts))})
    assert al.base == "tesseract"

    vision_one_exact = [geo_line("vision", 0, texts[0], 0)] + vision_approx[1:]
    al = A.align({"tesseract": tess, "vision": vision_one_exact})
    assert al.base == "vision"

    al = A.align({"vision": vision_approx, "claude": cloud("claude", list(texts))})
    assert al.base is None
    assert al.cells and all(A.NO_GEOMETRY in c.uncertain for c in al.cells)

    pdf = [geo_line("pdftext", i, t, i) for i, t in enumerate(texts)]
    assert A.align({"pdftext": pdf}).base == "pdftext"


# --------------------------------------------------------------------------- 07b-02 row merge boundary


def test_row_merge_overlap_boundary():
    """07b-02 (L1-S2): two base boxes overlapping 49 % of the smaller height stay two rows; 50 % is one row."""
    a = geo_line("vision", 0, "血红蛋白 128", 0, y0=0, height=100)
    b49 = geo_line("vision", 1, "g/L", 0, y0=51, height=100)
    b50 = geo_line("vision", 1, "g/L", 0, y0=50, height=100)
    b49["tokens"][0]["bbox_px"][0:3:2] = [470, 506]
    b50["tokens"][0]["bbox_px"][0:3:2] = [470, 506]
    b49["bbox_px"][0:3:2] = [470, 506]
    b50["bbox_px"][0:3:2] = [470, 506]
    assert len(A.align({"vision": [a, b49]}).rows) == 2
    merged = A.align({"vision": [a, b50]})
    assert len(merged.rows) == 1
    assert dict(merged.rows[0].lines)["vision"] == (0, 1)


# --------------------------------------------------------------------------- 07b-03 columns


def test_col_cluster_by_x0():
    """07b-03 (L1-S1): fx-labs-1x replay: every truth column maps to exactly one column id, distinct truth
    columns to distinct ids, and the cell sits on the vision row the truth names."""
    al = A.align(fixture_routes("fx-labs-1x"))
    assert al.base == "vision"
    by_col: dict[str, set] = {}
    for f in truth("fx-labs-1x")["fields"]:
        row = int(f["field_id"].split("-l")[1].split(":")[0])
        assert dict(al.rows[row].lines)["vision"] == (row,)
        c = truth_cell(al, row, f["col"])
        assert c.col is not None and c.geom == "vision"
        by_col.setdefault(f["col"], set()).add(c.col)
    assert all(len(ids) == 1 for ids in by_col.values()), by_col
    assert len({next(iter(ids)) for ids in by_col.values()}) == len(by_col)


# --------------------------------------------------------------------------- 07b-04 / 07b-05 approx geometry


@st.composite
def approx_pages(draw):
    n = draw(st.integers(2, 8))
    return n, draw(st.integers(0, n - 1)), draw(st.sampled_from(["tesseract-base", "vision-base"]))


def approx_page(n: int, t: int, layout: str) -> dict[str, list[dict]]:
    texts = list(POOL[:n])
    if layout == "tesseract-base":
        routes = {"vision": [geo_line("vision", i, x, i, geometry="approx") for i, x in enumerate(texts)],
                  "tesseract": [geo_line("tesseract", j, x, i)
                                for j, (i, x) in enumerate((i, x) for i, x in enumerate(texts) if i != t)]}
    else:
        routes = {"vision": [geo_line("vision", i, x, i, geometry="approx" if i == t else "exact")
                             for i, x in enumerate(texts)]}
    routes["mlx"] = [geo_line("mlx", i, x, i, tokens=False) for i, x in enumerate(texts)]
    routes["claude"] = cloud("claude", texts)
    routes["codex"] = cloud("codex", texts)
    return routes


@settings(max_examples=80, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(approx_pages())
@example((2, 1, "vision-base"))
def test_approx_without_exact_no_geometry(case):
    """07b-04 (L1-S4): a page of any size where every row but the target has exact tokens (tesseract, or vision
    itself) and the target row has only approx vision boxes, four routes reading identically: exactly the target
    row's cells are no_geometry - judged per row, never per page. ``@example``: the fx-approx-only shape
    (target only approx, another row exact, four routes agree on both)."""
    n, t, layout = case
    al = A.align(approx_page(n, t, layout))
    assert al.base == ("tesseract" if layout == "tesseract-base" else "vision")
    assert len(al.rows) == n
    flagged = {c.row for c in al.cells if A.NO_GEOMETRY in c.uncertain}
    assert flagged == {t}
    assert {c.row for c in al.cells} == set(range(n))
    for c in al.cells:
        assert set(c.engines) >= set(ROUTES4)
        if c.row == t:
            assert c.col is None and c.geom is None
        else:
            assert c.col is not None


def test_approx_only_fixture_row():
    """07b-04 companion (L1-S2): fx-approx-only - row 2 (vision line 7) only has approx tokens and no other exact
    route: its cells are no_geometry, no other cell is."""
    al = A.align(fixture_routes("fx-approx-only"))
    assert {c.row for c in al.cells if A.NO_GEOMETRY in c.uncertain} == {7}
    assert not al.uncertain


def test_approx_with_exact_corroboration_used():
    """07b-05 (L1-S2): vision approx boxes on rows that also carry tesseract exact boxes are used: no cell is
    no_geometry and the vision (approx) token places the cell."""
    texts = list(POOL[:4])
    routes = {"vision": [geo_line("vision", i, x, i, geometry="approx") for i, x in enumerate(texts)],
              "tesseract": [geo_line("tesseract", i, x, i) for i, x in enumerate(texts)],
              "claude": cloud("claude", texts)}
    al = A.align(routes)
    assert al.base == "tesseract"
    assert al.cells
    assert all(A.NO_GEOMETRY not in c.uncertain for c in al.cells)
    assert all(c.geom == "vision" and c.col is not None for c in al.cells)
    assert all(r.exact for r in al.rows)


# --------------------------------------------------------------------------- edits (07b-06, 07b-09, 07b-12)


@st.composite
def base_texts(draw, max_rows: int = 9):
    n = draw(st.integers(1, max_rows))
    texts: list[str] = []
    for _ in range(n):
        if texts and draw(st.integers(0, 3)) == 0:
            texts.append(texts[-1])          # adjacent duplicate (dup10 shape)
        else:
            texts.append(draw(st.sampled_from(POOL)))
    return texts


@st.composite
def edits(draw, texts: list[str]):
    """Cloud lines derived from ``texts`` at known positions: ``("row", i)``, ``("merge", i)`` (rows i, i+1) or
    ``("insert",)``; returns (lines, truth, deleted rows)."""
    lines, where, deleted = [], [], []
    i = 0
    while i < len(texts):
        if draw(st.integers(0, 4)) == 0:
            lines.append(draw(st.sampled_from(NOISE)))
            where.append(("insert",))
        op = draw(st.sampled_from(["keep", "keep", "keep", "delete", "merge"]))
        if op == "merge" and i + 1 < len(texts):
            lines.append(texts[i] + " " + texts[i + 1])
            where.append(("merge", i))
            i += 2
        elif op == "delete":
            deleted.append(i)
            i += 1
        else:
            lines.append(texts[i])
            where.append(("row", i))
            i += 1
    return lines, where, deleted


@st.composite
def edited_pages(draw):
    texts = draw(base_texts())
    lines, where, deleted = draw(edits(texts))
    return texts, lines, where, deleted


def run_ids(texts: list[str]) -> list[int]:
    out, run = [], -1
    for i, t in enumerate(texts):
        if i == 0 or t != texts[i - 1]:
            run += 1
        out.append(run)
    return out


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(edited_pages())
@example((["尿素 7.3 mmol/L", "尿素 7.3 mmol/L", "血红蛋白 128 g/L"], ["尿素 7.3 mmol/L", "血红蛋白 128 g/L"],
          [("row", 1), ("row", 2)], [0]))
@example((list(POOL[:4]), [POOL[0], POOL[2], POOL[3]], [("row", 0), ("row", 2), ("row", 3)], [1]))
@example(([POOL[0]] * 5 + [POOL[1], POOL[0]], [POOL[0], POOL[0], f"{POOL[0]} {POOL[0]}", POOL[0]],
          [("row", 0), ("row", 1), ("merge", 3), ("row", 6)], [2, 5]))
@example(([POOL[0], POOL[0], POOL[1], POOL[0]], [POOL[0], POOL[0]], [("row", 0), ("row", 1)], [2, 3]))
@example(([POOL[0], POOL[3], POOL[0], POOL[3], POOL[0]], [POOL[0], POOL[3], POOL[0]],
          [("row", 0), ("row", 3), ("row", 4)], [1, 2]))
@example(([POOL[0], POOL[3], POOL[0], POOL[3], POOL[0]], [POOL[0], POOL[3], POOL[0]],
          [("row", 0), ("row", 1), ("row", 2)], [3, 4]))
def test_alignment_recovers_known_correspondence(case):
    """07b-12 (L1-S4): deletions, insertions and merges at known positions (adjacent equal rows allowed):
    every pair's texts equal the true correspondence's texts (value equivalence), every surviving line is paired
    and no inserted line is; ``unaligned`` rows fall in the same-value runs of the deleted rows (as a multiset);
    ``merged_rows`` marks exactly the rows of merged pairs. No "one row missing shifts everything by one"."""
    texts, lines, where, deleted = case
    routes = {"vision": [geo_line("vision", i, t, i) for i, t in enumerate(texts)], "claude": cloud("claude", lines)}
    al = A.align(routes)
    pairs = al.pairs["claude"]
    for p in pairs:
        w = where[p.seq]
        assert p.lines == (p.seq,)
        if len(p.rows) == 1:
            assert w[0] == "row" and texts[p.rows[0]] == texts[w[1]], (p, w)
        else:
            assert w[0] == "merge" and p.rows == (p.rows[0], p.rows[0] + 1), (p, w)
            assert (texts[p.rows[0]], texts[p.rows[1]]) == (texts[w[1]], texts[w[1] + 1]), (p, w)
    assert {p.seq for p in pairs} == {j for j, w in enumerate(where) if w[0] != "insert"}
    assert set(al.extra.get("claude", ())) == {j for j, w in enumerate(where) if w[0] == "insert"}
    # user-approved 2026-10-23: text multiset (value equivalence); row/run positions are ambiguous (A M A M A)
    unaligned = codes(al, "unaligned:claude")
    assert Counter(texts[r] for r in unaligned) == Counter(texts[d] for d in deleted)
    assert codes(al, "merged_rows:claude") == {r for p in pairs if len(p.rows) == 2 for r in p.rows}


@st.composite
def shuffled_pages(draw):
    texts = draw(base_texts())
    lines, _, _ = draw(edits(texts))
    order = draw(st.permutations(range(len(lines))))
    keep = draw(st.lists(st.booleans(), min_size=len(lines), max_size=len(lines)))
    return texts, [lines[i] for i in order if keep[i]]


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(shuffled_pages())
def test_alignment_monotonic(case):
    """07b-06 (L1-S4): base rows + a cloud route with insertions, deletions, merges and an arbitrary shuffle:
    the aligned index pairs are strictly increasing on both sides; each route row is used at most once."""
    texts, lines = case
    al = A.align({"vision": [geo_line("vision", i, t, i) for i, t in enumerate(texts)],
                  "claude": cloud("claude", lines)})
    pairs = al.pairs["claude"]
    for prev, cur in zip(pairs, pairs[1:]):
        assert prev.rows[-1] < cur.rows[0] and prev.seq < cur.seq, (prev, cur)
    for p in pairs:
        assert all(0 <= r < len(texts) for r in p.rows) and 0 <= p.seq < len(lines)
    assert len({p.seq for p in pairs}) == len(pairs)


@st.composite
def multi_route_pages(draw):
    texts = draw(base_texts(max_rows=7))
    routes = {"tesseract": [geo_line("tesseract", i, t, i) for i, t in enumerate(texts)]}
    keep_v = draw(st.lists(st.booleans(), min_size=len(texts), max_size=len(texts)))
    routes["vision"] = [geo_line("vision", j, t, i, geometry="approx")
                        for j, (i, t) in enumerate((i, t) for i, t in enumerate(texts) if keep_v[i])]
    keep_m = draw(st.lists(st.booleans(), min_size=len(texts), max_size=len(texts)))
    routes["mlx"] = [geo_line("mlx", j, t, i, tokens=False)
                     for j, (i, t) in enumerate((i, t) for i, t in enumerate(texts) if keep_m[i])]
    for engine in ("claude", "codex"):
        routes[engine] = cloud(engine, draw(edits(texts))[0])
    return routes


@settings(max_examples=80, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(multi_route_pages(), st.data())
def test_nonbase_engine_permutation_invariant(routes, data):
    """07b-09 (L1-S6): permuting the non-base routes (vision approx, mlx boxes, claude, codex with edits) gives
    the same cell set, the same uncertain set and the same pairs. Also on every generated page: each candidate's
    span reads its value from its own line, bbox_px is null exactly on the cloud routes (S4)."""
    nonbase = [e for e in routes if e != "tesseract"]
    perm = data.draw(st.permutations(nonbase))
    first = A.align(routes)
    second = A.align({"tesseract": routes["tesseract"], **{e: routes[e] for e in perm}})
    assert first.base == second.base == "tesseract"
    assert set(first.cells) == set(second.cells)
    assert set(first.uncertain) == set(second.uncertain)
    assert {e: tuple(p) for e, p in first.pairs.items()} == {e: tuple(p) for e, p in second.pairs.items()}
    texts = {e: {ln["line_idx"]: ln["text"] for ln in lines} for e, lines in routes.items()}
    for c in first.cells:
        for engine, cand in c.candidates:
            s, e = cand.span
            assert texts[engine][cand.line_idx][s:e] == cand.value
            assert (cand.bbox_px is None) == (engine in ("claude", "codex"))


# --------------------------------------------------------------------------- 07b-07 / 07b-08 fixtures


def test_unaligned_and_merged_rows():
    """07b-07 (L1-S3): fx-missingrow - claude lacks table row 4 (vision line 9), codex joins rows 1 and 2
    (vision lines 6, 7) into one line. ``unaligned:claude`` is exactly row 9, ``merged_rows:codex`` exactly rows
    6 and 7, nothing else is uncertain; every other row pairs with the line of the same text, the merged pair
    is codex line 6 on rows (6, 7), and the merged line's fields land on both rows with their spans."""
    routes = fixture_routes("fx-missingrow")
    al = A.align(routes)
    assert set(al.uncertain) == {("unaligned:claude", 9), ("merged_rows:codex", 6), ("merged_rows:codex", 7)}
    vision = [ln["text"] for ln in routes["vision"]]
    for engine in ROUTES4:
        texts = [ln["text"] for ln in routes[engine]]
        for p in al.pairs[engine]:
            if len(p.rows) == 1:
                assert texts[p.lines[0]] == vision[p.rows[0]], (engine, p)
        covered = {r for p in al.pairs[engine] for r in p.rows}
        assert covered == set(range(len(vision))) - ({9} if engine == "claude" else set())
    assert [p for p in al.pairs["codex"] if len(p.rows) == 2] == [A.Pair(rows=(6, 7), seq=6, lines=(6,))]
    for f in truth("fx-missingrow")["fields"]:
        row = int(f["field_id"].split("-l")[1].split(":")[0])
        c = truth_cell(al, row, f["col"])
        for engine in ROUTES4:
            want = f["routes"].get(engine)
            got = c.candidate(engine)
            if want is None:
                assert got is None, (f["field_id"], engine)
            else:
                assert (got.value, got.line_idx, list(got.span)) == (want["value"], want["line_idx"], want["span"])


def test_duplicate_values_keep_ordinal():
    """07b-08 (L1-S2): fx-dup10 - the two value-10 cells (rows 6, 7) stay two cells, ordinals 0 and 1, count 2,
    each read by all four routes; the two 10^9/L unit cells likewise."""
    al = A.align(fixture_routes("fx-dup10"))
    tens = [c for c in al.cells if c.kind == "number" and c.candidate("vision") and c.candidate("vision").norm == "10"]
    assert [(c.row, c.ordinal, c.count) for c in sorted(tens, key=lambda c: c.row)] == [(6, 0, 2), (7, 1, 2)]
    assert all(c.engines == ROUTES4 and {c.candidate(e).value for e in ROUTES4} == {"10"} for c in tens)
    units = sorted((c for c in al.cells if c.kind == "unit" and c.candidate("vision").norm == "10^9/L"),
                   key=lambda c: c.row)
    assert [(c.row, c.ordinal, c.count) for c in units] == [(6, 0, 2), (8, 1, 2)]


# --------------------------------------------------------------------------- other contract points


def test_extra_line_not_paired_and_kept_out_of_cells():
    """A cloud line matching no row (a stamp) is listed in ``extra`` and contributes no candidate."""
    texts = list(POOL[:3])
    al = A.align({"vision": [geo_line("vision", i, t, i) for i, t in enumerate(texts)],
                  "claude": cloud("claude", [texts[0], "专用章", texts[1], texts[2]])})
    assert al.extra["claude"] == (1,)
    assert all(c.candidate("claude") is None or c.candidate("claude").line_idx != 1 for c in al.cells)
    assert not al.uncertain


def test_line_sim_tau_boundary():
    """tau decides whether a cloud line with one differing digit still aligns (sim 0.9)."""
    routes = {"vision": [geo_line("vision", 0, "血红蛋白 128 g/L", 0)], "claude": cloud("claude", ["血红蛋白 129 g/L"])}
    loose = A.align(routes)
    assert cell_at(loose, 0, "number").candidate("claude").value == "129"
    strict = A.align(routes, tau=0.95)
    assert set(strict.uncertain) == {("unaligned:claude", 0)}
    assert strict.extra["claude"] == (0,)
    assert cell_at(strict, 0, "number").candidate("claude") is None


def test_mlx_column_output_rebuilt_into_rows():
    """F-3: MLX emits a prescription table column by column, one box per cell; the boxes rebuild the rows, so
    each row's dose is read from the right row."""
    rows = [("示例片", "10mg", "每日1次"), ("示例片", "5mg", "每日2次"), ("示例片", "0.25g", "必要时")]
    vision = [geo_line("vision", i, " ".join(r), i) for i, r in enumerate(rows)]
    mlx, idx = [], 0
    for col in range(3):
        for i, r in enumerate(rows):
            ln = geo_line("mlx", idx, r[col], i, tokens=False)
            x0 = COLS[(0, 1, 4)[col]]
            ln["bbox_px"][0], ln["bbox_px"][2] = x0, x0 + 12 * len(r[col])
            ln["block_bbox"] = list(ln["bbox_px"])
            mlx.append(ln)
            idx += 1
    al = A.align({"vision": vision, "mlx": mlx})
    assert not al.uncertain
    for i, r in enumerate(rows):
        drug = cell_at(al, i, "drug")
        assert drug.candidate("mlx").dose == drug.candidate("vision").dose == r[1]
        num = cell_at(al, i, "number")
        assert num.candidate("mlx").value == r[1] and num.candidate("mlx").line_idx == 3 + i
        assert num.candidate("mlx").span == (0, len(r[1]))
        assert num.candidate("mlx").bbox_px is not None


def test_mlx_block_lines_text_aligned_inside_their_block():
    """MLX lines of a multi-line block have no bbox_px: they align by text, but only to rows inside their
    block_bbox - a block line elsewhere on the page stays unaligned."""
    texts = list(POOL[:4])
    vision = [geo_line("vision", i, t, i) for i, t in enumerate(texts)]
    block = [0, 100 + 50 * 2, 900, 100 + 50 * 3 + 24]   # covers rows 2 and 3 only
    mlx = [dict(cloud_line("mlx", j, t), block_bbox=block) for j, t in enumerate([texts[0], texts[2], texts[3]])]
    al = A.align({"vision": vision, "mlx": mlx})
    assert [p.rows for p in al.pairs["mlx"]] == [(2,), (3,)]
    assert al.extra["mlx"] == (0,)
    assert set(al.uncertain) == {("unaligned:mlx", 0), ("unaligned:mlx", 1)}


def test_empty_route_unaligned_everywhere_and_empty_page():
    """A route with no line is unaligned on every row; no routes at all is an empty alignment."""
    texts = list(POOL[:2])
    al = A.align({"vision": [geo_line("vision", i, t, i) for i, t in enumerate(texts)], "codex": []})
    assert set(al.uncertain) == {("unaligned:codex", 0), ("unaligned:codex", 1)}
    empty = A.align({})
    assert (empty.base, empty.rows, empty.cells, empty.uncertain) == (None, (), (), ())


def test_cloud_only_page_rows_from_text_all_no_geometry():
    """No geometric route at all: rows come from the route with the most lines (ties: engine order), all cells
    no_geometry."""
    texts = list(POOL[:3])
    al = A.align({"codex": cloud("codex", texts), "claude": cloud("claude", texts[:2])})
    assert al.base is None and len(al.rows) == 3
    assert set(al.uncertain) == {("unaligned:claude", 2)}
    assert all(A.NO_GEOMETRY in c.uncertain for c in al.cells)
    assert all(r.bbox_px is None for r in al.rows)


def test_item_label_majority_and_new_items():
    """item_label: the row's leading non-numeric text, majority over routes, normalised through items.yaml;
    a split tesseract label does not win; an unknown item with values is reported in new_items."""
    texts = ["血红蛋白 128 g/L", "某某指数 3.2 mmol/L", "日期：2026-09-18"]
    routes = {"vision": [geo_line("vision", i, t, i, geometry="approx") for i, t in enumerate(texts)],
              "tesseract": [geo_line("tesseract", 0, "血红 蛋白 128 g/L", 0)]
              + [geo_line("tesseract", i, t, i) for i, t in enumerate(texts) if i],
              "claude": cloud("claude", texts), "codex": cloud("codex", texts)}
    al = A.align(routes)
    assert [r.item_label for r in al.rows] == ["hemoglobin", "某某指数", "日期"]
    assert {c.item_label for c in al.cells if c.row == 0} == {"hemoglobin"}
    assert al.new_items == ("某某指数",)


def test_items_injected():
    """The caller's items table is the one used."""
    custom = items_mod.Items([items_mod.Item("custom-urea", None, ("尿素",))])
    al = A.align({"vision": [geo_line("vision", 0, "尿素 7.3 mmol/L", 0)]}, items=custom)
    assert al.rows[0].item_label == "custom-urea"
