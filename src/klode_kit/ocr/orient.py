"""Page orientation: right-angle rotation to upright (WI-04b; design 02 r2.2-A ``transform.rotate``, 04 U2).

``rotate`` is the counter-clockwise turn, in degrees, that makes the stored page upright (``0/90/180/270``,
the ``derived`` contract). A page photographed turned 90 degrees clockwise has ``rotate: 90``.

Detection works on the ink mask only (pixels darker than mid-grey), no OCR engine and no model:

1. **Axis.** Projected onto the vertical axis (one value per pixel row), horizontal text gives broad bands -
   one per text line. Projected onto the horizontal axis it oscillates far more, because every glyph
   boundary is a gap. The orientation (0 or 90) whose column profile has the larger normalised total
   variation relative to its row profile is the one whose lines run horizontally.
2. **Up or down.** Within that axis, text lines (row bands) of a document start at a common left margin and
   end raggedly. The candidate (r or r + 180) where more line starts share one position (within 3 px) than line
   ends is upright.

Scope and limits: documents with left-aligned horizontal lines (CJK / Latin forms, lab reports, prescriptions).
The synthetic fixture set is covered at all four rotations (``tests/unit/test_orient.py``). Skewed photos (not a
right angle), pages that are mostly pictures, and right-aligned scripts are out of scope; a page with no ink is
left as it is (``0``).

``upright(data)`` keeps the original bytes when no rotation is needed (``der_sha256 == img_sha256``); otherwise
it re-encodes the turned page as PNG (``optimize=True``), deterministic for a given Pillow version.
"""

from __future__ import annotations

import io

ROTATIONS = (0, 90, 180, 270)
INK_THRESHOLD = 128
EDGE_TOLERANCE_PX = 3


def _transpose(rotate: int):
    from PIL import Image

    return {90: Image.Transpose.ROTATE_90, 180: Image.Transpose.ROTATE_180, 270: Image.Transpose.ROTATE_270}[rotate]


def _check(rotate: int) -> int:
    if isinstance(rotate, bool) or rotate not in ROTATIONS:
        raise ValueError(f"rotate must be one of {ROTATIONS}, got {rotate!r}")
    return rotate


def apply(image, rotate: int):
    """Turn ``image`` counter-clockwise by ``rotate`` degrees (lossless transpose)."""
    if _check(rotate) == 0:
        return image
    return image.transpose(_transpose(rotate))


def _ink(image):
    return image.convert("L").point(lambda v: 255 if v < INK_THRESHOLD else 0)


def _profile(mask) -> list[float]:
    """Mean ink per pixel row."""
    from PIL import Image

    _, h = mask.size
    column = mask.resize((1, h), Image.Resampling.BOX)
    flat = getattr(column, "get_flattened_data", None) or column.getdata  # Pillow >= 13 / older
    return list(flat())


def _variation(profile: list[float]) -> float:
    total = sum(profile)
    if total <= 0 or len(profile) < 2:
        return 0.0
    return sum(abs(a - b) for a, b in zip(profile, profile[1:])) / total


def _axis_score(mask) -> float:
    """> 0 when the mask's text lines run horizontally."""
    box = mask.getbbox()
    if box is None:
        return 0.0
    crop = mask.crop(box)
    return _variation(_profile(apply(crop, 90))) - _variation(_profile(crop))


def _bands(mask) -> list[tuple[int, int]]:
    out, start = [], None
    for y, v in enumerate([*_profile(mask), 0]):
        if v > 0 and start is None:
            start = y
        elif v <= 0 and start is not None:
            out.append((start, y))
            start = None
    return out


def _modal_share(values: list[int]) -> float:
    return max(sum(1 for x in values if abs(x - c) <= EDGE_TOLERANCE_PX) for c in values) / len(values)


def _left_aligned(mask) -> float:
    """Share of lines starting at the common left margin minus share ending at a common right edge."""
    w, _ = mask.size
    starts, ends = [], []
    for y0, y1 in _bands(mask):
        box = mask.crop((0, y0, w, y1)).getbbox()
        if box is not None:
            starts.append(box[0])
            ends.append(box[2])
    if not starts:
        return 0.0
    return _modal_share(starts) - _modal_share(ends)


def detect(image) -> int:
    """Counter-clockwise rotation (0/90/180/270) that makes ``image`` upright; 0 for a page without ink."""
    mask = _ink(image)
    if mask.getbbox() is None:
        return 0
    base = 0 if _axis_score(mask) >= _axis_score(apply(mask, 90)) else 90
    flipped = base + 180
    return base if _left_aligned(apply(mask, base)) >= _left_aligned(apply(mask, flipped)) else flipped


def upright(data: bytes) -> tuple[int, bytes]:
    """``(rotate, derived bytes)`` for an encoded image; ``rotate == 0`` returns ``data`` itself."""
    from PIL import Image

    with Image.open(io.BytesIO(data)) as image:
        image.load()
        rotate = detect(image)
        if rotate == 0:
            return 0, data
        buf = io.BytesIO()
        apply(image, rotate).save(buf, format="PNG", optimize=True)
    return rotate, buf.getvalue()
