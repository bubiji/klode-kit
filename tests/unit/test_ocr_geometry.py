"""WI-04a: coordinate systems of the three local engines -> pixels, top-left origin (matrix 04a-01..03).

- Vision (ocrmac): ``(x, y, w, h)`` normalised to the image, origin bottom-left.
- MLX (Unlimited-OCR ``<|det|>``): ``[x0, y0, x1, y1]`` on a 0-999 grid, origin top-left.
- tesseract tsv: ``(left, top, width, height)`` in pixels, origin top-left.

The target is ``[x0, y0, x1, y1]`` in integer pixels of the derived image, every coordinate clamped to
``[0, width - 1]`` / ``[0, height - 1]`` so that a box never leaves the image (04a-03).
"""

from __future__ import annotations

import struct
import zlib

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from klode_kit.ocr import geometry as G

pytestmark = pytest.mark.unit


@st.composite
def size_and_box(draw):
    w = draw(st.integers(min_value=1, max_value=20000))
    h = draw(st.integers(min_value=1, max_value=20000))
    x0 = draw(st.integers(min_value=0, max_value=w - 1))
    x1 = draw(st.integers(min_value=x0, max_value=w - 1))
    y0 = draw(st.integers(min_value=0, max_value=h - 1))
    y1 = draw(st.integers(min_value=y0, max_value=h - 1))
    return (w, h), [x0, y0, x1, y1]


@pytest.mark.parametrize("system", ["vision", "mlx", "tsv"])
@settings(max_examples=300, deadline=None)
@given(sb=size_and_box())
@example(sb=((1, 1), [0, 0, 0, 0]))
@example(sb=((4000, 3000), [3999, 2999, 3999, 2999]))
@example(sb=((1000, 1000), [0, 0, 999, 999]))
def test_geometry_roundtrip(system, sb):
    """04a-01 (S5): pixel -> engine coordinates -> pixel is the identity within 1 px, for every system."""
    size, box = sb
    back = G.to_px(system, G.from_px(system, box, size), size)
    assert len(back) == 4
    assert all(isinstance(v, int) for v in back)
    assert all(abs(a - b) <= 1 for a, b in zip(back, box)), (box, back)


@pytest.mark.parametrize("system", ["vision", "mlx", "tsv"])
@settings(max_examples=300, deadline=None)
@given(
    w=st.integers(min_value=1, max_value=8000),
    h=st.integers(min_value=1, max_value=8000),
    raw=st.lists(st.floats(min_value=-2000, max_value=2000, allow_nan=False), min_size=4, max_size=4),
)
def test_to_px_never_leaves_image(system, w, h, raw):
    """04a-01b (S4, added): whatever an engine reports, the pixel box is ordered and inside the image."""
    if system == "vision":
        box = [v / 1000 for v in raw[:2]] + [abs(v) / 1000 for v in raw[2:]]
    elif system == "tsv":
        box = [int(raw[0]), int(raw[1]), abs(int(raw[2])), abs(int(raw[3]))]
    else:
        box = raw
    x0, y0, x1, y1 = G.to_px(system, box, (w, h))
    assert 0 <= x0 <= x1 <= w - 1
    assert 0 <= y0 <= y1 <= h - 1


def test_vision_bottom_left_flip():
    """04a-02: Vision y is measured from the bottom: y=0 lies on the bottom edge, y+h=1 on the top edge."""
    w, h = 1000, 500
    bottom = G.vision_to_px((0.1, 0.0, 0.2, 0.1), (w, h))
    top = G.vision_to_px((0.1, 0.9, 0.2, 0.1), (w, h))
    assert bottom == [100, 450, 300, h - 1]
    assert top == [100, 0, 300, 50]
    middle = G.vision_to_px((0.5, 0.4, 0.25, 0.2), (w, h))
    assert middle == [500, 200, 750, 300]


def test_mlx_0_999_bounds():
    """04a-03: grid 0 maps to pixel 0; grid 999 maps to at most width-1 / height-1 (never outside)."""
    for w, h in [(960, 616), (1, 1), (999, 999), (1000, 1000), (4321, 77)]:
        assert G.mlx_to_px([0, 0, 0, 0], (w, h)) == [0, 0, 0, 0]
        x0, y0, x1, y1 = G.mlx_to_px([999, 999, 999, 999], (w, h))
        assert x0 == x1 and y0 == y1
        assert x1 <= w - 1 and y1 <= h - 1
        assert x1 >= w - 2 and y1 >= h - 2  # the far edge, not a collapsed box
    # a recorded MLX title box on the 960x616 fx page sits where the drawn title is (top-left area)
    assert G.mlx_to_px([37, 38, 335, 94], (960, 616)) == [36, 23, 322, 58]


def test_tsv_is_left_top_width_height():
    """04a-03b (added): tesseract tsv columns become corners; an over-wide word is clamped to the image."""
    assert G.tsv_to_px((42, 25, 50, 29), (960, 616)) == [42, 25, 92, 54]
    assert G.tsv_to_px((900, 600, 200, 100), (960, 616)) == [900, 600, 959, 615]


@pytest.mark.parametrize(
    "system,box,size",
    [
        ("vision", (0.1, 0.1, 0.1), (100, 100)),
        ("mlx", [1, 2, 3], (100, 100)),
        ("tsv", (1, 2, 3, 4, 5), (100, 100)),
        ("mlx", [1, 2, 3, 4], (0, 100)),
        ("tsv", (1, 2, 3, 4), (100, -1)),
        ("pdf", [1, 2, 3, 4], (100, 100)),
    ],
)
def test_geometry_guards(system, box, size):
    """04a-03c (S3, added): wrong arity, empty image or an unknown system is a ValueError, not a guess."""
    with pytest.raises(ValueError):
        G.to_px(system, box, size)


def test_union_and_in_bounds():
    """04a-03d (added): a line box is the union of its parts; in_bounds is inclusive of the last pixel."""
    assert G.union([[10, 20, 30, 40], [5, 25, 12, 60]]) == [5, 20, 30, 60]
    assert G.in_bounds([0, 0, 9, 4], (10, 5))
    assert not G.in_bounds([0, 0, 10, 4], (10, 5))
    assert not G.in_bounds([3, 0, 2, 4], (10, 5))
    with pytest.raises(ValueError):
        G.union([])


def _png(path, w, h):
    raw = b"".join(b"\x00" + b"\xff" * (w * 3) for _ in range(h))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def test_image_size_reads_png_header(tmp_path):
    """04a-03e (added): the pixel size comes from the image itself (no Pillow needed for PNG)."""
    p = tmp_path / "a.png"
    _png(p, 37, 11)
    assert G.image_size(p) == (37, 11)
    bad = tmp_path / "b.png"
    bad.write_bytes(b"not an image")
    with pytest.raises(ValueError):
        G.image_size(bad)
