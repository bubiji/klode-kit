"""U3 (04 section 6): relatives put the pictures in the synced folder, the user says "run the pipeline" ->
``/klode-kit:kb-pipeline`` (its first segment is ``klode-kit pipeline --batch INBOX --stage audit``).

What the user sees (04): the run ends with ``records/2026-10-22-lab.md`` new, front matter ``status: pending_confirm``,
``uncertain: []``; a page without any disagreement is ``verified`` straight away. (``/workflows`` showing the running
``kb-pipeline`` is a user observation: ``test_u3_workflows_host.py``.)

Matrix U3-01, U3-02 (lane contract_replay; ``fx-u3-mixed``: page 1 - the four routes agree, page 2 - two majority
disagreements). Replay boundary: recorded OCR rows, ``doctor`` stub (the pipeline rig).

Facts the tests state, and one question for the user (impl-deviations "WI-31 待定"):

- the first segment (preflight, ocr, audit) ends at ``awaiting_confirm`` (exit 10) and writes **no** ``records/`` yet:
  records and their views appear when ``kb-audit --writeback`` ingests the audit (the second segment does it, with or
  without clicks) and ``views`` rebuilds ``records/``;
- in ``fx-u3-mixed`` the file ``records/2026-10-22-lab.md`` is the **verified** record of page 1; the pending record of
  page 2 is ``records/2026-10-22-lab-2.md`` (two records of one type and date get ``-2``). 04's wording ("the" file is
  ``pending_confirm``) is read here as "the view of the pending record": the tests find it by its status, and
  ``test_u3_first_segment_leaves_a_pending_view`` states the literal reading (a pending view at the end of the first
  segment) as a strict xfail.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from _support import pipeline_fx as F
from klode_kit.audit import confirm_page as CP

pytestmark = pytest.mark.contract_replay


def _front(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    return json.loads(text[4:text.index("\n---", 4)])


def _cli(rig: F.Rig, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "klode_kit", *args, "--project", str(rig.project)], env=rig.env,
                          capture_output=True, cwd=str(rig.project), timeout=180)


def first_segment(rig: F.Rig) -> subprocess.CompletedProcess:
    res = rig.run("--stage", "audit")
    assert res.returncode == 10, res.stderr.decode()
    assert json.loads(res.stdout.decode().splitlines()[-1])["reason"] == "awaiting_confirm"
    return res


def ingest_and_views(rig: F.Rig) -> dict[str, dict]:
    """What the second segment starts with when nobody clicked: ``kb-audit --writeback`` (ingest) then ``views``;
    returns ``{view file name: front matter}`` of ``records/*.md``."""
    done = _cli(rig, "kb-audit", "--batch", rig.batch_id, "--writeback")
    assert done.returncode in (0, 10), done.stderr.decode()
    views = _cli(rig, "views")
    assert views.returncode == 0, views.stderr.decode()
    return {p.name: _front(p) for p in sorted((rig.project / "records").glob("*.md"))}


def test_u3_pipeline_first_stage(tmp_path):
    """U3-01 (L2 S1): first segment, then the ingest: the pending record's view says ``pending_confirm`` with
    ``uncertain: []``; the page without disagreement is verified and the only one under ``verified/``."""
    rig = F.Rig(tmp_path, "fx-u3-mixed")
    res = first_segment(rig)
    # the first segment: the stage list of the workflow's first run, ending at the human
    stages = json.loads(res.stdout.decode().splitlines()[-1])["stages"]
    assert [(s["stage"], s["exit_code"]) for s in stages] == [("preflight", 0), ("ocr", 0), ("audit", 10)]
    confirm = rig.batch_dir / "audit" / "a1" / "confirm.html"
    assert confirm.is_file()
    assert not (rig.project / "records").exists()  # nothing is written back before the human (or the ingest)

    views = ingest_and_views(rig)
    assert sorted(views) == ["2026-10-22-lab-2.md", "2026-10-22-lab.md"]
    by_status = {v["status"]: (name, v) for name, v in views.items()}
    assert sorted(by_status) == ["pending_confirm", "verified"]
    pending_name, pending = by_status["pending_confirm"]
    verified_name, verified = by_status["verified"]
    assert pending["uncertain"] == [] and pending["verified_by"] is None
    assert pending["date"] == verified["date"] == "2026-10-22" and pending["type"] == verified["type"] == "lab"
    assert verified["verified_by"] == "auto"  # nobody clicked: the four routes agreed
    # verified/ links only the verified record; the pending one is not there
    listed = sorted(p.name for p in (rig.project / "verified").iterdir())
    assert listed == [verified_name]
    # the pending view really is the second page, whose disagreements are on the confirm page
    data = CP.page_data(confirm.read_bytes())
    todo = {r["rec_id"]: [f["verdict"] for f in r["fields"]] for r in data["records"]}
    assert todo == {verified["rec_id"]: [], pending["rec_id"]: ["majority", "majority"]}


@pytest.mark.xfail(strict=True, reason="04 U3 literal: the first segment writes no records/ yet (WI-31 finding)")
def test_u3_first_segment_leaves_a_pending_view(tmp_path):
    """U3-01 literal (04): at the end of the first segment ``records/2026-10-22-lab.md`` exists as ``pending_confirm``.
    Today the first segment ends before any writeback; strict xfail until the user decides what 04 meant."""
    rig = F.Rig(tmp_path, "fx-u3-mixed")
    first_segment(rig)
    names = sorted(p.name for p in (rig.project / "records").glob("*.md"))
    assert "2026-10-22-lab.md" in names
    assert _front(rig.project / "records" / "2026-10-22-lab.md")["status"] == "pending_confirm"


def _flip_one_value(engine: Path, route: str = "claude", page: str = "p001", old: str = "128", new: str = "129") -> int:
    """In ``engine/<route>/<page>.jsonl`` read the haemoglobin value ``old`` as ``new`` (every text field of that line)."""
    path = engine / route / f"{page}.jsonl"
    out, hits = [], 0
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if "血红蛋白" in row["text"] and old in row["text"]:
            hits += 1
            row["text"] = row["text"].replace(old, new)
            for tok in (row.get("tokens") or []):
                tok["text"] = tok["text"].replace(old, new)
        out.append(json.dumps(row, ensure_ascii=False))
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return hits


def test_u3_auto_verified_only_when_all_agree(tmp_path):
    """U3-02 (L2 S6, metamorphic): reading one value of the all-agree page differently on one route moves exactly that
    page's record from verified to pending_confirm, and nothing else changes status."""
    (tmp_path / "base").mkdir()
    base = F.Rig(tmp_path / "base", "fx-u3-mixed")
    first_segment(base)
    before = ingest_and_views(base)
    status_before = {v["rec_id"]: v["status"] for v in before.values()}
    assert sorted(status_before.values()) == ["pending_confirm", "verified"]

    flipped_engine = tmp_path / "engine-flipped"
    shutil.copytree(base.fx / "engine", flipped_engine)
    assert _flip_one_value(flipped_engine) == 1
    (tmp_path / "flip").mkdir()
    flipped = F.Rig(tmp_path / "flip", "fx-u3-mixed")
    flipped.env["KLODE_KIT_ENGINE_REPLAY"] = str(flipped_engine)
    first_segment(flipped)
    after = ingest_and_views(flipped)
    status_after = {v["rec_id"]: v["status"] for v in after.values()}
    assert set(status_after) == set(status_before)  # the same two records
    verified_before = {r for r, s in status_before.items() if s == "verified"}
    assert verified_before and all(status_after[r] == "pending_confirm" for r in verified_before)
    assert [r for r, s in status_after.items() if s == "verified"] == []
    # the other page's record is exactly as pending as before
    others = set(status_before) - verified_before
    assert all(status_after[r] == status_before[r] for r in others)
    # and the change is on the page the user has to click: the flipped value is a disagreement now
    data = CP.page_data((flipped.batch_dir / "audit" / "a1" / "confirm.html").read_bytes())
    todo = {r["rec_id"]: [f["verdict"] for f in r["fields"]] for r in data["records"]}
    assert all(todo[r] == ["majority"] for r in verified_before)
