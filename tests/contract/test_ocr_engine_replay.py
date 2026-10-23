"""WI-04a: real recorded engine output -> ``ocr_line`` (matrix 04a-04..06).

Input: the WI-00c3 recordings ``tests/recordings/{vision,mlx,tesseract}/<der_sha256>.json`` (real ocrmac 1.0.1,
mlx-vlm 0.7.1 Unlimited-OCR 8bit, tesseract 5.5.3 on the registered synthetic pages; ``fx-labs-1x`` itself has
no recording, the three ``fx-u1-3labs`` lab pages are the same layout). The conversion entry points are the ones
the live engines call after their raw call:

- vision: ``raw`` is the observation list already in pixels (top-left), as the spike wrote it;
- mlx: ``raw`` is the model text with ``<|det|>`` 0-999 boxes and HTML tables;
- tesseract: ``raw`` is the tsv.

Paired real-engine tests: tests/cli/test_ocr_engines_real.py (04a-10/11/12).
"""

from __future__ import annotations

import json

import pytest

from _support.flows import REPO
from klode_kit.contract import schema
from klode_kit.ocr import engines as E
from klode_kit.ocr import geometry as G

pytestmark = pytest.mark.contract_replay

REC = REPO / "tests" / "recordings"
FIXTURES = REPO / "tests" / "fixtures"
SHAS = sorted(p.stem for p in (REC / "mlx").glob("*.json"))


def load(engine: str, sha: str) -> dict:
    return json.loads((REC / engine / f"{sha}.json").read_text(encoding="utf-8"))


def convert(engine: str, sha: str) -> tuple[list[dict], tuple[int, int]]:
    rec = load(engine, sha)
    size = G.image_size(FIXTURES / rec["image"])
    page = f"d-{sha[:12]}/p001"
    if engine == "vision":
        return E.vision_lines(rec["raw"], size, page_id=page, der_sha256=sha), size
    if engine == "mlx":
        return E.mlx_lines(rec["raw"], size, page_id=page, der_sha256=sha), size
    return E.tesseract_lines(rec["raw"], size, page_id=page, der_sha256=sha), size


def boxes_of(line: dict) -> list:
    out = [line["bbox_px"], line.get("block_bbox")]
    out += [t["bbox_px"] for t in line["tokens"] or []]
    return [b for b in out if b is not None]


def test_recordings_present():
    """Guard against a vacuous parametrisation: seven recorded pages per local engine."""
    assert len(SHAS) == 7
    for engine in ("vision", "tesseract"):
        assert sorted(p.stem for p in (REC / engine).glob("*.json")) == SHAS


@pytest.mark.parametrize("engine", ["vision", "mlx", "tesseract"])
@pytest.mark.parametrize("sha", SHAS)
def test_engine_replay_conforms(engine, sha):
    """04a-04 (S2): every converted line passes the ocr_line schema; every box is 4 ints inside the image;
    line_idx runs 0..n-1; the page has text."""
    lines, size = convert(engine, sha)
    assert len(lines) >= 5
    assert [line["line_idx"] for line in lines] == list(range(len(lines)))
    for line in lines:
        schema.validate("ocr_line", line)
        assert line["engine"] == engine
        assert line["der_sha256"] == sha
        assert line["text"].strip()
        for box in boxes_of(line):
            assert len(box) == 4 and all(type(v) is int for v in box), box
            assert G.in_bounds(box, size), (box, size)
        if engine != "mlx":
            assert line["bbox_px"] is not None


@pytest.mark.parametrize("engine", ["vision", "tesseract"])
@pytest.mark.parametrize("sha", SHAS)
def test_token_spans_index_line_text(engine, sha):
    """04a-04b (S4, added): for every token of every recorded page, ``text[s:e] == token.text`` and the token
    box lies inside its line box - the span/box pair is what cells will cite (r2.2-A prov)."""
    lines, _ = convert(engine, sha)
    n = 0
    for line in lines:
        for t in line["tokens"]:
            s, e = t["span"]
            assert line["text"][s:e] == t["text"]
            lb, tb = line["bbox_px"], t["bbox_px"]
            assert lb[0] <= tb[0] <= tb[2] <= lb[2] and lb[1] <= tb[1] <= tb[3] <= lb[3]
            n += 1
    assert n >= 10


@pytest.mark.parametrize("sha", SHAS)
def test_mlx_tokens_null_block_bbox(sha):
    """04a-05 (S1): MLX has no word boxes: tokens is None; every recorded line came from a <|det|> block, so
    every line has a non-empty block_bbox."""
    lines, _ = convert("mlx", sha)
    for line in lines:
        assert line["tokens"] is None
        assert line["block_bbox"] and len(line["block_bbox"]) == 4


@pytest.mark.parametrize("sha", SHAS)
def test_approx_marked(sha):
    """04a-06 (S1): ocrmac boxes are observations, not words (R3-05), so every Vision token is approx."""
    lines, _ = convert("vision", sha)
    tokens = [t for line in lines for t in line["tokens"]]
    assert tokens
    assert {t["geometry"] for t in tokens} == {"approx"}


@pytest.mark.parametrize("sha", SHAS)
def test_tesseract_tokens_exact(sha):
    """04a-06b (added): tesseract word boxes are measured, so every tesseract token is exact."""
    lines, _ = convert("tesseract", sha)
    tokens = [t for line in lines for t in line["tokens"]]
    assert tokens and {t["geometry"] for t in tokens} == {"exact"}


@pytest.mark.parametrize("sha", SHAS)
def test_replay_lines_match_spike_lines(sha):
    """04a-04c (added): production conversion reproduces the spike's recorded ``lines`` (F-1 fix included)
    for vision and tesseract exactly; for mlx exactly once LaTeX units are flattened (F-2)."""
    for engine in ("vision", "tesseract"):
        lines, _ = convert(engine, sha)
        assert [line["text"] for line in lines] == load(engine, sha)["lines"]
    mlx, _ = convert("mlx", sha)
    want = [E.flatten_latex(x) for x in load("mlx", sha)["lines"]]
    assert [line["text"] for line in mlx] == want
    assert not any("\\(" in line["text"] for line in mlx)


def test_mlx_table_rows_stay_rows():
    """F-1 on real output: the recorded fx-u1-3labs p003 table yields one line per <tr> (7 rows)."""
    lines, _ = convert("mlx", "0c2b54bc5d68fc99b0a4ab6b0e328c5c75f14f00187b4a41ec2c566589bfd390")
    texts = [line["text"] for line in lines]
    assert "项目 结果 单位 提示 参考范围" in texts
    assert "血红蛋白 119 g/L  115-150" in texts
    assert len(texts) == 11
