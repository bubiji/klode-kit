"""WI-32c: the first pipeline segment (preflight, ocr, audit; ends at awaiting_confirm, exit 10) writes the records and
their views itself: a page without any disagreement is ``verified`` (auto), a page with disagreements is a
``pending_confirm`` candidate with no effective revision (user decision 2026-10-27, impl-deviations "WI-31 用户决定 3").

Replay boundary: ``fx-u3-mixed`` (page 1: four routes agree; page 2: two majority disagreements), recorded OCR rows,
``doctor`` stub (the pipeline rig). Everything else - kb-ocr, kb-audit, the ingest, confirm-page, views - is real.

Laws (S5): the first segment is idempotent (a second run changes no byte, a killed run converges to the same
records); the second segment advances the first one's records (one more revision, none duplicated, none lost);
a pending record is never a build input.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from _support import pipeline_fx as F
from _support import writeback_fx as W
from klode_kit.audit import confirm_page as CP
from klode_kit.build import records_to_txt as R

pytestmark = pytest.mark.contract_replay


def _front(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    return json.loads(text[4:text.index("\n---", 4)])


def _by_id(rig: F.Rig) -> dict[str, dict]:
    return {p.stem: _front(p) for p in sorted((rig.project / "records" / "_by_id").glob("*.md"))}


def _first(rig: F.Rig, **kw) -> subprocess.CompletedProcess:
    res = rig.run("--stage", "audit", **kw)
    assert res.returncode == 10, res.stderr.decode()
    return res


def _cli(rig: F.Rig, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "klode_kit", *args, "--project", str(rig.project)], env=rig.env,
                          capture_output=True, cwd=str(rig.project), timeout=180)


def _data(rig: F.Rig) -> dict[str, bytes]:
    """Every byte of the project except the pipeline lock's holder stamp (the pid of the last run)."""
    return {k: v for k, v in rig.tree().items() if k != "work/.pipeline.lock"}


def _shape(rig: F.Rig) -> dict:
    """What a converged run must show, without clocks: per record the candidate/effective revisions, the view names
    and links, and the commit log balance."""
    recs = {}
    for rec_id, f in _by_id(rig).items():
        recs[rec_id] = {"status": f["status"], "effective": f.get("effective"),
                        "candidates": [{k: c[k] for k in ("rev", "audit_rev", "status", "verified_by")}
                                       for c in f["candidates"]]}
    views = {p.name: p.read_text(encoding="utf-8") for p in sorted((rig.project / "records").glob("*.md"))}
    links = sorted(p.name for p in (rig.project / "verified").iterdir()) if (rig.project / "verified").is_dir() else []
    marks = [json.loads(x) for x in (rig.batch_dir / "audit" / "a1" / "commits.jsonl").read_text("utf-8").splitlines()
             if x.strip()]
    ops = sorted(m["op"] for m in marks)
    return {"records": recs, "views": views, "verified_links": links, "ops": ops}


def test_first_segment_writes_records_and_views(tmp_path):
    """32c-01 (L2 S1): at exit 10 the all-agree page is a verified(auto) record, the other a pending_confirm one."""
    rig = F.Rig(tmp_path, "fx-u3-mixed")
    res = _first(rig)
    stages = json.loads(res.stdout.decode().splitlines()[-1])["stages"]
    assert [(s["stage"], s["exit_code"]) for s in stages] == [("preflight", 0), ("ocr", 0), ("audit", 10)]

    recs = _by_id(rig)
    assert sorted(f["status"] for f in recs.values()) == ["pending_confirm", "verified"]
    verified = next(f for f in recs.values() if f["status"] == "verified")
    pending = next(f for f in recs.values() if f["status"] == "pending_confirm")
    assert verified["effective"]["verified_by"] == "auto" and verified["effective"]["rev"] == 1
    assert "effective" not in pending  # a pending candidate never touches effective
    assert [(c["rev"], c["status"]) for c in pending["candidates"]] == [(1, "pending_confirm")]

    views = {p.name: _front(p) for p in sorted((rig.project / "records").glob("*.md"))}
    assert sorted(v["status"] for v in views.values()) == ["pending_confirm", "verified"]
    pending_view = next(v for v in views.values() if v["status"] == "pending_confirm")
    assert pending_view["rec_id"] == pending["rec_id"] and pending_view["uncertain"] == []
    # the literal 04 name belongs to the verified page; the pending one is the "-2" view
    assert sorted(views) == ["2026-10-22-lab-2.md", "2026-10-22-lab.md"]
    # verified/ links only the verified record
    listed = sorted(p.name for p in (rig.project / "verified").iterdir())
    assert listed == [n for n, v in views.items() if v["status"] == "verified"]
    # the pipeline's child calls are the same four of the first segment plus the writes: no --force anywhere
    assert all("--force" not in " ".join(c["argv"]) for c in rig.calls())


def test_pending_record_is_not_a_build_input(tmp_path):
    """32c-02 (L2 S1): a pending record is excluded from the build snapshot; only the verified one binds."""
    rig = F.Rig(tmp_path, "fx-u3-mixed")
    _first(rig)
    bound, excluded = R.snapshot(rig.project)
    recs = _by_id(rig)
    assert len(recs) == 2
    assert set(bound) == {r for r, f in recs.items() if f["status"] == "verified"}
    assert [(e["rec_id"], e["reason"], e["status"]) for e in excluded] == [
        (r, "not_verified", "pending_confirm") for r, f in recs.items() if f["status"] == "pending_confirm"]
    # and the verified body of the bound record is never a pending one
    for rec_id, b in bound.items():
        body = _front(rig.project / "records" / "_rev" / f"{rec_id}.r{b['rev']}.md")
        assert body["status"] == "verified" and body["uncertain"] == [] and body["fields"]
    # the pending body claims no value
    for rec_id, f in recs.items():
        if f["status"] == "pending_confirm":
            body = _front(rig.project / "records" / "_rev" / f"{rec_id}.r1.md")
            assert body["fields"] == {} and body["open"]


def test_confirm_page_carries_the_ingested_rev_base(tmp_path):
    """32c-03 (L2 S1): the confirm page is made after the ingest, so its ``rev_base`` is the record's current rev."""
    rig = F.Rig(tmp_path, "fx-u3-mixed")
    _first(rig)
    data = CP.page_data((rig.batch_dir / "audit" / "a1" / "confirm.html").read_bytes())
    recs = _by_id(rig)
    assert {r["rec_id"]: r["rev_base"] for r in data["records"]} == {rec_id: 1 for rec_id in recs}


def test_first_segment_is_idempotent(tmp_path):
    """32c-04 (L2 S5): a second run of the first segment, and a bare ingest, change no byte of the project."""
    rig = F.Rig(tmp_path, "fx-u3-mixed")
    _first(rig)
    before = _data(rig)
    again = _first(rig)
    assert {s["how"] for s in json.loads(again.stdout.decode().splitlines()[-1])["stages"]} == {"reused"}
    assert _data(rig) == before
    ingest = _cli(rig, "kb-audit", "--batch", rig.batch_id, "--writeback")
    assert ingest.returncode == 10
    assert {r["result"] for r in json.loads(ingest.stdout.decode().splitlines()[-1])["per_record"]} == {"unchanged"}
    assert _cli(rig, "views").returncode == 0
    assert _data(rig) == before


def test_second_segment_advances_the_records(tmp_path):
    """32c-05 (L2 S5): the choices go on from the first segment's records: the pending record gets r2 (human,
    effective), the auto-verified one stays r1; nothing is duplicated or lost."""
    rig = F.Rig(tmp_path, "fx-u3-mixed")
    _first(rig)
    first = _by_id(rig)
    pending_id = next(r for r, f in first.items() if f["status"] == "pending_confirm")
    verified_id = next(r for r, f in first.items() if f["status"] == "verified")
    adir = rig.batch_dir / "audit" / "a1"
    choices = CP.export_choices((adir / "confirm.html").read_bytes(), W.picks(adir))
    path = W.write_choices(tmp_path / "choices.json", choices)
    res = rig.run("--choices", str(path), "--stage", "writeback")
    assert res.returncode == 0, res.stderr.decode()
    after = _by_id(rig)
    assert set(after) == set(first)
    assert after[verified_id] == first[verified_id]  # untouched: same bytes of front matter
    p = after[pending_id]
    assert [(c["rev"], c["status"]) for c in p["candidates"]] == [(1, "pending_confirm"), (2, "verified")]
    assert p["effective"]["rev"] == 2 and p["effective"]["verified_by"] == "human" and p["status"] == "verified"
    views = {p_.name: _front(p_)["status"] for p_ in (rig.project / "records").glob("*.md")}
    assert sorted(views.values()) == ["verified", "verified"]
    assert len(list((rig.project / "verified").iterdir())) == 2
    # one ingest + one human commit, each begun and ended
    marks = [json.loads(x) for x in (adir / "commits.jsonl").read_text("utf-8").splitlines() if x.strip()]
    assert sorted(m["op"] for m in marks) == ["begin"] * 3 + ["end"] * 3  # two ingest commits + one human


@pytest.mark.parametrize("point", ["writeback.begin", "writeback.fixes_appended", "writeback.rev_written",
                                   "writeback.byid_written", "writeback.committed", "writeback.receipt"])
def test_killed_first_segment_converges(tmp_path, point):
    """32c-06 (L2 S5): a first segment killed at any durable write of the ingest, then run again, ends in the state
    of an uninterrupted run (no half-written record, no duplicate revision)."""
    (tmp_path / "clean").mkdir()
    clean = F.Rig(tmp_path / "clean", "fx-u3-mixed")
    _first(clean)
    (tmp_path / "kill").mkdir()
    rig = F.Rig(tmp_path / "kill", "fx-u3-mixed")
    killed = rig.run("--stage", "audit", env={"KLODE_KIT_CRASH_AT": point})
    assert killed.returncode != 10  # the child was killed at the point: not a finished first segment
    _first(rig)
    assert _shape(rig) == _shape(clean)
    # a roll-forward or a fresh ingest, never both: every begin has exactly one end
    ops = _shape(rig)["ops"]
    assert ops.count("begin") == ops.count("end")
