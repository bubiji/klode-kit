"""WI-04b: page orientation (matrix 04b-02, U2 ``rotate: 90``).

``orient.detect(image)`` returns the counter-clockwise rotation (0/90/180/270) that makes the page upright;
``orient.apply(image, r)`` performs it; ``orient.upright(png bytes)`` returns ``(r, derived bytes)`` where a page
that is already upright keeps its original bytes (``der_sha256 == img_sha256``).

Law (S4/S5): for every synthetic document page ``u`` and every stored rotation ``r`` (``u`` turned clockwise by
``r``), ``detect`` recovers ``r`` and ``apply(stored, detect(stored))`` is pixel-identical to ``u``.
"""

from __future__ import annotations

import hashlib
import io

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from PIL import Image

from _support.kb_ocr import FIXTURES
from klode_kit.ocr import orient

pytestmark = pytest.mark.unit

LABS_1X = FIXTURES / "fx-labs-1x" / "images" / "p001.png"
# every upright synthetic document page of the fixture set (fx-rotated90 is stored rotated: its upright page is
# the derived image)
UPRIGHT = sorted(p for p in FIXTURES.glob("fx-*/images/*.png") if "fx-rotated90" not in p.parts)
UPRIGHT.append(FIXTURES / "fx-rotated90" / "derived" / "p001.upright.png")


def clockwise(image: Image.Image, r: int) -> Image.Image:
    return image.rotate(-r, expand=True)


def same_pixels(a: Image.Image, b: Image.Image) -> bool:
    return a.size == b.size and a.convert("L").tobytes() == b.convert("L").tobytes()


@pytest.mark.parametrize("r", [0, 90, 180, 270])
def test_orient_all_four(r):
    """04b-02 (S2): fx-labs-1x stored at each of the four rotations is detected correctly and turned back."""
    upright = Image.open(LABS_1X)
    stored = clockwise(upright, r)
    assert orient.detect(stored) == r
    assert same_pixels(orient.apply(stored, orient.detect(stored)), upright)


def test_fixture_set_is_not_vacuous():
    assert len(UPRIGHT) >= 20


@settings(max_examples=60, deadline=None)
@given(page=st.sampled_from(UPRIGHT), r=st.sampled_from([0, 90, 180, 270]))
def test_detect_inverts_any_stored_rotation(page, r):
    """Law (S4): over every synthetic page x every rotation, apply(stored, detect(stored)) == upright page."""
    upright = Image.open(page)
    stored = clockwise(upright, r)
    got = orient.detect(stored)
    assert got == r, (page.parent.parent.name, r, got)
    assert same_pixels(orient.apply(stored, got), upright)


def test_upright_rotated90_matches_registered_derived():
    """U2: fx-rotated90 needs rotate 90 and its derived bytes are exactly the registered upright page (der_sha256
    of the fixture's engine outputs)."""
    stored = (FIXTURES / "fx-rotated90" / "images" / "p001.png").read_bytes()
    expected = (FIXTURES / "fx-rotated90" / "derived" / "p001.upright.png").read_bytes()
    r, data = orient.upright(stored)
    assert r == 90
    assert hashlib.sha256(data).hexdigest() == hashlib.sha256(expected).hexdigest()


def test_upright_page_keeps_original_bytes():
    data = LABS_1X.read_bytes()
    assert orient.upright(data) == (0, data)


def test_blank_page_is_left_alone():
    """Boundary: no ink at all -> nothing to orient -> 0, original bytes."""
    buf = io.BytesIO()
    Image.new("L", (64, 32), 255).save(buf, format="PNG")
    assert orient.detect(Image.open(io.BytesIO(buf.getvalue()))) == 0
    assert orient.upright(buf.getvalue()) == (0, buf.getvalue())


@pytest.mark.parametrize("bad", [45, -90, 360, 1])
def test_apply_rejects_non_right_angles(bad):
    with pytest.raises(ValueError):
        orient.apply(Image.open(LABS_1X), bad)
