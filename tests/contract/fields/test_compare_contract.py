"""WI-07c contract samples (design 02 r2.2-D ①-④, matrix 07c-01..04) and the recorded-page replay (K1).

Each sample is a four-route fixture page (``tests/fixtures/fx-{dup10,colshift,swap,missingrow}/engine/``) run
through the real ``align`` and then ``compare``; the recordings part runs the seven WI-00c3 recorded pages
(vision / mlx / claude / codex vote, tesseract supplies geometry only).
"""

from __future__ import annotations

import json

import pytest

from _support import recordings as R
from klode_kit import items as items_mod
from klode_kit.audit import align as A
from klode_kit.audit import compare as C

pytestmark = pytest.mark.contract_replay

ROUTES4 = ("vision", "mlx", "claude", "codex")


def fixture_compare(name: str) -> tuple[A.Alignment, C.Comparison]:
    routes = {}
    for route in ROUTES4:
        path = R.FIXTURES / name / "engine" / route / "p001.jsonl"
        routes[route] = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    al = A.align(routes)
    return al, C.compare(al, routes=ROUTES4)


def test_dup10_two_cells_agree():
    """07c-01 (L1-S3): two rows both reading ``10`` are two cells, each 4/4 agree, ordinals 0 and 1 - not merged."""
    _, res = fixture_compare("fx-dup10")
    tens = [v for v in res.cells if v.kind == "number" and v.recommended == "10"]
    assert [(v.row, v.k, v.item_label, v.ordinal, v.count) for v in tens] == [(6, 0, "wbc", 0, 2), (7, 0, "urea", 1, 2)]
    for v in tens:
        assert v.agree
        assert v.votes == (("10", ROUTES4),)
        assert v.missing == () and v.uncertain == ()
    assert all(v.agree for v in res.cells)


def test_colshift_disputed_with_item_label():
    """07c-02 (L1-S3): mlx put the red-cell value into the unit column. The value cell is a disagreement carrying
    ``item_label`` rbc; mlx's later fields of the row moved forward, so the row's number cells are
    ``ordinal_shift:mlx`` and its missing places ``missing_cell:mlx``. Every other row agrees."""
    _, res = fixture_compare("fx-colshift")
    v = res.cell(7, "number", 0)
    assert v.item_label == "rbc"
    assert v.verdict == C.MAJORITY and not v.agree
    assert v.recommended == "4.35"
    assert dict(v.votes)["4.35"] == ("vision", "claude", "codex")
    assert "ordinal_shift:mlx" in v.uncertain
    for key in ((7, "number", 1), (7, "unit", 0)):
        w = res.cell(*key)
        assert "missing_cell:mlx" in w.uncertain and not w.agree and w.item_label == "rbc"
    assert all(w.agree for w in res.cells if w.row != 7)


def test_swap_suspect_never_agree():
    """07c-03 (L1-S3): codex swapped the values of rows 3/4 (4.35 <-> 210): both cells are disagreements marked
    ``swap_suspect:codex``, neither agrees, codex's readings stay as read; nothing else is swap_suspect."""
    _, res = fixture_compare("fx-swap")
    a, b = res.cell(7, "number", 0), res.cell(8, "number", 0)
    for v, want, codex in ((a, "4.35", "210"), (b, "210", "4.35")):
        assert "swap_suspect:codex" in v.uncertain
        assert not v.agree and v.verdict == C.MAJORITY
        assert v.recommended == want
        assert v.reading("codex") == codex
    others = [v for v in res.cells if (v.row, v.kind, v.k) not in ((7, "number", 0), (8, "number", 0))]
    assert not any(code.startswith("swap_suspect") for v in others for code in v.uncertain)
    assert all(v.agree for v in others)


def test_missingrow_missing_cell_to_confirm():
    """07c-04 (L1-S3): claude lacks row 5 (alignment row 9): every cell of that row is ``missing_cell:claude``
    (and keeps ``unaligned:claude``); vision/mlx/codex agree on it, which only yields a recommendation, never an
    agreement. codex's merged rows (6, 7) keep ``merged_rows:codex`` and do not agree either."""
    al, res = fixture_compare("fx-missingrow")
    row9 = [v for v in res.cells if v.row == 9]
    assert len(row9) == len([c for c in al.cells if c.row == 9]) >= 3
    for v in row9:
        assert "missing_cell:claude" in v.uncertain and "unaligned:claude" in v.uncertain
        assert v.missing == ("claude",)
        assert len(v.votes) == 1 and v.votes[0][1] == ("vision", "mlx", "codex")
        assert v.verdict == C.MAJORITY and v.recommended == v.reading("vision")
        assert not v.agree
    merged = [v for v in res.cells if v.row in (6, 7)]
    assert merged and all("merged_rows:codex" in v.uncertain and not v.agree for v in merged)
    assert all(v.agree for v in res.cells if v.row not in (6, 7, 9))


# --------------------------------------------------------------------------- recorded pages

# (fixture, page, item, truth row, col): fields WI-00c3 found misread by at least one route (vision 10~9/L,
# lowres 10^12亿L, rx-stamp stamp-covered doses, MLX column-wise rx table / LaTeX units)
MISREAD_FIELDS = {
    ("fx-u1-3labs", "p001", "白细胞", 1, "unit"), ("fx-u1-3labs", "p002", "白细胞", 1, "unit"),
    ("fx-u1-3labs", "p003", "白细胞", 1, "unit"), ("fx-rotated90", "p001", "白细胞", 1, "unit"),
    ("fx-vertical", "p001", "血小板", 3, "unit"), ("fx-lowres", "p001", "白细胞", 1, "unit"),
    ("fx-lowres", "p001", "红细胞", 2, "unit"), ("fx-rx-stamp", "p001", "示例片", 0, "value"),
    ("fx-rx-stamp", "p001", "示例片", 1, "value"), ("fx-rx-stamp", "p001", "示例片", 2, "value"),
}


def recorded(sha: str, order=(*ROUTES4, "tesseract")) -> C.Comparison:
    return C.compare(A.align({e: R.route_rows(e, sha) for e in order}), routes=ROUTES4)


def test_recorded_pages_no_false_auto_agree():
    """K1 on the seven recorded pages (L1-S3, WI-00c3 statistics): every truth field whose cell agrees has the
    truth value (false auto-pass 0); at least 20 truth fields agree; none of the fields WI-00c3 found misread
    agrees; tesseract never votes; no page has an unplaced value line."""
    items = items_mod.default()
    agreed, wrong, seen_misread = 0, [], set()
    for sha in R.SHAS:
        fixture, key = R.page_of(sha)
        truth = json.loads((R.FIXTURES / fixture / "truth.json").read_text(encoding="utf-8"))
        cells = [f for f in truth["fields"] if f["key"] == key]
        res = recorded(sha)
        assert res.uncertain == (), (fixture, key)
        assert all(v.reading("tesseract") is None for v in res.cells)
        for f in sorted(cells, key=lambda f: (f["row"], f["col"])):
            slug = items.resolve(f["item"])[0]
            occurrence = len({c["row"] for c in cells if c["item"] == f["item"] and c["row"] < f["row"]})
            rows = sorted({v.row for v in res.cells if v.item_label == slug})
            row = rows[occurrence]
            v = res.cell(row, "number" if f["col"] == "value" else "unit", 0)
            k = (fixture, key, f["item"], f["row"], f["col"])
            if k in MISREAD_FIELDS:
                seen_misread.add(k)
                assert v is None or not v.agree, (k, v)
            if v is not None and v.agree:
                agreed += 1
                if v.recommended != f["truth"]:
                    wrong.append((k, v.recommended, f["truth"]))
    assert wrong == []
    assert seen_misread == MISREAD_FIELDS
    assert agreed >= 20


@pytest.mark.parametrize("sha", R.SHAS)
def test_recorded_route_order_invariant(sha):
    """07c-06 on real data (L1-S6): reversing the route order fed to align and to compare gives the same votes."""
    a = recorded(sha)
    order = ("tesseract", *reversed(ROUTES4))
    b = C.compare(A.align({e: R.route_rows(e, sha) for e in order}), routes=tuple(reversed(ROUTES4)))
    assert a == b
