"""WI-08a: uncertain reason table and the candidate gate (matrix 08a-05, 08a-06, 08a-07; K1, K2).

The gate is driven through the real WI-07c ``compare`` on hand-built WI-07b alignments: the gate must take the
vote as compare cast it and never relax it (K1: verified(auto) iff every voting route is present and equal and
nothing is uncertain; K2: a non-empty uncertain list is never verified).
"""

from __future__ import annotations

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from klode_kit.audit import align as A
from klode_kit.audit import compare as C
from klode_kit.audit import gate as G
from klode_kit.contract import schema

pytestmark = pytest.mark.unit

VOTERS = ("vision", "mlx", "claude", "codex")
DER = "e" * 64
PAGE_ID = "d-e9f04e9657b8/p001"
REC_ID = "r-0123456789ab"
FIELD_ID = f"{REC_ID}/r003-number-0"

# r2.3 reason table + the codes WI-04a/04b/06/07c/07d added: reason -> the scopes the design allows.
REASON_SCOPES = {
    "route_failed": {"page"},
    "route_missing_page": {"page"},
    "parse_error": {"page"},
    "degenerate_output": {"page"},
    "route_disabled": {"page"},
    "route_unavailable": {"page"},
    "effort_locked": {"page"},
    "unaligned": {"row", "cell"},
    "merged_rows": {"row", "cell"},
    "missing_cell": {"row", "cell"},
    "ordinal_shift": {"row", "cell"},
    "extra_values": {"page", "row", "cell"},
    "swap_suspect": {"cell"},
    "tie": {"cell"},
    "all_differ": {"cell"},
    "no_geometry": {"row", "cell"},
    "no_bbox_crop": {"cell"},
}
WITH_ENGINE = {"route_failed", "route_missing_page", "parse_error", "degenerate_output", "route_disabled",
               "route_unavailable", "effort_locked", "unaligned", "merged_rows", "missing_cell", "ordinal_shift",
               "extra_values", "swap_suspect"}
PAGE_REASONS = {"route_failed", "route_missing_page", "parse_error", "degenerate_output", "route_disabled",
                "route_unavailable", "effort_locked"}


def code_of(reason: str, engine: str = "codex") -> str:
    return f"{reason}:{engine}" if reason in WITH_ENGINE else reason


def cand(value: str) -> A.Candidate:
    return A.Candidate(value, value, 0, (0, len(value)), None, DER)


def alignment(grid, codes=None, extra_values=None) -> A.Alignment:
    """``grid[(row, k)] = {engine: value}`` -> an alignment of number cells (``codes`` per key)."""
    codes = codes or {}
    cells = []
    for (row, k), reads in sorted(grid.items()):
        cands = tuple((e, cand(reads[e])) for e in A.ENGINE_ORDER if e in reads)
        cells.append(A.Cell(row, "number", k, f"c{k}", "vision", 0, 1, "hemoglobin", cands,
                            tuple(sorted(codes.get((row, k), ())))))
    return A.Alignment("vision", (), {}, tuple(cells), (), {}, (), dict(extra_values or {}))


def full(value: str = "128") -> dict:
    return {e: value for e in VOTERS}


def page_entry(reason: str, engine: str) -> dict:
    return G.entry(f"{reason}:{engine}", page_id=PAGE_ID)


def record_with(entries: list[dict]) -> dict:
    return {"rec_id": REC_ID, "doc_id": "d-e9f04e9657b8", "page_id": PAGE_ID, "record_idx": 0, "type": "lab",
            "status": "disputed",
            "candidates": [{"rev": 1, "audit_rev": "eb2b28a29018/a1", "status": "disputed", "uncertain": entries,
                            "created": "2026-10-23T00:00:00Z"}]}


# --------------------------------------------------------------------------- 08a-06


@pytest.mark.parametrize("reason", sorted(REASON_SCOPES))
def test_uncertain_entry_fields_per_reason(reason):
    """08a-06 (S2): every reason of the table yields an entry with reason/scope/fix_action/clear_evidence; the
    scope is one the reason table allows and does not depend on the engine; the entry fits the record contract."""
    e = G.entry(code_of(reason), page_id=PAGE_ID, field_id=FIELD_ID, row=3)
    assert e["reason"] == reason
    assert e["scope"] in REASON_SCOPES[reason]
    for key in ("id", "fix_action", "clear_evidence"):
        assert isinstance(e[key], str) and e[key].strip(), key
    if reason in WITH_ENGINE:
        assert e["engine"] == "codex"
        other = G.entry(code_of(reason, "vision"), page_id=PAGE_ID, field_id=FIELD_ID, row=3)
        assert other["scope"] == e["scope"] and other["engine"] == "vision"
        assert other["id"] != e["id"]
    else:
        assert "engine" not in e
    if e["scope"] == "cell":
        assert e["field_id"] == FIELD_ID
    if reason in PAGE_REASONS:
        assert e["scope"] == "page"
    assert schema.issues("record", record_with([e])) == []


@pytest.mark.parametrize("code", ["looks_odd", "looks_odd:codex", "missing_cell", "tie:codex:x", ""])
def test_unknown_or_malformed_reason_rejected(code):
    """08a-06 guard (S3): a code outside the table, or an engine reason without its engine, is refused."""
    with pytest.raises(ValueError):
        G.entry(code, page_id=PAGE_ID, field_id=FIELD_ID, row=3)


@pytest.mark.parametrize(("route", "status", "error", "reason"), [
    ("codex", "route_missing_page", "replay: no codex output for p001", "route_missing_page"),
    ("claude", "parse_error", "parse_error: schema broken twice", "parse_error"),
    ("vision", "route_failed", "RouteError: replay: injected failure on call 1", "route_failed"),
    ("claude", "route_failed", "route_disabled: claude is disabled in config/routes_enabled.json", "route_disabled"),
    ("vision", "route_failed", "route_unavailable: ocrmac is not installed", "route_unavailable"),
    ("codex", "route_failed", "effort_locked: codex.effort is locked to 'max'", "effort_locked"),
    ("mlx", "route_failed", "RouteError: degenerate_output; rerun route B for this page (failed: mlx: degenerate "
                            "repeat_ratio=0.9 ngram_ratio=0.7)", "degenerate_output"),
    ("claude", "route_failed", "budget_exceeded: the batch budget stopped cloud sending", "route_failed"),
])
def test_page_entries_from_batch_receipt(route, status, error, reason):
    """08a-06 (S2): the batch receipt's per-route page state becomes one page-scope entry with the specific
    reason (WI-04a degenerate_output, WI-04b route_disabled/route_unavailable, WI-06 parse_error/effort_locked)."""
    routes = {r: {"status": "done", "run_id": f"eb2b28a29018/{r}/0123456789ab/t1", "file": f"ocr/{r}/x/p001.jsonl"}
              for r in VOTERS}
    routes[route] = {"status": status, "run_id": f"eb2b28a29018/{route}/0123456789ab/t1", "tries": 2,
                     "error": error}
    page = {"key": "p001", "page_id": PAGE_ID, "doc_id": "d-e9f04e9657b8", "routes": routes, "uncertain": []}
    got = G.page_entries(page)
    assert [(u["reason"], u["scope"], u["engine"]) for u in got] == [(reason, "page", route)]
    assert got[0]["fix_action"] and got[0]["clear_evidence"]


def test_page_entries_none_when_all_done_or_skipped():
    """08a-06 boundary (S2): done and text-layer skipped routes raise nothing."""
    routes = {"vision": {"status": "done", "run_id": "eb2b28a29018/vision/0123456789ab/t1", "file": "f"},
              "mlx": {"status": "skipped", "reason": "text_layer"}}
    assert G.page_entries({"key": "p001", "page_id": PAGE_ID, "routes": routes, "uncertain": []}) == []


# --------------------------------------------------------------------------- 08a-05


@pytest.mark.parametrize("reason", sorted(PAGE_REASONS))
def test_page_level_to_candidate_disputed(reason):
    """08a-05 (S3, r3.3): a page-level uncertain makes the page's candidate disputed even when all four routes
    read every cell alike; no N-1 continuation. Control: the same page without it is verified(auto)."""
    al = alignment({(3, 0): full("128"), (4, 0): full("6.2")})
    cmp = C.compare(al, routes=VOTERS)
    clean = G.judge(cmp, rec_id=REC_ID, page_id=PAGE_ID)
    assert (clean.status, clean.verified_by, clean.uncertain) == (G.VERIFIED, "auto", ())

    blocked = G.judge(cmp, rec_id=REC_ID, page_id=PAGE_ID, page_uncertain=[page_entry(reason, "codex")])
    assert blocked.status == G.DISPUTED
    assert blocked.verified_by is None
    assert [(u["reason"], u["scope"], u.get("engine")) for u in blocked.uncertain] == [(reason, "page", "codex")]


def test_page_level_beats_pending():
    """08a-05 (S3): a page with both a disagreement and a missing route is disputed, not pending_confirm."""
    reads = full("128")
    reads["codex"] = "126"
    cmp = C.compare(alignment({(3, 0): reads}), routes=VOTERS)
    assert G.judge(cmp, rec_id=REC_ID, page_id=PAGE_ID).status == G.PENDING
    v = G.judge(cmp, rec_id=REC_ID, page_id=PAGE_ID, page_uncertain=[page_entry("route_missing_page", "mlx")])
    assert v.status == G.DISPUTED


def test_three_routes_agreeing_is_never_verified():
    """K1 example (S3): three routes agree and codex has no candidate -> pending_confirm with missing_cell:codex;
    the gate does not relax compare's verdict."""
    reads = full("128")
    del reads["codex"]
    cmp = C.compare(alignment({(3, 0): reads}), routes=VOTERS)
    v = G.judge(cmp, rec_id=REC_ID, page_id=PAGE_ID)
    assert v.status == G.PENDING and v.verified_by is None
    assert [(u["reason"], u.get("engine"), u.get("field_id")) for u in v.uncertain] == [
        ("missing_cell", "codex", f"{REC_ID}/{G.slot(3, 'number', 0)}")]


def test_crop_code_blocks_verified():
    """K2 example (S3): a no_bbox_crop merged in from the crop index keeps the record out of verified."""
    cmp = C.compare(alignment({(3, 0): full()}), routes=VOTERS)
    v = G.judge(cmp, rec_id=REC_ID, page_id=PAGE_ID, cell_codes={(3, "number", 0): ["no_bbox_crop"]})
    assert v.status == G.PENDING
    assert [u["reason"] for u in v.uncertain] == ["no_bbox_crop"]


def test_no_cells_is_not_verified():
    """K1 boundary (S2): a page without any cell proves nothing about the routes and is not verified."""
    cmp = C.compare(alignment({}), routes=VOTERS)
    assert G.judge(cmp, rec_id=REC_ID, page_id=PAGE_ID).status == G.PENDING


# --------------------------------------------------------------------------- 08a-07

VALUES = ("1", "2", "7")
ALIGN_CODES = ("no_geometry", "unaligned:mlx", "merged_rows:codex")


@st.composite
def scenes(draw):
    keys = draw(st.lists(st.tuples(st.integers(0, 4), st.integers(0, 1)), unique=True, max_size=6))
    grid, codes, crop = {}, {}, {}
    for key in keys:
        if draw(st.booleans()):
            grid[key] = full(draw(st.sampled_from(VALUES)))
        else:
            grid[key] = {e: v for e in VOTERS
                         if (v := draw(st.one_of(st.none(), st.sampled_from(VALUES)))) is not None}
        if draw(st.integers(0, 4)) == 0:
            codes[key] = draw(st.lists(st.sampled_from(ALIGN_CODES), min_size=1, max_size=2, unique=True))
        if draw(st.integers(0, 6)) == 0:
            crop[(key[0], "number", key[1])] = ["no_bbox_crop"]
    extra = {}
    if draw(st.integers(0, 6)) == 0:
        extra[draw(st.sampled_from(VOTERS))] = (draw(st.integers(0, 9)),)
    page = draw(st.lists(st.sampled_from([("route_missing_page", "codex"), ("route_failed", "vision"),
                                          ("parse_error", "claude")]), max_size=2, unique=True))
    if draw(st.integers(0, 2)) != 0:
        page = []
    return grid, codes, crop, extra, page


@settings(max_examples=300, deadline=None)
@given(scenes())
@example(({(0, 0): full("1"), (1, 0): full("1")}, {}, {}, {}, []))
@example(({(0, 0): full("1"), (1, 0): {"vision": "1", "mlx": "1", "claude": "1"}}, {}, {}, {}, []))
def test_candidate_verified_implies_clean(scene):
    """08a-07 (S4, K1/K2): over random four-route grids, alignment codes, crop codes, page entries and unplaced
    values, the candidate is verified(auto) exactly when the page has a cell, every cell has all four voting
    routes reading the same value, and nothing is uncertain; verified always carries uncertain == []."""
    grid, codes, crop, extra, page = scene
    cmp = C.compare(alignment(grid, codes, extra), routes=VOTERS)
    v = G.judge(cmp, rec_id=REC_ID, page_id=PAGE_ID, cell_codes=crop,
                page_uncertain=[page_entry(r, e) for r, e in page])

    clean = (bool(grid) and not any(c != "no_geometry" for cs in codes.values() for c in cs) and not crop and not extra and not page
             and all(set(reads) == set(VOTERS) and len(set(reads.values())) == 1 for reads in grid.values()))
    assert (v.status == G.VERIFIED) == clean
    if v.status == G.VERIFIED:
        assert v.verified_by == "auto"
        assert v.uncertain == ()
        assert all(c.verdict == C.AGREE and not c.missing for c in cmp.cells)
    else:
        assert v.verified_by is None
        assert v.status == (G.DISPUTED if page else G.PENDING)
    if v.uncertain:  # K2
        assert v.status != G.VERIFIED
