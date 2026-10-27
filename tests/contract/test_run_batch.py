"""WI-04b: kb-ocr / run_batch on replayed engines (matrix 04b-01, 03-09, 12, 13).

Layout under the data project: ``work/<batch_id>/`` holds ``manifest.jsonl``, ``derived.jsonl``, ``receipt.json``
(``klode-kit.ocr_batch/1``), ``receipts/ocr.json`` and ``ocr/<engine>/<cfg_fp>.t<seq>/<page key>.jsonl`` plus the
attempt's ``receipt.json`` (``klode-kit.receipt/1``).

Laws: every (page, route) ends in exactly one terminal state and the page count is conserved (S5, E68); a
resumed run never repeats a finished page (S5); ``--redo`` never touches an older attempt (S5); a crash at any
enumerated crash point followed by a resume gives the same result as an uninterrupted run (S5).

Boundary: engines are replayed through ``KLODE_KIT_ENGINE_REPLAY`` (tests/_support/kb_ocr.py). The cloud
adapters are WI-06a/b; their real paths are paired there (06-11/12) and the local engines with 04a-10/11/12.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from _support.flows import RUN_BATCH_CRASHPOINTS
from _support.kb_ocr import (
    FIXTURES,
    ROUTES,
    attempt_dirs,
    batch_dirs,
    jsonl,
    kb_ocr_env,
    log_calls,
    make_inbox,
    make_replay,
    normalised,
    only_batch,
    receipt,
    run_kb_ocr,
    sha256,
    tree,
    tree_sha,
)
from klode_kit.contract import ids, schema
from klode_kit.ocr import run_batch

pytestmark = pytest.mark.contract_replay

LABS_1X = FIXTURES / "fx-labs-1x"
ROT90 = FIXTURES / "fx-rotated90"
U1 = FIXTURES / "fx-u1-3labs"
REDO = FIXTURES / "fx-redo"
ATTEMPT_RE = re.compile(r"[0-9a-f]{12}\.t([1-9][0-9]*)")


def ok(res: subprocess.CompletedProcess) -> None:
    assert res.returncode == 0, res.stderr.decode()


def calls(log: Path, engine: str | None = None, page: str | None = None) -> list[dict]:
    return [c for c in log_calls(log)
            if (engine is None or c["tool"] == engine) and (page is None or c["page"] == page)]


# --------------------------------------------------------------------------- 04b-01 / 04b-03


def test_rotate_receipt_u2(tmp_path):
    """04b-01 (S1): fx-rotated90 through the launcher: receipt page rotate 90; derived.jsonl transform.rotate 90
    with parent = original sha; the derived image and every route's rows refer to the upright page."""
    project = tmp_path / "proj"
    project.mkdir()
    inbox = make_inbox(project / "_inbox" / "clinic-0918", [ROT90 / "images" / "p001.png"])
    log = tmp_path / "calls.jsonl"
    ok(run_kb_ocr(project, inbox, kb_ocr_env(ROT90 / "engine", log), "--routes", ",".join(ROUTES),
                  launcher=True))
    rec = receipt(project)
    orig = sha256((ROT90 / "images" / "p001.png").read_bytes())
    upright = sha256((ROT90 / "derived" / "p001.upright.png").read_bytes())
    assert [p["rotate"] for p in rec["pages"]] == [90]
    batch = only_batch(project)
    derived = jsonl(batch / "derived.jsonl")
    upright_lines = [d for d in derived if not d["path"].startswith("_masked/")]  # WI-32a: masked pages follow
    assert len(upright_lines) == 1
    derived = upright_lines
    schema.validate("derived", derived[0])
    assert derived[0]["transform"]["rotate"] == 90
    assert derived[0]["parent"] == orig
    assert derived[0]["der_sha256"] == upright == rec["pages"][0]["der_sha256"]
    assert derived[0]["page_id"] == f"{ids.doc_id(orig)}/p001"
    assert sha256((project / derived[0]["path"]).read_bytes()) == upright
    for engine in ROUTES:
        rows = jsonl(batch / rec["pages"][0]["routes"][engine]["file"])
        assert rows and {r["der_sha256"] for r in rows} == {upright}


def test_run_dir_layout(tmp_path):
    """04b-03 (S1): ocr/<engine>/<cfg_fp>.t1/p001.jsonl exists for every route; run_id is the four-part key."""
    project = tmp_path / "proj"
    project.mkdir()
    inbox = make_inbox(tmp_path / "inbox", [LABS_1X / "images" / "p001.png"])
    ok(run_kb_ocr(project, inbox, kb_ocr_env(LABS_1X / "engine", tmp_path / "calls.jsonl")))
    batch = only_batch(project)
    rec = receipt(project)
    assert batch.name == rec["batch_id"] == ids.batch_id([sha256((LABS_1X / "images" / "p001.png").read_bytes())])
    for engine in ROUTES:
        (attempt,) = attempt_dirs(batch, engine)
        m = ATTEMPT_RE.fullmatch(attempt.name)
        assert m and m.group(1) == "1"
        assert (attempt / "p001.jsonl").is_file()
        fp = attempt.name.split(".")[0]
        assert rec["attempts"][engine]["run_id"] == f"{batch.name}/{engine}/{fp}/t1"
        assert rec["pages"][0]["routes"][engine]["file"] == f"ocr/{engine}/{attempt.name}/p001.jsonl"
        step = json.loads((attempt / "receipt.json").read_text())
        schema.validate("receipt", step)
        assert step["status"] == "done"
        assert step["outputs"] == [{"path": "p001.jsonl", "sha256": sha256((attempt / "p001.jsonl").read_bytes())}]
    for line in jsonl(batch / "manifest.jsonl"):
        schema.validate("manifest", line)
    schema.validate("receipt", json.loads((batch / "receipts" / "ocr.json").read_text()))


# --------------------------------------------------------------------------- 04b-04


OUTCOMES = ("ok", "missing", "fail", "flaky1", "flaky3")
EXPECTED = {"ok": "done", "missing": "route_missing_page", "fail": "route_failed", "flaky1": "done",
            "flaky3": "route_failed"}  # route_retry = 2 (defaults): flaky1 succeeds on try 2, flaky3 never


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(data=st.data())
def test_page_conservation(tmp_path_factory, data):
    """04b-04 (S5): over page subsets x route subsets x injected outcomes, every requested (page, route) has
    exactly one terminal state (done / route_failed / route_missing_page) that matches the injection; the
    number of pages is conserved from inbox to manifest to receipt (E68); done <=> a page file exists; every
    non-done pair is listed in issues (never silently dropped)."""
    keys = data.draw(st.lists(st.sampled_from(["p001", "p002", "p003"]), min_size=1, max_size=3, unique=True))
    keys.sort()
    routes = data.draw(st.lists(st.sampled_from(ROUTES), min_size=1, max_size=4, unique=True))
    routes = [r for r in ROUTES if r in routes]
    outcome = {(r, i): data.draw(st.sampled_from(OUTCOMES)) for r in routes for i in range(len(keys))}
    tmp = tmp_path_factory.mktemp("cons")
    project = tmp / "proj"
    project.mkdir()
    inbox = make_inbox(tmp / "inbox", [U1 / "images" / f"{k}.png" for k in keys])
    # the batch renumbers the chosen images p001.. in file-name order
    plan = {(r, f"p{i + 1:03d}"): (outcome[(r, i)], U1 / "engine" / r / f"{keys[i]}.jsonl")
            for r in routes for i in range(len(keys))}
    replay = make_replay(tmp / "replay", plan)
    log = tmp / "calls.jsonl"
    old = {k: os.environ.get(k) for k in ("KLODE_KIT_ENGINE_REPLAY", "KLODE_KIT_FAKE_LOG")}
    os.environ["KLODE_KIT_ENGINE_REPLAY"], os.environ["KLODE_KIT_FAKE_LOG"] = str(replay), str(log)
    try:
        code = run_batch.main(["--batch", str(inbox), "--project", str(project), "--routes", ",".join(routes)])
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    assert code == 0
    rec = receipt(project)
    batch = only_batch(project)
    assert len(jsonl(batch / "manifest.jsonl")) == len(keys) == len(rec["pages"])
    assert [p["key"] for p in rec["pages"]] == [f"p{i + 1:03d}" for i in range(len(keys))]
    issues = []
    for i, page in enumerate(rec["pages"]):
        assert sorted(page["routes"]) == sorted(routes)
        for r in routes:
            state = page["routes"][r]["status"]
            assert state == EXPECTED[outcome[(r, i)]], (r, page["key"], outcome[(r, i)], state)
            files = list((batch / "ocr" / r).glob(f"*/{page['key']}.jsonl"))
            assert (state == "done") == (len(files) == 1)
            if state != "done":
                issues.append(f"{state}:{r}:{page['key']}")
                assert any(u["reason"] == state and u["engine"] == r and u["scope"] == "page"
                           for u in page["uncertain"])
    assert sorted(rec["issues"]) == sorted(issues)


# --------------------------------------------------------------------------- 04b-05


def test_resume_after_kill(tmp_path):
    """04b-05 (S5): SIGKILL right after the first page file is written, then resume: across both runs every
    (route, page) was called exactly once, and every page file has no duplicate rows."""
    project = tmp_path / "proj"
    project.mkdir()
    inbox = make_inbox(tmp_path / "inbox", sorted((U1 / "images").glob("*.png")))
    log = tmp_path / "calls.jsonl"
    first = run_kb_ocr(project, inbox, kb_ocr_env(U1 / "engine", log, {"KLODE_KIT_CRASH_AT": "run_batch.page_done"}))
    assert first.returncode == -signal.SIGKILL, first.stderr.decode()
    assert len(calls(log)) == 1
    ok(run_kb_ocr(project, inbox, kb_ocr_env(U1 / "engine", log)))
    got = sorted((c["tool"], c["page"]) for c in calls(log))
    assert got == sorted((r, k) for r in ROUTES for k in ("p001", "p002", "p003"))
    batch = only_batch(project)
    for r in ROUTES:
        (attempt,) = attempt_dirs(batch, r)
        for k in ("p001", "p002", "p003"):
            rows = jsonl(attempt / f"{k}.jsonl")
            assert [row["line_idx"] for row in rows] == list(range(len(rows)))
            assert rows == jsonl(U1 / "engine" / r / f"{k}.jsonl")


# --------------------------------------------------------------------------- 04b-06 / 04b-07


def test_redo_new_attempt_old_untouched(tmp_path):
    """04b-06 (S5): --redo makes t2 for every route and leaves each t1 tree byte-identical; a later plain
    resume reuses t2 (no t3, no calls) - the redo entry and plain resume are distinct (U6)."""
    project = tmp_path / "proj"
    project.mkdir()
    inbox = make_inbox(tmp_path / "inbox", [REDO / "images" / "p001.png"])
    log = tmp_path / "calls.jsonl"
    ok(run_kb_ocr(project, inbox, kb_ocr_env(REDO / "engine", log)))
    batch = only_batch(project)
    t1 = {r: tree_sha(attempt_dirs(batch, r)[0]) for r in ROUTES}
    ok(run_kb_ocr(project, inbox, kb_ocr_env(REDO / "engine_t3", log), "--redo"))
    for r in ROUTES:
        a1, a2 = attempt_dirs(batch, r)
        assert a1.name.endswith(".t1") and a2.name.endswith(".t2")
        assert tree_sha(a1) == t1[r]
        assert jsonl(a2 / "p001.jsonl") == jsonl(REDO / "engine_t3" / r / "p001.jsonl")
    assert receipt(project)["attempts"]["codex"]["run_id"].endswith("/t2")
    n = len(calls(log))
    ok(run_kb_ocr(project, inbox, kb_ocr_env(REDO / "engine_t3", log)))
    assert len(calls(log)) == n
    assert all(len(attempt_dirs(batch, r)) == 2 for r in ROUTES)
    assert {r: tree_sha(attempt_dirs(batch, r)[0]) for r in ROUTES} == t1


def test_resume_reuses_only_three_hash_match(tmp_path):
    """04b-07 (S3): one byte of t1's vision output changed -> plain resume makes vision t2 (calls vision only);
    the changed t1 is left as it is; the untouched routes reuse their t1 without any call."""
    project = tmp_path / "proj"
    project.mkdir()
    inbox = make_inbox(tmp_path / "inbox", [LABS_1X / "images" / "p001.png"])
    log = tmp_path / "calls.jsonl"
    ok(run_kb_ocr(project, inbox, kb_ocr_env(LABS_1X / "engine", log)))
    batch = only_batch(project)
    target = attempt_dirs(batch, "vision")[0] / "p001.jsonl"
    data = bytearray(target.read_bytes())
    data[-2] = ord("X") if data[-2] != ord("X") else ord("Y")
    target.write_bytes(bytes(data))
    n = len(calls(log))
    ok(run_kb_ocr(project, inbox, kb_ocr_env(LABS_1X / "engine", log)))
    new = calls(log)[n:]
    assert [(c["tool"], c["page"]) for c in new] == [("vision", "p001")]
    v1, v2 = attempt_dirs(batch, "vision")
    assert v2.name.endswith(".t2") and target.read_bytes() == bytes(data)
    for r in ("mlx", "claude", "codex"):
        assert len(attempt_dirs(batch, r)) == 1
    assert receipt(project)["pages"][0]["routes"]["vision"]["file"].startswith(f"ocr/vision/{v2.name}/")


# --------------------------------------------------------------------------- 04b-08 / 04b-09


@pytest.mark.parametrize("retry", [0, 2])
def test_route_retry_then_failed(tmp_path, retry):
    """04b-08 (S3): codex fails on every call -> exactly 1 + route_retry calls, route_failed, page-level
    uncertain; the other routes are unaffected and codex writes no page file."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / "klode-kit.toml").write_text(f"route_retry = {retry}\n", encoding="utf-8")
    inbox = make_inbox(tmp_path / "inbox", [LABS_1X / "images" / "p001.png"])
    plan = {(r, "p001"): ("ok", LABS_1X / "engine" / r / "p001.jsonl") for r in ROUTES}
    plan[("codex", "p001")] = ("fail", None)
    log = tmp_path / "calls.jsonl"
    ok(run_kb_ocr(project, inbox, kb_ocr_env(make_replay(tmp_path / "replay", plan), log)))
    assert len(calls(log, "codex", "p001")) == 1 + retry
    rec = receipt(project)
    page = rec["pages"][0]
    assert page["routes"]["codex"]["status"] == "route_failed"
    assert page["routes"]["codex"]["tries"] == 1 + retry
    assert {e: page["routes"][e]["status"] for e in ("vision", "mlx", "claude")} == dict.fromkeys(
        ("vision", "mlx", "claude"), "done")
    (u,) = [u for u in page["uncertain"] if u["engine"] == "codex"]
    assert (u["reason"], u["scope"], u["id"]) == ("route_failed", "page", f"{page['page_id']}:codex:route_failed")
    assert rec["issues"] == ["route_failed:codex:p001"]
    (attempt,) = attempt_dirs(only_batch(project), "codex")
    assert not (attempt / "p001.jsonl").exists()
    assert json.loads((attempt / "receipt.json").read_text())["status"] == "failed"


@pytest.mark.parametrize(("flaky", "status", "n"), [(2, "done", 3), (3, "route_failed", 3)])
def test_route_retry_boundary(tmp_path, flaky, status, n):
    """Boundary of route_retry = 2: two failures then success is done on the third call; three failures is
    route_failed after three calls."""
    project = tmp_path / "proj"
    project.mkdir()
    inbox = make_inbox(tmp_path / "inbox", [LABS_1X / "images" / "p001.png"])
    plan = {("codex", "p001"): (f"flaky{flaky}", LABS_1X / "engine" / "codex" / "p001.jsonl")}
    log = tmp_path / "calls.jsonl"
    ok(run_kb_ocr(project, inbox, kb_ocr_env(make_replay(tmp_path / "replay", plan), log), "--routes", "codex"))
    assert len(calls(log, "codex", "p001")) == n
    assert receipt(project)["pages"][0]["routes"]["codex"]["status"] == status


def test_missing_page_explicit(tmp_path):
    """04b-09 (S3): fx-redo t2 replay has no codex output for p001 -> receipt issue route_missing_page:codex:p001,
    page-level uncertain, a stderr line; never silently dropped."""
    project = tmp_path / "proj"
    project.mkdir()
    inbox = make_inbox(tmp_path / "inbox", [REDO / "images" / "p001.png"])
    res = run_kb_ocr(project, inbox, kb_ocr_env(REDO / "engine_t2", tmp_path / "calls.jsonl"))
    ok(res)
    rec = receipt(project)
    assert "route_missing_page:codex:p001" in rec["issues"]
    page = rec["pages"][0]
    assert page["routes"]["codex"]["status"] == "route_missing_page"
    assert any(u["reason"] == "route_missing_page" and u["engine"] == "codex" for u in page["uncertain"])
    assert "route_missing_page:codex:p001" in res.stderr.decode()
    assert rec["status"] != "done"


def test_bad_replay_rows_are_route_failed(tmp_path):
    """Rows that do not belong to the page (wrong der_sha256) are not accepted as done."""
    project = tmp_path / "proj"
    project.mkdir()
    inbox = make_inbox(tmp_path / "inbox", [LABS_1X / "images" / "p001.png"])
    plan = {("claude", "p001"): ("ok", U1 / "engine" / "claude" / "p002.jsonl")}
    ok(run_kb_ocr(project, inbox, kb_ocr_env(make_replay(tmp_path / "replay", plan), tmp_path / "c.jsonl"),
                  "--routes", "claude"))
    assert receipt(project)["pages"][0]["routes"]["claude"]["status"] == "route_failed"


def test_engine_replay_ignored_without_seams(tmp_path):
    """The replay seam is inert outside test mode: it is reported on stderr and no replay call happens; the
    cloud placeholder route (WI-06a) is route_failed with zero calls, not silently skipped."""
    project = tmp_path / "proj"
    project.mkdir()
    inbox = make_inbox(tmp_path / "inbox", [LABS_1X / "images" / "p001.png"])
    log = tmp_path / "calls.jsonl"
    env = kb_ocr_env(LABS_1X / "engine", log)
    env.pop("KLODE_KIT_TEST_SEAMS")
    res = run_kb_ocr(project, inbox, env, "--routes", "claude")
    ok(res)
    assert "KLODE_KIT_ENGINE_REPLAY" in res.stderr.decode()
    assert [c for c in log_calls(log) if "page" in c] == []  # WI-32a: no replay call (the sender may ask --version)
    route = receipt(project)["pages"][0]["routes"]["claude"]
    assert route["status"] == "route_failed" and route["error"]


def test_disabled_cloud_route_is_route_failed_without_calls(tmp_path):
    """routes_enabled = false for codex (r3.3): zero calls, route_failed with reason route_disabled."""
    project = tmp_path / "proj"
    project.mkdir()
    inbox = make_inbox(tmp_path / "inbox", [LABS_1X / "images" / "p001.png"])
    log = tmp_path / "calls.jsonl"
    os.environ["KLODE_KIT_ENGINE_REPLAY"], os.environ["KLODE_KIT_FAKE_LOG"] = str(LABS_1X / "engine"), str(log)
    try:
        code = run_batch.main(["--batch", str(inbox), "--project", str(project), "--routes", "vision,codex"],
                              routes_enabled={"claude": True, "codex": False})
    finally:
        os.environ.pop("KLODE_KIT_ENGINE_REPLAY")
        os.environ.pop("KLODE_KIT_FAKE_LOG")
    assert code == 0
    assert calls(log, "codex") == []
    page = receipt(project)["pages"][0]
    assert page["routes"]["codex"]["status"] == "route_failed"
    assert "route_disabled" in page["routes"]["codex"]["error"]
    assert page["routes"]["vision"]["status"] == "done"
    assert attempt_dirs(only_batch(project), "codex") == []


# --------------------------------------------------------------------------- 04b-12


def test_concurrent_runs_same_batch_locked(tmp_path):
    """04b-12 (S3): while a first kb-ocr holds the project lock, a second one exits 40 without writing; the
    first then completes with intact, non-interleaved page files."""
    project = tmp_path / "proj"
    project.mkdir()
    inbox = make_inbox(tmp_path / "inbox", [LABS_1X / "images" / "p001.png"])
    pause = tmp_path / "pause"
    pause.mkdir()
    log = tmp_path / "calls.jsonl"
    env1 = kb_ocr_env(LABS_1X / "engine", log, {"KLODE_KIT_PAUSE_AT": "run_batch.lock",
                                                 "KLODE_KIT_PAUSE_DIR": str(pause)})
    argv = [sys.executable, "-m", "klode_kit", "kb-ocr", "--batch", str(inbox), "--project", str(project)]
    first = subprocess.Popen(argv, env=env1, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 30
        while not (pause / "run_batch.lock.reached").exists():
            assert first.poll() is None, first.communicate()[1].decode()
            assert time.monotonic() < deadline, "first run never reached the lock point"
            time.sleep(0.02)
        before = tree(project)
        second = run_kb_ocr(project, inbox, kb_ocr_env(LABS_1X / "engine", log))
        assert second.returncode == 40, second.stderr.decode()
        assert str(first.pid) in second.stderr.decode()
        assert tree(project) == before
        (pause / "run_batch.lock.go").write_text("", encoding="utf-8")
        _, err = first.communicate(timeout=60)
    finally:
        if first.poll() is None:
            first.kill()
    assert first.returncode == 0, err.decode()
    batch = only_batch(project)
    for r in ROUTES:
        (attempt,) = attempt_dirs(batch, r)
        assert jsonl(attempt / "p001.jsonl") == jsonl(LABS_1X / "engine" / r / "p001.jsonl")
    assert len(calls(log)) == len(ROUTES)


# --------------------------------------------------------------------------- 04b-13


@pytest.fixture(scope="module")
def uninterrupted(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("ref")
    project = tmp / "proj"
    project.mkdir()
    res = run_kb_ocr(project, LABS_1X / "images", kb_ocr_env(LABS_1X / "engine", tmp / "calls.jsonl"))
    ok(res)
    return normalised(tree(only_batch(project)))


def test_crashpoint_list_is_not_vacuous():
    assert "run_batch.before_replace" in RUN_BATCH_CRASHPOINTS and len(RUN_BATCH_CRASHPOINTS) >= 5


@pytest.mark.parametrize("point", sorted(RUN_BATCH_CRASHPOINTS))
def test_atomic_page_write(tmp_path, uninterrupted, point):
    """04b-13 (S5), enumerated over the run_batch crash-point list (00s-16 pairing): SIGKILL at the point leaves
    no half-written .jsonl in place; the resumed run's batch directory equals an uninterrupted run's byte for
    byte (wall-clock receipt fields aside)."""
    project = tmp_path / "proj"
    project.mkdir()
    log = tmp_path / "calls.jsonl"
    crashed = run_kb_ocr(project, LABS_1X / "images",
                         kb_ocr_env(LABS_1X / "engine", log, {"KLODE_KIT_CRASH_AT": point}))
    assert crashed.returncode == -signal.SIGKILL, (point, crashed.stderr.decode())
    for batch in batch_dirs(project):
        for path in batch.rglob("*.jsonl"):
            jsonl(path)  # complete: parses and ends with a newline
    ok(run_kb_ocr(project, LABS_1X / "images", kb_ocr_env(LABS_1X / "engine", log)))
    assert normalised(tree(only_batch(project))) == uninterrupted
    assert not list(only_batch(project).rglob("*.tmp"))
