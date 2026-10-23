"""WI-04a edge cases added after green (coverage of guard paths; not part of the recorded red).

Same boundary rule as tests/unit/test_ocr_engines.py: only the engine call itself is replaced.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

from _support.flows import REPO
from klode_kit.ocr import engines as E
from klode_kit.ocr import geometry as G

pytestmark = pytest.mark.unit

DER = "0" * 64
PAGE = "d-000000000000/p001"


def test_malformed_det_box_keeps_text_without_block():
    lines = E.mlx_lines("<|det|>text [1, 2, x, 4]<|/det|>正文", (100, 50), page_id=PAGE, der_sha256=DER)
    assert [line["text"] for line in lines] == ["正文"]
    assert "block_bbox" not in lines[0] and lines[0]["bbox_px"] is None


def test_engine_params_are_fingerprint_inputs():
    assert E.OcrMacEngine(recognize=lambda *a: []).params == {
        "engine": "vision", "langs": ["zh-Hans", "ja-JP", "en-US"], "level": "accurate", "framework": "vision"}
    assert E.TesseractEngine(run_tsv=lambda *a: "").params == {"engine": "tesseract", "lang": "chi_sim+jpn", "psm": "6"}
    p = E.MlxEngine(generate=lambda *a: ("", None)).params
    assert p["prompt"] == "document parsing." and p["alt_prompt"] == "Free OCR." and p["max_tokens"] == 3072
    assert p["snapshot"] == E.MLX_SNAPSHOT


def test_mlx_truncation_is_reported():
    png = REPO / "tests" / "fixtures" / "fx-labs-1x" / "images" / "p001.png"
    eng = E.MlxEngine(max_tokens=10, generate=lambda prompt, image: ("<|det|>text [0, 0, 10, 10]<|/det|>甲", 9))
    lines, meta = eng.run(png, alt=False, page_id=PAGE, der_sha256=DER)
    assert [line["text"] for line in lines] == ["甲"]
    assert meta == {"prompt": "document parsing.", "generation_tokens": 9, "truncated": True}


def test_weights_status_config_missing(tmp_path):
    snap = tmp_path / "models--mlx-community--Unlimited-OCR-8bit" / "snapshots" / E.MLX_SNAPSHOT
    snap.mkdir(parents=True)
    st = E.mlx_weights_status(hub_cache=tmp_path)
    assert not st.ok and "config.json" in st.problem


def test_default_hub_cache_is_under_home(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_HUB_CACHE", raising=False)
    monkeypatch.delenv("HF_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert E.hub_cache_dir() == tmp_path / ".cache" / "huggingface" / "hub"


def test_missing_tesseract_binary_is_engine_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    png = REPO / "tests" / "fixtures" / "fx-labs-1x" / "images" / "p001.png"
    with pytest.raises(E.EngineUnavailable, match="not on PATH"):
        E.TesseractEngine().ocr_page(png, page_id=PAGE, der_sha256=DER)


def test_missing_ocrmac_is_engine_unavailable(monkeypatch):
    monkeypatch.setitem(sys.modules, "ocrmac", None)
    png = REPO / "tests" / "fixtures" / "fx-labs-1x" / "images" / "p001.png"
    with pytest.raises(E.EngineUnavailable, match="ocr-local"):
        E.OcrMacEngine().ocr_page(png, page_id=PAGE, der_sha256=DER)


def test_geometry_more_guards():
    with pytest.raises(ValueError):
        G.vision_to_px((0.1, 0.1, 0.1, 0.1), (10,))
    with pytest.raises(ValueError):
        G.mlx_to_px([1, 2, math.inf, 4], (10, 10))
    assert G.span_box([10, 0, 20, 5], 0, 0, 0) == [10, 0, 20, 5]
    assert G.clamp_px([-3.2, 1.6, 50.4, 9.9], (40, 8)) == [0, 2, 39, 7]


def test_image_size_non_png_uses_pillow(tmp_path):
    from PIL import Image

    p = tmp_path / "a.jpg"
    Image.new("RGB", (23, 9)).save(p)
    assert G.image_size(p) == (23, 9)


def test_route_failed_lists_every_attempt():
    png = REPO / "tests" / "fixtures" / "fx-labs-1x" / "images" / "p001.png"
    loop = "\n".join(["同一行"] * 10)
    res = E.ocr_page(png, page_id=PAGE, der_sha256=DER, mlx=E.MlxEngine(generate=lambda p, i: (loop, None)),
                     fallback=E.TesseractEngine(run_tsv=lambda *a: (_ for _ in ()).throw(E.EngineUnavailable("gone"))))
    failed = [u for u in res.uncertain if u["reason"] == "route_failed"][0]
    assert "document parsing." in failed["fix_action"] and "Free OCR." in failed["fix_action"]
    assert "gone" in failed["fix_action"]


def test_missing_weights_propagate_from_chain(tmp_path):
    """Missing weights are not a degenerate page: the chain re-raises so doctor-level failure is visible."""
    png = REPO / "tests" / "fixtures" / "fx-labs-1x" / "images" / "p001.png"
    with pytest.raises(E.MlxWeightsMissing):
        E.ocr_page(png, page_id=PAGE, der_sha256=DER, mlx=E.MlxEngine(hub_cache=tmp_path),
                   fallback=E.TesseractEngine(run_tsv=lambda *a: ""))
    assert Path(tmp_path).exists()
