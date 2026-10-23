"""WI-07d: crops of disputed cells for the confirm page (design 02 r2.2-A ``derived``, r2.3 reason table
``no_bbox_crop``, R2-08; 04 E23 "分歧小图按行 ±1 裁"; matrix 07d-*).

**Transform chain.** A ``derived.jsonl`` line's ``transform`` says how its image was made from the original:

1. ``render_dpi`` (rendered PDF pages): the original is the PDF page in points, scaled by ``render_dpi / 72``;
2. ``rotate``: the (scaled) original turned counter-clockwise by 0/90/180/270 degrees (``ocr.orient.apply``);
3. ``crop``: ``[x0, y0, x1, y1]`` cut from the turned frame, or ``null``;
4. ``mask_boxes``: pixels painted 0 - geometry is unchanged.

``to_derived(box, t, orig_size)`` maps a box of the original (size ``orig_size``, in original units) into the
derived image; ``to_original`` maps a derived box back. Boxes are half-open pixel-edge rectangles, so a box maps
exactly (floats; integers stay integers when no dpi scaling is involved). ``derived_size`` / ``original_size``
convert sizes; ``original_size`` is ``None`` when a crop hides the original size.

**Crop of a cell** (``crop_for_cell(alignment, cell, size)``; ``cell`` is a WI-07b ``Cell``, a WI-07c
``CellVote`` or a ``(row, kind, k)`` key, ``size`` the image's ``(w, h)`` in the alignment's pixel grid):

- ``rows``: the cell has geometry (``Cell.geom``, a token route placed it) and its row has a box: the union of the
  boxes of rows ``r - 1``, ``r``, ``r + 1`` (those that exist and have a box) and the geometry route's candidate
  box, padded by ``PAD_PX`` and clipped to the image. No uncertain code.
- ``row``: the cell has no geometry (only cloud candidates, or a ``no_geometry`` row) but its row has a box: the
  same vertical band of rows ``r - 1 .. r + 1``, at the full image width. ``no_bbox_crop``.
- ``half_page``: the row has no box (a page read by cloud routes only), or the band lies outside the image: half
  of the page (``ceil(h / 2)`` high, full width) centred on the row's estimated position ``(r + 0.5) / n x h``,
  shifted to stay inside the image. ``no_bbox_crop``.

The box is integer pixels (outward rounded), half-open, never empty and always inside the image.

**Writing** (``write_crops``): every cell of one page whose WI-07c verdict is not ``agree`` is cropped from a
LOCAL derived image of the page - the confirm page is opened on this machine only:

- the geometry's image is the derived line whose ``der_sha256`` the candidates with a box carry (exactly one);
- the source is the newest masked derivative of the page (``path`` under ``_masked/``) with the same parent and
  the same pixel grid (``rotate``, ``crop``, ``render_dpi``), else the geometry's image itself; a source under
  ``_mask_review/`` is refused, and its bytes must match its ``der_sha256``;
- crops go to ``work/<batch>/audit/a<N>/crops/<doc>_<page>-r<row>-<kind>-<k>.png`` with the page index
  ``crops/<doc>_<page>.jsonl`` (one record per crop, in cell order). Each PNG carries a ``klode-kit`` text chunk
  naming its cell, so a crop never has the bytes (or sha) of a whitelisted image. Nothing is written under
  ``_masked/`` and nothing is added to the MANIFEST or ``derived.jsonl``: crops never leave the machine.

Record: ``{page_id, cell: [row, kind, k], verdict, mode, rows, box_px, box_original, source_der_sha256, path,
sha256, uncertain}``; ``box_original`` maps ``box_px`` through the source's transform chain back onto the original
image (``None`` when a crop hides the original size).

Durability: images and the index are written to a temporary name and moved into place, ``crashpoint()`` after
every directory creation and every file. ``a<N>`` is immutable: a file already present with the same bytes is
left alone (reruns change nothing), one with other bytes is a ``CropError``.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import math
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from PIL import Image, PngImagePlugin

from klode_kit.audit import align as A
from klode_kit.audit.mask import MASKED_DIR, REVIEW_DIR
from klode_kit.ocr.orient import ROTATIONS
from klode_kit.seams import crashpoint

PAD_PX = 8
NO_BBOX_CROP = "no_bbox_crop"
ROWS, ROW, HALF_PAGE = "rows", "row", "half_page"
CROPS_DIR = "crops"
CRASHPOINTS = ("crop.mkdir", "crop.image_written", "crop.index_written")

_HEX64 = re.compile(r"[0-9a-f]{64}")
_BATCH = re.compile(r"[0-9a-f]{12}")
_PAGE = re.compile(r"(d-[0-9a-f]{12})/(p[0-9]{3,})")
_AGREE = "agree"


class CropError(Exception):
    """Bad input: unknown page / batch / cell, a bad transform, a source image whose bytes changed, a forbidden
    source directory, or an ``a<N>`` file that would change."""


# --------------------------------------------------------------------------- transform chain


def _number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


@dataclass(frozen=True)
class Transform:
    rotate: int = 0
    crop: tuple[float, float, float, float] | None = None
    render_dpi: int | None = None
    mask_boxes: tuple[tuple[float, ...], ...] = ()

    @classmethod
    def from_record(cls, record: Any) -> "Transform":
        """A ``derived`` line's ``transform`` (contract ``derived``); anything else is a ``CropError``."""
        if not isinstance(record, Mapping) or not {"rotate", "crop", "mask_boxes"} <= set(record):
            raise CropError(f"transform must hold rotate, crop and mask_boxes: {record!r}")
        rotate = record["rotate"]
        if isinstance(rotate, bool) or rotate not in ROTATIONS:
            raise CropError(f"transform rotate must be one of {ROTATIONS}: {rotate!r}")
        box = record["crop"]
        if box is not None:
            if not (isinstance(box, (list, tuple)) and len(box) == 4 and all(_number(v) and v >= 0 for v in box)
                    and box[0] < box[2] and box[1] < box[3]):
                raise CropError(f"transform crop must be [x0, y0, x1, y1] with x0 < x1, y0 < y1: {box!r}")
            box = tuple(box)
        dpi = record.get("render_dpi")
        if dpi is not None and (isinstance(dpi, bool) or not isinstance(dpi, int) or dpi < 1):
            raise CropError(f"transform render_dpi must be a positive integer: {dpi!r}")
        masks = record["mask_boxes"]
        if not isinstance(masks, (list, tuple)) or not all(
                isinstance(b, (list, tuple)) and len(b) == 4 and all(_number(v) for v in b) for b in masks):
            raise CropError(f"transform mask_boxes must be boxes of four numbers: {masks!r}")
        return cls(rotate, box, dpi, tuple(tuple(b) for b in masks))

    @property
    def grid(self) -> tuple:
        """What fixes the pixel grid (masking does not)."""
        return self.rotate, self.crop, self.render_dpi


def _scale(t: Transform) -> float:
    return t.render_dpi / 72 if t.render_dpi else 1


def _turned_size(t: Transform, orig_size: Sequence[float]) -> tuple[float, float]:
    s = _scale(t)
    w, h = orig_size[0] * s, orig_size[1] * s
    return (w, h) if t.rotate in (0, 180) else (h, w)


def _num(v: float) -> float | int:
    return int(v) if float(v).is_integer() else v


def _rotate(box: Sequence[float], rotate: int, size: tuple[float, float]) -> tuple[float, ...]:
    """Turn ``box`` of a frame of ``size`` counter-clockwise by ``rotate``."""
    w, h = size
    x0, y0, x1, y1 = box
    if rotate == 90:
        return y0, w - x1, y1, w - x0
    if rotate == 180:
        return w - x1, h - y1, w - x0, h - y0
    if rotate == 270:
        return h - y1, x0, h - y0, x1
    return x0, y0, x1, y1


def derived_size(t: Transform, orig_size: Sequence[float]) -> tuple:
    if t.crop is not None:
        return tuple(_num(t.crop[i + 2] - t.crop[i]) for i in (0, 1))
    return tuple(_num(v) for v in _turned_size(t, orig_size))


def original_size(t: Transform, size: Sequence[float]) -> tuple | None:
    if t.crop is not None:
        return None
    w, h = size if t.rotate in (0, 180) else (size[1], size[0])
    s = _scale(t)
    return _num(w / s), _num(h / s)


def to_derived(box: Sequence[float], t: Transform, orig_size: Sequence[float]) -> tuple:
    s = _scale(t)
    scaled = tuple(v * s for v in box)
    turned = _rotate(scaled, t.rotate, (orig_size[0] * s, orig_size[1] * s))
    if t.crop is not None:
        cx, cy = t.crop[0], t.crop[1]
        turned = (turned[0] - cx, turned[1] - cy, turned[2] - cx, turned[3] - cy)
    return tuple(_num(v) for v in turned)


def to_original(box: Sequence[float], t: Transform, orig_size: Sequence[float]) -> tuple:
    x0, y0, x1, y1 = box
    if t.crop is not None:
        cx, cy = t.crop[0], t.crop[1]
        x0, y0, x1, y1 = x0 + cx, y0 + cy, x1 + cx, y1 + cy
    undo = (360 - t.rotate) % 360
    back = _rotate((x0, y0, x1, y1), undo, _turned_size(t, orig_size))
    s = _scale(t)
    return tuple(_num(v / s) for v in back)


# --------------------------------------------------------------------------- crop of one cell


@dataclass(frozen=True)
class CropBox:
    box: tuple[int, int, int, int]
    mode: str
    rows: tuple[int, ...]
    uncertain: tuple[str, ...]


def _check_size(size: Any) -> tuple[int, int]:
    if not (isinstance(size, (list, tuple)) and len(size) == 2
            and all(type(v) is int and v >= 1 for v in size)):
        raise CropError(f"image size must be two positive integers: {size!r}")
    return size[0], size[1]


def _key(cell: Any) -> tuple[int, str, int]:
    if isinstance(cell, tuple) and len(cell) == 3:
        return cell
    if all(hasattr(cell, a) for a in ("row", "kind", "k")):
        return cell.row, cell.kind, cell.k
    raise CropError(f"not a cell: {cell!r}")


def _clip(box: Sequence[float], size: tuple[int, int]) -> tuple[int, int, int, int] | None:
    w, h = size
    x0, y0 = max(0, math.floor(box[0])), max(0, math.floor(box[1]))
    x1, y1 = min(w, math.ceil(box[2])), min(h, math.ceil(box[3]))
    return (x0, y0, x1, y1) if x0 < x1 and y0 < y1 else None


def _half_page(row: int, n: int, size: tuple[int, int]) -> tuple[int, int, int, int]:
    w, h = size
    half = (h + 1) // 2
    centre = (row + 0.5) / max(n, 1) * h
    y0 = min(max(0, round(centre - half / 2)), h - half)
    return 0, y0, w, y0 + half


def crop_for_cell(al: A.Alignment, cell: Any, size: Any, *, pad: int = PAD_PX) -> CropBox:
    """The crop box of one cell (module docstring, "Crop of a cell")."""
    size = _check_size(size)
    key = _key(cell)
    found = next((c for c in al.cells if (c.row, c.kind, c.k) == key), None)
    if found is None:
        raise CropError(f"the alignment has no cell {key!r}")
    r, n = found.row, len(al.rows)
    rows = tuple(i for i in (r - 1, r, r + 1) if 0 <= i < n and al.rows[i].bbox_px is not None)
    if al.rows[r].bbox_px is not None:
        boxes = [al.rows[i].bbox_px for i in rows]
        geo = found.candidate(found.geom) if found.geom is not None else None
        if geo is not None and geo.bbox_px is not None:
            boxes.append(geo.bbox_px)
        band = (min(b[0] for b in boxes) - pad, min(b[1] for b in boxes) - pad,
                max(b[2] for b in boxes) + pad, max(b[3] for b in boxes) + pad)
        mode = ROWS if geo is not None else ROW
        if mode == ROW:
            band = (0, band[1], size[0], band[3])
        box = _clip(band, size)
        if box is not None:
            return CropBox(box, mode, rows, () if mode == ROWS else (NO_BBOX_CROP,))
    return CropBox(_half_page(r, n, size), HALF_PAGE, (r,), (NO_BBOX_CROP,))


# --------------------------------------------------------------------------- writing


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fsync_dir(directory: Path) -> None:
    with contextlib.suppress(OSError):
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _ensure_dir(directory: Path) -> None:
    missing = []
    cur = directory
    while not cur.is_dir():
        missing.append(cur)
        cur = cur.parent
    for d in reversed(missing):
        try:
            os.mkdir(d)
        except FileExistsError:
            if not d.is_dir():
                raise
        crashpoint("crop.mkdir")


def _write_once(path: Path, data: bytes, after: str) -> None:
    """Atomically create ``path``; the same bytes already there: nothing; other bytes: ``CropError``."""
    if path.exists():
        if path.read_bytes() != data:
            raise CropError(f"{path} already exists with other content (an audit revision is never rewritten)")
        return
    _ensure_dir(path.parent)
    tmp = path.parent / f".{path.name}.{secrets.token_hex(8)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.remove(tmp)
        raise
    _fsync_dir(path.parent)
    crashpoint(after)


def _derived_lines(project: Path, batch_id: str, page_id: str) -> list[dict]:
    path = project / "work" / batch_id / "derived.jsonl"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CropError(f"no derived images for batch {batch_id}: {exc}") from exc
    try:
        lines = [json.loads(x) for x in text.splitlines() if x.strip()]
    except json.JSONDecodeError as exc:
        raise CropError(f"{path}: not JSON lines: {exc}") from exc
    out = [ln for ln in lines if isinstance(ln, dict) and ln.get("page_id") == page_id]
    if not out:
        raise CropError(f"batch {batch_id} lists no derived image for page {page_id}")
    return out


def _is_under(path: str, top: str) -> bool:
    return not os.path.isabs(path) and Path(path).parts[:1] == (top,)


def _source(lines: list[dict], alignment: A.Alignment) -> tuple[dict, Transform]:
    ders = {c.der_sha256 for cell in alignment.cells for _, c in cell.candidates if c.bbox_px is not None}
    if len(ders) > 1:
        raise CropError(f"the page's boxes come from more than one image: {sorted(map(str, ders))}")
    if ders:
        der = next(iter(ders))
        ref = next((ln for ln in lines if ln.get("der_sha256") == der), None)
        if ref is None:
            raise CropError(f"the page's boxes come from image {der}, which the batch does not list")
    else:
        ref = next((ln for ln in lines if not _is_under(str(ln.get("path", "")), MASKED_DIR)), lines[0])
    grid = Transform.from_record(ref.get("transform")).grid
    masked = [ln for ln in lines if _is_under(str(ln.get("path", "")), MASKED_DIR)
              and ln.get("parent") == ref.get("parent")
              and Transform.from_record(ln.get("transform")).grid == grid]
    src = masked[-1] if masked else ref
    return src, Transform.from_record(src.get("transform"))


def _load_source(project: Path, line: dict) -> Image.Image:
    rel = line.get("path")
    if not isinstance(rel, str) or not rel:
        raise CropError(f"derived image {line.get('der_sha256')} has no path")
    if REVIEW_DIR in Path(rel).parts:
        raise CropError(f"{rel}: images under {REVIEW_DIR}/ are never a crop source")
    path = Path(rel) if os.path.isabs(rel) else project / rel
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise CropError(f"cannot read derived image {path}: {exc}") from exc
    if _sha(data) != line.get("der_sha256"):
        raise CropError(f"{path}: bytes do not match der_sha256 (changed since the batch)")
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            return im.copy()
    except Exception as exc:  # PIL raises UnidentifiedImageError / OSError
        raise CropError(f"{path}: not a readable image ({exc})") from exc


def _png(image: Image.Image, label: str) -> bytes:
    info = PngImagePlugin.PngInfo()
    info.add_text("klode-kit", label)
    buf = io.BytesIO()
    image.save(buf, format="PNG", pnginfo=info)
    return buf.getvalue()


def _json_box(box: Sequence[float]) -> list:
    return [_num(round(v, 3)) for v in box]


def write_crops(project: str | os.PathLike, batch_id: str, audit_rev: int, *, page_id: str,
                alignment: A.Alignment, comparison: Any) -> list[dict]:
    """Crop every cell of ``page_id`` that is not ``agree`` (module docstring, "Writing"); returns the records."""
    project = Path(project)
    if not (isinstance(batch_id, str) and _BATCH.fullmatch(batch_id)):
        raise CropError(f"batch id must be 12 hex characters: {batch_id!r}")
    if isinstance(audit_rev, bool) or not isinstance(audit_rev, int) or audit_rev < 1:
        raise CropError(f"audit revision must be an integer >= 1: {audit_rev!r}")
    m = _PAGE.fullmatch(page_id) if isinstance(page_id, str) else None
    if m is None:
        raise CropError(f"page_id must be d-<12 hex>/p<NNN>: {page_id!r}")
    stem = f"{m.group(1)}_{m.group(2)}"
    lines = _derived_lines(project, batch_id, page_id)
    line, t = _source(lines, alignment)
    image = _load_source(project, line)
    orig = original_size(t, image.size)

    out_dir = project / "work" / batch_id / "audit" / f"a{audit_rev}" / CROPS_DIR
    planned: list[tuple[Path, bytes, dict]] = []
    for vote in sorted(comparison.cells, key=lambda v: (v.row, v.kind, v.k)):
        if vote.verdict == _AGREE:
            continue
        cb = crop_for_cell(alignment, vote, image.size)
        name = f"{stem}-r{vote.row:03d}-{vote.kind}-{vote.k}.png"
        data = _png(image.crop(cb.box), f"crop {page_id} r{vote.row} {vote.kind} {vote.k}")
        rel = out_dir.relative_to(project) / name
        planned.append((out_dir / name, data, {
            "page_id": page_id, "cell": [vote.row, vote.kind, vote.k], "verdict": vote.verdict,
            "mode": cb.mode, "rows": list(cb.rows), "box_px": list(cb.box),
            "box_original": _json_box(to_original(cb.box, t, orig)) if orig is not None else None,
            "source_der_sha256": line["der_sha256"], "path": rel.as_posix(), "sha256": _sha(data),
            "uncertain": list(cb.uncertain)}))
    index = out_dir / f"{stem}.jsonl"
    payload = b"".join((json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
                       for _, _, r in planned)
    for path, data, _ in planned + [(index, payload, None)]:
        if path.exists() and path.read_bytes() != data:
            raise CropError(f"{path} already exists with other content (an audit revision is never rewritten)")
    for path, data, _ in planned:
        _write_once(path, data, "crop.image_written")
    _write_once(index, payload, "crop.index_written")
    return [r for _, _, r in planned]
