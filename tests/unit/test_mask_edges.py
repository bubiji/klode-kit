"""WI-05a edges added during implementation (guards and branches of klode_kit.audit.mask beyond matrix 05a-*).

Same Vision boundary as tests/unit/test_mask.py (``ReplayVision``); paired real path tests/cli/test_mask_real.py.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from PIL import Image

from _support.mask_replay import FIXTURES, ReplayVision, files_under, make_batch, sha256
from klode_kit.audit import mask

pytestmark = pytest.mark.unit

RX = FIXTURES / "fx-rx-stamp" / "images" / "p001.png"
RX_SHA = "3e68e97d5936c3c9df501b6009171de839302556c1ad08fe5200834240d46f80"
SIZE = (1000, 1000)


def line(text, box=(100, 500, 600, 530)):
    return {"text": text, "bbox_px": list(box)}


def rows_masked(texts):
    rules = mask.load_rules()
    band = round(SIZE[1] * rules.header_band)
    boxes = mask.find_boxes([line(t) for t in texts], SIZE, rules)
    return [b for b in boxes if b[1] >= band]


@pytest.mark.parametrize("text", ["Patient ID: 12", "Name 测试患者甲", "患者 I D 番号", "Tel 5", "电话 03-1234-5678",
                                  "カルテ番号 12", "No. AB1234567", "保険証 123456789012"])
def test_header_texts_masked(text):
    """Keyword (CJK by substring with spaces removed, ASCII as a whole word) or identifier pattern -> masked."""
    assert rows_masked([text]), text


@pytest.mark.parametrize("text", ["LIPID 1.2 mmol/L", "葡萄糖 5.1 mmol/L", "全血 120 130 140", "日期：2026-09-19",
                                  "20260919", "Hb 128 g/L 115-150"])
def test_body_texts_not_masked(text):
    """Body rows (lab values, dates, numbers in separate cells, ASCII keyword inside a word) stay readable."""
    assert rows_masked([text]) == [], text


def test_line_without_box_is_ignored():
    """An ocr_line with ``bbox_px: null`` cannot be located and adds no box."""
    rules = mask.load_rules()
    boxes = mask.find_boxes([{"text": "姓名：测试患者甲", "bbox_px": None}], SIZE, rules)
    assert boxes == [[0, 0, SIZE[0], round(SIZE[1] * rules.header_band)]]


def test_not_full_width_pads_the_line_only(tmp_path):
    """``full_width = false``: the box is the padded line box, not the page width."""
    path = tmp_path / "r.toml"
    path.write_text("keywords = ['姓名']\npatterns = []\nheader_band = 0.0\npad_px = 2\npad_frac = 0.0\n"
                    "full_width = false\n", encoding="utf-8")
    rules = mask.load_rules(path)
    assert mask.find_boxes([line("姓名：测试患者甲", (100, 500, 600, 530))], SIZE, rules) == [[98, 498, 603, 533]]
    assert mask.find_boxes([line("结果", (100, 500, 600, 530))], SIZE, rules) == []


@pytest.mark.parametrize("body", [
    "keywords = [1]\npatterns = []\nheader_band = 0.1\npad_px = 1\npad_frac = 0.1\nfull_width = true\n",
    "keywords = ['a']\npatterns = ['']\nheader_band = 0.1\npad_px = 1\npad_frac = 0.1\nfull_width = true\n",
    "keywords = ['a']\npatterns = []\nheader_band = 0.1\npad_px = 1\npad_frac = -0.1\nfull_width = true\n",
    "keywords = ['a']\npatterns = []\nheader_band = 0.1\npad_px = true\npad_frac = 0.1\nfull_width = true\n",
    "keywords = ['a']\npatterns = []\nheader_band = 0.1\npad_px = 1\npad_frac = 0.1\nfull_width = 1\n",
    "keywords = ['a']\npatterns = []\nheader_band = 0.1\npad_px = 1\npad_frac = 0.1\n",
])
def test_more_bad_rules(tmp_path, body):
    path = tmp_path / "r.toml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(Exception) as info:
        mask.load_rules(path)
    assert getattr(info.value, "exit_code", None) == 40


def test_rules_fingerprint_in_tool_version(tmp_path):
    """Different rules -> different ``tool_version``; equal rules -> equal."""
    path = tmp_path / "r.toml"
    path.write_text("keywords = ['姓名']\npatterns = []\nheader_band = 0.1\npad_px = 1\npad_frac = 0.1\n"
                    "full_width = true\n", encoding="utf-8")
    assert mask.load_rules(path).tool_version != mask.load_rules().tool_version
    assert mask.load_rules().tool_version == mask.load_rules().tool_version


def test_apply_rejects_unsupported_mode():
    with pytest.raises(ValueError):
        mask.apply(Image.new("P", (10, 10)), [[0, 0, 5, 5]])


def _png(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.parametrize("mode", ["P", "LA", "1"])
def test_other_modes_are_normalised(tmp_path, mode):
    """A palette / grey+alpha / bilevel page is converted (RGB or RGBA) and masked: the band is 0."""
    data = _png(Image.new(mode, (200, 300), 1))
    src = tmp_path / "p.png"
    src.write_bytes(data)
    sha = sha256(data)
    row = mask.mask_page(tmp_path, src, parent=sha, page_id=f"d-{sha[:12]}/p001", der_sha256=sha,
                         engine=ReplayVision({sha: []}))
    out = Image.open(tmp_path / "_masked" / row["path"])
    assert out.mode in ("RGB", "RGBA")
    band = round(300 * mask.load_rules().header_band)
    assert all(e == (0, 0) for e in out.crop((0, 0, 200, band)).getextrema())


def test_mask_page_guards(tmp_path):
    engine = ReplayVision()
    with pytest.raises(mask.MaskError):
        mask.mask_page(tmp_path, RX, parent="x", page_id="d-3e68e97d5936/p001", der_sha256=RX_SHA, engine=engine)
    with pytest.raises(mask.MaskError):
        mask.mask_page(tmp_path, tmp_path / "missing.png", parent=RX_SHA, page_id="d-3e68e97d5936/p001",
                       der_sha256=RX_SHA, engine=engine)
    with pytest.raises(mask.MaskError):
        mask.mask_page(tmp_path, RX, parent=RX_SHA, page_id="d-3e68e97d5936/p001", der_sha256="0" * 64,
                       engine=engine)
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not an image")
    with pytest.raises(mask.MaskError):
        mask.mask_page(tmp_path, bad, parent=RX_SHA, page_id="d-3e68e97d5936/p001",
                       der_sha256=sha256(b"not an image"), engine=engine)
    assert files_under(tmp_path / "_masked") == []


@pytest.mark.parametrize("batch", ["../x", "ABCDEF123456", 12])
def test_bad_batch_id(tmp_path, batch):
    with pytest.raises(mask.MaskError):
        mask.mask_batch(tmp_path, batch, engine=ReplayVision())


def test_derived_jsonl_not_json(tmp_path):
    d = tmp_path / "work" / "0123456789ab"
    d.mkdir(parents=True)
    (d / "derived.jsonl").write_text("{not json\n", encoding="utf-8")
    with pytest.raises(mask.MaskError):
        mask.mask_batch(tmp_path, "0123456789ab", engine=ReplayVision())


def test_derived_image_missing(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    batch = make_batch(project, [RX])
    der = json.loads((project / "work" / batch / "derived.jsonl").read_text().splitlines()[0])
    (project / der["path"]).rename(tmp_path / "moved.png")
    with pytest.raises(mask.MaskError):
        mask.mask_batch(project, batch, engine=ReplayVision())


@pytest.fixture
def masked_rx(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    batch = make_batch(project, [RX])
    rows = mask.mask_batch(project, batch, engine=ReplayVision())
    return project, batch, rows[0]


def test_listed_but_file_lost_is_rewritten_without_new_row(masked_rx):
    """State: the MANIFEST lists the page but its image was removed -> the rerun writes the same bytes again and
    adds no row."""
    project, batch, row = masked_rx
    out = project / "_masked" / row["path"]
    out.rename(project / "lost.png")
    mask.mask_batch(project, batch, engine=ReplayVision())
    assert sha256(out.read_bytes()) == row["der_sha256"]
    assert mask.read_manifest(project) == [row]


def test_rules_change_appends_new_row(masked_rx, tmp_path):
    """State: masking again under other rules gives other bytes -> a second row; the file holds the newest."""
    project, batch, row = masked_rx
    path = tmp_path / "r.toml"
    path.write_text("keywords = ['姓名']\npatterns = []\nheader_band = 0.0\npad_px = 1\npad_frac = 0.0\n"
                    "full_width = false\n", encoding="utf-8")
    new = mask.mask_batch(project, batch, engine=ReplayVision(), rules=mask.load_rules(path))[0]
    assert new["der_sha256"] != row["der_sha256"]
    assert mask.read_manifest(project) == [row, new]
    assert sha256((project / "_masked" / new["path"]).read_bytes()) == new["der_sha256"]


def test_stale_tmp_files_removed(masked_rx):
    """A ``.<name>.<hex>.tmp`` left by a killed run is removed by the next run."""
    project, batch, _ = masked_rx
    stale = project / "_masked" / "d-3e68e97d5936" / ".p001.m.png.0011223344556677.tmp"
    stale.write_bytes(b"partial")
    mask.mask_batch(project, batch, engine=ReplayVision())
    assert not stale.exists()


def test_read_manifest_ignores_torn_tail(masked_rx):
    project, _, row = masked_rx
    path = project / "_masked" / "MANIFEST.jsonl"
    path.write_bytes(path.read_bytes() + b'{"der_sha256": "ab')
    assert mask.read_manifest(project) == [row]


def _good_row():
    return {"der_sha256": "a" * 64, "parent": "b" * 64, "page_id": "d-0123456789ab/p001",
            "mask_boxes": [[0, 0, 5, 5]], "tool_version": "t", "path": "d-0123456789ab/p001.m.png"}


@pytest.mark.parametrize("patch", [
    {"der_sha256": "A" * 64}, {"parent": None}, {"page_id": "x"}, {"path": "d-0123456789ab/p002.m.png"},
    {"mask_boxes": [[0, 0, 5]]}, {"mask_boxes": [[0, -1, 5, 5]]}, {"mask_boxes": "x"}, {"tool_version": ""},
    {"extra": 1},
])
def test_invalid_manifest_rows_rejected(tmp_path, patch):
    row = {**_good_row(), **patch}
    (tmp_path / "_masked").mkdir()
    (tmp_path / "_masked" / "MANIFEST.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(mask.MaskError):
        mask.read_manifest(tmp_path)


def test_manifest_line_not_json(tmp_path):
    (tmp_path / "_masked").mkdir()
    (tmp_path / "_masked" / "MANIFEST.jsonl").write_text("{x}\n\n", encoding="utf-8")
    with pytest.raises(mask.MaskError):
        mask.read_manifest(tmp_path)


def test_valid_row_and_blank_lines_read(tmp_path):
    (tmp_path / "_masked").mkdir()
    (tmp_path / "_masked" / "MANIFEST.jsonl").write_text("\n" + json.dumps(_good_row()) + "\n", encoding="utf-8")
    assert mask.read_manifest(tmp_path) == [_good_row()]


def test_default_engine_is_vision(monkeypatch, tmp_path):
    """Without ``engine`` the batch uses route A (``OcrMacEngine``); here its Vision call is the recording."""
    from klode_kit.ocr import engines as E
    from klode_kit.ocr import geometry as G

    rec = json.loads((Path(__file__).resolve().parents[1] / "recordings" / "vision" / f"{RX_SHA}.json")
                     .read_text(encoding="utf-8"))

    def recognize(image, langs, level):
        size = G.image_size(image)
        return [(o["text"], 1.0, G.px_to_vision(o["bbox_px"], size)) for o in rec["raw"]]

    real_init = E.OcrMacEngine.__init__

    def init(self, *a, **k):
        real_init(self, *a, recognize=recognize, **k)

    monkeypatch.setattr(E.OcrMacEngine, "__init__", init)
    project = tmp_path / "proj"
    project.mkdir()
    batch = make_batch(project, [RX])
    row = mask.mask_batch(project, batch)[0]
    out = Image.open(project / "_masked" / row["path"])
    assert all(e == (0, 0) for e in out.crop((40, 90, 232, 116)).getextrema())
