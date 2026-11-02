"""Page orientation: right-angle rotation to upright (WI-04b; design 02 r2.2-A ``transform.rotate``, 04 U2).

``rotate`` is the counter-clockwise turn, in degrees, that makes the stored page upright (``0/90/180/270``,
the ``derived`` contract). A page photographed turned 90 degrees clockwise has ``rotate: 90``.

Detection (WI-33c, real-data pilot P7) has two sources, reported by ``detect_info`` as ``source``:

1. **``osd``** - tesseract orientation detection (``tesseract <png> stdout --psm 0``, ``osd.traineddata`` ships with
   tesseract, already a hard dependency). Its ``Rotate: N`` is the *clockwise* turn that corrects the page, so
   ``rotate = (-N) % 360``. It is used only when the answer carries ``Orientation confidence >= OSD_MIN_CONFIDENCE``.
   Measured on 33 real upright pages (phone screenshots of lab reports with sparse text, grey bars and table
   rules, plus PDF pages at 150 dpi) OSD said ``Rotate: 0`` for all 33 and restored 39 rotated copies exactly
   (confidence 1.06-13.6), while the ink heuristic below wrongly turned 15 of the 33.
2. **``heuristic``** - the original ink-mask detector, used only when OSD gives no answer (error, "Too few
   characters" on tiny or nearly empty images such as ``fx-lowres``, no ``Rotate`` line) or a confidence below the
   threshold. A page without ink is left as it is (``0``, source ``none``) and OSD is not run.

   The ink heuristic works on the mask only (pixels darker than mid-grey): (a) *axis* - the orientation (0 or 90)
   whose column profile has the larger normalised total variation relative to its row profile has horizontal
   lines; (b) *up or down* - the candidate (r or r + 180) where more line starts share one position (within
   3 px) than line ends is upright. It suits dense left-aligned text and is unreliable on sparse real pages.

**Recognition cross-check for 180 (WI-36, real-data finding P19).** OSD's confidence cannot separate a wrong answer
from a right one: on a skewed phone photo of an upright receipt on a perforated metal background OSD said
``Rotate: 180`` with confidence 2.57, inside the 2.0-3.8 range of genuinely rotated fixtures. Apple Vision was tried
first and is useless here (it reads rotated text almost equally well: 311 vs 287 on that photo). A tesseract
recognition score does separate 0 from 180: ``tess_score`` = sum of ``len(text)`` over TSV words with confidence >=
``TESS_WORD_CONF`` (60), ``-l chi_sim+eng --psm 3``, on a grayscale copy whose long side is at most
``TESS_MAX_SIDE`` = 1600 px. On the real photos upright beat upside-down (167 vs 118, 53 vs 30) and on 27 fixture
cases at 180/270 the true rotation always beat 0. At 90/270 it does not separate (10 failures), so the check runs
ONLY when OSD proposes 180: the turn is kept (``check`` ``osd+tess``) only when ``s180 > s0`` strictly; otherwise
``rotate`` is 0 with ``source`` ``osd-rejected``. 90/270 proposals are unchanged. If tesseract gives no score
(``None``) the plain OSD answer stands. ``detect_info(score=...)`` is the injectable scorer
(``score(image, rotate) -> float | None``). Default on for the real OSD path; when only ``run_osd`` is injected
(unit stubs) the cross-check is off unless ``score`` is also given.

The decision source and confidence are not stored in ``derived.jsonl`` (only ``transform.rotate`` is); they are
available from ``detect_info`` for logging. The synthetic fixture set is covered at all four rotations
(``tests/unit/test_orient.py``, ``tests/cli/test_orient_osd_wi33c.py``). Skewed photos (not a right angle) are out of scope.

``upright(data)`` keeps the original bytes when no rotation is needed (``der_sha256 == img_sha256``); otherwise
it re-encodes the turned page as PNG (``optimize=True``), deterministic for a given Pillow version.
"""

from __future__ import annotations

import io
import os
import re
import subprocess
import tempfile
from collections.abc import Callable

ROTATIONS = (0, 90, 180, 270)
INK_THRESHOLD = 128
EDGE_TOLERANCE_PX = 3
# Minimum tesseract "Orientation confidence" to trust OSD. Real pages measured 1.06-13.6 (all correct), rotated
# synthetic fixtures 2.0-3.8; tesseract's own "reliable" bar is 2, but low-confidence answers on the real set were
# still right, so the bar is set low (0.5) and only rejects near-guesses.
OSD_MIN_CONFIDENCE = 0.5
OSD_TIMEOUT_S = 60
TESS_MAX_SIDE = 1600
TESS_WORD_CONF = 60
TESS_LANGS = "chi_sim+eng"


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


_ROTATE_RE = re.compile(r"^Rotate:\s*(\d+)\s*$", re.M)
_CONF_RE = re.compile(r"^Orientation confidence:\s*([0-9]+(?:\.[0-9]+)?)\s*$", re.M)


def parse_osd(stdout: str) -> tuple[int | None, float | None]:
    """``(rotate_ccw, confidence)`` from ``tesseract --psm 0`` text; ``None`` for a missing or invalid field.

    tesseract's ``Rotate: N`` is the clockwise turn that corrects the page; ``rotate_ccw == (-N) % 360``.
    """
    m, c = _ROTATE_RE.search(stdout), _CONF_RE.search(stdout)
    n = int(m.group(1)) if m else None
    return ((-n) % 360 if n in ROTATIONS else None), (float(c.group(1)) if c else None)


def _tesseract_osd(png: str) -> str:
    res = subprocess.run(["tesseract", png, "stdout", "--psm", "0"], capture_output=True, text=True,
                         timeout=OSD_TIMEOUT_S, check=False)
    return res.stdout


def _osd(image, run_osd: Callable[[str], str]) -> tuple[int | None, float | None]:
    fd, name = tempfile.mkstemp(suffix=".png")
    try:
        with os.fdopen(fd, "wb") as fh:
            image.convert("L").save(fh, format="PNG")
        return parse_osd(run_osd(name))
    except Exception:  # noqa: BLE001 - any OSD failure (missing binary, timeout, odd output) means "no answer"
        return None, None
    finally:
        os.unlink(name)


def _tesseract_tsv(png: str) -> str:
    res = subprocess.run(["tesseract", png, "stdout", "-l", TESS_LANGS, "--psm", "3", "tsv"], capture_output=True,
                         text=True, timeout=OSD_TIMEOUT_S, check=False)
    return res.stdout


def parse_tsv_score(tsv: str) -> float:
    """Sum of ``len(text)`` over TSV word rows (level 5) with confidence >= ``TESS_WORD_CONF``."""
    total = 0
    for line in tsv.splitlines()[1:]:
        cols = line.split("\t")
        if len(cols) < 12 or cols[0] != "5":
            continue
        try:
            conf = float(cols[10])
        except ValueError:
            continue
        if conf >= TESS_WORD_CONF:
            total += len(cols[11].strip())
    return float(total)


def tess_score(image, rotate: int, *, run_tsv: Callable[[str], str] | None = None) -> float | None:
    """Tesseract recognition score of ``image`` turned ``rotate`` degrees ccw (None when tesseract fails)."""
    from PIL import Image

    turned = apply(image, rotate).convert("L")
    long_side = max(turned.size)
    if long_side > TESS_MAX_SIDE:
        k = TESS_MAX_SIDE / long_side
        turned = turned.resize((max(1, int(turned.width * k)), max(1, int(turned.height * k))),
                               Image.Resampling.LANCZOS)
    fd, name = tempfile.mkstemp(suffix=".png")
    try:
        with os.fdopen(fd, "wb") as fh:
            turned.save(fh, format="PNG")
        return parse_tsv_score((run_tsv or _tesseract_tsv)(name))
    except Exception:  # noqa: BLE001 - missing binary, timeout: no usable score
        return None
    finally:
        os.unlink(name)


def _heuristic(mask) -> int:
    base = 0 if _axis_score(mask) >= _axis_score(apply(mask, 90)) else 90
    flipped = base + 180
    return base if _left_aligned(apply(mask, base)) >= _left_aligned(apply(mask, flipped)) else flipped


def detect_info(image, *, run_osd: Callable[[str], str] | None = None,
                score: Callable[[object, int], float | None] | None = None) -> dict:
    """``{"rotate", "source", "confidence"}`` (+ ``"check"`` when the 180 was confirmed): ccw turn to upright.

    ``source`` is ``osd`` (tesseract answered with confidence >= ``OSD_MIN_CONFIDENCE``), ``osd-rejected`` (OSD
    proposed 180 but the recognition score preferred the page as stored; ``rotate`` 0), ``heuristic`` (ink
    heuristic, OSD gave no usable answer) or ``none`` (page without ink, ``rotate`` 0). ``check`` (only then
    present) is ``osd+tess``. ``run_osd(png_path) -> stdout`` is the tesseract OSD process; ``score`` the
    recognition scorer (default ``tess_score``; see the module docstring).
    """
    mask = _ink(image)
    if mask.getbbox() is None:
        return {"rotate": 0, "source": "none", "confidence": None}
    rotate, conf = _osd(image, run_osd or _tesseract_osd)
    if rotate is not None and conf is not None and conf >= OSD_MIN_CONFIDENCE:
        if rotate == 180:
            scorer = score or (tess_score if run_osd is None else None)  # injected OSD stub: no real tesseract
            s0, s180 = (scorer(image, 0), scorer(image, 180)) if scorer else (None, None)
            if s0 is not None and s180 is not None:
                if s180 > s0:
                    return {"rotate": 180, "source": "osd", "confidence": conf, "check": "osd+tess"}
                return {"rotate": 0, "source": "osd-rejected", "confidence": conf}
        return {"rotate": rotate, "source": "osd", "confidence": conf}
    return {"rotate": _heuristic(mask), "source": "heuristic", "confidence": conf}


def detect(image, *, run_osd: Callable[[str], str] | None = None) -> int:
    """Counter-clockwise rotation (0/90/180/270) that makes ``image`` upright (see ``detect_info``)."""
    return detect_info(image, run_osd=run_osd)["rotate"]


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
