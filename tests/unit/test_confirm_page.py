"""WI-08b: the confirm page ``confirm.html`` and its ``choices.json`` export (matrix 08b-01..05, 08b-07..09;
04 U8; design 02 r2.3, r3.3, r4.1).

The audit revision is produced in process from the fixtures' recorded engine outputs (``_support.confirm_fx``),
then rendered by ``klode_kit.audit.confirm_page``. Structure is asserted through a standard-library DOM
(``_support.htmltree``) with semantic selectors (classes / ``data-*``), never through raw string positions.

The export in this lane is the Python exporter ``confirm_page.export_choices``; the node exporter
``templates/confirm_export.js`` is checked against it (same output for the same clicks) and against the schema in
the cli_integration lane (``tests/cli/test_confirm_export_node.py``, 08b-06).

Law: none for the rendering (matrix: "No law: 渲染层"). Added while implementing the matrix rows: the export
law "one item per field, never more" (00b1-14 / F17 at the source) as a property over random click sequences.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from _support.confirm_fx import FIXTURES, audited, candidates, make_batch, write_record
from _support.htmltree import parse
from klode_kit.audit import confirm_page as CP
from klode_kit.audit import stage
from klode_kit.contract import schema
from klode_kit.ocr import send

pytestmark = pytest.mark.unit

U8 = FIXTURES / "fx-u8"
REDO = FIXTURES / "fx-redo"
U3 = FIXTURES / "fx-u3-mixed"
ROUTES = ("vision", "mlx", "claude", "codex")
STAMP = re.compile(rb'(<meta name="klode-kit-confirm-sha256" content=")([0-9a-f]{64})(")')


def table_fixtures() -> list[Path]:
    return [fx for fx in sorted(FIXTURES.glob("fx-*"))
            if (fx / "scenario.json").is_file() and (fx / "engine").is_dir() and (fx / "images").is_dir()]


def rendered(tmp_path: Path, fx: Path, **kw) -> tuple[str, Path, bytes]:
    batch, adir = audited(tmp_path, fx, **kw)
    path = CP.write(tmp_path, batch)
    assert path == adir / "confirm.html"
    return batch, adir, path.read_bytes()


def items(doc) -> list:
    return doc.by_class("kk-item")


def item_of(doc, field_id: str):
    (node,) = [n for n in items(doc) if n.get("data-field-id") == field_id]
    return node


def sealed_sha(html: bytes) -> str:
    """The sha the page is identified by: sha256 of the file with its own stamp zeroed (a file cannot hold the
    plain sha256 of itself)."""
    m = STAMP.search(html)
    assert m is not None, "no klode-kit-confirm-sha256 stamp"
    return hashlib.sha256(html[:m.start(2)] + b"0" * 64 + html[m.end(2):]).hexdigest()


# --------------------------------------------------------------------------- 08b-01


def test_item_structure_u8(tmp_path):
    """08b-01 (S2): every dispute of fx-u8 is one item with its crop, four candidates labelled by route, the
    star on the majority value, and the 'none of these' and 'type it' options."""
    batch, adir, html = rendered(tmp_path, U8)
    doc = parse(html)
    disputed = [f for c in candidates(adir) for f in c["fields"] if f["verdict"] != "agree"]
    assert len(disputed) == 4
    assert sorted(n.get("data-field-id") for n in items(doc)) == sorted(f["field_id"] for f in disputed)
    for f in disputed:
        node = item_of(doc, f["field_id"])
        (img,) = node.find_all("img")
        src = img.get("src")
        assert src == "crops/" + Path(f["crop"]).name
        assert (adir / src).is_file()
        cands = node.by_class("kk-cand")
        assert [c.get("data-route") for c in cands] == list(ROUTES)
        seen = {e: v for v, es in f["votes"] for e in es}
        for c in cands:
            route = c.get("data-route")
            assert route in c.text() and seen[route] in c.text()
            radios = [i for i in c.find_all("input") if i.get("type") == "radio"]
            assert [r.get("value") for r in radios] == [f"route:{route}"]
        stars = node.by_class("kk-star")
        if f["verdict"] == "majority":
            starred = sorted(s.parent.get("data-route") for s in stars)
            assert starred == sorted(next(es for v, es in f["votes"] if v == f["recommended"]))
            assert all("★" in s.text() for s in stars)
        else:
            assert stars == [] and "★" not in node.text()
        text = node.text()
        assert "都不对" in text and "手输" in text
        values = {i.get("value") for i in node.find_all("input") if i.get("type") == "radio"}
        assert {"none", "manual"} <= values
        assert [i for i in node.find_all("input") if i.get("type") == "text"], "manual entry box"


# --------------------------------------------------------------------------- 08b-02 / 08b-03


def redo_a2(tmp_path: Path) -> tuple[str, Path, bytes]:
    """fx-redo: a1 (t1, codex disagrees) picked by a human -> effective r1 verified; then the t2 runs (codex
    misses p001) give a2, disputed by route_missing_page:codex."""
    batch, a1 = audited(tmp_path, REDO)
    (cand,) = candidates(a1)
    write_record(tmp_path, {
        "rec_id": cand["rec_id"], "doc_id": cand["doc_id"], "page_id": cand["page_id"], "record_idx": 0,
        "type": "lab", "date": "2026-10-21", "status": "verified",
        "effective": {"rev": 1, "audit_rev": f"{batch}/a1", "status": "verified", "verified_by": "human"},
        "candidates": [{"rev": 1, "audit_rev": f"{batch}/a1", "status": "verified", "uncertain": [],
                        "created": "2026-10-22T00:00:00Z"}]})
    batch2, a2 = audited(tmp_path, REDO, engine="engine_t2", seq=2)
    assert (batch2, a2.name) == (batch, "a2")
    (cand2,) = candidates(a2)
    assert cand2["status"] == "disputed"
    path = CP.write(tmp_path, batch)
    assert path == a2 / "confirm.html"
    return batch, a2, path.read_bytes()


def test_missing_route_not_clickable(tmp_path):
    """08b-02 (S3): a page blocked by a missing route shows 'missing route, not selectable' and the repair
    command on every one of its items, and offers no input at all there."""
    batch, a2, html = redo_a2(tmp_path)
    doc = parse(html)
    (cand,) = candidates(a2)
    shown = [f for f in cand["fields"] if f["verdict"] != "agree" or f["uncertain"] or f["missing"]]
    assert shown
    assert sorted(n.get("data-field-id") for n in items(doc)) == sorted(f["field_id"] for f in shown)
    for node in items(doc):
        text = node.text()
        assert "缺路，不可点选" in text
        assert "kb-ocr" in text and "--routes codex" in text
        assert node.find_all("input") == [] and node.find_all("textarea") == [] and node.find_all("button") == []
        assert "codex" in text
    # nothing of that page can be exported, whatever is claimed
    with pytest.raises(CP.ConfirmError):
        CP.export_choices(html, [{"field_id": shown[0]["field_id"], "kind": "route", "route": "vision"}])


def test_effective_vs_candidate_label(tmp_path):
    """08b-03 (S1): the record shows its effective revision next to the candidate of this audit revision."""
    _, _, html = redo_a2(tmp_path)
    text = parse(html).text()
    assert "生效 r1 (verified) / 候选 r2 (disputed: codex:route_missing_page)" in text


def test_label_without_effective(tmp_path):
    """08b-03 boundary (S2): a record never written back has no effective revision; its candidate is r1 and
    rev_base is 0."""
    batch, adir, html = rendered(tmp_path, REDO)
    text = parse(html).text()
    assert "生效 无 / 候选 r1 (pending_confirm)" in text
    (cand,) = candidates(adir)
    fid = next(f["field_id"] for f in cand["fields"] if f["verdict"] != "agree")
    out = CP.export_choices(html, [{"field_id": fid, "kind": "route", "route": "vision"}])
    assert [i["rev_base"] for i in out["items"]] == [0]


# --------------------------------------------------------------------------- 08b-04


def test_advice_display_only(tmp_path):
    """08b-04 (S3): kb-verifier advice is shown as a hint on its field and nothing else: no option is
    preselected, and an export without clicks (or with clicks elsewhere) carries no advice value."""
    batch, adir = audited(tmp_path, U8)
    fields = [f for c in candidates(adir) for f in c["fields"] if f["verdict"] != "agree"]
    tie = next(f for f in fields if f["verdict"] == "tie")
    other = next(f for f in fields if f["verdict"] == "majority")
    (adir / "advice.json").write_text(json.dumps([
        {"field_id": tie["field_id"], "suggestion": "4.99", "evidence": "row 7 reads 4.99 on the crop"}]),
        encoding="utf-8")
    html = CP.write(tmp_path, batch).read_bytes()
    doc = parse(html)
    hint = item_of(doc, tie["field_id"]).by_class("kk-advice")
    assert len(hint) == 1
    assert "4.99" in hint[0].text() and "row 7 reads 4.99 on the crop" in hint[0].text()
    assert "kb-verifier" in hint[0].text()
    assert hint[0].find_all("input") == []
    assert all("checked" not in n.attrs for n in doc.find_all("input"))
    assert all(n.by_class("kk-advice") == [] for n in items(doc) if n.get("data-field-id") != tie["field_id"])

    none = CP.export_choices(html, [])
    assert none["items"] == []
    assert "4.99" not in json.dumps(none)
    some = CP.export_choices(html, [{"field_id": other["field_id"], "kind": "route", "route": "vision"}])
    assert [i["field_id"] for i in some["items"]] == [other["field_id"]]
    assert "4.99" not in json.dumps(some)


def test_advice_unknown_field_and_malformed(tmp_path):
    """08b-04 guard (S3): advice for a field that is not on the page is not shown; advice that is not a list of
    {field_id, suggestion} objects is refused (ConfirmError) and no confirm.html is written."""
    batch, adir = audited(tmp_path, U8)
    (adir / "advice.json").write_text(json.dumps({"field_id": "x"}), encoding="utf-8")
    with pytest.raises(CP.ConfirmError):
        CP.write(tmp_path, batch)
    assert not (adir / "confirm.html").exists()
    (adir / "advice.json").write_text(json.dumps([{"field_id": "r-000000000000/r001-number-0",
                                                     "suggestion": "77.7"}]), encoding="utf-8")
    html = CP.write(tmp_path, batch).read_bytes()
    assert parse(html).by_class("kk-advice") == []
    assert b"77.7" not in html


# --------------------------------------------------------------------------- 08b-05


URL_ATTRS = ("src", "href", "action", "data", "poster", "srcset", "formaction", "background", "xlink:href")
EXTERNAL = re.compile(r"^\s*(?:[a-z][a-z0-9+.-]*:|//)", re.I)


@pytest.mark.parametrize("fx", table_fixtures(), ids=lambda p: p.name)
def test_no_external_urls(tmp_path, fx):
    """08b-05 (S3): for every fixture the page references nothing outside itself: no http(s) or
    protocol-relative src/href/url(), no scheme at all except data: on images; every local reference resolves
    inside the audit revision."""
    batch, adir, html = rendered(tmp_path, fx)
    text = html.decode("utf-8")
    assert "http://" not in text and "https://" not in text
    doc = parse(html)
    for node in doc.iter():
        for name in URL_ATTRS:
            value = node.attrs.get(name)
            if value is None:
                continue
            if EXTERNAL.match(value):
                assert node.tag == "img" and value.startswith("data:image/"), (node.tag, name, value)
            else:
                target = (adir / value).resolve()
                assert target.is_file() and adir.resolve() in target.parents, value
        assert not re.search(r"url\(\s*['\"]?\s*(?:https?:)?//", node.attrs.get("style") or "", re.I)
    for style in doc.find_all("style"):
        css = style.text()
        assert "@import" not in css and not re.search(r"url\(", css)
    assert doc.find_all("link") == [] and doc.find_all("iframe") == []
    assert all(s.get("src") is None for s in doc.find_all("script"))


# --------------------------------------------------------------------------- 08b-07


def test_zero_disputes_page(tmp_path):
    """08b-07 (S2): fx-u3-mixed p001 has no dispute: the page says so, offers nothing to click, and the export
    (with no clicks, or with a claimed click on one of its agreeing fields) has no items."""
    batch, adir, html = rendered(tmp_path, U3, keys=("p001",))
    (cand,) = candidates(adir)
    assert cand["status"] == "verified"
    doc = parse(html)
    assert "无分歧" in doc.text()
    assert items(doc) == [] and doc.find_all("input") == []
    assert CP.export_choices(html, [])["items"] == []
    with pytest.raises(CP.ConfirmError):
        CP.export_choices(html, [{"field_id": cand["fields"][0]["field_id"], "kind": "manual", "value": "1"}])


# --------------------------------------------------------------------------- 08b-08


def hand_rev(tmp_path: Path, *, label: str, values: dict[str, str]) -> tuple[str, Path]:
    """A hand-made audit revision: one pending record, one disputed cell whose route values are ``values``."""
    batch = "0123456789ab"
    adir = tmp_path / "work" / batch / "audit" / "a1"
    (adir / "crops").mkdir(parents=True)
    shutil.copyfile(U8 / "images" / "p001.png", adir / "crops" / "d-000000000000_p001-r001-number-0.png")
    rec, fid = "r-000000000001", "r-000000000001/r001-number-0"
    runs = {e: f"{batch}/{e}/0123456789ab/t1" for e in ROUTES}
    (adir / "runs.json").write_text(json.dumps(runs), encoding="utf-8")
    cell = {"field_id": fid, "rec_id": rec, "slot": "r001-number-0", "cell": {"row": 1, "col": "number-0"},
            "candidates": {e: {"value": v} for e, v in values.items()}, "prov": {}}
    (adir / "cells.jsonl").write_text(json.dumps(cell) + "\n", encoding="utf-8")
    votes: dict[str, list[str]] = {}
    for e, v in values.items():
        votes.setdefault(v, []).append(e)
    field = {"field_id": fid, "verdict": "all_differ", "recommended": None, "missing": [],
             "uncertain": ["all_differ"], "item_label": label, "votes": [[v, es] for v, es in votes.items()],
             "crop": f"work/{batch}/audit/a1/crops/d-000000000000_p001-r001-number-0.png"}
    cand = {"rec_id": rec, "doc_id": "d-000000000000", "page_id": "d-000000000000/p001", "record_idx": 0,
            "audit_rev": f"{batch}/a1", "status": "pending_confirm", "verified_by": None,
            "uncertain": [{"id": f"{fid}:all_differ", "reason": "all_differ", "scope": "cell", "field_id": fid,
                           "fix_action": "x", "clear_evidence": "y"}], "fields": [field]}
    (adir / "candidates.jsonl").write_text(json.dumps(cand) + "\n", encoding="utf-8")
    return batch, adir


EVIL = "</script><script>alert(1)</script>"


def test_ocr_text_escaped(tmp_path):
    """08b-08 (S3): OCR text that looks like markup stays text: the DOM has exactly the page's own two scripts
    (data, exporter), no element came from the OCR text, and the export returns the text byte for byte."""
    values = {"vision": EVIL, "mlx": "<img src=x onerror=alert(2)>", "claude": "a&b\"'", "codex": " </p>"}
    batch, adir = hand_rev(tmp_path, label="<script>alert(3)</script>", values=values)
    html = CP.write(tmp_path, batch).read_bytes()
    doc = parse(html)
    scripts = doc.find_all("script")
    assert [s.get("type") for s in scripts] == ["application/json", None]
    assert [s.get("id") for s in scripts] == ["klode-kit-confirm-data", "klode-kit-confirm-export"]
    assert doc.find_all(lambda n: n.tag == "img" and n.get("onerror") is not None) == []
    assert all(not k.startswith("on") for n in doc.iter() for k in n.attrs)
    node = item_of(doc, "r-000000000001/r001-number-0")
    assert EVIL in node.text() and "<script>alert(3)</script>" in node.text()
    data = json.loads(scripts[0].text())
    assert data["records"][0]["fields"][0]["seen"] == values
    out = CP.export_choices(html, [{"field_id": "r-000000000001/r001-number-0", "kind": "route",
                                    "route": "vision"}])
    assert out["items"][0]["seen"] == values
    manual = CP.export_choices(html, [{"field_id": "r-000000000001/r001-number-0", "kind": "manual",
                                       "value": EVIL}])
    assert manual["items"][0]["choice"] == {"kind": "manual", "value": EVIL}


# --------------------------------------------------------------------------- 08b-09


def test_tie_shows_tie_label(tmp_path):
    """08b-09 (S2): fx-u8 p002-l07 (row 7 of p002, 2:2) is labelled 'tie' and carries no star."""
    batch, adir, html = rendered(tmp_path, U8)
    pages = {p["key"]: p["page_id"] for p in json.loads((U8 / "scenario.json").read_text("utf-8"))["pages"]}
    cand = next(c for c in candidates(adir) if c["page_id"] == pages["p002"])
    (f,) = [f for f in cand["fields"] if f["field_id"].endswith("/r007-number-0")]
    assert f["verdict"] == "tie"
    node = item_of(parse(html), f["field_id"])
    assert "平票" in node.text()
    assert node.by_class("kk-star") == [] and "★" not in node.text()


# --------------------------------------------------------------------------- export (Python exporter)


def u8_page(tmp_path: Path) -> tuple[str, Path, bytes, list[dict]]:
    batch, adir, html = rendered(tmp_path, U8)
    fields = [(c, f) for c in candidates(adir) for f in c["fields"] if f["verdict"] != "agree"]
    return batch, adir, html, fields


def test_export_full_u8_schema_valid(tmp_path):
    """Export (S1, the 08b-06 contract in-process): one click per dispute gives a schema-valid choices.json with
    batch, audit_rev, the sealed page sha, the run fingerprints, and field-addressed items in page order whose
    seen equals the cells.jsonl reading."""
    batch, adir, html, fields = u8_page(tmp_path)
    picks = [{"field_id": fields[0][1]["field_id"], "kind": "route", "route": "vision"},
             {"field_id": fields[1][1]["field_id"], "kind": "manual", "value": "210"},
             {"field_id": fields[2][1]["field_id"], "kind": "none"},
             {"field_id": fields[3][1]["field_id"], "kind": "none", "reason": "not_a_field"}]
    out = CP.export_choices(html, list(reversed(picks)))
    assert schema.issues("choices", out) == []
    assert out["schema"] == "klode-kit.choices/1"
    assert out["batch_id"] == batch and out["audit_rev"] == f"{batch}/a1"
    assert out["confirm_sha256"] == sealed_sha(html) == CP.confirm_sha256(html)
    runs = json.loads((adir / "runs.json").read_text("utf-8"))
    assert out["fingerprints"] == {e: r for e, r in runs.items() if r}
    cells = {json.loads(x)["field_id"]: json.loads(x) for x in (adir / "cells.jsonl").read_text("utf-8").splitlines()}
    assert [i["field_id"] for i in out["items"]] == [f["field_id"] for _, f in fields]
    for item, (cand, f) in zip(out["items"], fields):
        assert item["rec_id"] == cand["rec_id"] and item["rev_base"] == 0
        assert item["cell"] == cells[f["field_id"]]["cell"]
        assert item["seen"] == {e: c["value"] for e, c in cells[f["field_id"]]["candidates"].items()}
    assert [i["choice"] for i in out["items"]] == [{"kind": "route", "route": "vision"},
                                                   {"kind": "manual", "value": "210"}, {"kind": "none"},
                                                   {"kind": "none"}]
    assert "evidence" not in out["items"][2]
    assert out["items"][3]["evidence"] == {"reason": "not_a_field"}


def test_export_partial_record_allowed(tmp_path):
    """Export boundary (S2): a record with only some of its disputes clicked still exports those clicks (the
    writeback refuses the partial record, 08c-18); unclicked fields produce no item."""
    _, _, html, fields = u8_page(tmp_path)
    out = CP.export_choices(html, [{"field_id": fields[0][1]["field_id"], "kind": "none"}])
    assert [i["field_id"] for i in out["items"]] == [fields[0][1]["field_id"]]
    assert schema.issues("choices", out) == []


def test_export_blank_manual_is_none(tmp_path):
    """Export boundary (S2): a typed value that is empty or blank is exported as 'none' (r2.3: empty string =
    none = stays disputed), never as a manual choice with an empty value."""
    _, _, html, fields = u8_page(tmp_path)
    for blank in ("", "   ", "\t"):
        out = CP.export_choices(html, [{"field_id": fields[0][1]["field_id"], "kind": "manual", "value": blank}])
        assert out["items"][0]["choice"] == {"kind": "none"}


@pytest.mark.parametrize("bad", [
    {"kind": "route", "route": "tesseract"},
    {"kind": "route"},
    {"kind": "partial"},
    {"kind": "manual"},
    {"kind": "manual", "value": 12},
    {"kind": "none", "reason": "because"},
    {"kind": "route", "route": "vision", "reason": "not_a_field"},
])
def test_export_bad_selection_refused(tmp_path, bad):
    """Export guard (S3): a selection that the page cannot produce (unknown route, missing value, the removed
    'partial' kind, a reason other than not_a_field or on a non-none choice) is refused."""
    _, _, html, fields = u8_page(tmp_path)
    with pytest.raises(CP.ConfirmError):
        CP.export_choices(html, [{"field_id": fields[0][1]["field_id"], **bad}])


def test_export_unknown_field_refused(tmp_path):
    _, _, html, _ = u8_page(tmp_path)
    with pytest.raises(CP.ConfirmError):
        CP.export_choices(html, [{"field_id": "r-000000000000/r001-number-0", "kind": "none"}])


def test_export_edited_page_refused(tmp_path):
    """Export guard (S3): a page whose bytes no longer match its stamp (edited after rendering) is refused."""
    _, _, html, fields = u8_page(tmp_path)
    edited = html.replace("都不对".encode(), "都不队".encode(), 1)
    assert edited != html
    with pytest.raises(CP.ConfirmError):
        CP.export_choices(edited, [])


def test_export_route_missing_value_refused(tmp_path):
    """Export guard (S3, 08c-19 at the source): a route that has no value for the cell cannot be chosen."""
    values = {"vision": "1", "mlx": "2", "claude": "3"}
    batch, adir = hand_rev(tmp_path, label="x", values=values)
    html = CP.write(tmp_path, batch).read_bytes()
    with pytest.raises(CP.ConfirmError):
        CP.export_choices(html, [{"field_id": "r-000000000001/r001-number-0", "kind": "route", "route": "codex"}])
    node = item_of(parse(html), "r-000000000001/r001-number-0")
    (codex,) = [c for c in node.by_class("kk-cand") if c.get("data-route") == "codex"]
    assert codex.find_all("input") == []


SEL = st.fixed_dictionaries({
    "slot": st.integers(0, 5),
    "kind": st.sampled_from(["route", "manual", "none"]),
    "route": st.sampled_from(ROUTES),
    "value": st.text(max_size=6),
    "reason": st.sampled_from([None, "not_a_field"]),
})


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(clicks=st.lists(SEL, max_size=12))
def test_export_one_item_per_field(tmp_path_factory, clicks):
    """00b1-14 / F17 at the source (S4): over any click sequence (re-clicks, changes of mind, every kind) the
    export has at most one item per field, each the LAST click on that field, in page order, and is
    schema-valid whenever it has an item."""
    if "u8" not in PAGE_CACHE:
        PAGE_CACHE["u8"] = u8_page(tmp_path_factory.mktemp("u8"))
    _, _, html, fields = PAGE_CACHE["u8"]
    fids = [f["field_id"] for _, f in fields]
    sels = []
    for c in clicks:
        if c["slot"] >= len(fids):
            continue
        s = {"field_id": fids[c["slot"]], "kind": c["kind"]}
        if c["kind"] == "route":
            s["route"] = c["route"]
        elif c["kind"] == "manual":
            s["value"] = c["value"]
        elif c["reason"]:
            s["reason"] = c["reason"]
        sels.append(s)
    out = CP.export_choices(html, sels)
    got = [i["field_id"] for i in out["items"]]
    assert len(got) == len(set(got))
    assert got == [f for f in fids if f in {s["field_id"] for s in sels}]
    last = {s["field_id"]: s for s in sels}
    for item in out["items"]:
        s = last[item["field_id"]]
        if s["kind"] == "manual" and not s["value"].strip():
            assert item["choice"] == {"kind": "none"}
        else:
            assert item["choice"]["kind"] == s["kind"]
    if got:
        assert schema.issues("choices", out) == []


PAGE_CACHE: dict = {}


# --------------------------------------------------------------------------- write-once, determinism, privacy


def test_confirm_written_once(tmp_path):
    """08a write-once mechanism (S3): confirm.html of a<N> is written once and read-only; a second write raises
    WriteOnceViolation and leaves the bytes as they were."""
    batch, adir, html = rendered(tmp_path, U8)
    assert not (adir / "confirm.html").stat().st_mode & 0o222
    with pytest.raises(stage.WriteOnceViolation):
        CP.write(tmp_path, batch)
    assert (adir / "confirm.html").read_bytes() == html


def test_render_deterministic(tmp_path):
    """Determinism (S5): the page is a function of the audit revision: two projects audited from the same
    fixture render byte-identical pages, and rendering again without writing gives the same bytes."""
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    batch, _, html_a = rendered(a, U8)
    _, _, html_b = rendered(b, U8)
    assert html_a == html_b
    assert CP.render(a, batch, 1) == html_a


def test_page_private_local_only(tmp_path):
    """Privacy (S3): the page and its crops live under work/<batch>/audit/a<N>/ only: nothing is written under
    _masked/ or _mask_review/, the page's sha is never a registered (sendable) sha, and the page carries no
    header text of the documents (name, number) - only the disputed cells."""
    (tmp_path / "_masked").mkdir()
    before = sorted(p.relative_to(tmp_path).as_posix() for p in (tmp_path / "_masked").rglob("*"))
    batch, adir, html = rendered(tmp_path, U8)
    assert sorted(p.relative_to(tmp_path).as_posix() for p in (tmp_path / "_masked").rglob("*")) == before
    assert not (tmp_path / "_mask_review").exists()
    written = {p for p in tmp_path.rglob("confirm.html")}
    assert written == {adir / "confirm.html"}
    shas = send.approved_shas(tmp_path)
    assert hashlib.sha256(html).hexdigest() not in shas
    assert all(hashlib.sha256(p.read_bytes()).hexdigest() not in shas for p in (adir / "crops").glob("*.png"))
    text = html.decode("utf-8")
    for header in ("测试患者甲", "T-0000001", "示例医院"):
        assert header not in text


def test_write_latest_revision_and_guards(tmp_path):
    """Guards (S3): write() picks the newest audit revision; a batch without an audit revision, a bad revision
    number or a crop outside the revision is refused without writing."""
    with pytest.raises(CP.ConfirmError):
        CP.write(tmp_path, "0123456789ab")
    batch, adir = hand_rev(tmp_path, label="x", values={"vision": "1", "mlx": "2", "claude": "3", "codex": "4"})
    with pytest.raises(CP.ConfirmError):
        CP.write(tmp_path, batch, 2)
    with pytest.raises(CP.ConfirmError):
        CP.write(tmp_path, "not-a-batch")
    cand = json.loads((adir / "candidates.jsonl").read_text("utf-8"))
    cand["fields"][0]["crop"] = "_masked/elsewhere.png"
    (adir / "candidates.jsonl").write_text(json.dumps(cand) + "\n", encoding="utf-8")
    with pytest.raises(CP.ConfirmError):
        CP.write(tmp_path, batch)
    assert not (adir / "confirm.html").exists()


def test_cli_confirm_page(tmp_path, capsys):
    """CLI ``klode-kit confirm-page`` (S2): writes the page and prints its path (exit 0); run again it reports
    the page already written and leaves it (exit 0); no audit revision -> exit 2."""
    batch, adir = audited(tmp_path, U8)
    assert CP.main(["--batch", batch, "--project", str(tmp_path)]) == 0
    out = capsys.readouterr()
    assert str(adir / "confirm.html") in out.out
    html = (adir / "confirm.html").read_bytes()
    assert CP.main(["--batch", batch, "--project", str(tmp_path)]) == 0
    assert "already" in capsys.readouterr().err
    assert (adir / "confirm.html").read_bytes() == html
    assert CP.main(["--batch", "0123456789ab", "--project", str(tmp_path)]) == 2
    assert CP.main(["--batch", "zz", "--project", str(tmp_path)]) == 2
