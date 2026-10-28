"""WI-08c: auto verified only when four routes agree with exact geometry, judged on the whole chain
align -> compare -> gate -> writeback (matrix 08c-10, 08c-17; K1, K2; section 0.3 note on 08c-10; F5, F18).

The generator builds a kb-ocr batch of 1-3 pages (one record per page: the pipeline's record model, WI-08a) from
true row texts with adjacent duplicates, per row exact or approx-only geometry (vision tokens ``approx`` and no
other exact route), and per route edits at known positions: ``misread`` (another number), ``delete`` and ``merge``
(rows i and i+1 read as one line; not on vision, the geometry route). The batch then goes through the real
``kb-audit`` (``stage.run``) and the writeback ingest (``writeback.run`` without choices); the verdict read back is
the record's ``effective`` revision.

The expectation is computed from the generator's truth only - never from the implementation's ``uncertain`` or
``agree``:

- a page is *touched* when a deleted or merged row lies in it; its same-value runs containing such a row are the
  touched runs (07b-12: the run, not the row, is what the edit identifies);
- touched page (one direction): verified(auto) => every cell value equals the value of the true row text, and for
  every touched run the rows belonging to a verified record are at most the run's rows present in each route (a
  merged line counts once, for its own row's run; the row merged into it is not counted - section 0.3 note);
- untouched page (both directions): verified(auto) <=> every row is read identically by the four routes and has
  exact geometry.

Records map to pages, so the matrix's ``@example`` "one page, two records: one row approx-only, the other exact,
four routes agreeing" is expressed as two one-row pages of one batch: the approx one must not be verified, the
exact one must be.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import tempfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st
from PIL import Image

from _support import writeback_fx as W
from klode_kit.audit import fields as F
from klode_kit.audit import stage
from klode_kit.audit import writeback as WB
from klode_kit.contract import ids

pytestmark = pytest.mark.contract_replay

ROUTES = ("vision", "mlx", "claude", "codex")
CFG = "0123456789ab"
COLS = (40, 330, 470, 620, 690)
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


def misread(text: str) -> str:
    """Another number at the same place: the last digit of the first number + 1 (mod 10)."""
    m = re.search(r"\d+(?:\.\d+)?", text)
    assert m is not None
    num = m.group()
    alt = num[:-1] + str((int(num[-1]) + 1) % 10)
    return text[:m.start()] + alt + text[m.end():]


# --------------------------------------------------------------------------- generator


@st.composite
def page_spec(draw):
    n = draw(st.integers(1, 5))
    texts: list[str] = []
    for _ in range(n):
        if texts and draw(st.integers(0, 3)) == 0:
            texts.append(texts[-1])
        else:
            texts.append(draw(st.sampled_from(POOL)))
    approx = [draw(st.integers(0, 4)) == 0 for _ in range(n)]
    edits: dict[str, list[tuple[str, int]]] = {}
    for route in ROUTES:
        ops, i = [], 0
        options = ["keep"] * 6 + ["misread"] + ([] if route == "vision" else ["delete", "merge"])
        while i < n:
            op = draw(st.sampled_from(options))
            if op == "merge" and i + 1 >= n:
                op = "keep"
            ops.append((op, i))
            i += 2 if op == "merge" else 1
        edits[route] = ops
    return texts, approx, edits


PAGES = st.lists(page_spec(), min_size=1, max_size=3)


def _keep_all(n: int) -> dict[str, list[tuple[str, int]]]:
    return {r: [("keep", i) for i in range(n)] for r in ROUTES}


# --------------------------------------------------------------------------- building the batch


def _geo(engine, page_id, der, idx, text, top, height, *, geometry, tokens):
    toks = []
    for i, m in enumerate(re.finditer(r"\S+", text)):
        x0 = COLS[i] if i < len(COLS) else COLS[-1] + 60 * (i - len(COLS) + 1)
        toks.append({"text": m.group(), "bbox_px": [x0, top, x0 + 12 * len(m.group()), top + height],
                     "span": [m.start(), m.end()], "geometry": geometry})
    bbox = [min(t["bbox_px"][0] for t in toks), top, max(t["bbox_px"][2] for t in toks), top + height]
    return {"engine": engine, "page_id": page_id, "der_sha256": der, "line_idx": idx, "text": text,
            "bbox_px": bbox, "tokens": toks if tokens else None}


def _route_lines(route, spec, page_id, der):
    texts, approx, edits = spec
    out = []
    for op, i in edits[route]:
        if op == "delete":
            continue
        text = misread(texts[i]) if op == "misread" else texts[i]
        top, height = 100 + 50 * i, 24
        if op == "merge":
            text, height = texts[i] + " " + texts[i + 1], 74
        idx = len(out)
        if route == "vision":
            out.append(_geo(route, page_id, der, idx, text, top, height,
                            geometry="approx" if approx[i] else "exact", tokens=True))
        elif route == "mlx":
            out.append(_geo(route, page_id, der, idx, text, top, height, geometry=None, tokens=False))
        else:
            out.append({"engine": route, "page_id": page_id, "der_sha256": der, "line_idx": idx, "text": text,
                        "bbox_px": None, "tokens": None})
    return out


def build(root: Path, specs) -> tuple[Path, str, list[dict]]:
    project = root / "proj"
    project.mkdir()
    pages = []
    for k, spec in enumerate(specs, start=1):
        n = len(spec[0])
        img = Image.new("L", (900, 100 + 50 * n + 200), 255)
        img.putpixel((k, 1), 0)
        path = root / f"p{k:03d}.png"
        img.save(path, format="PNG")
        der = hashlib.sha256(path.read_bytes()).hexdigest()
        doc = ids.doc_id(der)
        pages.append({"key": f"p{k:03d}", "der": der, "doc_id": doc, "page_id": f"{doc}/p001", "path": path,
                      "spec": spec})
    b = ids.batch_id(p["der"] for p in pages)
    bdir = project / "work" / b
    bdir.mkdir(parents=True)
    (bdir / "derived.jsonl").write_text("".join(json.dumps(
        {"der_sha256": p["der"], "page_id": p["page_id"], "parent": p["der"], "path": str(p["path"]),
         "transform": {"crop": None, "mask_boxes": [], "rotate": 0}}) + "\n" for p in pages), encoding="utf-8")
    (bdir / "manifest.jsonl").write_text("".join(json.dumps(
        {"doc_id": p["doc_id"], "img_sha256": p["der"], "kind": "image", "path": str(p["path"])}) + "\n"
        for p in pages), encoding="utf-8")
    attempts, out_pages = {}, []
    for p in pages:
        out_pages.append({"key": p["key"], "doc_id": p["doc_id"], "page_id": p["page_id"], "routes": {},
                          "uncertain": []})
    for r in ROUTES:
        rid = f"{b}/{r}/{CFG}/t1"
        adir = f"ocr/{r}/{CFG}.t1"
        attempts[r] = {"run_id": rid, "dir": adir, "status": "done"}
        for p, op in zip(pages, out_pages):
            lines = _route_lines(r, p["spec"], p["page_id"], p["der"])
            target = bdir / adir / f"{p['key']}.jsonl"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("".join(json.dumps({**x, "run_id": rid}, ensure_ascii=False) + "\n" for x in lines),
                              encoding="utf-8")
            op["routes"][r] = {"status": "done", "run_id": rid, "file": f"{adir}/{p['key']}.jsonl"}
    (bdir / "receipt.json").write_text(json.dumps({"schema": "klode-kit.ocr_batch/1", "batch_id": b,
                                                   "routes": list(ROUTES), "pages": out_pages,
                                                   "attempts": attempts}), encoding="utf-8")
    return project, b, pages


# --------------------------------------------------------------------------- truth


def run_ids(texts: list[str]) -> list[int]:
    out, run = [], -1
    for i, t in enumerate(texts):
        if i == 0 or t != texts[i - 1]:
            run += 1
        out.append(run)
    return out


def readings(spec) -> list[set[str]]:
    """Per row, the texts the four routes give it (untouched pages only: every route reads every row)."""
    texts, _, edits = spec
    out = [set() for _ in texts]
    for route in ROUTES:
        for op, i in edits[route]:
            out[i].add(misread(texts[i]) if op == "misread" else texts[i])
    return out


def touched_runs(spec) -> set[int]:
    texts, _, edits = spec
    runs = run_ids(texts)
    hit = set()
    for route in ROUTES:
        for op, i in edits[route]:
            if op == "delete":
                hit.add(runs[i])
            elif op == "merge":
                hit |= {runs[i], runs[i + 1]}
    return hit


def present(spec, route: str, run: int) -> int:
    texts, _, edits = spec
    runs = run_ids(texts)
    return sum(1 for op, i in edits[route] if op != "delete" and runs[i] == run)


def truth_values(rec_id: str, texts: list[str]) -> dict[str, str]:
    out = {}
    for row, text in enumerate(texts):
        seen: dict[str, int] = {}
        for f in F.extract(text):
            k = seen.get(f.kind, 0)
            seen[f.kind] = k + 1
            out[f"{rec_id}/r{row:03d}-{f.kind}-{k}"] = f.value
    return out


def check(project: Path, pages: list[dict]) -> None:
    for p in pages:
        texts, approx, _ = p["spec"]
        rec_id = ids.rec_id({"doc_id": p["doc_id"], "page_id": p["page_id"], "record_idx": 0})
        front = W.front(project, rec_id)
        assert front is not None
        eff = front.get("effective")
        auto = bool(eff) and eff["status"] == "verified" and eff["verified_by"] == "auto"
        if eff is not None:
            assert auto, eff  # without choices only an auto-verified candidate may become effective
        runs = touched_runs(p["spec"])
        if runs:
            if auto:
                assert W.body(project, rec_id, eff["rev"])["fields"] == truth_values(rec_id, texts)
            rid = run_ids(texts)
            for run in runs:
                verified_rows = sum(1 for r in rid if r == run) if auto else 0
                assert verified_rows <= min(present(p["spec"], route, run) for route in ROUTES), (run, p["spec"])
        else:
            expected = all(len(rd) == 1 for rd in readings(p["spec"]))   # WI-32b: approx geometry no longer blocks
            assert auto == expected, (p["spec"], front["candidates"][-1])


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(PAGES)
@example([(["血红蛋白 128 g/L"], [True], _keep_all(1)), (["尿素 7.3 mmol/L"], [False], _keep_all(1))])
@example([(["尿素 7.3 mmol/L", "尿素 7.3 mmol/L", "血红蛋白 128 g/L"], [False] * 3,
           {"vision": [("keep", 0), ("keep", 1), ("keep", 2)], "mlx": [("keep", 0), ("keep", 1), ("keep", 2)],
            "claude": [("delete", 0), ("keep", 1), ("keep", 2)], "codex": [("keep", 0), ("keep", 1), ("keep", 2)]})])
@example([(["血红蛋白 128 g/L", "尿素 7.3 mmol/L"], [False, False],
           {"vision": [("keep", 0), ("keep", 1)], "mlx": [("merge", 0)], "claude": [("keep", 0), ("keep", 1)],
            "codex": [("keep", 0), ("keep", 1)]})])
def test_auto_verified_iff_all_agree_no_uncertain(specs):
    """08c-10 (S4): see the module docstring."""
    with tempfile.TemporaryDirectory() as tmp:
        project, b, pages = build(Path(tmp), specs)
        assert stage.run(project, b, out=io.StringIO()) in (0, 10)
        assert WB.run(project, b, None, out=io.StringIO(), stdout=io.StringIO()) in (0, 10)
        check(project, pages)


def test_example_approx_record_not_verified_exact_record_verified(tmp_path):
    """08c-10 ``@example`` stated directly (S1): the approx-only record is pending, the exact one verified(auto)."""
    specs = [(["血红蛋白 128 g/L"], [True], _keep_all(1)), (["尿素 7.3 mmol/L"], [False], _keep_all(1))]
    project, b, pages = build(tmp_path, specs)
    assert stage.run(project, b, out=io.StringIO()) == 0     # WI-32b: both records verify
    assert WB.run(project, b, None, out=io.StringIO(), stdout=io.StringIO()) == 0
    fronts = [W.front(project, ids.rec_id({"doc_id": p["doc_id"], "page_id": p["page_id"], "record_idx": 0}))
              for p in pages]
    assert fronts[0]["effective"]["verified_by"] == "auto" and fronts[0]["status"] == "verified"
    assert fronts[1]["effective"]["verified_by"] == "auto" and fronts[1]["status"] == "verified"


# --------------------------------------------------------------------------- 08c-17


def test_no_geometry_row_never_auto_verified(tmp_path):
    """08c-17 (S3): fx-approx-only - one row has approx geometry only and the four routes read it identically:
    after align -> compare -> gate -> writeback the record's candidate is pending_confirm with a ``no_geometry``
    entry and no effective revision exists."""
    project = tmp_path / "proj"
    project.mkdir()
    b, adir = W.audited(project, W.APPROX)
    code, receipt, err = W.submit(project, None, tmp_path, batch_id=b)
    assert code == 0, err     # WI-32b: four equal routes verify despite approx-only geometry
    (cand,) = W.candidates(adir)
    front = W.front(project, cand["rec_id"])
    assert front["effective"]["verified_by"] == "auto"
    assert front["candidates"][-1]["status"] == "verified"
    assert front["candidates"][-1]["uncertain"] == []
    assert any("no_geometry" in f["notes"] for f in cand["fields"])
    assert front["status"] == "verified"
    assert receipt["per_record"][0]["result"] == "committed"
