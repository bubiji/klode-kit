"""WI-33d (real-data pilot P8, decision D-35): a lab / prescription batch whose pages have a text layer is audited
(unit lane; the real-poppler end-to-end cases are in tests/cli/test_med_intake_textlayer.py).

Matrix (all synthetic; the pdftext rows are the recorded rows of ``fx-redo`` turned into text-layer rows):

- audit of text-layer pages: a batch with a record type (``--type`` / the receipt's ``record_type``) audits them with
  ``pdftext`` as the one voting route (exact geometry, design R2-08); without a type (book / guide, U4) nothing changes
  (the old case ``test_text_layer_only_nothing_to_audit`` still holds);
- the zero-record guard (``pipeline.zero_record_message``): pages but no candidate is a failure, unless the batch is a
  book (no type, only text pages);
- the two ``ocr:`` facts of ``med_intake``: skipped (text layer) is never a failure.

Law (S5, last test): the failures and the skipped pages of an OCR receipt are disjoint, and every page that is not a
text-layer page is accounted for by exactly its own failures - a skip is never counted as a failure.
"""

from __future__ import annotations

import io
import json
import shutil
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from _support.flows import REPO
from klode_kit import med_intake as M
from klode_kit import pipeline as P
from klode_kit.audit import stage

pytestmark = pytest.mark.unit

FX = REPO / "tests" / "fixtures" / "fx-redo"
BATCH = "eb2b28a29018"
ROUTES = ("vision", "mlx", "claude", "codex")
PAGE = {"key": "p001", "doc_id": "d-e9f04e9657b8", "page_id": "d-e9f04e9657b8/p001"}
DER = "e9f04e9657b807e822e220b1cb33ab5fa6052e3ac53fcce4b27fd950449a1fdc"


def text_batch(project: Path, *, record_type: str | None = None, extra_line: str | None = None) -> Path:
    """A kb-ocr batch of one PDF page with a text layer: routes skipped, ``text/p001.jsonl`` of pdftext rows."""
    bdir = project / "work" / BATCH
    (bdir / "text").mkdir(parents=True)
    (bdir / "derived.jsonl").write_text(json.dumps(
        {"der_sha256": DER, "page_id": PAGE["page_id"], "parent": DER, "path": str(FX / "images" / "p001.png"),
         "transform": {"crop": None, "mask_boxes": [], "rotate": 0}}) + "\n", encoding="utf-8")
    rows = []
    for raw in (FX / "engine_t3" / "vision" / "p001.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(raw)
        row["engine"] = "pdftext"
        rows.append(row)
    if extra_line is not None:
        rows.append({**rows[-1], "line_idx": len(rows), "text": extra_line, "tokens": [
            {"bbox_px": [40, 900, 200, 930], "geometry": "exact", "span": [0, len(extra_line)], "text": extra_line}],
            "bbox_px": [40, 900, 200, 930]})
    (bdir / "text" / "p001.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                                              encoding="utf-8")
    page = {**PAGE, "text_class": "text", "text_file": "text/p001.jsonl",
            "routes": {r: {"status": "skipped", "reason": "text_layer"} for r in ROUTES}, "uncertain": []}
    rec = {"schema": "klode-kit.ocr_batch/1", "batch_id": BATCH, "routes": list(ROUTES), "pages": [page],
           "attempts": {}, "text_layer": True, "routes_skipped": list(ROUTES)}
    if record_type:
        rec["record_type"] = record_type
    (bdir / "receipt.json").write_text(json.dumps(rec), encoding="utf-8")
    return bdir


def audit(project: Path, **kw) -> tuple[int, str]:
    buf = io.StringIO()
    return stage.run(project, BATCH, out=buf, **kw), buf.getvalue()


def candidates(bdir: Path) -> list[dict]:
    path = bdir / "audit" / "a1" / "candidates.jsonl"
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]


# ---------------------------------------------------------------- audit of text-layer pages
def test_text_page_with_type_is_audited_by_pdftext_alone(tmp_path):
    bdir = text_batch(tmp_path)
    code, out = audit(tmp_path, record_type="lab")
    assert code == 0, out
    (cand,) = candidates(bdir)
    assert (cand["status"], cand["verified_by"], cand["uncertain"]) == ("verified", "auto", [])
    assert cand["fields"] and all(f["verdict"] == "agree" and not f["missing"] for f in cand["fields"])
    assert {e for f in cand["fields"] for _, es in f["votes"] for e in es} == {"pdftext"}
    cells = [json.loads(x) for x in (bdir / "audit" / "a1" / "cells.jsonl").read_text(encoding="utf-8").splitlines()]
    assert cells and all(set(c["prov"]) == {"pdftext"} for c in cells)
    # the provenance of a text-layer reading is the page's text file (contract: run_id "text/<key>")
    assert {c["prov"]["pdftext"]["run_id"] for c in cells} == {"text/p001"}
    assert all(c["prov"]["pdftext"]["bbox_px"] is not None for c in cells)  # exact geometry, never a bare row
    # the route is part of the revision (the writeback and the confirm page read the voting routes from runs.json)
    assert json.loads((bdir / "audit" / "a1" / "runs.json").read_text())["pdftext"] == "text-layer"


def test_the_receipts_record_type_is_enough(tmp_path):
    bdir = text_batch(tmp_path, record_type="rx")
    code, out = audit(tmp_path)
    assert code == 0, out
    assert [c["status"] for c in candidates(bdir)] == ["verified"]


def test_text_page_without_type_is_still_the_book_path(tmp_path):
    """U4: no record type -> nothing to audit, no audit directory (the recorded case stays true)."""
    bdir = text_batch(tmp_path)
    code, out = audit(tmp_path)
    assert code == 0 and "nothing to audit" in out
    assert not (bdir / "audit").exists()


def test_text_page_without_any_field_is_not_auto_verified(tmp_path):
    """K1 holds for the one route as for four: a page with no cell at all is never ``verified`` by the vote."""
    bdir = text_batch(tmp_path)
    path = bdir / "text" / "p001.jsonl"
    head = path.read_text(encoding="utf-8").splitlines()[:1]  # the hospital / title line only
    path.write_text("\n".join(head) + "\n", encoding="utf-8")
    code, out = audit(tmp_path, record_type="lab")
    (cand,) = candidates(bdir)
    assert (cand["status"], cand["verified_by"], cand["fields"]) == ("pending_confirm", None, []) and code == 10, out


def test_audit_reuse_does_not_hide_a_page_added_by_the_type(tmp_path):
    """A revision made without the text page must not be reused once the type makes the page auditable."""
    bdir = text_batch(tmp_path)
    page = json.loads((bdir / "receipt.json").read_text())["pages"][0]
    rec = json.loads((bdir / "receipt.json").read_text())
    image_page = {**page, "key": "p002", "page_id": "d-e9f04e9657b8/p002"}
    image_page.pop("text_class"), image_page.pop("text_file")
    # an image page whose three routes did not run: disputed record (page entries), enough to make revision a1
    image_page["routes"] = {r: {"status": "route_failed", "error": "route_disabled: x"} for r in ROUTES}
    rec["pages"] = [page, image_page]
    (bdir / "receipt.json").write_text(json.dumps(rec), encoding="utf-8")
    with (bdir / "derived.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"der_sha256": DER, "page_id": image_page["page_id"], "parent": DER,
                             "path": str(FX / "images" / "p001.png"),
                             "transform": {"crop": None, "mask_boxes": [], "rotate": 0}}) + "\n")
    audit(tmp_path)  # no type: only the image page, revision a1
    assert len(candidates(bdir)) == 1
    code, out = audit(tmp_path, record_type="lab")  # the type adds the text page: a new revision
    assert (bdir / "audit" / "a2" / "candidates.jsonl").is_file(), out
    got = [json.loads(x) for x in (bdir / "audit" / "a2" / "candidates.jsonl").read_text().splitlines()]
    assert sorted(c["page_id"] for c in got) == sorted([PAGE["page_id"], "d-e9f04e9657b8/p002"])


def test_main_type_option(tmp_path, capsys):
    text_batch(tmp_path)
    assert stage.main(["--batch", BATCH, "--project", str(tmp_path), "--type", "scan"]) == 2
    assert stage.main(["--batch", BATCH, "--project", str(tmp_path), "--type", "lab"]) == 0
    assert (tmp_path / "work" / BATCH / "audit" / "a1" / "candidates.jsonl").is_file()


# ---------------------------------------------------------------- the zero-record guard
def _receipt_dir(tmp_path: Path, pages: list[dict], cands: int) -> Path:
    bdir = tmp_path / "work" / BATCH
    bdir.mkdir(parents=True)
    (bdir / "receipt.json").write_text(json.dumps({"pages": pages}), encoding="utf-8")
    if cands:
        adir = bdir / "audit" / "a1"
        adir.mkdir(parents=True)
        (adir / "candidates.jsonl").write_text("".join('{"rec_id": "r%d"}\n' % i for i in range(cands)),
                                               encoding="utf-8")
    return bdir


TEXT = {"key": "p001", "text_class": "text"}
IMAGE = {"key": "p002"}


@pytest.mark.parametrize("pages,cands,rtype,bad", [
    ([TEXT], 0, "lab", True),            # P8: pages, a type, no record
    ([TEXT, TEXT], 0, "rx", True),
    ([IMAGE], 0, None, True),            # an image page always yields a record
    ([IMAGE, TEXT], 0, None, True),
    ([TEXT], 0, None, False),            # book / guide (U4): a text layer without a type is legitimately empty
    ([TEXT], 1, "lab", False),
    ([IMAGE], 3, None, False),
    ([], 0, "lab", False),               # no page, nothing was lost
])
def test_zero_record_guard(tmp_path, pages, cands, rtype, bad):
    bdir = _receipt_dir(tmp_path, pages, cands)
    msg = P.zero_record_message(bdir, rtype)
    assert (msg is not None) is bad
    if bad:
        assert str(len(pages)) in msg and "0" in msg


def test_the_guard_uses_the_closed_exit_set():
    assert P.EXIT_CODE[P.Reason.content_gate] == 20


# ---------------------------------------------------------------- skipped is not failed
def _receipt(pages: list[tuple[str, bool, tuple[str, ...]]], routes=ROUTES) -> dict:
    """``(key, text_layer, failed_routes)`` per page."""
    rows = []
    for key, text, failed in pages:
        if text:
            per = {r: {"status": "skipped", "reason": "text_layer"} for r in routes}
        else:
            per = {r: {"status": "route_failed" if r in failed else "done"} for r in routes}
        row = {"key": key, "routes": per}
        if text:
            row["text_class"] = "text"
        rows.append(row)
    return {"pages": rows, "attempts": {} if all(t for _, t, _ in pages) else {r: {"status": "done"} for r in routes}}


def test_skipped_pages_are_not_failures():
    rec = _receipt([("p001", True, ()), ("p002", True, ())])
    assert M.ocr_failures(rec) == []
    assert M.ocr_skipped(rec) == ["p001", "p002"]


def test_ocr_line_names_the_skip_not_a_failure():
    all_text = _receipt([(f"p{i:03d}", True, ()) for i in range(1, 14)])
    assert M.ocr_line(all_text) == "ocr: 13 页文字层，跳过 OCR"
    mixed = _receipt([("p001", False, ()), ("p002", True, ()), ("p003", False, ())])
    assert M.ocr_line(mixed) == "ocr: 2 页 × 4 路 OK，1 页文字层跳过 OCR"
    hurt = _receipt([("p001", False, ("codex",)), ("p002", True, ())])
    assert M.ocr_line(hurt) == "ocr: 1 页 × 4 路，1 处失败（codex:p001），1 页文字层跳过 OCR"


def test_ocr_line_without_text_layer_is_what_it_was():
    assert M.ocr_line(_receipt([("p001", False, ()), ("p002", False, ())])) == "ocr: 2 页 × 4 路 OK"
    assert M.ocr_skipped(_receipt([("p001", False, ())])) == []


@settings(max_examples=60, deadline=None)
@given(pages=st.lists(st.tuples(st.booleans(), st.sets(st.sampled_from(ROUTES))), min_size=1, max_size=8))
def test_law_skipped_and_failed_are_disjoint(pages):
    spec = [(f"p{i + 1:03d}", text, tuple(sorted(failed)) if not text else ()) for i, (text, failed) in
            enumerate(pages)]
    rec = _receipt(spec)
    failures, skipped = M.ocr_failures(rec), M.ocr_skipped(rec)
    assert skipped == [k for k, text, _ in spec if text]
    assert not {f.split(":")[1] for f in failures} & set(skipped)
    assert failures == sorted(f"{r}:{k}" for k, text, failed in spec if not text for r in failed)
    n_text = len(skipped)
    line = M.ocr_line(rec)
    assert ("跳过 OCR" in line) == (n_text > 0)
    assert ("处失败" in line) == bool(failures)
