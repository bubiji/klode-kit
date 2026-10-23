"""WI-04c: kb-ocr on PDFs with the real poppler tools (matrix 04c-04..07).

Layout added for PDF input (``<batch>`` = ``work/<batch_id>``):

    <batch>/text/<page key>.jsonl     pdftext ``ocr_line`` rows of a page classified ``text``
    <batch>/text/<doc_id>_whole.txt   whole text of a PDF's text pages (``text_to_whole``)
    <batch>/page_map.jsonl            one row per PDF page: page_id, key, class, dpi, size_px, whole,
                                      first_line, n_lines
    <batch>/derived/<page key>.png    every PDF page rendered at ``dpi`` (``transform.render_dpi``)

Every PDF page is rendered; only ``empty``/``garbled`` pages go to the routes, ``text`` pages never do.

Boundary: the four routes are replayed through ``KLODE_KIT_ENGINE_REPLAY`` (cloud real paths: 06-11/12; local
engines: 04a-10/11/12). pdftotext / pdfinfo / pdftoppm are real here; a missing tool FAILS (never skips).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from _support.kb_ocr import (
    FIXTURES,
    ROUTES,
    attempt_dirs,
    jsonl,
    kb_ocr_env,
    log_calls,
    make_inbox,
    only_batch,
    receipt,
    run_kb_ocr,
    sha256,
)
from klode_kit.contract import ids, schema
from klode_kit.ocr import geometry as G
from klode_kit.ocr import textlayer
from klode_kit.ocr.text_to_whole import locate

pytestmark = pytest.mark.cli_integration

GUIDE = FIXTURES / "fx-text-pdf" / "guide-2026.pdf"
MIXED = FIXTURES / "fx-mixed-pdf" / "mixed.pdf"
CORRUPT = FIXTURES / "fx-corrupt-pdf" / "corrupt.pdf"
POPPLER = ("pdfinfo", "pdftotext", "pdftoppm")


def ok(res: subprocess.CompletedProcess) -> None:
    assert res.returncode == 0, res.stderr.decode()


def project_at(tmp_path: Path) -> Path:
    project = tmp_path / "proj"
    project.mkdir()
    return project


def empty_replay(tmp_path: Path) -> Path:
    d = tmp_path / "replay"
    d.mkdir()
    return d


def called_pairs(log: Path) -> set[tuple[str, str]]:
    return {(c["tool"], c["page"]) for c in log_calls(log)}


def check_derived(project: Path, batch: Path, pdf_sha: str) -> dict[str, dict]:
    """Every derived line of the batch is a rendered PDF page bound to the PDF; returns them by page_id."""
    rows = jsonl(batch / "derived.jsonl")
    out = {}
    for row in rows:
        schema.validate("derived", row)
        assert row["parent"] == pdf_sha
        assert row["transform"]["render_dpi"] >= 72
        assert row["transform"]["rotate"] == 0
        assert sha256((project / row["path"]).read_bytes()) == row["der_sha256"]
        out[row["page_id"]] = row
    return out


def test_poppler_tools_present():
    """The cli lane needs poppler: a missing tool fails the lane here, by name."""
    missing = [t for t in POPPLER if shutil.which(t) is None]
    assert missing == [], f"poppler tools missing: {missing} (brew install poppler)"


# --------------------------------------------------------------------------- 04c-04


def test_text_pdf_receipt_u4(tmp_path):
    """04c-04 (S1, U4): `kb-ocr --batch guide-2026.pdf` (the PDF itself as input): text_layer true,
    routes_skipped == [vision, mlx, claude, codex], zero route calls, no ocr/ directory; every page has pdftext
    rows that pass ocr_line with px = pt * dpi / 72 inside the rendered page; the whole text maps back."""
    project = project_at(tmp_path)
    log = tmp_path / "calls.jsonl"
    ok(run_kb_ocr(project, GUIDE, kb_ocr_env(empty_replay(tmp_path), log)))
    rec = receipt(project)
    assert rec["text_layer"] is True
    assert rec["routes_skipped"] == ["vision", "mlx", "claude", "codex"]
    assert rec["status"] == "done" and rec["issues"] == []
    assert log_calls(log) == []
    batch = only_batch(project)
    assert not (batch / "ocr").exists()

    pdf_sha = sha256(GUIDE.read_bytes())
    doc = ids.doc_id(pdf_sha)
    (man,) = jsonl(batch / "manifest.jsonl")
    assert (man["kind"], man["img_sha256"], man["doc_id"]) == ("pdf", pdf_sha, doc)
    derived = check_derived(project, batch, pdf_sha)
    scenario = json.loads((FIXTURES / "fx-text-pdf" / "scenario.json").read_text(encoding="utf-8"))
    page_map = jsonl(batch / "page_map.jsonl")
    assert [p["text_class"] for p in rec["pages"]] == ["text"] * len(scenario["pages"])
    assert [r["page_id"] for r in page_map] == [f"{doc}/p{i:03d}" for i in range(1, len(scenario["pages"]) + 1)]
    for page, pm, sc in zip(rec["pages"], page_map, scenario["pages"]):
        assert pm["page_id"] == page["page_id"] and pm["class"] == "text"
        dpi = pm["dpi"]
        der = derived[page["page_id"]]
        assert der["transform"]["render_dpi"] == dpi and der["der_sha256"] == page["der_sha256"]
        size = G.image_size(project / der["path"])
        assert list(size) == pm["size_px"]
        assert all(r["status"] == "skipped" for r in page["routes"].values())
        rows = jsonl(batch / page["text_file"])
        assert page["text_file"] == f"text/{page['key']}.jsonl"
        assert rows and [r["line_idx"] for r in rows] == list(range(len(rows)))
        for row in rows:
            schema.validate("ocr_line", row)
            assert (row["engine"], row["page_id"], row["der_sha256"]) == ("pdftext", page["page_id"],
                                                                             page["der_sha256"])
            assert row["bbox_px"] == [textlayer.pt_to_px(v, dpi) for v in row["bbox_pt"]]
            assert G.in_bounds(row["bbox_px"], size)
            assert all(t["geometry"] == "exact" for t in row["tokens"])
        assert sc["title"].replace(" ", "") in "".join(r["text"] for r in rows).replace(" ", "")

    whole_path = batch / page_map[0]["whole"]
    assert whole_path.parent == batch / "text"
    whole_lines = whole_path.read_text(encoding="utf-8").split("\n")[:-1]
    for n, line in enumerate(whole_lines, start=1):
        pid, idx = locate(page_map, n)
        key = next(p["key"] for p in rec["pages"] if p["page_id"] == pid)
        assert jsonl(batch / "text" / f"{key}.jsonl")[idx]["text"] == line
    assert len(whole_lines) == sum(pm["n_lines"] for pm in page_map) > 0


# --------------------------------------------------------------------------- 04c-05


def _replay_rows(rec: dict, dest: Path, keys: list[str]) -> None:
    for engine in ROUTES:
        (dest / engine).mkdir(parents=True, exist_ok=True)
        for page in rec["pages"]:
            if page["key"] not in keys:
                continue
            row = {"engine": engine, "page_id": page["page_id"], "der_sha256": page["der_sha256"], "line_idx": 0,
                   "text": f"{engine} 读到的 {page['key']}", "bbox_px": None, "tokens": None}
            (dest / engine / f"{page['key']}.jsonl").write_text(json.dumps(row, ensure_ascii=False) + "\n",
                                                                encoding="utf-8")


def test_real_pdftotext_mixed(tmp_path):
    """04c-05 (S1): fx-mixed-pdf page by page: text / empty / garbled / empty (scan); only pages 2-4 reach the
    four routes (call log) and get four-route JSONL; page 1 gets pdftext rows and no route output."""
    project = project_at(tmp_path)
    inbox = make_inbox(project / "_inbox", [MIXED])
    replay = empty_replay(tmp_path)
    log = tmp_path / "calls.jsonl"
    ok(run_kb_ocr(project, inbox, kb_ocr_env(replay, log)))
    rec = receipt(project)
    scenario = json.loads((FIXTURES / "fx-mixed-pdf" / "scenario.json").read_text(encoding="utf-8"))
    assert [p["text_class"] for p in rec["pages"]] == [p["class"] for p in scenario["pages"]]
    assert [p["key"] for p in rec["pages"]] == ["p001", "p002", "p003", "p004"]
    assert rec["text_layer"] == "partial"
    assert rec["routes_skipped"] == []
    routed = ["p002", "p003", "p004"]
    assert called_pairs(log) == {(e, k) for e in ROUTES for k in routed}
    batch = only_batch(project)
    check_derived(project, batch, sha256(MIXED.read_bytes()))
    assert sorted(p.name for p in (batch / "text").glob("p*.jsonl")) == ["p001.jsonl"]
    assert all(r["status"] == "skipped" for r in rec["pages"][0]["routes"].values())

    # second run: the routes now answer -> every routed page has JSONL on all four routes, page 1 on none
    _replay_rows(rec, replay, routed)
    ok(run_kb_ocr(project, inbox, kb_ocr_env(replay, log)))
    rec = receipt(project)
    assert rec["status"] == "done" and rec["issues"] == []
    for engine in ROUTES:
        attempts = attempt_dirs(batch, engine)
        latest = attempts[-1]
        assert sorted(p.name for p in latest.glob("p*.jsonl")) == [f"{k}.jsonl" for k in routed]
        for attempt in attempts:
            assert not (attempt / "p001.jsonl").exists()
        for page in rec["pages"][1:]:
            assert page["routes"][engine]["status"] == "done"
    assert (called_pairs(log) & {(e, "p001") for e in ROUTES}) == set()


# --------------------------------------------------------------------------- 04c-06


def test_corrupt_pdf_no_partial(tmp_path):
    """04c-06 (S3): a truncated PDF -> exit != 0, stderr and receipt name pdf_unreadable, no text/ output,
    no route call, no ocr/ directory."""
    project = project_at(tmp_path)
    inbox = make_inbox(project / "_inbox", [CORRUPT])
    log = tmp_path / "calls.jsonl"
    res = run_kb_ocr(project, inbox, kb_ocr_env(empty_replay(tmp_path), log))
    assert res.returncode != 0
    assert "pdf_unreadable" in res.stderr.decode()
    rec = receipt(project)
    assert rec["status"] == "failed"
    assert [(e["path"], e["reason"]) for e in rec["errors"]] == [("corrupt.pdf", "pdf_unreadable")]
    batch = only_batch(project)
    text = batch / "text"
    assert not text.exists() or not any(text.iterdir())
    assert not (batch / "ocr").exists()
    assert not (batch / "page_map.jsonl").exists()
    assert log_calls(log) == []


def test_corrupt_pdf_next_to_good_pdf_stops_everything(tmp_path):
    """One unreadable PDF in an inbox stops the whole batch before anything is extracted (no partial text/)."""
    project = project_at(tmp_path)
    inbox = make_inbox(project / "_inbox", [GUIDE, CORRUPT])
    res = run_kb_ocr(project, inbox, kb_ocr_env(empty_replay(tmp_path), tmp_path / "calls.jsonl"))
    assert res.returncode != 0
    rec = receipt(project)
    assert [e["reason"] for e in rec["errors"]] == ["pdf_unreadable"]
    text = only_batch(project) / "text"
    assert not text.exists() or not any(text.iterdir())


# --------------------------------------------------------------------------- 04c-07


def test_zero_text_pages_all_routed(tmp_path):
    """04c-07 (S2): a PDF of only the empty and scanned pages of fx-mixed-pdf: text_layer false, routes_skipped
    empty, both pages reach all four routes, no pdftext page rows."""
    work = tmp_path / "split"
    work.mkdir()
    subprocess.run(["pdfseparate", "-f", "2", "-l", "2", str(MIXED), str(work / "a.pdf")], check=True)
    subprocess.run(["pdfseparate", "-f", "4", "-l", "4", str(MIXED), str(work / "b.pdf")], check=True)
    subprocess.run(["pdfunite", str(work / "a.pdf"), str(work / "b.pdf"), str(work / "scan-only.pdf")], check=True)
    project = project_at(tmp_path)
    inbox = make_inbox(project / "_inbox", [work / "scan-only.pdf"])
    log = tmp_path / "calls.jsonl"
    ok(run_kb_ocr(project, inbox, kb_ocr_env(empty_replay(tmp_path), log)))
    rec = receipt(project)
    assert rec["text_layer"] is False
    assert rec["routes_skipped"] == []
    assert [p["text_class"] for p in rec["pages"]] == ["empty", "empty"]
    assert called_pairs(log) == {(e, k) for e in ROUTES for k in ("p001", "p002")}
    text = only_batch(project) / "text"
    assert not list(text.glob("p*.jsonl")) if text.exists() else True
