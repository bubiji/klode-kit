"""Pixel geometry for the local OCR engines (WI-04a; design 02 r2.2-A, R2-08, R3-05).

Every box in the OCR contract is ``[x0, y0, x1, y1]`` in integer pixels of the derived image, origin top-left,
each coordinate clamped to ``[0, width - 1]`` / ``[0, height - 1]`` so that a box never leaves the image.

Engine coordinate systems:

- ``vision`` (Apple Vision via ocrmac): ``(x, y, w, h)`` normalised to the image, origin **bottom-left**;
- ``mlx`` (Unlimited-OCR ``<|det|>`` boxes): ``[x0, y0, x1, y1]`` on a 0-999 grid, origin top-left
  (pixel = value / 999 * size, the DeepSeek-OCR convention, so 999 is the far edge);
- ``tsv`` (tesseract): ``(left, top, width, height)`` in pixels, origin top-left.

``from_px`` is the exact inverse before rounding and returns floats (no 0-999 quantisation), so
``to_px(s, from_px(s, b))`` is ``b`` within 1 px for every system (matrix 04a-01).
"""

from __future__ import annotations

import math
import struct
from collections.abc import Iterable, Sequence
from pathlib import Path

SYSTEMS = ("vision", "mlx", "tsv")
MLX_GRID = 999

Size = tuple[int, int]


def _size(size: Sequence[int]) -> Size:
    if len(size) != 2:
        raise ValueError(f"size must be (width, height), got {size!r}")
    w, h = int(size[0]), int(size[1])
    if w < 1 or h < 1:
        raise ValueError(f"image size must be positive, got {w}x{h}")
    return w, h


def _four(box: Sequence[float], what: str) -> list[float]:
    if len(box) != 4:
        raise ValueError(f"{what} box needs 4 numbers, got {len(box)}")
    vals = [float(v) for v in box]
    if not all(math.isfinite(v) for v in vals):
        raise ValueError(f"{what} box has a non-finite value: {box!r}")
    return vals


def _px(v: float, limit: int) -> int:
    return min(max(math.floor(v + 0.5), 0), limit - 1)


def _corners(x0: float, y0: float, x1: float, y1: float, size: Size) -> list[int]:
    w, h = size
    a, c = sorted((_px(x0, w), _px(x1, w)))
    b, d = sorted((_px(y0, h), _px(y1, h)))
    return [a, b, c, d]


def vision_to_px(box: Sequence[float], size: Sequence[int]) -> list[int]:
    """Vision ``(x, y, w, h)``, normalised, origin bottom-left -> pixel corners, origin top-left."""
    w, h = _size(size)
    x, y, bw, bh = _four(box, "vision")
    return _corners(x * w, (1 - y - bh) * h, (x + bw) * w, (1 - y) * h, (w, h))


def px_to_vision(bbox: Sequence[float], size: Sequence[int]) -> tuple[float, float, float, float]:
    w, h = _size(size)
    x0, y0, x1, y1 = _four(bbox, "pixel")
    return x0 / w, 1 - y1 / h, (x1 - x0) / w, (y1 - y0) / h


def mlx_to_px(box: Sequence[float], size: Sequence[int]) -> list[int]:
    """Unlimited-OCR ``[x0, y0, x1, y1]`` on the 0-999 grid -> pixel corners."""
    w, h = _size(size)
    x0, y0, x1, y1 = _four(box, "mlx")
    return _corners(x0 * w / MLX_GRID, y0 * h / MLX_GRID, x1 * w / MLX_GRID, y1 * h / MLX_GRID, (w, h))


def px_to_mlx(bbox: Sequence[float], size: Sequence[int]) -> tuple[float, float, float, float]:
    w, h = _size(size)
    x0, y0, x1, y1 = _four(bbox, "pixel")
    return x0 * MLX_GRID / w, y0 * MLX_GRID / h, x1 * MLX_GRID / w, y1 * MLX_GRID / h


def tsv_to_px(box: Sequence[float], size: Sequence[int]) -> list[int]:
    """tesseract tsv ``(left, top, width, height)`` -> pixel corners."""
    w, h = _size(size)
    left, top, bw, bh = _four(box, "tsv")
    return _corners(left, top, left + bw, top + bh, (w, h))


def px_to_tsv(bbox: Sequence[float], size: Sequence[int]) -> tuple[float, float, float, float]:
    _size(size)
    x0, y0, x1, y1 = _four(bbox, "pixel")
    return x0, y0, x1 - x0, y1 - y0


_TO = {"vision": vision_to_px, "mlx": mlx_to_px, "tsv": tsv_to_px}
_FROM = {"vision": px_to_vision, "mlx": px_to_mlx, "tsv": px_to_tsv}


def _system(system: str) -> str:
    if system not in _TO:
        raise ValueError(f"unknown coordinate system {system!r}; known: {', '.join(SYSTEMS)}")
    return system


def to_px(system: str, box: Sequence[float], size: Sequence[int]) -> list[int]:
    return _TO[_system(system)](box, size)


def from_px(system: str, bbox: Sequence[float], size: Sequence[int]) -> tuple[float, float, float, float]:
    return _FROM[_system(system)](bbox, size)


def clamp_px(bbox: Sequence[float], size: Sequence[int]) -> list[int]:
    """A pixel box (possibly fractional or slightly outside) -> clamped integer corners."""
    x0, y0, x1, y1 = _four(bbox, "pixel")
    return _corners(x0, y0, x1, y1, _size(size))


def union(boxes: Iterable[Sequence[int]]) -> list[int]:
    boxes = list(boxes)
    if not boxes:
        raise ValueError("union of no boxes")
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def in_bounds(bbox: Sequence[int], size: Sequence[int]) -> bool:
    w, h = _size(size)
    x0, y0, x1, y1 = bbox
    return 0 <= x0 <= x1 <= w - 1 and 0 <= y0 <= y1 <= h - 1


def span_box(bbox: Sequence[int], start: int, end: int, length: int) -> list[int]:
    """The part of ``bbox`` holding characters ``[start, end)`` of a ``length``-character text, estimated by
    character proportion along x (R3-05 ``geometry: approx``)."""
    x0, y0, x1, y1 = bbox
    if length <= 0:
        return [x0, y0, x1, y1]
    width = x1 - x0
    a = x0 + math.floor(width * start / length + 0.5)
    b = x0 + math.floor(width * end / length + 0.5)
    return [a, y0, max(a, b), y1]


_PNG_SIG = b"\x89PNG\r\n\x1a\n"


def image_size(path: str | Path) -> Size:
    """``(width, height)`` of an image. PNG is read from its IHDR header; other formats need Pillow."""
    path = Path(path)
    with path.open("rb") as fh:
        head = fh.read(24)
    if head[:8] == _PNG_SIG and head[12:16] == b"IHDR":
        w, h = struct.unpack(">II", head[16:24])
        return _size((w, h))
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - Pillow ships with the ocr-local and dev groups
        raise ValueError(f"{path}: not a PNG and Pillow is not installed") from exc
    try:
        with Image.open(path) as im:
            return _size(im.size)
    except Exception as exc:  # PIL raises UnidentifiedImageError / OSError
        raise ValueError(f"{path}: not a readable image ({exc})") from exc
