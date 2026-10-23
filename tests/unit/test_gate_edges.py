"""WI-06a edges of the shared G2 gate (``klode_kit.gate``), added after the first green.

What counts as a generated test fake must stay narrow: a small, readable ``#!`` script carrying the marker
line. A big file (a real CLI is a large binary) or an unreadable one is never trusted on the marker alone, and
without a valid ack it is gated.
"""

from __future__ import annotations

import os

import pytest

from klode_kit import gate as G
from klode_kit.ocr.cloud_budget import Ledger

pytestmark = pytest.mark.unit

CLOSED = {"KLODE_KIT_REAL": "0"}
OPEN = {"KLODE_KIT_REAL": "1", "KLODE_KIT_GATE_ACK": "G2-20261023"}


def _exe(tmp_path, body: bytes, name: str = "claude", mode: int = 0o755):
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)
    exe = d / name
    exe.write_bytes(body)
    exe.chmod(mode)
    return d, exe


MARKED = f"#!/bin/sh\n# {G.TEST_FAKE_MARKER}\nexit 0\n".encode()


def test_marked_script_is_a_generated_fake(tmp_path):
    d, exe = _exe(tmp_path, MARKED)
    assert G.vet("claude", path=str(d), env=CLOSED, allow_generated_fakes=True) == exe.resolve()


@pytest.mark.parametrize("body", [
    pytest.param(MARKED + b"#" * (64 * 1024), id="larger-than-64KiB"),
    pytest.param(MARKED.replace(b"#!/bin/sh\n", b""), id="no-shebang"),
    pytest.param(MARKED.replace(G.TEST_FAKE_MARKER.encode(), b"something else"), id="no-marker"),
])
def test_not_a_generated_fake_is_gated(tmp_path, body):
    d, _ = _exe(tmp_path, body)
    with pytest.raises(G.GateClosed):
        G.vet("claude", path=str(d), env=CLOSED, allow_generated_fakes=True)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any file")
def test_unreadable_marked_script_is_gated(tmp_path):
    d, _ = _exe(tmp_path, MARKED, mode=0o111)
    with pytest.raises(G.GateClosed):
        G.vet("claude", path=str(d), env=CLOSED, allow_generated_fakes=True)


def test_generated_fake_needs_the_callers_consent(tmp_path):
    """The bridge passes allow_generated_fakes=False: the marker alone does not open its gate."""
    d, _ = _exe(tmp_path, MARKED)
    with pytest.raises(G.GateClosed):
        G.vet("claude", path=str(d), env=CLOSED)


def test_open_gate_returns_resolution_or_none(tmp_path):
    d, exe = _exe(tmp_path, b"#!/bin/sh\nexit 0\n")
    assert G.vet("claude", path=str(d), env=OPEN) == exe.resolve()
    assert G.vet("claude", path=str(tmp_path / "empty"), env=OPEN) is None


@pytest.mark.parametrize("calls", [-1, True, 1.5])
def test_ledger_calls_must_be_a_count(calls):
    with pytest.raises(ValueError):
        Ledger(1.0, calls=calls)
