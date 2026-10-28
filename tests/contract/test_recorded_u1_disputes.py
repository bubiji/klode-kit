"""WI-32b: the confirm page lists only real disputes (U1 on the real four-route recordings, replayed).

The seven WI-00c3 recordings are what the real engines returned for the synthetic fixtures; the three pages of
``fx-u1-3labs`` replayed through ``med-intake`` reproduce the real U1 run (WI-31): no tesseract route, Vision boxes
all approx, so every cell has ``no_geometry`` / ``no_bbox_crop``, and 65 cells reached the confirm page although
only 9 are disagreements (a route lost or misread a value). With WI-32b the page lists those 9.

The oracle is the audit's own raw data (``cells.jsonl``: what each route read): a field is a real dispute exactly
when a route has no value there or two routes read different values. It is independent of the vote code.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from _support import recorded_replay as RR
from _support.intake_fx import OCR_LINE, IntakeRig

pytestmark = pytest.mark.contract_replay

ROUTES = RR.ROUTES4
REAL_DISPUTES = 9      # 3 pages x 3: the WI-00c3 misreads (vision unit slip, mlx lost value, ...), counted on the data


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _audit(rig: IntakeRig) -> tuple[list[dict], list[dict]]:
    a1 = rig.batch_dir / "audit" / "a1"
    return _jsonl(a1 / "cells.jsonl"), _jsonl(a1 / "candidates.jsonl")


def _is_dispute(cell: dict) -> bool:
    values = {e: c["value"] for e, c in cell["candidates"].items() if e in ROUTES}
    return set(values) != set(ROUTES) or len(set(values.values())) > 1


@pytest.fixture(scope="module")
def recorded_run(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("rec")
    engine = RR.build(tmp / "engine", "fx-u1-3labs")
    rig = IntakeRig(tmp / "rig", engine=engine)
    res = rig.run()
    return rig, res


def test_confirm_page_lists_only_real_disputes(recorded_run):
    """32b-01 (L2-S1): the command says 9 disputes (was 65), exit 10, awaiting_confirm; the number is the receipt's."""
    rig, res = recorded_run
    assert res.returncode == 10, res.stderr.decode()
    assert res.stdout.decode().splitlines() == [
        OCR_LINE, f"audit: {REAL_DISPUTES} 条分歧 → confirm/20261022.html", "status: awaiting_confirm"]
    assert rig.intake_receipt()["disputes"] == REAL_DISPUTES


def test_shown_fields_are_exactly_the_real_disputes(recorded_run):
    """32b-02 (L2-S3, K1): per field, shown on the page <=> a route is absent or two routes differ (cells.jsonl);
    nothing real is dropped and nothing equal is listed. No field is forgiven when a route lost it."""
    rig, _ = recorded_run
    cells, cands = _audit(rig)
    assert len(cells) == 65                         # the 65 cells the real run listed, one field each
    truth = {c["field_id"]: _is_dispute(c) for c in cells}
    shown = {}
    for cand in cands:
        for f in cand["fields"]:
            shown[f["field_id"]] = (f["verdict"] != "agree" or bool(f["uncertain"]) or bool(f["missing"]))
    assert set(shown) == set(truth)
    assert shown == truth
    assert sum(truth.values()) == REAL_DISPUTES
    assert len(cells) - REAL_DISPUTES == 56         # the 56 formerly forced fields are now plain agreements


def test_forgiven_fields_keep_their_trace(recorded_run):
    """32b-03 (L2-S3): an auto-agreed field has an empty ``uncertain``, and its ``no_geometry`` doubt is kept on the
    field as ``notes`` (the audit data still says the box was missing); the uncertain list of the page holds
    entries for disputes only and the candidate stays ``pending_confirm`` because disputes remain."""
    rig, _ = recorded_run
    cells, cands = _audit(rig)
    dispute = {c["field_id"] for c in cells if _is_dispute(c)}
    for cand in cands:
        assert cand["status"] == "pending_confirm" and cand["verified_by"] is None
        for f in cand["fields"]:
            if f["field_id"] in dispute:
                assert f["uncertain"] != [] or len(f["votes"]) > 1 or f["missing"]
                continue
            assert f["uncertain"] == [] and f["missing"] == [] and f["verdict"] == "agree"
            assert "no_geometry" in f["notes"] and f["crop"] is None
        for u in cand["uncertain"]:
            assert u["field_id"] in dispute
    assert not (rig.project / "records").exists()


def test_a_page_where_all_routes_agree_is_verified_auto(tmp_path):
    """32b-04 (L2-S1): the same pages with all four routes reading the same lines (Vision's rows under each route
    name): no dispute at all although no cell has exact geometry - the candidates are ``verified`` by ``auto``,
    their ``uncertain`` list is empty (a non-empty list is never verified), the command says so."""
    engine = tmp_path / "engine"
    src = RR.build(tmp_path / "src", "fx-u1-3labs", routes=("vision",))
    for route in ROUTES:
        shutil.copytree(src / "vision", engine / route)
        for p in (engine / route).glob("*.jsonl"):
            cloud = route in ("claude", "codex")      # cloud rows carry no boxes (ocr_line contract)
            rows = [{**r, "engine": route, **({"bbox_px": None, "tokens": None} if cloud else {})}
                    for r in _jsonl(p)]
            p.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows),
                         encoding="utf-8")
    rig = IntakeRig(tmp_path / "rig", engine=engine)
    res = rig.run()
    assert res.returncode == 0, res.stderr.decode()
    assert res.stdout.decode().splitlines()[1] == "audit: 0 条分歧，无需确认"
    cells, cands = _audit(rig)
    assert cells and not any(_is_dispute(c) for c in cells)
    for cand in cands:
        assert cand["status"] == "verified" and cand["verified_by"] == "auto" and cand["uncertain"] == []
        assert all("no_geometry" in f["notes"] and f["uncertain"] == [] for f in cand["fields"])
