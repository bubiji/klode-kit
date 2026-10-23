"""WI-05a: header masking (matrix 05a-01..06, 05a-08; design 02 r2.2-B, r5 "打码对照图位置").

Vision boundary: ``_support.mask_replay.ReplayVision`` (real recorded ocrmac observations through the real
``engines.vision_lines``). Paired real-engine test: tests/cli/test_mask_real.py (05a-07).

Box convention under test: ``mask_boxes`` are ``[x0, y0, x1, y1]`` integer pixel ranges, half-open
(``x0 <= x < x1``), already clipped to the image.
"""

from __future__ import annotations

import io
import json
import multiprocessing
import os
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from PIL import Image, ImageChops

from _support.mask_replay import (
    FIXTURES,
    ReplayVision,
    files_under,
    fixture_images,
    make_batch,
    sha256,
    worker,
)
from klode_kit.audit import mask

pytestmark = pytest.mark.unit

RX = FIXTURES / "fx-rx-stamp" / "images" / "p001.png"
RX_SHA = "3e68e97d5936c3c9df501b6009171de839302556c1ad08fe5200834240d46f80"
RX_PAGE = "d-3e68e97d5936/p001"
U1 = FIXTURES / "fx-u1-3labs" / "images" / "p001.png"
FIELDS = {"der_sha256", "parent", "page_id", "mask_boxes", "tool_version"}


def _open(path: Path) -> Image.Image:
    with Image.open(path) as im:
        im.load()
        return im.copy()


def _clip(box, size):
    w, h = size
    x0, y0, x1, y1 = box
    return max(0, x0), max(0, y0), min(w, x1), min(h, y1)


def _all_zero(im: Image.Image, box) -> bool:
    crop = im.crop(box)
    extrema = crop.getextrema()
    if isinstance(extrema[0], tuple):
        return all(e == (0, 0) for e in extrema)
    return extrema == (0, 0)


def _same(a: Image.Image, b: Image.Image, box) -> bool:
    return ImageChops.difference(a.crop(box), b.crop(box)).getbbox() is None


def _manifest_lines(project: Path) -> list[str]:
    path = project / "_masked" / "MANIFEST.jsonl"
    return path.read_text(encoding="utf-8").splitlines()


# --------------------------------------------------------------------------- 05a-01 / 05a-04: apply


box_st = st.tuples(
    st.integers(-80, 1040), st.integers(-80, 560), st.integers(1, 400), st.integers(1, 300)
).map(lambda t: [t[0], t[1], t[0] + t[2], t[1] + t[3]])


@pytest.mark.parametrize("src", [RX, U1], ids=["rgb", "grey"])
@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(boxes=st.lists(box_st, min_size=0, max_size=5))
def test_mask_pixel_conservation(src, boxes):
    """05a-01 (L2·S4): for any boxes, every pixel inside a (clipped) box is 0 and every pixel outside all boxes
    equals the original; the image keeps its size and mode."""
    orig = _open(src)
    out = mask.apply(orig, boxes)
    assert out.size == orig.size and out.mode == orig.mode
    assert _same(orig, _open(src), (0, 0, *orig.size)), "apply must not modify its input"
    clipped = [c for c in (_clip(b, orig.size) for b in boxes) if c[0] < c[2] and c[1] < c[3]]
    for c in clipped:
        assert _all_zero(out, c), c
    diff = ImageChops.difference(orig, out).convert("L")
    for c in clipped:
        diff.paste(0, c)
    assert diff.getbbox() is None, "a pixel outside every box changed"


def test_mask_box_clipped_at_border():
    """05a-04 (L1·S2): a box half outside the image does not raise; its inside part is 0; size unchanged."""
    orig = _open(RX)
    w, h = orig.size
    out = mask.apply(orig, [[w - 50, -30, w + 70, 40]])
    assert out.size == (w, h)
    assert _all_zero(out, (w - 50, 0, w, 40))
    assert _same(orig, out, (0, 40, w, h))
    assert _same(orig, out, (0, 0, w - 50, 40))


def test_mask_box_fully_outside_changes_nothing():
    """Boundary: a box entirely outside the image changes no pixel."""
    orig = _open(RX)
    w, h = orig.size
    out = mask.apply(orig, [[w + 1, h + 1, w + 50, h + 50], [-100, -100, 0, 0]])
    assert _same(orig, out, (0, 0, w, h))


@pytest.mark.parametrize("bad", [[10, 10, 10, 20], [10, 20, 30, 5], [1, 2, 3], [0, 0, 1.5, 4], "0,0,1,1"])
def test_apply_rejects_malformed_box(bad):
    """Guard: a box that is not four integers with x0 < x1 and y0 < y1 is a ValueError."""
    with pytest.raises(ValueError):
        mask.apply(_open(RX), [bad])


# --------------------------------------------------------------------------- find_boxes / rules


def test_keyword_lines_found_on_recorded_page():
    """The recorded Vision lines of fx-rx-stamp: the 姓名 and 编号 lines are covered by mask boxes; the date
    line and the table rows are not touched."""
    engine = ReplayVision()
    lines = engine.ocr_page(RX, page_id=RX_PAGE, der_sha256=RX_SHA)
    boxes = mask.find_boxes(lines, (960, 478), mask.load_rules())
    out = mask.apply(_open(RX), boxes)
    assert _all_zero(out, (40, 90, 232, 116))  # 姓名：测试患者甲
    assert _all_zero(out, (40, 129, 246, 156))  # 编号：T-0000001
    orig = _open(RX)
    assert _same(orig, out, (40, 172, 254, 196))  # 日期 line kept
    assert _same(orig, out, (0, 228, 960, 478))  # table kept


def test_no_keyword_header_region_masked(tmp_path):
    """05a-05 (L1·S2): a page with no keyword line still gets the top band of ``mask_rules`` blacked out, and
    ``mask_boxes`` is not empty."""
    w, h = 800, 600
    page = Image.new("RGB", (w, h), (255, 255, 255))
    for y in range(0, h, 40):
        page.paste((90, 90, 90), (30, y + 10, 700, y + 22))
    buf = io.BytesIO()
    page.save(buf, format="PNG")
    data = buf.getvalue()
    sha = sha256(data)
    project = tmp_path / "proj"
    project.mkdir()
    src = tmp_path / "page.png"
    src.write_bytes(data)
    obs = [{"text": "检验项目 结果 单位", "bbox_px": [30, 300, 700, 322]},
           {"text": "葡萄糖 5.1 mmol/L", "bbox_px": [30, 340, 700, 362]}]
    engine = ReplayVision({sha: obs})
    row = mask.mask_page(project, src, parent=sha, page_id=f"d-{sha[:12]}/p001", der_sha256=sha, engine=engine)
    rules = mask.load_rules()
    band = round(h * rules.header_band)
    assert band > 0
    assert row["mask_boxes"], row
    out = _open(project / "_masked" / f"d-{sha[:12]}" / "p001.m.png")
    assert _all_zero(out, (0, 0, w, band))
    assert _same(page, out, (0, 300, w, h))


def test_rules_file_shape():
    """The packaged rules carry the Chinese and Japanese header keywords the plan names."""
    rules = mask.load_rules()
    for kw in ("姓名", "患者", "编号", "ID", "病历号", "住院号", "床号", "電話"):
        assert kw in rules.keywords, kw
    assert 0 < rules.header_band < 0.5


@pytest.mark.parametrize("body", [
    "keywords = []\nheader_band = 0.1\npad_px = 4\npad_frac = 0.25\npatterns = []\nfull_width = true\n",
    "keywords = ['姓名']\nheader_band = 0.9\npad_px = 4\npad_frac = 0.25\npatterns = []\nfull_width = true\n",
    "keywords = ['姓名']\nheader_band = 0.1\npad_px = -1\npad_frac = 0.25\npatterns = []\nfull_width = true\n",
    "keywords = ['姓名']\nheader_band = 0.1\npad_px = 4\npad_frac = 0.25\npatterns = ['(']\nfull_width = true\n",
    "keywords = ['姓名']\nheader_band = 0.1\npad_px = 4\npad_frac = 0.25\npatterns = []\nfull_width = true\nextra = 1\n",
    "keywords = ['姓名'\n",
])
def test_bad_rules_file_is_config_error(tmp_path, body):
    """Guard: an empty keyword list, an out-of-range band, a negative pad, a bad regex, an unknown key or bad
    TOML is a configuration error (exit code 40)."""
    path = tmp_path / "rules.toml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(Exception) as info:
        mask.load_rules(path)
    assert getattr(info.value, "exit_code", None) == 40


# --------------------------------------------------------------------------- 05a-02 / 03 / 06: batch


@pytest.fixture
def rx_project(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    batch = make_batch(project, [RX])
    return project, batch


def test_manifest_row_fields(rx_project):
    """05a-02 (L2·S1): one MANIFEST row with the five fields; ``der_sha256`` is the sha of the masked file;
    ``parent`` is the original image sha; ``page_id`` is the page."""
    project, batch = rx_project
    rows = mask.mask_batch(project, batch, engine=ReplayVision())
    lines = _manifest_lines(project)
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert FIELDS <= set(row)
    out = project / "_masked" / "d-3e68e97d5936" / "p001.m.png"
    assert row["der_sha256"] == sha256(out.read_bytes())
    assert row["parent"] == RX_SHA
    assert row["page_id"] == RX_PAGE
    assert row["mask_boxes"] and all(len(b) == 4 and all(type(v) is int for v in b) for b in row["mask_boxes"])
    assert isinstance(row["tool_version"], str) and row["tool_version"]
    assert rows == [row]
    assert mask.read_manifest(project) == [row]
    assert row["der_sha256"] != RX_SHA


def test_manifest_idempotent(rx_project):
    """05a-03 (L2·S5): a second run adds no MANIFEST row and writes the same ``.m.png`` bytes."""
    project, batch = rx_project
    mask.mask_batch(project, batch, engine=ReplayVision())
    out = project / "_masked" / "d-3e68e97d5936" / "p001.m.png"
    first_manifest = (project / "_masked" / "MANIFEST.jsonl").read_bytes()
    first_png = out.read_bytes()
    mask.mask_batch(project, batch, engine=ReplayVision())
    assert (project / "_masked" / "MANIFEST.jsonl").read_bytes() == first_manifest
    assert out.read_bytes() == first_png
    assert len(_manifest_lines(project)) == 1


def test_review_images_only_in_review_dir(rx_project):
    """05a-06 (L2·S3): ``_masked/`` holds only ``*.m.png`` and the MANIFEST; the before/after image exists only
    under ``_mask_review/`` and its sha is never in the MANIFEST."""
    project, batch = rx_project
    mask.mask_batch(project, batch, engine=ReplayVision())
    masked = files_under(project / "_masked")
    assert masked
    for f in masked:
        assert f.name == "MANIFEST.jsonl" or f.name.endswith(".m.png"), f
    review = files_under(project / "_mask_review")
    assert review, "the before/after image is written"
    listed = {r["der_sha256"] for r in mask.read_manifest(project)}
    masked_shas = {sha256(f.read_bytes()) for f in masked}
    for f in review:
        s = sha256(f.read_bytes())
        assert s not in listed and s not in masked_shas
    others = [f for f in files_under(project) if "_masked" not in f.parts and "_mask_review" not in f.parts
              and "work" not in f.parts and "_inbox" not in f.parts]
    assert others == []


def test_masked_image_has_header_blacked(rx_project):
    """The written ``.m.png`` has the name and number lines at 0 and the table unchanged."""
    project, batch = rx_project
    mask.mask_batch(project, batch, engine=ReplayVision())
    out = _open(project / "_masked" / "d-3e68e97d5936" / "p001.m.png")
    assert _all_zero(out, (40, 90, 232, 116))
    assert _all_zero(out, (40, 129, 246, 156))
    assert _same(_open(RX), out, (0, 228, 960, 478))


def test_turned_page_masks_derived_image(tmp_path):
    """Input is the WI-04b derived (upright) image: for the rotated fixture the masked file has the derived
    image's size and ``parent`` is the original (unturned) image sha."""
    project = tmp_path / "proj"
    project.mkdir()
    src = sorted((FIXTURES / "fx-rotated90" / "images").glob("*.png"))[0]
    batch = make_batch(project, [src])
    derived = [json.loads(x) for x in (project / "work" / batch / "derived.jsonl").read_text().splitlines()]
    assert derived[0]["transform"]["rotate"] != 0
    der_path = project / derived[0]["path"]
    w, h = Image.open(der_path).size
    obs = [{"text": "姓名：测试患者甲", "bbox_px": [40, 90, 232, 116]}]
    rows = mask.mask_batch(project, batch, engine=ReplayVision({derived[0]["der_sha256"]: obs}))
    assert rows[0]["parent"] == sha256(src.read_bytes())
    out = _open(project / "_masked" / derived[0]["page_id"].split("/")[0] / "p001.m.png")
    assert out.size == (w, h)
    assert _all_zero(out, (40, 90, 232, 116))


def test_derived_sha_mismatch_writes_nothing(rx_project):
    """Guard: a derived image whose bytes no longer match ``der_sha256`` is a MaskError and nothing is
    written under ``_masked/``."""
    project, batch = rx_project
    der = json.loads((project / "work" / batch / "derived.jsonl").read_text().splitlines()[0])
    target = project / der["path"]
    target.write_bytes(target.read_bytes() + b"\0")
    with pytest.raises(mask.MaskError):
        mask.mask_batch(project, batch, engine=ReplayVision({sha256(target.read_bytes()): []}))
    assert files_under(project / "_masked") == []


def test_missing_batch_is_mask_error(tmp_path):
    """Guard: an unknown batch (no derived.jsonl) is a MaskError."""
    with pytest.raises(mask.MaskError):
        mask.mask_batch(tmp_path, "0" * 12, engine=ReplayVision())


@pytest.mark.parametrize("page_id", ["../evil/p001", "d-3e68e97d5936/../../x", "d-3E68E97D5936/p001", ""])
def test_bad_page_id_rejected(tmp_path, page_id):
    """Guard: ``page_id`` decides the output path, so anything but ``d-<12 hex>/p<NNN>`` is a MaskError and
    nothing is written."""
    with pytest.raises(mask.MaskError):
        mask.mask_page(tmp_path, RX, parent=RX_SHA, page_id=page_id, der_sha256=RX_SHA, engine=ReplayVision())
    assert files_under(tmp_path / "_masked") == []


def test_torn_manifest_tail_is_repaired(rx_project):
    """State: a MANIFEST whose last line was cut by a crash is repaired on the next run: every line is complete
    JSON and the page is listed once."""
    project, batch = rx_project
    (project / "_masked").mkdir()
    (project / "_masked" / "MANIFEST.jsonl").write_text('{"der_sha256": "ab', encoding="utf-8")
    mask.mask_batch(project, batch, engine=ReplayVision())
    lines = _manifest_lines(project)
    assert len(lines) == 1
    assert json.loads(lines[0])["page_id"] == RX_PAGE


def test_corrupt_manifest_row_is_mask_error(rx_project):
    """Guard: a complete MANIFEST line that is not a valid row is a MaskError, not silently dropped."""
    project, batch = rx_project
    (project / "_masked").mkdir()
    (project / "_masked" / "MANIFEST.jsonl").write_text('{"der_sha256": "zz"}\n', encoding="utf-8")
    with pytest.raises(mask.MaskError):
        mask.read_manifest(project)


def test_read_manifest_empty_project(tmp_path):
    """Boundary: no MANIFEST yet -> no rows."""
    assert mask.read_manifest(tmp_path) == []


# --------------------------------------------------------------------------- 05a-08 / crash: processes


def _spawn(project: Path, batch: str, n: int, env=None) -> list[int]:
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(n) if n > 1 else None
    procs = [ctx.Process(target=worker, args=(str(project), batch, barrier, env)) for _ in range(n)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(120)
    return [p.exitcode for p in procs]


def test_concurrent_mask_manifest(tmp_path):
    """05a-08 (L2·S5): two processes mask the same batch at once: no duplicate MANIFEST row, every line is
    complete JSON, one row per page, and every listed sha is the file on disk."""
    project = tmp_path / "proj"
    project.mkdir()
    batch = make_batch(project, fixture_images("fx-u1-3labs"))
    for _ in range(2):
        assert _spawn(project, batch, 2) == [0, 0]
    lines = _manifest_lines(project)
    rows = [json.loads(x) for x in lines]
    assert len(rows) == 3
    assert len({r["der_sha256"] for r in rows}) == 3
    assert len({r["page_id"] for r in rows}) == 3
    for r in rows:
        doc, page = r["page_id"].split("/")
        assert sha256((project / "_masked" / doc / f"{page}.m.png").read_bytes()) == r["der_sha256"]
    assert [f for f in files_under(project / "_masked") if f.name.endswith(".tmp")] == []


@pytest.mark.parametrize("point", ["mask.image_written", "mask.review_written"])
def test_crash_before_manifest_then_rerun(rx_project, point):
    """State: a run killed after a durable write but before the MANIFEST append leaves no MANIFEST row for an
    image; the rerun completes it with exactly one row whose sha matches the file."""
    project, batch = rx_project
    codes = _spawn(project, batch, 1, {"KLODE_KIT_CRASH_AT": point})
    assert codes[0] not in (0, None)
    assert [ln for ln in (_manifest_lines(project) if (project / "_masked" / "MANIFEST.jsonl").exists() else [])
            if ln.strip()] == []
    assert _spawn(project, batch, 1) == [0]
    rows = mask.read_manifest(project)
    assert len(rows) == 1
    out = project / "_masked" / "d-3e68e97d5936" / "p001.m.png"
    assert rows[0]["der_sha256"] == sha256(out.read_bytes())
    assert [f for f in files_under(project) if f.name.endswith(".tmp")] == []


def test_crashpoints_declared():
    """Every durable write is separated by a named crash point (matrix section 0.3)."""
    assert {"mask.image_written", "mask.review_written", "mask.manifest_appended"} <= set(mask.CRASHPOINTS)
    assert os.sep not in "".join(mask.CRASHPOINTS)
