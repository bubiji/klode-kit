"""WI-07d: crops for the confirm page (matrix 07d-01..06; plan WI-07d; design 02 r2.2-A derived, r2.3 reason table,
R2-08; 04 E23 "分歧小图按行 ±1 裁").

Contract under test (``klode_kit.audit.crop``):

- **Transform chain** (``Transform`` from a ``derived.jsonl`` ``transform`` record): the original image (pixels, or
  PDF points when ``render_dpi`` is set: scaled by ``render_dpi / 72``) is turned counter-clockwise by ``rotate``
  (``orient.apply``), then cut to ``crop`` (``[x0, y0, x1, y1]`` in the turned frame) when it is not ``null``;
  ``mask_boxes`` only paint pixels and move nothing. ``to_derived(box, t, orig_size)`` maps an original box into
  the derived image, ``to_original(box, t, orig_size)`` maps it back; ``original_size(t, size)`` recovers the
  original size from the derived size (``None`` when a crop hides it).
- ``crop_for_cell(alignment, cell, size)`` -> ``CropBox(box, mode, rows, uncertain)``: ``cell`` is a WI-07b
  ``Cell``, a WI-07c ``CellVote`` or a ``(row, kind, k)`` key; ``size`` is the image's ``(w, h)``.
  - a cell with geometry (``geom``) on a row with a box: mode ``rows`` - the boxes of rows ``r - 1 .. r + 1``
    (those that exist) and the cell's geometry box, padded and clipped to the image; no uncertain code;
  - a cell without geometry (only cloud candidates, or ``no_geometry``) on a row with a box: mode ``row`` - the
    band of rows ``r - 1 .. r + 1`` at the full image width; ``no_bbox_crop``;
  - a row without any box (a page read by cloud routes only): mode ``half_page``; ``no_bbox_crop``.
  The box is integer pixels, half-open, never empty, always inside the image.
- ``write_crops(project, batch_id, audit_rev, *, page_id, alignment, comparison)`` crops every cell of the page
  that is not ``agree`` from a LOCAL derived image of the page (the masked derivative when there is one with the
  geometry's pixel grid, else the image the geometry was read from), checked against its ``der_sha256``; writes
  ``work/<batch>/audit/a<N>/crops/<doc>_<page>-r<row>-<kind>-<k>.png`` and the page index
  ``crops/<doc>_<page>.jsonl``; returns the records (``cell``, ``mode``, ``rows``, ``box_px``, ``box_original``,
  ``source_der_sha256``, ``path``, ``sha256``, ``uncertain``, ...). Crops never go under ``_masked/`` and never
  enter the MANIFEST whitelist; a source under ``_mask_review/`` is refused.

Laws: the transform chain round-trips (07d-01, S5); a crop never leaves the image (07d-02, S4); writing is
idempotent (S5).
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from PIL import Image, ImageChops, ImageOps

from _support.mask_replay import FIXTURES, ReplayVision, fixture_images, files_under, make_batch
from klode_kit.audit import align as A
from klode_kit.audit import compare as C
from klode_kit.audit import crop
from klode_kit.audit import mask
from klode_kit.ocr import orient

pytestmark = pytest.mark.unit

ROUTES4 = ("vision", "mlx", "claude", "codex")
DER = "d" * 64
PAGE = "d-000000000000/p001"
LABELS = "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉"
LABS = FIXTURES / "fx-labs-1x"
LABS_PAGE = "d-19f31b8d0fa1/p001"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fixture_routes(name: str, key: str = "p001") -> dict[str, list[dict]]:
    out = {}
    for route in ROUTES4:
        path = FIXTURES / name / "engine" / route / f"{key}.jsonl"
        out[route] = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    return out


def token_line(i: int, box, *, der: str = DER, geometry: str = "exact", extra: str = "") -> dict:
    """A vision line ``指标<L> <100+i>`` with a label token and a number token inside ``box``."""
    x0, y0, x1, y1 = box
    label = f"指标{LABELS[i]}"
    num = str(100 + i)
    text = f"{label} {num}{extra}"
    mid = x0 + (x1 - x0) // 2
    return {"engine": "vision", "page_id": PAGE, "der_sha256": der, "line_idx": i, "text": text,
            "bbox_px": [x0, y0, x1, y1],
            "tokens": [{"text": label, "span": [0, len(label)], "bbox_px": [x0, y0, mid, y1], "geometry": geometry},
                       {"text": num, "span": [len(label) + 1, len(label) + 1 + len(num)],
                        "bbox_px": [mid + 1, y0, x1, y1], "geometry": geometry}]}


def cloud_line(engine: str, i: int, text: str, der: str = DER) -> dict:
    return {"engine": engine, "page_id": PAGE, "der_sha256": der, "line_idx": i, "text": text, "bbox_px": None,
            "tokens": None}


def row_boxes(n: int, *, top: int = 40, height: int = 24, gap: int = 20, x0: int = 40, x1: int = 600):
    return [(x0, top + i * (height + gap), x1, top + i * (height + gap) + height) for i in range(n)]


def inside(box, size) -> bool:
    w, h = size
    x0, y0, x1, y1 = box
    return all(isinstance(v, int) for v in box) and 0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h


def covers(outer, inner) -> bool:
    return outer[0] <= inner[0] and outer[1] <= inner[1] and outer[2] >= inner[2] and outer[3] >= inner[3]


def clip(box, size):
    w, h = size
    return (max(0, box[0]), max(0, box[1]), min(w, box[2]), min(h, box[3]))


# --------------------------------------------------------------------------- 07d-01 transform round trip


@st.composite
def chains(draw):
    rotate = draw(st.sampled_from(orient.ROTATIONS))
    dpi = draw(st.none() | st.integers(50, 600))
    w = draw(st.integers(1, 3000))
    h = draw(st.integers(1, 3000))
    scale = dpi / 72 if dpi else 1.0
    pw, ph = w * scale, h * scale
    rw, rh = (pw, ph) if rotate in (0, 180) else (ph, pw)
    box = None
    if draw(st.booleans()):
        cx0 = draw(st.integers(0, max(0, math.floor(rw) - 1)))
        cy0 = draw(st.integers(0, max(0, math.floor(rh) - 1)))
        cx1 = draw(st.integers(cx0 + 1, max(cx0 + 1, math.floor(rw))))
        cy1 = draw(st.integers(cy0 + 1, max(cy0 + 1, math.floor(rh))))
        box = [cx0, cy0, cx1, cy1]
    record = {"rotate": rotate, "crop": box, "mask_boxes": []}
    if dpi:
        record["render_dpi"] = dpi
    bx0 = draw(st.floats(0, w, allow_nan=False))
    by0 = draw(st.floats(0, h, allow_nan=False))
    bx1 = draw(st.floats(bx0, w, allow_nan=False))
    by1 = draw(st.floats(by0, h, allow_nan=False))
    return record, (w, h), (bx0, by0, bx1, by1)


@settings(max_examples=300, deadline=None)
@given(chains())
def test_transform_roundtrip(case):
    """07d-01 (L1-S5): for rotate in {0, 90, 180, 270} x random crop offsets (x render dpi), mapping an original
    box into the derived image and back gives the same box within 1 px, and so does the reverse trip."""
    record, orig, box = case
    t = crop.Transform.from_record(record)
    there = crop.to_derived(box, t, orig)
    back = crop.to_original(there, t, orig)
    assert all(abs(a - b) <= 1 for a, b in zip(back, box)), (box, there, back)
    again = crop.to_derived(back, t, orig)
    assert all(abs(a - b) <= 1 for a, b in zip(again, there)), (there, again)
    assert there[0] <= there[2] and there[1] <= there[3]


@settings(max_examples=150, deadline=None)
@given(st.data())
def test_transform_matches_pixels(data):
    """07d-01 (L1-S5, pixel truth): a mark drawn on an original image, turned with ``orient.apply`` and cut with
    the crop, is found in the derived image exactly where ``to_derived`` puts it, and its derived ink box maps back
    onto the original mark (a round trip through real pixels, not only through the formulas)."""
    w = data.draw(st.integers(2, 40))
    h = data.draw(st.integers(2, 40))
    rx0 = data.draw(st.integers(0, w - 1))
    ry0 = data.draw(st.integers(0, h - 1))
    rx1 = data.draw(st.integers(rx0 + 1, w))
    ry1 = data.draw(st.integers(ry0 + 1, h))
    rotate = data.draw(st.sampled_from(orient.ROTATIONS))
    original = Image.new("L", (w, h), 255)
    original.paste(0, (rx0, ry0, rx1, ry1))
    turned = orient.apply(original, rotate)
    ink = ImageOps.invert(turned).getbbox()
    tw, th = turned.size
    if data.draw(st.booleans()):
        cx0 = data.draw(st.integers(0, ink[0]))
        cy0 = data.draw(st.integers(0, ink[1]))
        cx1 = data.draw(st.integers(ink[2], tw))
        cy1 = data.draw(st.integers(ink[3], th))
        cut = [cx0, cy0, cx1, cy1]
        derived = turned.crop(tuple(cut))
    else:
        cut = None
        derived = turned
    t = crop.Transform.from_record({"rotate": rotate, "crop": cut, "mask_boxes": []})
    found = ImageOps.invert(derived).getbbox()
    assert tuple(crop.to_derived((rx0, ry0, rx1, ry1), t, (w, h))) == found
    assert tuple(crop.to_original(found, t, (w, h))) == (rx0, ry0, rx1, ry1)
    assert tuple(crop.derived_size(t, (w, h))) == derived.size
    expected = (w, h) if cut is None else None
    assert crop.original_size(t, derived.size) == expected


def test_render_dpi_scales_to_points():
    """07d-01 (L1-S2): a rendered PDF page maps back to PDF points (px x 72 / dpi)."""
    t = crop.Transform.from_record({"rotate": 0, "crop": None, "mask_boxes": [], "render_dpi": 144})
    assert tuple(crop.to_original((20, 40, 200, 100), t, (612, 792))) == pytest.approx((10, 20, 100, 50))
    assert tuple(crop.derived_size(t, (612, 792))) == pytest.approx((1224, 1584))
    assert tuple(crop.original_size(t, (1224, 1584))) == pytest.approx((612, 792))


@pytest.mark.parametrize("record", [
    {"rotate": 45, "crop": None, "mask_boxes": []},
    {"rotate": True, "crop": None, "mask_boxes": []},
    {"rotate": 0, "crop": [10, 10, 5, 20], "mask_boxes": []},
    {"rotate": 0, "crop": [0, 0, 10], "mask_boxes": []},
    {"rotate": 0, "crop": None, "mask_boxes": [], "render_dpi": 0},
    {"rotate": 0, "crop": None},
    "rotate=0",
])
def test_transform_record_rejected(record):
    """07d-01 guard (L1-S3): a transform record outside the derived contract is a ``CropError``."""
    with pytest.raises(crop.CropError):
        crop.Transform.from_record(record)


# --------------------------------------------------------------------------- 07d-02 / 07d-04 bounds


@st.composite
def pages(draw):
    n = draw(st.integers(1, 12))
    top = draw(st.integers(0, 400))
    height = draw(st.integers(8, 60))
    gap = draw(st.integers(0, 40))
    x0 = draw(st.integers(0, 300))
    x1 = draw(st.integers(x0 + 40, x0 + 1200))
    size = (draw(st.integers(1, 2000)), draw(st.integers(1, 2000)))
    target = draw(st.sampled_from(sorted({0, n - 1, draw(st.integers(0, n - 1))})))
    return n, row_boxes(n, top=top, height=height, gap=gap, x0=x0, x1=x1), size, target


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(pages())
def test_crop_within_bounds(case):
    """07d-02 (L1-S4): for any row (first and last included) and any image size, the crop box is integer,
    non-empty and inside the image; on a ``rows`` crop it covers the visible part of rows r-1..r+1."""
    n, boxes, size, target = case
    al = A.align({"vision": [token_line(i, b) for i, b in enumerate(boxes)]})
    assert len(al.rows) == n
    cb = crop.crop_for_cell(al, (target, "number", 0), size)
    assert inside(cb.box, size), (cb, size)
    if cb.mode == crop.ROWS:
        assert cb.rows == tuple(r for r in (target - 1, target, target + 1) if 0 <= r < n)
        for r in cb.rows:
            vis = clip(al.rows[r].bbox_px, size)
            if vis[0] < vis[2] and vis[1] < vis[3]:
                assert covers(cb.box, vis)
    else:
        assert crop.NO_BBOX_CROP in cb.uncertain


def test_crop_rows_plus_minus_one():
    """07d-03 (L1-S1): fx-labs-1x row 5 (血红蛋白 128) crops rows 4-6 and no part of rows 3 or 7."""
    al = A.align(fixture_routes("fx-labs-1x"))
    size = Image.open(LABS / "images" / "p001.png").size
    cb = crop.crop_for_cell(al, (5, "number", 0), size)
    assert cb.mode == crop.ROWS and cb.uncertain == ()
    assert cb.rows == (4, 5, 6)
    for r in (4, 5, 6):
        assert covers(cb.box, al.rows[r].bbox_px)
    assert cb.box[1] >= al.rows[3].bbox_px[3]
    assert cb.box[3] <= al.rows[7].bbox_px[1]
    assert inside(cb.box, size)


def test_crop_accepts_cell_vote_and_cell():
    """07d-03 (L1-S1): a WI-07c vote, a WI-07b cell and a ``(row, kind, k)`` key give the same crop."""
    al = A.align(fixture_routes("fx-labs-1x"))
    cmp = C.compare(al, routes=ROUTES4)
    size = (960, 616)
    by_key = crop.crop_for_cell(al, (6, "unit", 0), size)
    assert crop.crop_for_cell(al, cmp.cell(6, "unit", 0), size) == by_key
    assert crop.crop_for_cell(al, next(c for c in al.cells if (c.row, c.kind, c.k) == (6, "unit", 0)), size) \
        == by_key


def test_first_last_row_boundary():
    """07d-04 (L1-S2): row 0 touching the top edge and the last row touching the bottom edge: a crop of two rows,
    no negative coordinate, nothing past the image."""
    size = (640, 4 * 44)
    boxes = [(10, 0, 630, 24), (10, 44, 630, 68), (10, 88, 630, 112), (10, 150, 630, 176)]
    al = A.align({"vision": [token_line(i, b) for i, b in enumerate(boxes)]})
    first = crop.crop_for_cell(al, (0, "number", 0), size)
    last = crop.crop_for_cell(al, (3, "number", 0), size)
    assert first.rows == (0, 1) and last.rows == (2, 3)
    assert first.box[1] == 0 and first.box[0] >= 0
    assert last.box[3] == size[1] and last.box[2] <= size[0]
    assert inside(first.box, size) and inside(last.box, size)
    single = A.align({"vision": [token_line(0, (0, 0, 640, 176))]})
    only = crop.crop_for_cell(single, (0, "number", 0), size)
    assert only.rows == (0,) and only.box == (0, 0, 640, 176)


# --------------------------------------------------------------------------- 07d-05 no bbox


def test_no_bbox_fallback():
    """07d-05 (L1-S3): a page read by cloud routes only has no box anywhere: half a page, ``no_bbox_crop``; a cell
    only a cloud route read, on a boxed row: the whole row at full width, ``no_bbox_crop``."""
    texts = [f"指标{LABELS[i]} {100 + i}" for i in range(8)]
    cloud = A.align({"claude": [cloud_line("claude", i, t) for i, t in enumerate(texts)],
                     "codex": [cloud_line("codex", i, t) for i, t in enumerate(texts)]})
    size = (800, 1000)
    for r in (0, 3, 7):
        cb = crop.crop_for_cell(cloud, (r, "number", 0), size)
        assert cb.mode == crop.HALF_PAGE
        assert cb.uncertain == (crop.NO_BBOX_CROP,)
        assert inside(cb.box, size)
        assert cb.box[0] == 0 and cb.box[2] == size[0]
        assert cb.box[3] - cb.box[1] == size[1] // 2 + size[1] % 2
    assert crop.crop_for_cell(cloud, (0, "number", 0), size).box[1] == 0
    assert crop.crop_for_cell(cloud, (7, "number", 0), size).box[3] == size[1]

    boxes = row_boxes(3)
    vision = [token_line(i, b) for i, b in enumerate(boxes)]
    claude = [cloud_line("claude", i, f"指标{LABELS[i]} {100 + i}" + (" 7" if i == 1 else "")) for i in range(3)]
    al = A.align({"vision": vision, "claude": claude})
    extra = next(c for c in al.cells if (c.row, c.kind, c.k) == (1, "number", 1))
    assert extra.geom is None and extra.engines == ("claude",)
    cb = crop.crop_for_cell(al, extra, size)
    assert cb.mode == crop.ROW and cb.uncertain == (crop.NO_BBOX_CROP,)
    assert cb.box[0] == 0 and cb.box[2] == size[0]
    assert covers(cb.box, al.rows[1].bbox_px)
    assert cb.rows == (0, 1, 2)
    assert cb.box[1] >= al.rows[0].bbox_px[1] - crop.PAD_PX and cb.box[3] <= al.rows[2].bbox_px[3] + crop.PAD_PX
    geo = crop.crop_for_cell(al, (1, "number", 0), size)
    assert geo.mode == crop.ROWS and geo.uncertain == ()


def test_no_geometry_row_falls_back_to_row():
    """07d-05 (L1-S3): a row with approx boxes only (``no_geometry``) gets the whole-row crop and
    ``no_bbox_crop``; its exact neighbours keep the ±1-row crop."""
    boxes = row_boxes(3)
    lines = [token_line(i, b, geometry="approx" if i == 1 else "exact") for i, b in enumerate(boxes)]
    al = A.align({"vision": lines})
    size = (700, 200)
    cb = crop.crop_for_cell(al, (1, "number", 0), size)
    assert cb.mode == crop.ROW and crop.NO_BBOX_CROP in cb.uncertain
    assert crop.crop_for_cell(al, (0, "number", 0), size).mode == crop.ROWS


@pytest.mark.parametrize("cell", [(9, "number", 0), (0, "date", 0), "0"])
def test_unknown_cell_rejected(cell):
    """07d-05 guard (L1-S3): a cell the alignment does not hold is a ``CropError``."""
    al = A.align({"vision": [token_line(0, (10, 10, 400, 30))]})
    with pytest.raises(crop.CropError):
        crop.crop_for_cell(al, cell, (500, 100))


@pytest.mark.parametrize("size", [(0, 100), (100, 0), (-1, 5), (10.5, 10), "100x100"])
def test_bad_image_size_rejected(size):
    al = A.align({"vision": [token_line(0, (10, 10, 400, 30))]})
    with pytest.raises(crop.CropError):
        crop.crop_for_cell(al, (0, "number", 0), size)


# --------------------------------------------------------------------------- write_crops: project fixtures


def labs_routes(disputed: bool = True) -> dict[str, list[dict]]:
    routes = fixture_routes("fx-labs-1x")
    if disputed:
        for ln in routes["claude"]:
            if ln["line_idx"] == 5:
                ln["text"] = ln["text"].replace("128", "123")
    return routes


def labs_project(tmp_path: Path, *, band: float | None = None) -> tuple[Path, str]:
    project = tmp_path / "proj"
    project.mkdir()
    batch = make_batch(project, fixture_images("fx-labs-1x"))
    if band is not None:
        rules = mask.MaskRules(keywords=("姓名",), patterns=(), header_band=band, pad_px=4, pad_frac=0.3,
                               full_width=True)
        derived = [json.loads(x) for x in (project / "work" / batch / "derived.jsonl").read_text().splitlines()]
        mask.mask_batch(project, batch, engine=ReplayVision({d["der_sha256"]: [] for d in derived}), rules=rules)
    return project, batch


def page_inputs(routes):
    al = A.align(routes)
    return al, C.compare(al, routes=ROUTES4)


def test_write_crops_disputed_cells(tmp_path):
    """Every cell that is not ``agree`` gets one crop written under ``work/<batch>/audit/a<N>/crops/``: the PNG
    holds exactly the source pixels of ``box_px``; the page index lists the same records."""
    project, batch = labs_project(tmp_path)
    al, cmp = page_inputs(labs_routes())
    disputed = {(v.row, v.kind, v.k) for v in cmp.cells if not v.agree}
    assert (5, "number", 0) in disputed

    records = crop.write_crops(project, batch, 1, page_id=LABS_PAGE, alignment=al, comparison=cmp)

    assert {tuple(r["cell"]) for r in records} == disputed
    crops_dir = project / "work" / batch / "audit" / "a1" / "crops"
    source = Image.open(LABS / "images" / "p001.png")
    for r in records:
        path = project / r["path"]
        assert path.parent == crops_dir
        assert sha256(path.read_bytes()) == r["sha256"]
        assert r["source_der_sha256"] == "19f31b8d0fa18d2a17b2c60e6ed1815197510c43c1a6c7d296b82b61077817f5"
        got = Image.open(path)
        want = source.crop(tuple(r["box_px"]))
        assert got.size == want.size
        assert ImageChops.difference(got.convert("RGB"), want.convert("RGB")).getbbox() is None
        assert r["box_original"] == r["box_px"]
    hb = next(r for r in records if tuple(r["cell"]) == (5, "number", 0))
    assert hb["mode"] == crop.ROWS and hb["rows"] == [4, 5, 6] and hb["uncertain"] == []
    index = crops_dir / "d-19f31b8d0fa1_p001.jsonl"
    assert [json.loads(x) for x in index.read_text().splitlines()] == records


def test_write_crops_no_dispute_writes_nothing_but_index(tmp_path):
    """A page where every cell agrees has no crop images; its index is empty."""
    project, batch = labs_project(tmp_path)
    al, cmp = page_inputs(labs_routes(disputed=False))
    assert all(v.agree for v in cmp.cells)
    assert crop.write_crops(project, batch, 1, page_id=LABS_PAGE, alignment=al, comparison=cmp) == []
    crops_dir = project / "work" / batch / "audit" / "a1" / "crops"
    assert [p.name for p in files_under(crops_dir)] == ["d-19f31b8d0fa1_p001.jsonl"]


def test_crop_from_masked_derivative(tmp_path):
    """07d-06 (L2-S3): with a masked derivative registered for the page, crops come from it: every pixel of the
    crop inside a mask box is 0, while the same pixels of the upright image are not all 0."""
    project, batch = labs_project(tmp_path, band=0.45)
    masked_line = [json.loads(x) for x in (project / "work" / batch / "derived.jsonl").read_text().splitlines()][-1]
    assert masked_line["path"].startswith("_masked/")
    boxes = masked_line["transform"]["mask_boxes"]
    al, cmp = page_inputs(labs_routes())

    records = crop.write_crops(project, batch, 1, page_id=LABS_PAGE, alignment=al, comparison=cmp)

    upright = Image.open(LABS / "images" / "p001.png").convert("L")
    hit = 0
    for r in records:
        assert r["source_der_sha256"] == masked_line["der_sha256"]
        got = Image.open(project / r["path"]).convert("L")
        x0, y0, x1, y1 = r["box_px"]
        for bx0, by0, bx1, by1 in boxes:
            ix0, iy0, ix1, iy1 = max(x0, bx0), max(y0, by0), min(x1, bx1), min(y1, by1)
            if ix0 >= ix1 or iy0 >= iy1:
                continue
            hit += 1
            inner = got.crop((ix0 - x0, iy0 - y0, ix1 - x0, iy1 - y0))
            assert inner.getextrema() == (0, 0)
            assert upright.crop((ix0, iy0, ix1, iy1)).getextrema() != (0, 0)
    assert hit > 0


def test_crops_stay_local_and_unlisted(tmp_path):
    """Privacy: crops are written only under ``work/<batch>/audit/a<N>/crops/``; ``_masked/`` and
    ``_mask_review/`` keep every byte, ``derived.jsonl`` is unchanged, and no crop sha is in the MANIFEST
    whitelist the sender checks."""
    project, batch = labs_project(tmp_path, band=0.45)
    before = {p: p.read_bytes() for p in files_under(project)}
    al, cmp = page_inputs(labs_routes())

    records = crop.write_crops(project, batch, 1, page_id=LABS_PAGE, alignment=al, comparison=cmp)

    assert records
    after = {p: p.read_bytes() for p in files_under(project)}
    assert {p: b for p, b in after.items() if p in before} == before
    crops_dir = project / "work" / batch / "audit" / "a1" / "crops"
    assert all(crops_dir in p.parents for p in set(after) - set(before))
    whitelist = {row["der_sha256"] for row in mask.read_manifest(project)}
    assert whitelist
    assert not {r["sha256"] for r in records} & whitelist
    assert not {sha256(b) for p, b in after.items() if p not in before} & whitelist


def test_write_crops_idempotent(tmp_path):
    """S5: writing the crops of a page again returns the same records and changes no byte."""
    project, batch = labs_project(tmp_path)
    al, cmp = page_inputs(labs_routes())
    first = crop.write_crops(project, batch, 1, page_id=LABS_PAGE, alignment=al, comparison=cmp)
    snap = {p: p.read_bytes() for p in files_under(project)}
    second = crop.write_crops(project, batch, 1, page_id=LABS_PAGE, alignment=al, comparison=cmp)
    assert second == first
    assert {p: p.read_bytes() for p in files_under(project)} == snap


def test_existing_different_crop_refused(tmp_path):
    """A crop already written for this audit revision with other bytes is never overwritten (``a<N>`` is
    immutable): ``CropError`` and the file keeps its bytes."""
    project, batch = labs_project(tmp_path)
    al, cmp = page_inputs(labs_routes())
    records = crop.write_crops(project, batch, 1, page_id=LABS_PAGE, alignment=al, comparison=cmp)
    path = project / records[0]["path"]
    path.write_bytes(b"not the crop")
    with pytest.raises(crop.CropError):
        crop.write_crops(project, batch, 1, page_id=LABS_PAGE, alignment=al, comparison=cmp)
    assert path.read_bytes() == b"not the crop"


def test_changed_source_rejected(tmp_path):
    """A source image whose bytes no longer match its ``der_sha256`` is refused before anything is written."""
    project, batch = labs_project(tmp_path)
    src = project / "_inbox" / "000_p001.png"
    src.write_bytes(src.read_bytes() + b"\0")
    al, cmp = page_inputs(labs_routes())
    with pytest.raises(crop.CropError):
        crop.write_crops(project, batch, 1, page_id=LABS_PAGE, alignment=al, comparison=cmp)
    assert not (project / "work" / batch / "audit").exists()


def test_review_dir_source_refused(tmp_path):
    """A derived line pointing into ``_mask_review/`` (the unmasked before/after sheets) is never a crop source."""
    project, batch = labs_project(tmp_path)
    dpath = project / "work" / batch / "derived.jsonl"
    line = json.loads(dpath.read_text().splitlines()[0])
    review = project / "_mask_review" / "d-19f31b8d0fa1" / "p001.png"
    review.parent.mkdir(parents=True)
    review.write_bytes((project / line["path"]).read_bytes())
    line["path"] = "_mask_review/d-19f31b8d0fa1/p001.png"
    dpath.write_text(json.dumps(line) + "\n")
    al, cmp = page_inputs(labs_routes())
    with pytest.raises(crop.CropError):
        crop.write_crops(project, batch, 1, page_id=LABS_PAGE, alignment=al, comparison=cmp)
    assert not (project / "work" / batch / "audit").exists()


@pytest.mark.parametrize("kwargs", [
    {"batch_id": "../x"},
    {"batch_id": "0123456789ab"},
    {"audit_rev": 0},
    {"audit_rev": True},
    {"page_id": "d-000000000000/p009"},
    {"page_id": "../p001"},
])
def test_write_crops_bad_input(tmp_path, kwargs):
    project, batch = labs_project(tmp_path)
    al, cmp = page_inputs(labs_routes())
    args = {"batch_id": batch, "audit_rev": 1, "page_id": LABS_PAGE, **kwargs}
    with pytest.raises(crop.CropError):
        crop.write_crops(project, args["batch_id"], args["audit_rev"], page_id=args["page_id"], alignment=al,
                         comparison=cmp)


def test_geometry_from_unregistered_image_refused(tmp_path):
    """Boxes read from an image the page's derived lines do not list cannot be placed: ``CropError``."""
    project, batch = labs_project(tmp_path)
    routes = labs_routes()
    for ln in routes["vision"]:
        ln["der_sha256"] = "e" * 64
    al, cmp = page_inputs(routes)
    with pytest.raises(crop.CropError):
        crop.write_crops(project, batch, 1, page_id=LABS_PAGE, alignment=al, comparison=cmp)


def test_rotated_page_box_original(tmp_path):
    """S5 end to end: on a page stored turned (fx-rotated90, ``rotate: 90``) each crop's ``box_original`` cut from
    the ORIGINAL image and turned upright holds exactly the pixels of the crop."""
    project = tmp_path / "proj"
    project.mkdir()
    batch = make_batch(project, fixture_images("fx-rotated90"))
    line = json.loads((project / "work" / batch / "derived.jsonl").read_text().splitlines()[0])
    assert line["transform"]["rotate"] == 90
    upright = Image.open(project / line["path"])
    w, h = upright.size
    boxes = row_boxes(3, top=h // 4, x0=w // 10, x1=w - w // 10)
    vision = [dict(token_line(i, b, der=line["der_sha256"]), page_id=line["page_id"]) for i, b in enumerate(boxes)]
    claude = [dict(cloud_line("claude", i, f"指标{LABELS[i]} {200 + i}", der=line["der_sha256"]),
                   page_id=line["page_id"]) for i in range(3)]
    al = A.align({"vision": vision, "claude": claude})
    cmp = C.compare(al, routes=("vision", "claude"))
    assert not any(v.agree for v in cmp.cells)

    records = crop.write_crops(project, batch, 2, page_id=line["page_id"], alignment=al, comparison=cmp)

    original = Image.open(next((project / "_inbox").glob("*.png")))
    assert original.size == (h, w)
    assert len(records) == 3
    for r in records:
        assert r["box_original"] != r["box_px"]
        cut = orient.apply(original.crop(tuple(r["box_original"])), 90)
        got = Image.open(project / r["path"])
        assert ImageChops.difference(cut.convert("RGB"), got.convert("RGB")).getbbox() is None


def test_crashpoints_declared():
    """Durable writes of the crops are separated by named crash points (matrix section 0.3)."""
    assert {"crop.mkdir", "crop.image_written", "crop.index_written"} <= set(crop.CRASHPOINTS)


# --------------------------------------------------------------------------- added while implementing (guards)


def test_single_unregistered_geometry_image_refused(tmp_path):
    """Every box read from one image the batch does not list: ``CropError``, nothing written."""
    project, batch = labs_project(tmp_path)
    routes = labs_routes()
    for engine in ("vision", "mlx"):
        for ln in routes[engine]:
            ln["der_sha256"] = "e" * 64
    al, cmp = page_inputs(routes)
    with pytest.raises(crop.CropError):
        crop.write_crops(project, batch, 1, page_id=LABS_PAGE, alignment=al, comparison=cmp)
    assert not (project / "work" / batch / "audit").exists()


def test_cloud_only_page_written_as_half_pages(tmp_path):
    """07d-05 through ``write_crops``: a page only cloud routes read has no geometry image; its crops come from the
    page's local image, half a page each, with ``no_bbox_crop``."""
    project, batch = labs_project(tmp_path)
    routes = labs_routes()
    cloud = {e: routes[e] for e in ("claude", "codex")}
    al = A.align(cloud)
    cmp = C.compare(al, routes=("claude", "codex"))
    records = crop.write_crops(project, batch, 1, page_id=LABS_PAGE, alignment=al, comparison=cmp)
    assert records
    for r in records:
        assert r["mode"] == crop.HALF_PAGE and r["uncertain"] == [crop.NO_BBOX_CROP]
        assert r["box_px"][2] - r["box_px"][0] == 960 and r["box_px"][3] - r["box_px"][1] == 308
        assert Image.open(project / r["path"]).size == (960, 308)


def test_transform_bad_mask_boxes_rejected():
    with pytest.raises(crop.CropError):
        crop.Transform.from_record({"rotate": 0, "crop": None, "mask_boxes": [[0, 0, 1]]})


@pytest.mark.parametrize("damage", ["not_json", "no_path", "missing_file", "not_image"])
def test_broken_derived_source_rejected(tmp_path, damage):
    """A derived list or source image that cannot be read is a ``CropError`` before anything is written."""
    project, batch = labs_project(tmp_path)
    dpath = project / "work" / batch / "derived.jsonl"
    line = json.loads(dpath.read_text().splitlines()[0])
    if damage == "not_json":
        dpath.write_text("{not json\n")
    elif damage == "no_path":
        del line["path"]
        dpath.write_text(json.dumps(line) + "\n")
    elif damage == "missing_file":
        line["path"] = "_inbox/gone.png"
        dpath.write_text(json.dumps(line) + "\n")
    else:
        junk = project / "_inbox" / "junk.png"
        junk.write_bytes(b"not an image")
        routes = labs_routes()
        for ln in (x for e in routes.values() for x in e):
            ln["der_sha256"] = sha256(b"not an image")
        line.update(path="_inbox/junk.png", der_sha256=sha256(b"not an image"))
        dpath.write_text(json.dumps(line) + "\n")
        al, cmp = page_inputs(routes)
        with pytest.raises(crop.CropError):
            crop.write_crops(project, batch, 1, page_id=LABS_PAGE, alignment=al, comparison=cmp)
        assert not (project / "work" / batch / "audit").exists()
        return
    al, cmp = page_inputs(labs_routes())
    with pytest.raises(crop.CropError):
        crop.write_crops(project, batch, 1, page_id=LABS_PAGE, alignment=al, comparison=cmp)
    assert not (project / "work" / batch / "audit").exists()


_CHILD = """
import sys
sys.path[:0] = [{tests!r}, {unit!r}]
from pathlib import Path
import test_crop as T
from klode_kit.audit import crop
al, cmp = T.page_inputs(T.labs_routes())
crop.write_crops(Path({project!r}), {batch!r}, 1, page_id=T.LABS_PAGE, alignment=al, comparison=cmp)
"""


@pytest.mark.parametrize("point", crop.CRASHPOINTS)
def test_crash_then_rerun_completes(tmp_path, point):
    """A run killed at any crash point leaves no partial crop: no temporary file survives a rerun, and the rerun
    writes exactly the records of an uninterrupted run."""
    import os
    import subprocess
    import sys

    project, batch = labs_project(tmp_path)
    here = Path(__file__).resolve().parent
    code = _CHILD.format(tests=str(here.parent), unit=str(here), project=str(project), batch=batch)
    env = dict(os.environ, KLODE_KIT_CRASH_AT=point, KLODE_KIT_TEST_SEAMS="1", KLODE_KIT_REAL="0")
    res = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, timeout=120)
    assert res.returncode == -9, res.stderr.decode()  # SIGKILL at the crash point, not an error
    al, cmp = page_inputs(labs_routes())
    records = crop.write_crops(project, batch, 1, page_id=LABS_PAGE, alignment=al, comparison=cmp)
    crops_dir = project / "work" / batch / "audit" / "a1" / "crops"
    assert [p for p in files_under(project) if p.name.endswith(".tmp")] == []
    assert sorted(p.name for p in files_under(crops_dir)) == sorted(
        [Path(r["path"]).name for r in records] + ["d-19f31b8d0fa1_p001.jsonl"])
    (tmp_path / "clean").mkdir()
    clean_project, clean_batch = labs_project(tmp_path / "clean")
    clean = crop.write_crops(clean_project, clean_batch, 1, page_id=LABS_PAGE, alignment=al, comparison=cmp)
    assert [{k: v for k, v in r.items() if k != "path"} for r in records] == \
        [{k: v for k, v in r.items() if k != "path"} for r in clean]
