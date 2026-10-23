"""WI-05b: derived registration edge (added after the first green run): a derived.jsonl whose last line lacks
its newline gets one before the masked lines are appended, so every line stays a JSON object."""

from __future__ import annotations

import json

import pytest

from _support.mask_replay import FIXTURES, ReplayVision, make_batch
from klode_kit.audit import mask

pytestmark = pytest.mark.unit

RX = FIXTURES / "fx-rx-stamp" / "images" / "p001.png"


def test_registration_after_unterminated_last_line(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    batch = make_batch(project, [RX])
    path = project / "work" / batch / "derived.jsonl"
    path.write_bytes(path.read_bytes().rstrip(b"\n"))
    upright = json.loads(path.read_text())

    mask.mask_batch(project, batch, engine=ReplayVision({upright["der_sha256"]: []}))

    lines = [json.loads(x) for x in path.read_text().splitlines()]
    assert lines[0] == upright
    assert lines[1]["path"].startswith("_masked/") and len(lines) == 2
