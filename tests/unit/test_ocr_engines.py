"""WI-04a: local engines A/B - route B fallback chain, MLX text clean-up, weights check (matrix 04a-07..09).

Boundary doubles only (plan 0.4: "Vision, MLX, tesseract -> raw engine output replay"): the MLX ``generate`` call,
the tesseract ``tsv`` subprocess and the Vision ``recognize`` call are replaced by functions returning raw engine
output. Everything after the raw output (layout tags, coordinates, degenerate detection, the chain) is real.
Paired real-engine tests: tests/cli/test_ocr_engines_real.py (04a-10/11/12).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from _support.flows import REPO
from klode_kit.contract import schema
from klode_kit.ocr import engines as E
from klode_kit.ocr.degenerate import repeat_ratio

pytestmark = pytest.mark.unit

PNG = REPO / "tests" / "fixtures" / "fx-u1-3labs" / "images" / "p003.png"  # 960 x 616
DER = "0c2b54bc5d68fc99b0a4ab6b0e328c5c75f14f00187b4a41ec2c566589bfd390"
PAGE = f"d-{DER[:12]}/p001"
REC = REPO / "tests" / "recordings"

TSV_HEAD = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"


def make_tsv(lines: list[str]) -> str:
    rows = [TSV_HEAD]
    for i, line in enumerate(lines):
        for j, word in enumerate(line.split()):
            rows.append(f"5\t1\t1\t1\t{i + 1}\t{j + 1}\t{10 + 60 * j}\t{5 + 5 * i}\t50\t4\t95.0\t{word}\n")
    return "".join(rows)


def lines_with_ratio(distinct: int, repeats: int) -> list[str]:
    uniq = [f"第{i:03d}行 检验项目{i} 结果{i * 7 % 101}" for i in range(distinct)]
    return uniq + [uniq[0]] * repeats


class FakeMlx:
    """Replays raw MLX output per prompt; records which prompts were asked (for ordering only)."""

    def __init__(self, by_prompt: dict[str, str | Exception]):
        self.by_prompt = by_prompt
        self.asked: list[str] = []

    def __call__(self, prompt: str, image: str):
        self.asked.append(prompt)
        out = self.by_prompt[prompt]
        if isinstance(out, Exception):
            raise out
        return out, None


class FakeTsv:
    def __init__(self, tsv: str | Exception):
        self.tsv = tsv
        self.calls = 0

    def __call__(self, image: str, lang: str, psm: str) -> str:
        self.calls += 1
        if isinstance(self.tsv, Exception):
            raise self.tsv
        return self.tsv


def recorded_tsv() -> str:
    return json.loads((REC / "tesseract" / f"{DER}.json").read_text(encoding="utf-8"))["raw"]


def recorded_mlx_raw() -> str:
    return json.loads((REC / "mlx" / f"{DER}.json").read_text(encoding="utf-8"))["raw"]


def run_chain(mlx: FakeMlx, tsv: FakeTsv, **kw) -> E.PageResult:
    return E.ocr_page(
        PNG,
        page_id=PAGE,
        der_sha256=DER,
        mlx=E.MlxEngine(generate=mlx),
        fallback=E.TesseractEngine(run_tsv=tsv),
        **kw,
    )


def assert_lines_valid(lines):
    assert lines, "a successful page has at least one line"
    for line in lines:
        schema.validate("ocr_line", line)


# --------------------------------------------------------------------------- 04a-07 / 08 / 09


def test_degenerate_fallback_chain():
    """04a-07 (S3): primary and alt prompt both degenerate (ratio 0.5), tesseract fine -> tesseract wins,
    degenerate_output recorded, order primary -> alt -> tesseract."""
    bad = "\n".join(lines_with_ratio(10, 10))
    assert repeat_ratio(bad.splitlines()) == 0.5
    mlx = FakeMlx({E.MLX_PROMPT: bad, E.MLX_ALT_PROMPT: bad})
    tsv = FakeTsv(recorded_tsv())
    res = run_chain(mlx, tsv)
    assert res.status == "ok"
    assert res.engine == "tesseract"
    assert [(a["engine"], a["prompt"]) for a in res.attempts] == [
        ("mlx", E.MLX_PROMPT),
        ("mlx", E.MLX_ALT_PROMPT),
        ("tesseract", None),
    ]
    assert [a["degenerate"] for a in res.attempts] == [True, True, False]
    assert res.attempts[0]["repeat_ratio"] == 0.5
    degen = [u for u in res.uncertain if u["reason"] == "degenerate_output"]
    assert len(degen) == 1
    assert degen[0]["scope"] == "page" and degen[0]["engine"] == "mlx"
    assert degen[0]["cleared_by"] == "auto"  # cleared by the replacement route (02 r2.3 reason table)
    assert not [u for u in res.uncertain if u["reason"] == "route_failed"]
    assert_lines_valid(res.lines)
    assert {line["engine"] for line in res.lines} == {"tesseract"}
    assert res.lines[0]["text"] == "示例 医院 检验 报告 单"


@pytest.mark.parametrize("repeats,expect_fallback", [(30, False), (31, True)])
def test_degenerate_threshold_boundary(repeats, expect_fallback):
    """04a-08 (S2): repeat_ratio 0.30 keeps the primary output; 0.31 moves on to the alt prompt."""
    lines = lines_with_ratio(100 - repeats, repeats)
    assert repeat_ratio(lines) == repeats / 100
    mlx = FakeMlx({E.MLX_PROMPT: "\n".join(lines), E.MLX_ALT_PROMPT: recorded_mlx_raw()})
    tsv = FakeTsv(recorded_tsv())
    res = run_chain(mlx, tsv)
    assert res.status == "ok"
    assert res.engine == "mlx"
    assert tsv.calls == 0
    if expect_fallback:
        assert mlx.asked == [E.MLX_PROMPT, E.MLX_ALT_PROMPT]
        assert res.lines[0]["text"] == "示例医院 检验报告单"
        assert [u["reason"] for u in res.uncertain] == ["degenerate_output"]
    else:
        assert mlx.asked == [E.MLX_PROMPT]
        assert [line["text"] for line in res.lines] == lines
        assert res.uncertain == []
    assert_lines_valid(res.lines)


def test_all_fallbacks_degenerate():
    """04a-09 (S3): all three degenerate -> page-level route_failed in uncertain, and no lines at all
    (``None``, never an empty list that would be written as an empty JSONL posing as success)."""
    bad_lines = lines_with_ratio(5, 5)
    bad = "\n".join(bad_lines)
    mlx = FakeMlx({E.MLX_PROMPT: bad, E.MLX_ALT_PROMPT: bad})
    tsv = FakeTsv(make_tsv(bad_lines))
    res = run_chain(mlx, tsv)
    assert res.status == "route_failed"
    assert res.lines is None
    assert res.engine is None
    assert len(res.attempts) == 3 and all(a["degenerate"] for a in res.attempts)
    failed = [u for u in res.uncertain if u["reason"] == "route_failed"]
    assert len(failed) == 1
    assert failed[0]["scope"] == "page" and failed[0]["engine"] == "mlx"
    assert PAGE in failed[0]["id"]
    degen = [u for u in res.uncertain if u["reason"] == "degenerate_output"]
    assert degen and "cleared_by" not in degen[0]


def test_mlx_error_falls_through_to_tesseract():
    """04a-09b (S3, added): an MLX exception is an attempt with an error, not a crash; the chain continues."""
    mlx = FakeMlx({E.MLX_PROMPT: RuntimeError("metal oom"), E.MLX_ALT_PROMPT: RuntimeError("metal oom")})
    res = run_chain(mlx, FakeTsv(recorded_tsv()))
    assert res.status == "ok" and res.engine == "tesseract"
    assert "metal oom" in res.attempts[0]["error"]
    assert [a["engine"] for a in res.attempts] == ["mlx", "mlx", "tesseract"]


def test_tesseract_error_is_route_failed():
    """04a-09c (S3, added): MLX degenerate and tesseract raising -> route_failed, lines None."""
    bad = "\n".join(lines_with_ratio(4, 6))
    res = run_chain(FakeMlx({E.MLX_PROMPT: bad, E.MLX_ALT_PROMPT: bad}), FakeTsv(E.EngineUnavailable("no tesseract")))
    assert res.status == "route_failed" and res.lines is None
    assert "no tesseract" in res.attempts[-1]["error"]


def test_single_line_loop_is_degenerate():
    """04a-08b (added; 189 alignment): the 189 MLX failure is one line repeating a phrase; the line-level
    ratio is 0 there, so the 189 6-gram check (> 0.5) must still catch it."""
    loop = "示例医院 检验报告单\n" + "检验检验检验报告" * 200
    mlx = FakeMlx({E.MLX_PROMPT: loop, E.MLX_ALT_PROMPT: recorded_mlx_raw()})
    res = run_chain(mlx, FakeTsv(recorded_tsv()))
    assert res.attempts[0]["repeat_ratio"] == 0.0
    assert res.attempts[0]["ngram_ratio"] > 0.5
    assert res.attempts[0]["degenerate"] is True
    assert res.engine == "mlx" and mlx.asked == [E.MLX_PROMPT, E.MLX_ALT_PROMPT]


@pytest.mark.parametrize("max_ratio,expect", [(0.5, ["mlx"]), (0.2, ["mlx", "mlx", "tesseract"])])
def test_threshold_is_configurable(max_ratio, expect):
    """04a-08c (added): ``repeat_ratio_max`` from config moves the boundary (defaults.toml 0.3)."""
    bad = "\n".join(lines_with_ratio(7, 3))  # 0.3
    res = run_chain(FakeMlx({E.MLX_PROMPT: bad, E.MLX_ALT_PROMPT: bad}), FakeTsv(recorded_tsv()), max_ratio=max_ratio)
    assert [a["engine"] for a in res.attempts] == expect


# --------------------------------------------------------------------------- MLX text: F-1, F-2


def test_strip_layout_tags_keeps_table_rows():
    """F-1 (WI-00c3): 189 ``strip_layout_tags`` collapsed a table into one line; each <tr> is one line."""
    raw = (
        "<|det|>table [1, 2, 3, 4]<|/det|><table><tr><td>项目</td><td>结果</td></tr>"
        "<tr><td>血红蛋白</td><td>128</td></tr>\n<tr><td>白细胞</td><td></td></tr></table>"
    )
    out = E.strip_layout_tags(raw)
    assert [x.strip() for x in out.splitlines() if x.strip()] == ["项目 结果", "血红蛋白 128", "白细胞"]


@pytest.mark.parametrize(
    "src,want",
    [
        (r"\( 10^9/L \)", "10^9/L"),
        (r"白细胞 6.2 \( 10^12/L \) 3.5-9.5", "白细胞 6.2 10^12/L 3.5-9.5"),
        (r"\( {10}^{ \times  {12}}/\mathrm{L} \)", "10^×12/L"),
        (r"\[ 5 \mathrm{mg} \]", "5mg"),
        ("no math 10^9/L", "no math 10^9/L"),
        (r"\% stays", r"\% stays"),
    ],
)
def test_flatten_latex(src, want):
    """F-2 (WI-00c3): MLX writes units as inline LaTeX; flatten math spans only, keep everything else."""
    assert E.flatten_latex(src) == want


def test_mlx_lines_one_block_per_det():
    """04a-05b (added): each line carries the pixel box of its <|det|> block; a one-line block also gives the
    line box; a multi-line (table) block leaves bbox_px null."""
    lines = E.mlx_lines(recorded_mlx_raw(), (960, 616), page_id=PAGE, der_sha256=DER)
    assert lines[0]["text"] == "示例医院 检验报告单"
    assert lines[0]["block_bbox"] == [36, 23, 322, 58]
    assert lines[0]["bbox_px"] == lines[0]["block_bbox"]
    table = [line for line in lines if line["block_bbox"] == lines[4]["block_bbox"]]
    assert len(table) == 7 and all(line["bbox_px"] is None for line in table)
    assert [line["line_idx"] for line in lines] == list(range(len(lines)))


def test_mlx_text_without_det_has_no_block():
    """04a-05c (added): text before any <|det|> is kept; it simply has no block_bbox (schema: optional)."""
    lines = E.mlx_lines("前导文字\n<|det|>text [0, 0, 999, 999]<|/det|>正文", (100, 50), page_id=PAGE, der_sha256=DER)
    assert [line["text"] for line in lines] == ["前导文字", "正文"]
    assert "block_bbox" not in lines[0]
    assert lines[1]["block_bbox"] == [0, 0, 99, 49]
    for line in lines:
        schema.validate("ocr_line", line)


# --------------------------------------------------------------------------- Vision through the boundary


def test_ocrmac_engine_converts_normalised_boxes():
    """04a-02b (added): OcrMacEngine turns Vision's normalised bottom-left boxes into pixel lines with approx
    tokens; two observations on one row become one line, left to right."""

    def recognize(image, langs, level):
        assert list(langs) == list(E.VISION_LANGS)
        return [
            ("结果 128", 0.9, (0.5, 0.8, 0.2, 0.1)),
            ("血红蛋白", 0.9, (0.1, 0.8, 0.2, 0.1)),
            ("示例医院", 0.5, (0.1, 0.95, 0.3, 0.05)),
        ]

    lines = E.OcrMacEngine(recognize=recognize).ocr_page(PNG, page_id=PAGE, der_sha256=DER)
    assert [line["text"] for line in lines] == ["示例医院", "血红蛋白 结果 128"]
    w, h = 960, 616
    assert lines[0]["bbox_px"] == [96, 0, 384, round(0.05 * h)]
    assert lines[1]["bbox_px"] == [96, round(0.1 * h), 672, round(0.2 * h)]
    toks = lines[1]["tokens"]
    assert [t["text"] for t in toks] == ["血红蛋白", "结果", "128"]
    assert {t["geometry"] for t in toks} == {"approx"}
    for t in toks:
        s, e = t["span"]
        assert lines[1]["text"][s:e] == t["text"]
    assert toks[1]["bbox_px"][0] == 480 and toks[2]["bbox_px"][2] == 672
    assert toks[1]["bbox_px"][2] < toks[2]["bbox_px"][0] + 1
    for line in lines:
        schema.validate("ocr_line", line)


def test_vision_languages_are_multilingual():
    """04a-02c (added): route A asks Vision for Simplified Chinese and Japanese (plus English for codes)."""
    assert E.VISION_LANGS[:2] == ("zh-Hans", "ja-JP")
    assert E.TESS_LANG == "chi_sim+jpn"


# --------------------------------------------------------------------------- weights (doctor hook)


def _fake_cache(root: Path, snapshot: str, *, weights: bool = True, dangling: bool = False) -> Path:
    repo = root / "models--mlx-community--Unlimited-OCR-8bit"
    snap = repo / "snapshots" / snapshot
    blobs = repo / "blobs"
    snap.mkdir(parents=True)
    blobs.mkdir(parents=True)
    (blobs / "cfg").write_text("{}")
    os.symlink(blobs / "cfg", snap / "config.json")
    if weights:
        target = blobs / "w"
        if not dangling:
            target.write_bytes(b"\0" * 16)
        os.symlink(target, snap / "model.safetensors")
    return snap


def test_mlx_weights_status(tmp_path):
    """04a-12b (added): the doctor hook. Present -> ok with the snapshot path; absent snapshot, absent or
    dangling weights -> not ok with a reason (no network, nothing downloaded)."""
    snap = _fake_cache(tmp_path / "ok", E.MLX_SNAPSHOT)
    st = E.mlx_weights_status(hub_cache=tmp_path / "ok")
    assert st.ok and st.path == snap and st.problem is None

    st = E.mlx_weights_status(hub_cache=tmp_path / "empty")
    assert not st.ok and "snapshot" in st.problem

    _fake_cache(tmp_path / "nw", E.MLX_SNAPSHOT, weights=False)
    st = E.mlx_weights_status(hub_cache=tmp_path / "nw")
    assert not st.ok and "safetensors" in st.problem

    _fake_cache(tmp_path / "dl", E.MLX_SNAPSHOT, dangling=True)
    st = E.mlx_weights_status(hub_cache=tmp_path / "dl")
    assert not st.ok and "safetensors" in st.problem


def test_mlx_weights_status_follows_hf_env(tmp_path, monkeypatch):
    """04a-12c (added): HF_HUB_CACHE, then HF_HOME/hub, decide where the cache is."""
    _fake_cache(tmp_path / "hub", E.MLX_SNAPSHOT)
    monkeypatch.delenv("HF_HUB_CACHE", raising=False)
    monkeypatch.setenv("HF_HOME", str(tmp_path))
    assert E.mlx_weights_status().ok
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "nowhere"))
    assert not E.mlx_weights_status().ok


def test_mlx_engine_missing_weights_raises(tmp_path):
    """04a-12d (S3, added): a real MlxEngine without weights raises MlxWeightsMissing naming the doctor -
    never a skip, never a download."""
    with pytest.raises(E.MlxWeightsMissing) as exc:
        E.MlxEngine(hub_cache=tmp_path / "empty").ocr_page(PNG, page_id=PAGE, der_sha256=DER)
    assert "doctor" in str(exc.value)
