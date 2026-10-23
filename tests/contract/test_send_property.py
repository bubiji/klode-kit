"""WI-05b: the K6 law over generated file sets (matrix 05b-07, S4).

Law: a send starts a model call  <=>  the bytes at the path now hash to ``expect_sha`` AND that sha is registered.
Every refusal is exit 60 with zero processes started for it, and every call that happens carries registered
bytes. ``expect_sha`` is the caller's belief (the sha it registered / saw); the extra conjunct keeps a swapped-in
*other* registered image from being attributed to the wrong der_sha (provenance), and changes nothing on the
privacy side.

Generated: k images (distinct solid colours) -> an arbitrary subset is masked the WI-05a way (``mask.mask_page``,
real MANIFEST rows); the sent set is every masked file plus every unmasked original; an arbitrary subset of the
sent files is tampered (one byte flipped, or overwritten with another sent file's bytes).
"""

from __future__ import annotations

import io
import tempfile

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from PIL import Image

from _support.mask_replay import ReplayVision
from _support.send_fake import FakeCloud, isolation_receipt, routes_state, sha256, source_env
from klode_kit.audit import mask
from klode_kit.ocr import send as S

pytestmark = pytest.mark.contract_replay


def _png(colour: int, size: tuple[int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("L", size, colour).save(buf, format="PNG")
    return buf.getvalue()


@st.composite
def scenarios(draw):
    k = draw(st.integers(1, 3))
    colours = draw(st.lists(st.integers(0, 255), min_size=k, max_size=k, unique=True))
    masked = draw(st.lists(st.booleans(), min_size=k, max_size=k))
    n_sent = k + sum(masked)
    tamper = draw(st.lists(st.sampled_from(["none", "flip", "swap"]), min_size=n_sent, max_size=n_sent))
    swap_with = draw(st.lists(st.integers(0, n_sent - 1), min_size=n_sent, max_size=n_sent))
    return colours, masked, tamper, swap_with


@settings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(sc=scenarios())
def test_send_only_manifest_property(tmp_path_factory, monkeypatch, sc):
    colours, masked, tamper, swap_with = sc
    root = tmp_path_factory.mktemp("prop")
    systmp = root / "systmp"
    systmp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(systmp))
    home = root / "home"
    home.mkdir()
    fake = FakeCloud(root / "fake")
    iso = isolation_receipt("claude", fake, home, root / "iso", root / "iso.json")
    state = routes_state(root / "routes.json")
    project = root / "project"
    project.mkdir()

    sent: list[tuple] = []  # (path, expect_sha)
    for i, (colour, do_mask) in enumerate(zip(colours, masked)):
        data = _png(colour, (40 + i, 30))
        original = project / "_inbox" / f"img{i}.png"
        original.parent.mkdir(exist_ok=True)
        original.write_bytes(data)
        sent.append((original, sha256(data)))
        if do_mask:
            row = mask.mask_page(project, original, parent=sha256(data), page_id=f"d-{i:012x}/p001",
                                 der_sha256=sha256(data), engine=ReplayVision({sha256(data): []}))
            sent.append((project / "_masked" / row["path"], row["der_sha256"]))
    registered = {r["der_sha256"] for r in mask.read_manifest(project)}
    originals = {p: p.read_bytes() for p, _ in sent}
    for (path, _), how, other in zip(sent, tamper, swap_with):
        if how == "flip":
            data = bytearray(originals[path])
            data[-1] ^= 0xFF
            path.write_bytes(bytes(data))
        elif how == "swap":
            path.write_bytes(originals[sent[other][0]])

    for path, expect in sent:
        before = len(fake.calls())
        current = sha256(path.read_bytes())
        should_call = current == expect and current in registered
        try:
            S.send(path, expect, route="claude", project=project, receipt_dir=root / "receipts",
                   isolation_receipt=iso, routes_state=state, source_env=source_env(fake, home, systmp))
            called = True
        except S.SendRefused as exc:
            assert exc.exit_code == 60
            assert len(fake.calls()) == before  # nothing started for a refusal
            called = False
        assert called == should_call, (path.name, current in registered, current == expect)

    for call in fake.model_calls():
        assert set(call["pngs"].values()) <= registered
    assert list(systmp.iterdir()) == []
