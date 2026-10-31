"""WI-33f: ``extra_values`` is ONE page-level confirm item per (page, engine), not a code on every cell
(real-data pilot P11, decision D-37).

Law (K1 refined; the safety property is kept, the blast radius is not):

    (a) extra_values never adds cell-level uncertainty: with or without unplaced value lines the same cells agree
        and carry the same codes;
    (b) a record with an unresolved extra_values page item is never ``verified``;
    (c) items on a page's confirm page = cell disagreements + one per engine with unplaced value lines.

The page item lists the unplaced lines (route row, ``line_idx``, text, crop when a box exists) and is resolved by
one human action on the confirm page: ``确认无遗漏`` (``none`` + ``evidence.reason: acknowledged``) or a typed value
(``manual``; needs a region, so a line with a box). The "valued with no digit" root cause: ``Alignment.extra_values``
holds *route-row sequence numbers* (rows are re-sorted top to bottom), not ``line_idx``; anyone reading
``lines[seq]`` saw an unrelated (CJK-only) line. ``Alignment.extra_lines`` now carries ``line_idx`` and the text.
"""

from __future__ import annotations

import io
import json
import shutil
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from _support import writeback_fx as W
from _support.htmltree import parse
from klode_kit.audit import align as A
from klode_kit.audit import compare as C
from klode_kit.audit import confirm_page as CP
from klode_kit.audit import fields as F
from klode_kit.audit import gate as G
from klode_kit.audit import whitelist as WL
from klode_kit.audit import writeback as WB
from klode_kit.contract import schema

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures"
ROUTES4 = ("vision", "mlx", "claude", "codex")
VALS = ("1", "2", "10")
PAGE_ID = "d-e9f04e9657b8/p001"
REC_ID = "r-0123456789ab"
LINE = "血糖 5.6 mmol/L"


# --------------------------------------------------------------------------- builders


def fixture_routes(name: str, key: str = "p001") -> dict[str, list[dict]]:
    out = {}
    for route in ROUTES4:
        path = FIXTURES / name / "engine" / route / f"{key}.jsonl"
        out[route] = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    return out


def routes_with(extra: dict[str, list[dict]]) -> dict[str, list[dict]]:
    base = fixture_routes("fx-dup10")
    der = base["claude"][0]["der_sha256"]
    for engine, specs in extra.items():
        nxt = max(ln["line_idx"] for ln in base[engine]) + 1
        for i, (text, box) in enumerate(specs):
            base[engine].append({"bbox_px": box, "der_sha256": der, "engine": engine, "line_idx": nxt + i,
                                 "page_id": base[engine][0]["page_id"], "text": text, "tokens": None})
    return base


def cand(norm: str) -> A.Candidate:
    return A.Candidate(norm, norm, 0, (0, len(norm)), None, None)


def cell(row, kind, k, reads, uncertain=()):
    order = [e for e in A.ENGINE_ORDER if e in reads]
    return A.Cell(row, kind, k, None, None, 0, 1, None,
                  tuple((e, cand(reads[e])) for e in order if reads[e] is not None), tuple(uncertain))


def xl(seq: int, text: str = "示 5") -> A.ExtraLine:
    return A.ExtraLine(seq, (seq,), text, None, None, None)


def alignment(cells, extras=None):
    """``extras``: ``{engine: (seq, ...)}`` -> ``extra_values`` and the matching ``extra_lines``."""
    extras = extras or {}
    return A.Alignment(None, (), {}, tuple(cells), (), {}, (), extra_values=dict(extras),
                       extra_lines={e: tuple(xl(s) for s in seqs) for e, seqs in extras.items()})


# --------------------------------------------------------------------------- align: lines, not row numbers


def test_extra_lines_carry_line_idx_and_text_not_the_route_row_number():
    """S1: two unplaced valued mlx lines whose reading order is the opposite of their ``line_idx``: the route-row
    numbers (``extra_values``) are 9 and 10, the lines are idx 21 and 20 - each ExtraLine names its own line."""
    low, high = "血糖 5.6 mmol/L", "备注 7 项"
    routes = routes_with({"mlx": [(low, [40, 480, 300, 500]), (high, [40, 455, 300, 475])]})
    al = A.align(routes)
    assert al.extra_values["mlx"] == (9, 10)
    lines = {ln["line_idx"]: ln["text"] for ln in routes["mlx"]}
    got = al.extra_lines["mlx"]
    assert [(x.seq, x.line_idx, x.text) for x in got] == [(9, (10,), high), (10, (9,), low)]
    for x in got:
        assert x.text == lines[x.line_idx[0]] and x.seq != x.line_idx[0]
        assert x.bbox_px is not None and x.der_sha256 and x.run_id is None


def test_cjk_only_unplaced_lines_are_not_valued_but_a_drug_is():
    """S2: a stamp / footer made of CJK words with no digit, date or drug is unplaced (``extra``) but never valued;
    a synthetic drug name is a value by design."""
    routes = routes_with({"mlx": [("示例专用章", [40, 455, 300, 475]), ("页脚 备注", [40, 480, 300, 500]),
                                  ("示例片 一日三次", [40, 505, 300, 520])]})
    al = A.align(routes)
    assert len(al.extra["mlx"]) == 3
    assert [x.text for x in al.extra_lines["mlx"]] == ["示例片 一日三次"]
    assert al.extra_values["mlx"] == tuple(x.seq for x in al.extra_lines["mlx"])


@settings(max_examples=60, deadline=None)
@given(st.lists(st.text(alphabet="页脚备注汉字章盖印科室", min_size=1, max_size=8), min_size=1, max_size=4))
def test_digitless_cjk_lines_never_become_extra_lines(words):
    """S4: whatever CJK-only (no digit, no drug) text is left unplaced, ``extra_lines`` / ``extra_values`` stay
    empty; and every listed ExtraLine really holds a number, date or drug."""
    specs = [(w, [40, 455 + 30 * i, 300, 475 + 30 * i]) for i, w in enumerate(words)]
    al = A.align(routes_with({"mlx": specs}))
    assert al.extra_lines.get("mlx", ()) == () and al.extra_values.get("mlx", ()) == ()
    for lines in al.extra_lines.values():
        for x in lines:
            assert any(f.kind in A.VALUE_KINDS for f in F.extract(x.text))


# --------------------------------------------------------------------------- compare: law (a)


def test_unplaced_value_line_no_longer_blocks_the_cells():
    """S1: dup10 + a claude-only value line: every cell still agrees with no code; the doubt is one page item."""
    al = A.align(routes_with({"claude": [(LINE, None)]}))
    res = C.compare(al, routes=ROUTES4, whitelist=WL.EMPTY)
    assert res.cells and all(v.agree and v.uncertain == () for v in res.cells)
    assert [code for code, _ in res.uncertain] == ["extra_values:claude"]
    (item,) = res.page_items
    assert (item.code, item.engine) == ("extra_values:claude", "claude")
    assert [(x.line_idx, x.text) for x in item.lines] == [((9,), LINE)]


def test_one_page_item_per_engine_in_engine_order():
    """S2: two engines with unplaced lines, several lines each: one item per engine, ENGINE_ORDER."""
    al = A.align(routes_with({"codex": [(LINE, None), ("尿酸 300 umol/L", None)], "claude": [("钾 4.1 mmol/L", None)]}))
    res = C.compare(al, routes=ROUTES4, whitelist=WL.EMPTY)
    assert [(i.engine, len(i.lines)) for i in res.page_items] == [("claude", 1), ("codex", 2)]


def test_non_voting_engine_extra_makes_no_item():
    """S3: tesseract only supplies geometry: its unplaced lines are no page item."""
    res = C.compare(alignment([cell(0, "number", 0, {e: "5" for e in ROUTES4})], {"tesseract": (1,)}),
                    routes=ROUTES4, whitelist=WL.EMPTY)
    assert res.page_items == () and res.uncertain == ()


@st.composite
def grids(draw):
    cells = []
    for r in range(draw(st.integers(1, 3))):
        for k in range(draw(st.integers(1, 3))):
            base = draw(st.sampled_from(VALS))
            reads = {}
            for e in ROUTES4:
                how = draw(st.sampled_from(("same", "same", "same", "other", "none")))
                reads[e] = base if how == "same" else (None if how == "none" else draw(st.sampled_from(VALS)))
            row_unc = tuple(sorted(draw(st.sets(st.sampled_from(("no_geometry", "unaligned:mlx")), max_size=2))))
            cells.append(cell(r, "number", k, reads, row_unc))
    extras = draw(st.dictionaries(st.sampled_from(("vision", "mlx", "claude", "codex", "tesseract")),
                                  st.lists(st.integers(0, 9), min_size=1, max_size=3, unique=True).map(tuple),
                                  max_size=3))
    return cells, extras


@settings(max_examples=300, deadline=None)
@given(grids())
def test_extra_values_never_adds_cell_level_uncertainty(grid):
    """LAW (a), S5/S6: comparing the same cells with and without unplaced value lines gives identical cells
    (verdict, uncertain, notes, votes); the extras only add page items, one per voting engine that has some."""
    cells, extras = grid
    plain = C.compare(alignment(cells), routes=ROUTES4, whitelist=WL.EMPTY)
    with_extra = C.compare(alignment(cells, extras), routes=ROUTES4, whitelist=WL.EMPTY)
    assert with_extra.cells == plain.cells
    flagged = {(v.row, v.kind, v.k) for v in with_extra.cells if not v.agree}
    assert flagged == {(v.row, v.kind, v.k) for v in plain.cells if not v.agree}
    assert not any(u.startswith("extra_values") for v in with_extra.cells for u in v.uncertain)
    voting = [e for e in A.ENGINE_ORDER if e in ROUTES4 and extras.get(e)]
    assert [i.engine for i in with_extra.page_items] == voting
    assert plain.page_items == ()


# --------------------------------------------------------------------------- gate: law (b)


def judged(cells, extras):
    res = C.compare(alignment(cells, extras), routes=ROUTES4, whitelist=WL.EMPTY)
    return G.judge(res, rec_id=REC_ID, page_id=PAGE_ID)


def test_all_agree_with_extras_is_pending_confirm_with_one_entry_per_engine():
    """S1: not verified(auto) and not disputed: pending_confirm, one non-blocking page entry per engine, no code
    on any field."""
    v = judged([cell(0, "number", 0, {e: "5" for e in ROUTES4}), cell(1, "number", 0, {e: "7" for e in ROUTES4})],
               {"mlx": (3, 4), "claude": (1,)})
    assert (v.status, v.verified_by) == (G.PENDING, None)
    assert [(e["reason"], e["scope"], e["engine"], e["id"]) for e in v.uncertain] == [
        ("extra_values", "page", "mlx", f"{PAGE_ID}:mlx:extra_values"),
        ("extra_values", "page", "claude", f"{PAGE_ID}:claude:extra_values")]   # ENGINE_ORDER: mlx before claude
    assert all(f["uncertain"] == [] and f["verdict"] == "agree" for f in v.fields)
    assert not any(G.blocks_page(e) for e in v.uncertain)
    assert G.blocks_page(G.entry("route_failed:mlx", page_id=PAGE_ID))


def test_without_extras_the_same_cells_are_verified_auto():
    """S1 (contrast): K1 untouched - no unplaced value, all routes equal -> verified(auto)."""
    v = judged([cell(0, "number", 0, {e: "5" for e in ROUTES4})], {})
    assert (v.status, v.verified_by, v.uncertain) == (G.VERIFIED, "auto", ())


@settings(max_examples=300, deadline=None)
@given(grids())
def test_unresolved_extra_values_page_item_is_never_verified(grid):
    """LAW (b), S5: whenever a voting route has an unplaced value line, the candidate is never ``verified``
    (and never ``disputed`` by it alone); it has a non-blocking extra_values entry for each such engine."""
    cells, extras = grid
    v = judged(cells, extras)
    voting = [e for e in A.ENGINE_ORDER if e in ROUTES4 and extras.get(e)]
    if voting:
        assert v.status != G.VERIFIED and v.verified_by is None
        assert [e["engine"] for e in v.uncertain if e["reason"] == "extra_values"] == voting
        assert v.status == G.PENDING
    else:
        assert not any(e["reason"] == "extra_values" for e in v.uncertain)


# --------------------------------------------------------------------------- stage / confirm page / writeback


def fx_copy(tmp_path: Path, name: str, extra: dict[str, list[tuple[str, list | None]]], key: str = "p001") -> Path:
    """A copy of fixture ``name`` whose ``engine/<e>/<key>.jsonl`` got extra lines (the recorded fixtures are
    never touched)."""
    dst = tmp_path / "fx" / name
    shutil.copytree(FIXTURES / name, dst)
    for engine, specs in extra.items():
        path = dst / "engine" / engine / f"{key}.jsonl"
        rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
        for text, box in specs:
            rows.append({**rows[-1], "bbox_px": box, "line_idx": max(r["line_idx"] for r in rows) + 1,
                         "text": text, "tokens": None})
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return dst


def audited(tmp_path: Path, fx: Path):
    proj = tmp_path / "proj"
    proj.mkdir(exist_ok=True)
    b, adir = W.audited(proj, fx)
    return proj, b, adir


def page_item_id(rec_id: str, engine: str) -> str:
    return f"{rec_id}/p-extra_values-{engine}"


def test_candidate_has_one_page_item_and_clean_fields(tmp_path):
    """S1: the audit candidate: pending_confirm, every field agree with no code, one extra_values entry and one
    ``page_items`` entry that names the unplaced line (idx, text) - no crop for a cloud line without a box."""
    fx = fx_copy(tmp_path, "fx-dup10", {"claude": [(LINE, None)]})
    proj, b, adir = audited(tmp_path, fx)
    (c,) = W.candidates(adir)
    assert c["status"] == "pending_confirm" and c["verified_by"] is None
    assert all(f["verdict"] == "agree" and f["uncertain"] == [] for f in c["fields"])
    assert [(u["reason"], u["engine"], u["scope"]) for u in c["uncertain"]] == [("extra_values", "claude", "page")]
    (item,) = c["page_items"]
    assert item["field_id"] == page_item_id(c["rec_id"], "claude")
    assert (item["reason"], item["engine"]) == ("extra_values", "claude")
    assert item["lines"] == [{"line_idx": [9], "text": LINE, "bbox_px": None, "crop": None}]


def test_confirm_page_shows_one_item_with_the_lines_and_two_actions(tmp_path):
    """S1 + law (c): the page has exactly one item - the page item - with the unplaced text, the acknowledge and
    the type-it options, no route radios; the clickable record is not blocked."""
    fx = fx_copy(tmp_path, "fx-dup10", {"claude": [(LINE, None)]})
    proj, b, adir = audited(tmp_path, fx)
    doc = parse(W.page(proj, b, adir))
    (node,) = doc.by_class("kk-item")
    (c,) = W.candidates(adir)
    assert node.get("data-field-id") == page_item_id(c["rec_id"], "claude")
    assert "kk-page-item" in node.get("class").split()
    assert LINE in node.text() and "确认无遗漏" in node.text()
    # a cloud line has no box, so no region: typing a value is not offered (the writeback would refuse it)
    assert "手输" not in node.text()
    assert [i.get("value") for i in node.find_all("input") if i.get("type") == "radio"] == ["none:acknowledged"]
    assert doc.by_class("kk-blocked") == []
    data = CP.page_data(W.page(proj, b, adir))
    (rec,) = data["records"]
    assert rec["clickable"] is True and [f["field_id"] for f in rec["fields"]] == [node.get("data-field-id")]


def test_ingest_leaves_a_record_with_an_unresolved_page_item_unverified(tmp_path):
    """LAW (b) at the writeback: ingest (auto) of a candidate with a page item never makes the record verified."""
    fx = fx_copy(tmp_path, "fx-dup10", {"claude": [(LINE, None)]})
    proj, b, adir = audited(tmp_path, fx)
    (c,) = W.candidates(adir)
    code = WB.run(proj, b, None, out=io.StringIO(), stdout=io.StringIO())
    assert code == 10
    front = W.front(proj, c["rec_id"])
    assert front["status"] == "pending_confirm" and "effective" not in front
    assert page_item_id(c["rec_id"], "claude") in W.body(proj, c["rec_id"], 1)["open"]


def ack(fid: str) -> dict:
    return {"field_id": fid, "kind": "none", "reason": "acknowledged"}


def test_acknowledge_exports_and_writes_back_verified_by_human(tmp_path):
    """S1: 确认无遗漏 -> choices.json item (row null, none + evidence acknowledged, seen = the unplaced text) that
    fits the contract; the writeback accepts it, the record is verified by a human, with one decision."""
    fx = fx_copy(tmp_path, "fx-dup10", {"claude": [(LINE, None)]})
    proj, b, adir = audited(tmp_path, fx)
    (c,) = W.candidates(adir)
    fid = page_item_id(c["rec_id"], "claude")
    choices = W.export(proj, b, adir, [ack(fid)])
    assert schema.issues("choices", choices) == []
    (it,) = choices["items"]
    assert it["cell"] == {"row": None, "col": "extra_values:claude"}
    assert it["choice"] == {"kind": "none"} and it["evidence"] == {"reason": "acknowledged"}
    assert it["seen"] == {"vision": None, "mlx": None, "claude": LINE, "codex": None}
    code, receipt, err = W.submit(proj, choices, tmp_path / "out")
    assert code == 0, err
    front = W.front(proj, c["rec_id"])
    assert front["status"] == "verified" and front["effective"]["verified_by"] == "human"
    body = W.body(proj, c["rec_id"], 1)
    assert body["open"] == [] and body["uncertain"] == [] and fid not in body["fields"]
    assert len(body["fields"]) == len(c["fields"])
    (d,) = [d for d in W.fixes(adir) if d["field_id"] == fid]
    assert (d["by"], d["choice"], d["value"]) == ("human", "none", None)
    assert d["evidence"]["reason"] == "acknowledged"
    # the same choices again change nothing (idempotent)
    code2, receipt2, _ = W.submit(proj, choices, tmp_path / "out")
    assert code2 == 0 and receipt2["per_record"] == [{"rec_id": c["rec_id"], "result": "unchanged"}]


def test_acknowledge_is_only_for_a_page_item(tmp_path):
    """S3: the exporter refuses ``acknowledged`` on a cell field, and the writeback refuses a forged choices file
    that does it, or that picks a route / plain none for the page item."""
    fx = fx_copy(tmp_path, "fx-u8", {"claude": [(LINE, None)]})
    proj, b, adir = audited(tmp_path, fx)
    todo = W.to_confirm(adir)
    (rec_id,) = [r for r, fids in todo.items() if page_item_id(r, "claude") in fids]
    pid = page_item_id(rec_id, "claude")
    cell_fid = next(f for f in todo[rec_id] if f != pid)
    with pytest.raises(CP.ConfirmError):
        W.export(proj, b, adir, [ack(cell_fid)])
    base = [{"field_id": f, "kind": "route", "route": W.route_pick(adir, f)} for f in todo[rec_id] if f != pid]
    good = W.export(proj, b, adir, base + [ack(pid)])
    for bad in ({"kind": "route", "route": "claude"}, {"kind": "none"}):
        forged = W.forged(good)
        item = next(i for i in forged["items"] if i["field_id"] == pid)
        item["choice"] = dict(bad)
        item.pop("evidence", None)
        code, receipt, _ = W.submit(proj, forged, tmp_path / "out")
        assert code == 30 and receipt["per_record"][0]["result"] == "rejected"
    forged = W.forged(good)
    item = next(i for i in forged["items"] if i["field_id"] == cell_fid)
    item["evidence"] = {"reason": "acknowledged"}
    item["choice"] = {"kind": "none"}
    code, receipt, _ = W.submit(proj, forged, tmp_path / "out")
    assert code == 30 and W.front(proj, rec_id) is None


def test_omitting_the_page_item_is_refused_and_acknowledging_completes(tmp_path):
    """LAW (b) via choices, S3: the 4 cell disputes of fx-u8 answered but the page item left out -> rejected,
    nothing written, record unverified; adding the acknowledgement verifies it."""
    fx = fx_copy(tmp_path, "fx-u8", {"claude": [(LINE, None)]})
    proj, b, adir = audited(tmp_path, fx)
    todo = W.to_confirm(adir)
    (rec_id,) = [r for r, fids in todo.items() if page_item_id(r, "claude") in fids]
    pid = page_item_id(rec_id, "claude")
    cells_only = [{"field_id": f, "kind": "route", "route": W.route_pick(adir, f)} for f in todo[rec_id] if f != pid]
    code, receipt, _ = W.submit(proj, W.export(proj, b, adir, cells_only), tmp_path / "out")
    res = next(r for r in receipt["per_record"] if r["rec_id"] == rec_id)
    assert code == 30 and res["result"] == "rejected" and res["missing"] == [pid]
    assert W.front(proj, rec_id) is None
    code, receipt, err = W.submit(proj, W.export(proj, b, adir, cells_only + [ack(pid)]), tmp_path / "out")
    assert W.front(proj, rec_id)["status"] == "verified"


@pytest.mark.parametrize("extra", [{}, {"claude": [(LINE, None)]},
                                   {"claude": [(LINE, None), ("钾 4.1 mmol/L", None)], "codex": [("尿酸 300 umol/L", None)]}])
def test_confirm_items_equal_cell_disagreements_plus_one_per_engine(tmp_path, extra):
    """LAW (c), S4-ish over three shapes: items on the page(s) = disputes of the baseline audit of the same
    fixture + the number of engines with unplaced value lines (two lines of one engine are still one item)."""
    base_proj = tmp_path / "base"
    base_proj.mkdir()
    _, base_adir = W.audited(base_proj, FIXTURES / "fx-u8")
    disputes = sum(1 for c in W.candidates(base_adir) for f in c["fields"] if W.shown(f))
    fx = fx_copy(tmp_path, "fx-u8", extra)
    proj, b, adir = audited(tmp_path, fx)
    doc = parse(W.page(proj, b, adir))
    assert len(doc.by_class("kk-item")) == disputes + len(extra)
    data = CP.page_data(W.page(proj, b, adir))
    assert sum(len(r["fields"]) for r in data["records"]) == disputes + len(extra)
    assert not any(x.startswith("extra_values") for c in W.candidates(adir) for f in c["fields"]
                   for x in f["uncertain"])


def test_mlx_line_with_a_box_gets_a_crop_and_a_typed_value_with_a_region(tmp_path):
    """S1: an unplaced mlx line with a box: the item shows its crop (a PNG of the revision), a typed value is
    accepted with the line's box as region and lands in the verified body under the page item's id."""
    box = [40, 455, 300, 475]
    fx = fx_copy(tmp_path, "fx-dup10", {"mlx": [(LINE, box)]})
    proj, b, adir = audited(tmp_path, fx)
    (c,) = W.candidates(adir)
    (item,) = c["page_items"]
    (line,) = item["lines"]
    assert line["bbox_px"] == box and line["crop"].startswith(f"work/{b}/audit/{adir.name}/crops/")
    assert (proj / line["crop"]).is_file()
    doc = parse(W.page(proj, b, adir))
    (node,) = doc.by_class("kk-item")
    (img,) = node.find_all("img")
    assert img.get("src") == "crops/" + Path(line["crop"]).name
    assert sorted(i.get("value") for i in node.find_all("input") if i.get("type") == "radio") == [
        "manual", "none:acknowledged"]
    fid = item["field_id"]
    choices = W.export(proj, b, adir, [{"field_id": fid, "kind": "manual", "value": "血糖 5.6"}])
    assert choices["items"][0]["choice"] == {"kind": "manual", "value": "血糖 5.6"}
    code, _, err = W.submit(proj, choices, tmp_path / "out")
    assert code == 0, err
    assert W.body(proj, c["rec_id"], 1)["fields"][fid] == "血糖 5.6"
    (d,) = [d for d in W.fixes(adir) if d["field_id"] == fid]
    assert (d["choice"], d["value"]) == ("manual", "血糖 5.6")
    assert d["evidence"]["region"]["bbox_px"] == box


def test_typed_value_on_a_line_without_a_box_is_refused(tmp_path):
    """S3: a cloud line has no region: a typed value is refused (acknowledge is the way), nothing is written."""
    fx = fx_copy(tmp_path, "fx-dup10", {"claude": [(LINE, None)]})
    proj, b, adir = audited(tmp_path, fx)
    (c,) = W.candidates(adir)
    fid = page_item_id(c["rec_id"], "claude")
    code, receipt, _ = W.submit(proj, W.export(proj, b, adir, [{"field_id": fid, "kind": "manual", "value": "5.6"}]),
                                tmp_path / "out")
    assert code == 30 and receipt["per_record"][0]["result"] == "rejected"
    assert W.front(proj, c["rec_id"]) is None


def test_extra_values_entry_is_not_a_page_blocker(tmp_path):
    """S3: the extra_values entry has scope ``page`` but never blocks the page (``G.blocks_page``); every real
    page-level reason (a failed or missing route) still does."""
    plain = G.entry("extra_values:mlx", page_id=PAGE_ID)
    assert plain["scope"] == "page" and not G.blocks_page(plain)
    assert all(G.blocks_page(G.entry(f"{r}:mlx", page_id=PAGE_ID)) for r in sorted(G.PAGE_REASONS))
    assert "extra_values" not in G.PAGE_REASONS
