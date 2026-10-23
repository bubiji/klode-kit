"""WI-05b: the send receipt carries no path and no pixels (matrix 05b-11, S4; design 02 r2.2-B "日志").

Receipt (``SendResult.receipt``, also written to ``receipt_dir``): ``der_sha256``, size, route, model, effort,
timing, exit code, argv/config shas, review check names - and nothing derived from where the image lives.

Property, over generated directory and file names (CJK, spaces, ASCII; no hex digits, so a name cannot hide inside
a sha by accident): the same image sent from two different generated paths gives the same receipt (time fields
removed), and no generated name component appears in the receipt text. The same holds for refusals.

Boundary: the cloud CLI is ``_support.send_fake.FakeCloud``; paired real path: model lane 06-11/12.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from PIL import Image

from _support.send_fake import FakeCloud, isolation_receipt, masked_project, routes_state, sha256, source_env
from klode_kit.ocr import send as S

pytestmark = pytest.mark.unit

VOLATILE = ("started", "ended", "duration_s")
CJK = "甲乙丙丁患者测试"
NAME = st.text(alphabet=CJK + " ghijkmnopqrstuvwxyzGHJKLMNPQRSTUVWXYZ_", min_size=3, max_size=12).filter(
    lambda s: s.strip() == s and any(c in CJK for c in s))  # a CJK char: no receipt word can contain it
PARTS = st.lists(NAME, min_size=1, max_size=3, unique=True)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("receipt-world")
    systmp = root / "systmp"
    systmp.mkdir()
    saved = tempfile.tempdir
    tempfile.tempdir = str(systmp)
    try:
        home = root / "home"
        home.mkdir()
        fake = FakeCloud(root / "fake")
        project, row = masked_project(root / "project")
        iso = isolation_receipt("claude", fake, home, root / "iso", root / "iso.json")
        yield {"root": root, "systmp": systmp, "home": home, "fake": fake, "project": project, "row": row,
               "iso": iso, "state": routes_state(root / "routes.json")}
    finally:
        tempfile.tempdir = saved


def _send(world, path: Path, sha: str, receipts: Path):
    return S.send(path, sha, route="claude", project=world["project"], receipt_dir=receipts,
                  isolation_receipt=world["iso"], routes_state=world["state"],
                  source_env=source_env(world["fake"], world["home"], world["systmp"]))


def _stable(receipt: dict) -> dict:
    return {k: v for k, v in receipt.items() if k not in VOLATILE}


def _place(base: Path, parts: list[str], data: bytes) -> Path:
    target = base.joinpath(*parts[:-1], parts[-1] + ".png")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


@settings(max_examples=15, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(a=PARTS, b=PARTS)
def test_receipt_no_path_leak(world, monkeypatch, a, b):
    monkeypatch.setattr(tempfile, "tempdir", str(world["systmp"]))
    case = Path(tempfile.mkdtemp(dir=world["root"], prefix="case-"))
    data = (world["project"] / "_masked" / world["row"]["path"]).read_bytes()
    sha = world["row"]["der_sha256"]
    pa = _place(world["project"] / "copies" / case.name / "A", a, data)
    pb = _place(world["project"] / "copies" / case.name / "B", b, data)

    ra = _send(world, pa, sha, case / "ra").receipt
    rb = _send(world, pb, sha, case / "rb").receipt

    assert _stable(ra) == _stable(rb)
    texts = [json.dumps(ra, ensure_ascii=False)] + [p.read_text(encoding="utf-8") for p in (case / "ra").glob("*")]
    for part in [*a, *b, a[-1] + ".png", b[-1] + ".png", case.name]:
        for text in texts:
            assert part not in text, part

    stray = _place(world["project"] / "copies" / case.name / "C", a, data + b"\0")
    with pytest.raises(S.SendRefused):
        _send(world, stray, sha256(stray.read_bytes()), case / "rc")
    [refusal] = [p.read_text(encoding="utf-8") for p in (case / "rc").glob("*.json")]
    for part in [*a, a[-1] + ".png", case.name]:
        assert part not in refusal, part


def test_receipt_fields(world, tmp_path):
    """The receipt's keys are exactly the documented set; size comes from the PNG header, no pixel data."""
    row = world["row"]
    res = _send(world, world["project"] / "_masked" / row["path"], row["der_sha256"], tmp_path / "r")
    rec = res.receipt
    assert set(rec) == {"schema", "route", "status", "exit_code", "reason", "der_sha256", "expect_sha256", "bytes",
                        "width", "height", "model", "effort", "cli_version", "argv_sha",
                        "isolation_receipt_sha", "review", "timed_out", "guard_log", *VOLATILE}
    assert rec["schema"] == "klode-kit.send_receipt/1"
    with Image.open(world["project"] / "_masked" / row["path"]) as im:
        assert (rec["width"], rec["height"]) == im.size
    assert rec["bytes"] == len((world["project"] / "_masked" / row["path"]).read_bytes())
    assert rec["model"] == "opus" and rec["effort"] == "max" and rec["reason"] is None
    assert rec["isolation_receipt_sha"] == sha256(world["iso"].read_bytes())
    assert all(isinstance(v, (str, int, float, bool, list, dict, type(None))) for v in rec.values())
    assert max(len(json.dumps(v)) for v in rec.values()) < 4096
