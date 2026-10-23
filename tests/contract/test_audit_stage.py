"""WI-08a: the audit stage ``kb-audit`` on replayed kb-ocr runs (matrix 08a-01..04, 08a-08; r3.2 audit_rev,
r2.3 uncertain_log, r4.1 candidates never touch effective).

Engine boundary: the four routes are replayed through ``KLODE_KIT_ENGINE_REPLAY`` (pairing: 06-11 / 06-12 and
the WI-04b real-engine cli tests). kb-ocr and kb-audit run as real processes on a temporary data project.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from _support.kb_ocr import (FIXTURES, ROUTES, jsonl, kb_ocr_env, make_inbox, only_batch, receipt, run_kb_ocr,
                             tree, tree_sha)
from klode_kit.audit import stage
from klode_kit.contract import schema

pytestmark = pytest.mark.contract_replay

REDO = FIXTURES / "fx-redo"
LABS3 = FIXTURES / "fx-u1-3labs"
AUDIT_DONE = (0, 10)  # 0: every candidate verified; 10: awaiting_confirm (r2.6)


def setup(tmp_path: Path, fx: Path) -> tuple[Path, Path]:
    project = tmp_path / "proj"
    project.mkdir()
    inbox = make_inbox(tmp_path / "inbox", sorted((fx / "images").glob("*.png")))
    return project, inbox


def ocr(project: Path, inbox: Path, replay: Path, log: Path, *args: str) -> None:
    res = run_kb_ocr(project, inbox, kb_ocr_env(replay, log), *args)
    assert res.returncode == 0, res.stderr.decode()


def audit(project: Path, batch: str, env: dict, *args: str) -> subprocess.CompletedProcess:
    argv = [sys.executable, "-m", "klode_kit", "kb-audit", "--batch", batch, "--project", str(project), *args]
    return subprocess.run(argv, env=env, capture_output=True, timeout=120, cwd=str(project))


def revs(batch_dir: Path) -> list[str]:
    root = batch_dir / "audit"
    return sorted(p.name for p in root.iterdir() if p.is_dir()) if root.is_dir() else []


# --------------------------------------------------------------------------- 08a-01


def test_audit_rev_new_on_run_change(tmp_path):
    """08a-01 (S1): a new run set (kb-ocr --redo) gives a new a<N+1> whose runs.json names the new runs; a1's
    tree is untouched. The candidates follow the gate (t1 codex disagrees -> pending_confirm; t2 codex misses
    the page -> disputed; t3 four routes agree -> verified auto) and nothing is written under records/."""
    project, inbox = setup(tmp_path, REDO)
    log = tmp_path / "calls.jsonl"
    env = kb_ocr_env(REDO / "engine", log)
    ocr(project, inbox, REDO / "engine", log)
    batch = only_batch(project)
    first = audit(project, batch.name, env)
    assert first.returncode in AUDIT_DONE, first.stderr.decode()
    a1 = batch / "audit" / "a1"
    runs1 = json.loads((a1 / "runs.json").read_text(encoding="utf-8"))
    assert runs1 == {r: receipt(project)["attempts"][r]["run_id"] for r in ROUTES}
    assert [c["status"] for c in jsonl(a1 / "candidates.jsonl")] == ["pending_confirm"]
    sha1 = tree_sha(a1)

    ocr(project, inbox, REDO / "engine_t2", log, "--redo")
    second = audit(project, batch.name, env)
    assert second.returncode in AUDIT_DONE, second.stderr.decode()
    assert revs(batch) == ["a1", "a2"]
    a2 = batch / "audit" / "a2"
    runs2 = json.loads((a2 / "runs.json").read_text(encoding="utf-8"))
    assert runs2 == {r: receipt(project)["attempts"][r]["run_id"] for r in ROUTES}
    assert all(runs2[r] != runs1[r] for r in ROUTES)
    assert tree_sha(a1) == sha1
    (cand,) = jsonl(a2 / "candidates.jsonl")
    assert cand["status"] == "disputed" and cand["audit_rev"] == f"{batch.name}/a2"
    assert ("route_missing_page", "page", "codex") in {(u["reason"], u["scope"], u.get("engine"))
                                                        for u in cand["uncertain"]}

    ocr(project, inbox, REDO / "engine_t3", log, "--redo")
    third = audit(project, batch.name, env)
    assert third.returncode == 0, third.stderr.decode()
    assert revs(batch) == ["a1", "a2", "a3"]
    (cand3,) = jsonl(batch / "audit" / "a3" / "candidates.jsonl")
    assert (cand3["status"], cand3["verified_by"], cand3["uncertain"]) == ("verified", "auto", [])
    assert tree_sha(a1) == sha1
    for rev in ("a1", "a2", "a3"):
        for line in jsonl(batch / "audit" / rev / "cells.jsonl"):
            assert schema.issues("cells", line) == [], line
    assert not (project / "records").exists()


# --------------------------------------------------------------------------- 08a-02


def test_audit_rev_reuse_when_same(tmp_path):
    """08a-02 (S5): with the run set unchanged (a second kb-audit, or kb-ocr reusing its done attempts) kb-audit
    keeps a1 and writes nothing: the audit tree is byte-identical and the exit code is the same."""
    project, inbox = setup(tmp_path, REDO)
    log = tmp_path / "calls.jsonl"
    env = kb_ocr_env(REDO / "engine", log)
    ocr(project, inbox, REDO / "engine", log)
    batch = only_batch(project)
    first = audit(project, batch.name, env)
    assert first.returncode in AUDIT_DONE, first.stderr.decode()
    before = tree(batch / "audit")
    again = audit(project, batch.name, env)
    assert again.returncode == first.returncode, again.stderr.decode()
    assert revs(batch) == ["a1"]
    assert tree(batch / "audit") == before

    ocr(project, inbox, REDO / "engine", log)  # no --redo: done attempts are reused, run ids unchanged
    third = audit(project, batch.name, env)
    assert third.returncode == first.returncode, third.stderr.decode()
    assert revs(batch) == ["a1"]
    assert tree(batch / "audit") == before


# --------------------------------------------------------------------------- 08a-03


def test_write_once(tmp_path):
    """08a-03 (S3): in one a<N>, cells.jsonl and confirm.html are written once; a second write raises
    WriteOnceViolation and leaves the bytes as they were."""
    project, inbox = setup(tmp_path, REDO)
    log = tmp_path / "calls.jsonl"
    ocr(project, inbox, REDO / "engine", log)
    batch = only_batch(project)
    res = audit(project, batch.name, kb_ocr_env(REDO / "engine", log))
    assert res.returncode in AUDIT_DONE, res.stderr.decode()
    a1 = batch / "audit" / "a1"
    cells = (a1 / "cells.jsonl").read_bytes()
    assert cells
    with pytest.raises(stage.WriteOnceViolation):
        stage.write_once(a1, "cells.jsonl", b"{}\n")
    assert (a1 / "cells.jsonl").read_bytes() == cells

    stage.write_once(a1, "confirm.html", b"<!doctype html><p>1</p>\n")
    with pytest.raises(stage.WriteOnceViolation):
        stage.write_once(a1, "confirm.html", b"<!doctype html><p>2</p>\n")
    assert (a1 / "confirm.html").read_bytes() == b"<!doctype html><p>1</p>\n"


def test_write_once_unknown_name_refused(tmp_path):
    """08a-03 guard (S3): only the a<N> artifacts go through write_once; any other name is refused, unwritten."""
    with pytest.raises(ValueError):
        stage.write_once(tmp_path, "notes.txt", b"x")
    assert list(tmp_path.iterdir()) == []


# --------------------------------------------------------------------------- 08a-04

ENTRY_IDS = ("p:codex:route_missing_page", "f1:tie", "f2:missing_cell:mlx", "p:r005:unaligned:claude")


def _entry(i: str) -> dict:
    return {"id": i, "reason": "tie", "scope": "cell", "fix_action": "pick on the confirm page",
            "clear_evidence": "a human choice for the cell"}


@settings(max_examples=60, deadline=None)
@given(st.data())
def test_uncertain_log_append_only(data):
    """08a-04 (S5): over random add / clear sequences, every step keeps the earlier bytes as an exact prefix;
    a clear appends a line with cleared_by / cleared_at / cleared_evidence and never removes the add line;
    re-adding an open entry writes nothing; clearing an entry that is not open is refused without a write."""
    with tempfile.TemporaryDirectory() as tmp:
        log = stage.UncertainLog(Path(tmp) / "audit" / "uncertain_log.jsonl")
        open_ids: set[str] = set()
        prev = b""
        for step in range(data.draw(st.integers(1, 8), label="steps")):
            at = f"2026-10-23T00:00:{step:02d}Z"
            if not open_ids or data.draw(st.booleans(), label="add?"):
                ids = data.draw(st.lists(st.sampled_from(ENTRY_IDS), min_size=1, unique=True), label="add")
                log.add([_entry(i) for i in ids], audit_rev="eb2b28a29018/a1", at=at)
                new_ids = [i for i in ids if i not in open_ids]
                open_ids |= set(ids)
                cur = log.path.read_bytes()
                assert cur[:len(prev)] == prev
                added = [json.loads(x) for x in cur[len(prev):].decode("utf-8").splitlines()]
                assert [a["id"] for a in added] == new_ids
            else:
                ids = data.draw(st.lists(st.sampled_from(sorted(open_ids)), min_size=1, unique=True), label="clear")
                closed = [i for i in ENTRY_IDS if i not in open_ids]
                if closed and data.draw(st.booleans(), label="bad clear"):
                    with pytest.raises(ValueError):
                        log.clear([closed[0]], by="auto", at=at, evidence="x")
                    assert log.path.read_bytes() == prev
                log.clear(ids, by="human", at=at, evidence="choices item")
                open_ids -= set(ids)
                cur = log.path.read_bytes()
                assert cur[:len(prev)] == prev
                added = [json.loads(x) for x in cur[len(prev):].decode("utf-8").splitlines()]
                assert sorted(a["id"] for a in added) == sorted(ids)
                assert all((a["cleared_by"], a["cleared_at"], a["cleared_evidence"]) == ("human", at, "choices item")
                           for a in added)
            prev = log.path.read_bytes() if log.path.exists() else b""
            assert set(log.open_entries()) == open_ids
            assert len(log.lines()) == len(prev.splitlines())


# --------------------------------------------------------------------------- 08a-08


def test_concurrent_audit_same_batch(tmp_path):
    """08a-08 (S3): while one kb-audit holds the project lock a second exits 40 without writing; afterwards
    exactly one audit revision exists. On fx-u1-3labs page 3 is verified, pages 1-2 await confirmation."""
    project, inbox = setup(tmp_path, LABS3)
    log = tmp_path / "calls.jsonl"
    ocr(project, inbox, LABS3 / "engine", log)
    batch = only_batch(project)
    pause = tmp_path / "pause"
    pause.mkdir()
    env1 = kb_ocr_env(LABS3 / "engine", log, {"KLODE_KIT_PAUSE_AT": "audit.lock", "KLODE_KIT_PAUSE_DIR": str(pause)})
    argv = [sys.executable, "-m", "klode_kit", "kb-audit", "--batch", batch.name, "--project", str(project)]
    first = subprocess.Popen(argv, env=env1, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(project))
    try:
        deadline = time.monotonic() + 30
        while not (pause / "audit.lock.reached").exists():
            assert first.poll() is None, first.communicate()[1].decode()
            assert time.monotonic() < deadline, "first kb-audit never reached the lock point"
            time.sleep(0.02)
        before = tree(project)
        second = audit(project, batch.name, kb_ocr_env(LABS3 / "engine", log))
        assert second.returncode == 40, second.stderr.decode()
        assert str(first.pid) in second.stderr.decode()
        assert tree(project) == before
        (pause / "audit.lock.go").write_text("", encoding="utf-8")
        _, err = first.communicate(timeout=120)
    finally:
        if first.poll() is None:
            first.kill()
    assert first.returncode == 10, err.decode()
    assert revs(batch) == ["a1"]
    # user-approved 2026-10-24: each fixture image is its own doc, so map page_id to the batch page key
    key = {p["page_id"]: p["key"] for p in receipt(project)["pages"]}
    statuses = {key[c["page_id"]]: c["status"] for c in jsonl(batch / "audit" / "a1" / "candidates.jsonl")}
    assert statuses == {"p001": "pending_confirm", "p002": "pending_confirm", "p003": "verified"}
