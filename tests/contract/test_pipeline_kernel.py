"""WI-09a: pipeline kernel, contract_replay lane (matrix 09a-01 .. 09a-06, 09a-08, 09a-10).

Replay boundary: the OCR routes are the engine replay seam (recorded rows of fx-u3-mixed); ``doctor`` is a
stub inside the launcher wrapper (``_support/pipeline_fx``). kb-ocr, kb-audit, confirm-page and views run for
real as children of the kernel, through the launcher wrapper that logs every call. Paired real paths:
06-11 / 00d2-01 (replays against the real CLIs) and ``tests/cli/test_pipeline_cli.py`` (real doctor).

Laws: reuse iff the three hashes match (09a-02, S5); a second ``resume`` changes nothing (09a-10, S5).
"""

from __future__ import annotations

import itertools
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from _support.confirm_fx import FIXTURES as _FX  # noqa: F401  (fixtures root)
from _support import pipeline_fx as F
from _support import writeback_fx as W
from klode_kit import pipeline as P
from klode_kit.contract.schema import issues

pytestmark = pytest.mark.contract_replay


@pytest.fixture()
def rig(tmp_path: Path) -> F.Rig:
    return F.Rig(tmp_path)


def _all_valid(rig: F.Rig) -> None:
    for path in sorted(rig.receipts_dir.glob("*.json")):
        assert issues("receipt", json.loads(path.read_text(encoding="utf-8"))) == [], path


# ---------------------------------------------------------------- 09a-01
def test_stage_order(rig: F.Rig) -> None:
    res = rig.run()
    assert res.returncode == 10, res.stderr.decode()
    assert rig.receipt_names() == ["preflight", "ocr", "audit"]
    _all_valid(rig)
    assert [rig.receipt(s)["status"] for s in ("preflight", "ocr", "audit")] == ["done"] * 3
    assert [rig.receipt(s)["exit_code"] for s in ("preflight", "ocr", "audit")] == [0, 0, 10]
    # the children, in order: doctor, kb-ocr, kb-audit, then the confirm page (WI-08b); no writeback yet
    assert [c["argv"][0] for c in rig.calls()] == ["doctor", "kb-ocr", "kb-audit", "confirm-page"]
    assert (rig.batch_dir / "audit" / "a1" / "confirm.html").is_file()
    summary = json.loads(res.stdout.decode().strip().splitlines()[-1])
    assert summary["exit_code"] == 10 and summary["reason"] == "awaiting_confirm"
    assert summary["batch_id"] == rig.batch_id


def test_stage_option_stops_after_that_stage(rig: F.Rig) -> None:
    assert rig.run("--stage", "ocr").returncode == 0
    assert rig.receipt_names() == ["preflight", "ocr"]
    assert [c["argv"][0] for c in rig.calls()] == ["doctor", "kb-ocr"]


def test_every_child_goes_through_the_launcher_and_never_gets_force(rig: F.Rig) -> None:
    assert rig.run().returncode == 10
    rig.run("--choices", str(_choices(rig)))
    argvs = [c["argv"] for c in rig.calls()]
    assert argvs  # the wrapper IS the launcher: every call in the log came through ``--launcher``
    assert all("--force" not in a and not any(x.startswith("--force") for x in a) for a in argvs)


# ---------------------------------------------------------------- 09a-02 (the powerset of the three hashes)
SUBSETS = [c for n in range(4) for c in itertools.combinations(("inputs", "config", "outputs"), n)]


@pytest.mark.parametrize("changed", SUBSETS, ids=lambda c: "+".join(c) or "none")
def test_done_reuse_requires_three_hashes(rig: F.Rig, changed: tuple[str, ...]) -> None:
    assert rig.run("--stage", "ocr").returncode == 0
    assert len(rig.calls("kb-ocr")) == 1
    path = rig.receipts_dir / "ocr.json"
    rec = json.loads(path.read_text(encoding="utf-8"))
    if "inputs" in changed:
        rec["inputs_hash"] = "0" * 64
    if "config" in changed:
        rec["config_hash"] = "f" * 64
    if "outputs" in changed:
        rec["outputs"][0]["sha256"] = "a" * 64
    path.write_text(json.dumps(rec), encoding="utf-8")
    assert rig.run("--stage", "ocr").returncode == 0
    assert len(rig.calls("kb-ocr")) == 1 + (1 if changed else 0)
    # converged: whatever happened, the receipt is trustworthy again and the next run reuses it
    assert rig.run("--stage", "ocr").returncode == 0
    assert len(rig.calls("kb-ocr")) == 1 + (1 if changed else 0)


def test_missing_product_reruns_the_step(rig: F.Rig) -> None:
    assert rig.run("--stage", "ocr").returncode == 0
    victim = rig.receipt("ocr")["outputs"][0]["path"]
    (rig.project / victim).unlink()
    assert rig.run("--stage", "ocr").returncode == 0
    assert len(rig.calls("kb-ocr")) == 2
    assert (rig.project / victim).is_file()


def test_changed_product_bytes_rerun_the_step(rig: F.Rig) -> None:
    assert rig.run("--stage", "ocr").returncode == 0
    victim = rig.project / rig.receipt("ocr")["outputs"][0]["path"]
    victim.write_bytes(victim.read_bytes() + b"\n")
    assert rig.run("--stage", "ocr").returncode == 0
    assert len(rig.calls("kb-ocr")) == 2


def test_changed_configuration_reruns_the_step(rig: F.Rig) -> None:
    assert rig.run("--stage", "ocr").returncode == 0
    (rig.project / "klode-kit.toml").write_text("route_retry = 3\n", encoding="utf-8")
    assert rig.run("--stage", "ocr").returncode == 0
    assert len(rig.calls("kb-ocr")) == 2


def test_rerun_with_identical_products_keeps_downstream(rig: F.Rig) -> None:
    """The audit step's inputs are the ocr step's recorded products: an ocr rerun that reproduces them leaves
    the audit reusable."""
    assert rig.run().returncode == 10
    assert len(rig.calls("kb-audit")) == 1
    rec = rig.receipt("ocr")
    rec["outputs"][0]["sha256"] = "b" * 64  # ocr no longer matches its product -> reruns, same bytes again
    (rig.receipts_dir / "ocr.json").write_text(json.dumps(rec), encoding="utf-8")
    assert rig.run().returncode == 10
    assert len(rig.calls("kb-ocr")) == 2
    # the rerun reproduced identical products, so the audit inputs are unchanged: audit is reused
    assert len(rig.calls("kb-audit")) == 1


# ---------------------------------------------------------------- 09a-03 / 09a-04: the project lock
def test_lock_exit40(rig: F.Rig) -> None:
    first = rig.popen(env=rig.pause_env("pipeline.lock"))
    try:
        rig.wait_reached("pipeline.lock", first)
        before = rig.tree()
        second = rig.run()
        assert second.returncode == 40, second.stderr.decode()
        assert "lock" in second.stderr.decode()
        assert str(first.pid) in second.stderr.decode()  # names the holder
        assert rig.tree() == before  # the loser wrote nothing, not even into the lock file
        rig.release("pipeline.lock")
        out, err = first.communicate(timeout=180)
    finally:
        if first.poll() is None:
            first.kill()
    assert first.returncode == 10, err.decode()
    assert rig.receipt_names() == ["preflight", "ocr", "audit"]
    _all_valid(rig)
    assert (rig.batch_dir / "audit" / "a1" / "confirm.html").is_file()


def test_lock_released_after_crash(rig: F.Rig) -> None:
    first = rig.popen(env=rig.pause_env("pipeline.lock"))
    try:
        rig.wait_reached("pipeline.lock", first)
        os.kill(first.pid, signal.SIGKILL)
        first.wait(timeout=30)
    finally:
        if first.poll() is None:
            first.kill()
    res = rig.run()
    assert res.returncode == 10, res.stderr.decode()
    assert rig.receipt_names() == ["preflight", "ocr", "audit"]
    _all_valid(rig)


# ---------------------------------------------------------------- 09a-05 / 09a-06: crashed vs live running receipts
def test_crash_recovery_dead_pid(rig: F.Rig) -> None:
    first = rig.popen(env=rig.pause_env("pipeline.stage_running"))
    try:
        rig.wait_reached("pipeline.stage_running", first)
        os.kill(first.pid, signal.SIGKILL)
        first.wait(timeout=30)
    finally:
        if first.poll() is None:
            first.kill()
    crashed = rig.receipt("preflight")
    assert crashed["status"] == "running" and crashed["pid"] == first.pid
    assert rig.calls() == []  # it died before its child started
    res = rig.run()
    assert res.returncode == 10, res.stderr.decode()
    now = rig.receipt("preflight")
    assert now["status"] == "done" and now["pid"] != first.pid
    assert rig.receipt_names() == ["preflight", "ocr", "audit"]
    assert [c["argv"][0] for c in rig.calls()] == ["doctor", "kb-ocr", "kb-audit", "confirm-page"]


def test_running_live_pid_not_stolen(rig: F.Rig) -> None:
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        rig.receipts_dir.mkdir(parents=True)
        running = {"schema": "klode-kit.receipt/1", "step": "preflight", "status": "running",
                   "started": "2026-10-24T00:00:00Z", "inputs_hash": "0" * 64, "config_hash": "1" * 64,
                   "config_version": 1, "outputs": [], "pid": other.pid}
        path = rig.receipts_dir / "preflight.json"
        path.write_text(json.dumps(running), encoding="utf-8")
        res = rig.run()
        assert res.returncode == 40, res.stderr.decode()
        assert str(other.pid) in res.stderr.decode()
        assert json.loads(path.read_text(encoding="utf-8")) == running  # not touched
        assert rig.calls() == []  # no child ran
        assert rig.receipt_names() == ["preflight"]
    finally:
        other.kill()
        other.wait()


# ---------------------------------------------------------------- failures of children
def test_failed_preflight_is_exit40_and_stops(rig: F.Rig) -> None:
    res = rig.run(env={"FAKE_DOCTOR_EXIT": "40"})
    assert res.returncode == 40
    assert rig.receipt("preflight")["status"] == "failed"
    assert rig.receipt("preflight")["exit_code"] == 40
    assert rig.receipt_names() == ["preflight"]
    assert rig.calls("kb-ocr") == []
    # a failed step is never reused: the next run runs it again and goes on
    res = rig.run()
    assert res.returncode == 10 and len(rig.calls("doctor")) == 2


def test_unknown_child_exit_is_environment_not_a_new_code(rig: F.Rig) -> None:
    res = rig.run(env={"FAKE_DOCTOR_EXIT": "7"})
    assert res.returncode == 40
    assert rig.receipt("preflight")["exit_code"] == 40


# ---------------------------------------------------------------- 09a-08 (kernel part)
def test_env_errors_skip_loops(tmp_path: Path) -> None:
    """A stage that ends in an environment-class error ends the run with that code and never enters a loop:
    no loop receipt, no further stage. (The full build-stage version with the fake claude's authentication
    failure lands with WI-09b.)"""
    rig = F.Rig(tmp_path)
    calls: list[str] = []

    def failing_build(ctx):
        calls.append("build")
        return P.Outcome(reason=P.Reason.auth, detail="claude: not logged in", outputs=[])

    registry = P.default_registry()
    registry["build"] = P.Stage("build", inputs=lambda ctx: {"stub": 1}, run=failing_build)
    # reach build with an already audited, fully verified project: no pending record -> writeback ingests
    code = P.run(rig.project, _all_verified_inbox(rig), stage="build", registry=registry,
                 launcher=rig.wrapper, env=rig.env)
    assert code == 40
    assert calls == ["build"]
    assert rig.receipt("build")["status"] == "failed" and rig.receipt("build")["exit_code"] == 40
    assert not (rig.batch_dir / "pipeline" / "loops").exists()
    assert all("loop" not in json.loads(p.read_text(encoding="utf-8")) for p in rig.receipts_dir.glob("*.json"))


def test_content_gate_failure_without_loops_ends_with_exit20(tmp_path: Path) -> None:
    rig = F.Rig(tmp_path)
    registry = P.default_registry()
    registry["build"] = P.Stage("build", inputs=lambda ctx: {"stub": 2},
                                run=lambda ctx: P.Outcome(reason=P.Reason.content_gate, detail="gate 1", outputs=[]))
    code = P.run(rig.project, _all_verified_inbox(rig), stage="build", registry=registry,
                 launcher=rig.wrapper, env=rig.env)
    assert code == 20  # 09c brings the loop; the kernel only reports
    assert rig.receipt("build")["exit_code"] == 20


def test_unimplemented_stage_is_reported_not_faked(tmp_path: Path) -> None:
    rig = F.Rig(tmp_path)
    code = P.run(rig.project, _all_verified_inbox(rig), stage="build", launcher=rig.wrapper, env=rig.env)
    assert code == 2
    assert not (rig.receipts_dir / "build.json").exists()
    assert not (rig.receipts_dir / "check.json").exists() and not (rig.receipts_dir / "publish.json").exists()


# ---------------------------------------------------------------- the human segment: choices -> writeback -> views
def _choices(rig: F.Rig) -> Path:
    """The human's clicks: every field to confirm gets a voting route (WI-08b export of the written page)."""
    adir = rig.batch_dir / "audit" / "a1"
    html = (adir / "confirm.html").read_bytes()
    from klode_kit.audit import confirm_page as CP

    choices = CP.export_choices(html, W.picks(adir))
    return W.write_choices(rig.project.parent / "choices.json", choices)


def _all_verified_inbox(rig: F.Rig) -> Path:
    """Run the first segment and the human segment so the project is audited, confirmed and written back; the
    inbox is returned for a follow-up call."""
    assert rig.run().returncode == 10
    res = rig.run("--choices", str(_choices(rig)), "--stage", "writeback")
    assert res.returncode == 0, res.stderr.decode()
    return rig.inbox


def test_resume_with_choices_writes_back_and_rebuilds_views(rig: F.Rig) -> None:
    assert rig.run().returncode == 10
    assert not (rig.project / "records").exists()  # nothing is written back before the human decides
    choices = _choices(rig)
    res = rig.run("--choices", str(choices), "--stage", "writeback")
    assert res.returncode == 0, res.stderr.decode()
    assert rig.receipt_names() == ["preflight", "ocr", "audit", "writeback"]
    _all_valid(rig)
    cmds = [c["argv"][0] for c in rig.calls()]
    # audit and its confirm page were reused (done), writeback = kb-audit --writeback (choices, ingest), then views
    assert cmds == ["doctor", "kb-ocr", "kb-audit", "confirm-page", "kb-audit", "kb-audit", "views"]
    wb = [c["argv"] for c in rig.calls("kb-audit")][1:]
    stored = rig.batch_dir / "pipeline" / "choices.json"  # the kernel keeps the human's file next to its receipts
    assert stored.read_bytes() == choices.read_bytes()
    assert any("--writeback" in a and str(stored) in a for a in wb)  # choices mode
    assert any("--writeback" in a and str(stored) not in a for a in wb)  # ingest
    assert list((rig.project / "records" / "_by_id").glob("*.md"))
    assert list(rig.project.glob("records/*.md"))  # the views the writeback stage rebuilt
    assert rig.receipt("writeback")["exit_code"] == 0


def test_resume_without_choices_still_awaits(rig: F.Rig) -> None:
    assert rig.run().returncode == 10
    assert rig.run("--stage", "writeback").returncode == 10
    assert rig.receipt_names() == ["preflight", "ocr", "audit"]
    assert rig.calls("views") == [] and len(rig.calls("kb-audit")) == 1


def test_forged_choices_are_refused_with_30_and_nothing_written(rig: F.Rig) -> None:
    assert rig.run().returncode == 10
    bad = json.loads(_choices(rig).read_text(encoding="utf-8"))
    bad["batch_id"] = "0" * 12
    path = rig.project.parent / "bad.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    res = rig.run("--choices", str(path), "--stage", "writeback")
    assert res.returncode == 30
    assert rig.receipt("writeback")["status"] == "failed"
    assert not (rig.project / "records").exists()
    assert rig.calls("views") == []


# ---------------------------------------------------------------- 09a-10
def test_resume_idempotent(rig: F.Rig) -> None:
    _all_verified_inbox(rig)
    before_receipts = {p.name: p.read_bytes() for p in rig.receipts_dir.glob("*.json")}
    before_calls = len(rig.calls())
    before_tree = rig.tree()
    codes = [rig.run("--resume", "--stage", "writeback").returncode for _ in range(2)]
    assert codes == [0, 0]
    assert {p.name: p.read_bytes() for p in rig.receipts_dir.glob("*.json")} == before_receipts  # no new / changed receipt
    assert len(rig.calls()) == before_calls  # not a single child
    lock = ".pipeline.lock"
    assert {k: v for k, v in rig.tree().items() if not k.endswith(lock)} == \
           {k: v for k, v in before_tree.items() if not k.endswith(lock)}


def test_resume_idempotent_while_awaiting(rig: F.Rig) -> None:
    assert rig.run().returncode == 10
    before = {p.name: p.read_bytes() for p in rig.receipts_dir.glob("*.json")}
    n = len(rig.calls())
    assert [rig.run("--resume").returncode for _ in range(2)] == [10, 10]
    assert {p.name: p.read_bytes() for p in rig.receipts_dir.glob("*.json")} == before
    assert len(rig.calls()) == n
