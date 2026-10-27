"""U1 (04 section 6): a family member drops three lab screenshots into ``_inbox/2026-10-22/`` and says "today's
screenshots in" -> ``klode-kit med-intake --inbox _inbox/2026-10-22/``.

What the user sees (04): the terminal prints ``ocr: 3 页 × 4 路 OK`` and ``audit: 5 条分歧 → confirm/20261022.html``,
the state is ``awaiting_confirm``; the batch receipt shows a time per route and 0 failed pages.

Matrix U1-01, U1-02, U1-03, UC-01 (lane contract_replay). Replay boundary: the four OCR routes are the recorded rows of
the synthetic fixtures (``KLODE_KIT_ENGINE_REPLAY``); ``doctor`` is the pipeline rig's launcher stub. Everything the
user touches - the ``med-intake`` command, kb-ocr, kb-audit, the confirm page and its links - runs for real. The real
four-route run of the same command is ``test_u1_med_intake_real.py`` (model_integration, G2).

Observable-path note (reported to the user, not changed in 04): 04 names ``work/20261022/ocr/receipt.json`` for the
per-route times. The batch receipt that carries them is ``work/20261022/receipt.json`` (``route_stats``, WI-13c); the
``ocr/`` directory under the batch holds the per-route attempts (each with its own ``receipt.json``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from _support.intake_fx import AUDIT_LINE, OCR_LINE, IntakeRig

pytestmark = pytest.mark.contract_replay

ROUTES = ["vision", "mlx", "claude", "codex"]


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _truth_disputes(fixture: str) -> int:
    """Fields the fixture's truth designs as not-all-agree (majority, all_differ, ...): what the audit must list."""
    truth = json.loads((FIXTURES / fixture / "truth.json").read_text(encoding="utf-8"))
    return sum(1 for f in truth["fields"] if f["designed"] != "agree")


def _lines(res) -> list[str]:
    return res.stdout.decode().splitlines()


def test_u1_med_intake(tmp_path):
    """U1-01 (L2 S1): the two lines, exit 10, ``awaiting_confirm``, the aliases of 04, and the per-route times."""
    rig = IntakeRig(tmp_path)
    res = rig.run()
    assert res.returncode == 10, res.stderr.decode()
    lines = _lines(res)
    assert OCR_LINE == "ocr: 3 页 × 4 路 OK" and AUDIT_LINE == "audit: 5 条分歧 → confirm/20261022.html"
    assert lines == [OCR_LINE, AUDIT_LINE, "status: awaiting_confirm"]

    # the paths 04 names: work/20261022/ and confirm/20261022.html, both reachable
    work = rig.project / "work" / "20261022"
    page = rig.project / "confirm" / "20261022.html"
    assert work.is_dir() and page.is_file()
    assert work.resolve() == rig.batch_dir.resolve()
    assert page.read_bytes().startswith(b"<!DOCTYPE html>") or b"<html" in page.read_bytes()[:400]

    # a time per route and no failed page: the batch receipt of the batch (see the module note on the path)
    receipt = json.loads((work / "receipt.json").read_text(encoding="utf-8"))
    stats = receipt["route_stats"]
    assert sorted(stats) == sorted(ROUTES)
    for route in ROUTES:
        assert isinstance(stats[route]["elapsed_ms"], int) and stats[route]["elapsed_ms"] >= 0, (route, stats[route])
        assert stats[route]["failed_pages"] == 0, (route, stats[route])
    assert receipt["routes"] == ROUTES and len(receipt["pages"]) == 3
    assert all(p["routes"][r]["status"] == "done" for p in receipt["pages"] for r in ROUTES)
    assert receipt["issues"] == []
    # the per-route attempts are where 04 looks for them: work/20261022/ocr/<route>/<fp>.t1/
    for route in ROUTES:
        attempts = sorted((work / "ocr" / route).iterdir())
        assert len(attempts) == 1 and (attempts[0] / "receipt.json").is_file()
        assert sorted(f.name for f in attempts[0].glob("p00*.jsonl")) == ["p001.jsonl", "p002.jsonl", "p003.jsonl"]

    # it stopped at the human: the confirm page is the only thing waiting, nothing is written back
    assert not (rig.project / "records").exists()
    # the page opened through its folder shows the crops (WI-13c): the link files exist and resolve
    assert (rig.project / "confirm" / "20261022" / "confirm.html").resolve() == (
        rig.batch_dir / "audit" / "a1" / "confirm.html").resolve()
    assert (rig.project / "confirm" / "20261022" / "crops").is_dir()


def test_u1_rerun_idempotent(tmp_path, fake_log):
    """U1-02 (L2 S5): running it again prints the same, names the same batch, and calls no OCR route again."""
    rig = IntakeRig(tmp_path)
    first = rig.run()
    assert first.returncode == 10, first.stderr.decode()
    calls_after_first = fake_log.read_text(encoding="utf-8").splitlines() if fake_log.exists() else []
    assert calls_after_first, "the replay boundary was exercised by the first run (route calls were logged)"
    tree = {k: v for k, v in rig.tree().items() if not k.endswith(".lock")}
    again = rig.run()
    assert again.returncode == 10, again.stderr.decode()
    assert again.stdout == first.stdout
    assert fake_log.read_text(encoding="utf-8").splitlines() == calls_after_first  # no new route call
    batches = sorted(p.name for p in (rig.project / "work").iterdir() if p.is_dir() and not p.is_symlink())
    assert batches == [rig.batch_id]  # the same batch, not a second one
    assert {k: v for k, v in rig.tree().items() if not k.endswith(".lock")} == tree


@pytest.mark.parametrize("fixture, expected_ocr", [("fx-u1-3labs", "ocr: 3 页 × 4 路 OK"),
                                                    ("fx-u3-mixed", "ocr: 2 页 × 4 路 OK")])
def test_u1_dispute_count_tracks_fixture(tmp_path, fixture, expected_ocr):
    """U1-03 (L2 S2): the numbers in the two lines are measured, not the example's: the dispute count equals the number
    of fields the fixture's truth designs as not-all-agree, and the page count is the inbox's."""
    rig = IntakeRig(tmp_path, fixture)
    res = rig.run()
    want = _truth_disputes(fixture)
    assert want > 0
    assert res.returncode == 10, res.stderr.decode()
    lines = _lines(res)
    assert lines[0] == expected_ocr
    assert lines[1] == f"audit: {want} 条分歧 → confirm/20261022.html"
    assert rig.intake_receipt()["disputes"] == want


def test_u1_two_inboxes_concurrent(tmp_path):
    """UC-01 (L2 S3): two ``med-intake`` runs at once on one data project: one answers exit 40 with the reason and
    takes nothing; the other runs to the end and its products equal those of the same run alone."""
    solo = IntakeRig(tmp_path / "solo")
    assert solo.run().returncode == 10
    one = IntakeRig(tmp_path / "one")
    other = IntakeRig(tmp_path / "other", "fx-u3-mixed")
    other.project = one.project
    point = "med_intake.lock"
    first = one.popen(env=one.pause_env(point))
    try:
        one.wait_reached(point, first)
        loser = other.run()
        assert loser.returncode == 40, loser.stderr.decode()
        assert b"another med-intake" in loser.stderr
        assert loser.stdout == b"" and not other.batch_dir.exists()
        one.release(point)
        out, err = first.communicate(timeout=180)
        assert first.returncode == 10, err.decode()
    finally:
        if first.poll() is None:
            first.kill()
            first.communicate()
    # the winner's observable products are those of the solo run
    assert out == solo.run().stdout
    assert one.intake_receipt() == solo.intake_receipt()
    assert (one.batch_dir / "audit" / "a1" / "confirm.html").read_bytes() == (
        solo.batch_dir / "audit" / "a1" / "confirm.html").read_bytes()
    assert (one.batch_dir / "audit" / "a1" / "candidates.jsonl").read_bytes() == (
        solo.batch_dir / "audit" / "a1" / "candidates.jsonl").read_bytes()
    assert str((one.work / "20261022").readlink()) == one.batch_id
