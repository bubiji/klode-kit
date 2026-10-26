"""WI-08e: crash safety of ``kb-audit --reopen`` and of the commit of its page - contract_replay lane (matrix 08e-12).

Same method as 08c-13 / 09d-02, no mock: the real CLI is SIGKILLed at a crash point (``KLODE_KIT_CRASH_AT``) and run
again; the products must equal those of an uninterrupted run (wall-clock stamps normalised).

- reopen: its own five points plus the shared project lock (``writeback.lock``); the event line is never doubled;
  a log line without its page is completed by the next reopen. The reopen flow, run normally under the audit hook,
  makes at most one durable write between two crash points (00s-16) and reaches exactly the listed points.
- commit of the reopen page: the writeback's points (``flows.WRITEBACK_CRASHPOINTS``; ``writeback.mkdir`` is not on
  this path, ``records/`` exists), then rerun == uninterrupted; every begin has its end.
- a SIGKILL at a random moment (S4).
"""

from __future__ import annotations

import signal
import subprocess
import tempfile
import time
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from _support import reopen_fx as RF
from _support import writeback_fx as W
from _support.flows import WRITEBACK_CRASHPOINTS, audit_env, base_env
from _support.reopen_fx import AUTO_REC, HB, NEW_HB, base, template  # noqa: F401
from klode_kit.audit import reopen as RO
from klode_kit.contract import schema
from klode_kit.devtools.audit import partition, read_log

pytestmark = pytest.mark.contract_replay

REOPEN_POINTS = frozenset(RO.CRASHPOINTS) | {"writeback.lock"}
COMMIT_POINTS = sorted(WRITEBACK_CRASHPOINTS - {"writeback.mkdir"})


def state_of(b: RF.Base) -> dict[str, bytes]:
    """Every file of the project, wall-clock stamps normalised; the per-run receipt log is left out (one line per run,
    by design: a recovered run has a line of its own, as in 08c-13)."""
    return W.normalised({k: v for k, v in RF.project_files(b.project).items()
                         if not k.endswith("writeback_receipts.jsonl")})


@pytest.fixture(scope="module")
def reopen_reference(template, tmp_path_factory) -> dict[str, bytes]:
    b = RF.copy(template, tmp_path_factory.mktemp("reopen-ref"))
    res = RF.cli(b.project, HB)
    assert res.returncode == 10, res.stderr.decode()
    return state_of(b)


@pytest.fixture(scope="module")
def commit_reference(template, tmp_path_factory) -> dict[str, bytes]:
    root = tmp_path_factory.mktemp("commit-ref")
    b = RF.copy(template, root)
    choices = prepared_choices(b, root)
    res = W.cli(b.project, choices, base_env())
    assert res.returncode == 0, res.stderr.decode()
    return state_of(b)


def prepared_choices(b: RF.Base, root: Path) -> Path:
    code, rc, err = RF.reopen(b.project, HB)
    assert code == 10, err
    return W.write_choices(root / "choices.json", RF.choose(b.project, rc, "manual", value=NEW_HB))


def consistent(b: RF.Base) -> None:
    for path in sorted((b.project / "records" / "_by_id").glob("*.md")):
        front = W.front(b.project, path.stem)
        assert schema.issues("record", front) == [], path
        eff = front.get("effective")
        assert front["status"] == (eff["status"] if eff else front["candidates"][-1]["status"])
        for c in front["candidates"]:
            body = W.body(b.project, path.stem, c["rev"])
            assert (body["rev"], body["audit_rev"]) == (c["rev"], c["audit_rev"])


def all_committed(b: RF.Base) -> None:
    for adir in W.revisions(b.project / "work" / b.batch):
        marks = W.commits(adir)
        begins = {m["commit_id"] for m in marks if m["op"] == "begin"}
        ends = {m["commit_id"] for m in marks if m["op"] == "end"}
        assert begins == ends and {r["commit_id"] for r in W.fixes(adir)} <= ends
    assert not [p for p in (b.project / "records").rglob("*") if p.name.endswith(".tmp")]
    assert not [p for p in (b.adir / "reopen").glob("*") if p.name.endswith(".tmp")]


# --------------------------------------------------------------------------- the reopen itself


@pytest.mark.parametrize("point", sorted(REOPEN_POINTS))
def test_reopen_crash_then_rerun_equals_uninterrupted(base, reopen_reference, point):
    """08e-12 (S5): SIGKILL at ``point``; the rerun leaves exactly the uninterrupted products - one event line, one
    page, no temporary file, no change in ``records/``."""
    res = RF.cli(base.project, HB, base_env({"KLODE_KIT_CRASH_AT": point}))
    assert res.returncode == -signal.SIGKILL, (point, res.returncode, res.stderr.decode())
    assert len(RF.log(base.adir)) <= 1
    again = RF.cli(base.project, HB)
    assert again.returncode == 10, again.stderr.decode()
    assert len(RF.log(base.adir)) == 1 and len(RF.pages(base.adir)) == 1
    all_committed(base)
    assert state_of(base) == reopen_reference


def test_reopen_flow_partition_holds(base, tmp_path):
    """08e-12 (S5, 00s-16): under the audit hook the reopen flow has at most one durable write between two crash points
    and reaches exactly the listed crash points."""
    log = tmp_path / "audit.jsonl"
    res = subprocess.run(RF.argv(base.project, HB), env=audit_env(log), capture_output=True, timeout=120)
    assert res.returncode == 10, res.stderr.decode()
    p = partition(read_log(log), [base.project])
    assert sum(len(s.writes) for s in p.segments) >= 4
    assert p.violations == []
    assert p.crashpoint_names == REOPEN_POINTS


# --------------------------------------------------------------------------- the commit of the page


@pytest.mark.parametrize("point", COMMIT_POINTS)
def test_commit_of_reopen_choices_crash_then_rerun(template, tmp_path, commit_reference, point):
    """08e-12 (S5): the writeback commit of the reopen page, SIGKILLed at each of its points, completes on rerun and
    equals the uninterrupted commit; never half."""
    b = RF.copy(template, tmp_path)
    choices = prepared_choices(b, tmp_path)
    res = W.cli(b.project, choices, base_env({"KLODE_KIT_CRASH_AT": point}))
    assert res.returncode == -signal.SIGKILL, (point, res.returncode, res.stderr.decode())
    consistent(b)
    again = W.cli(b.project, choices, base_env())
    assert again.returncode == 0, again.stderr.decode()
    consistent(b)
    all_committed(b)
    assert W.front(b.project, AUTO_REC)["effective"]["rev"] == 2
    assert RF.values(b.project, AUTO_REC, 2)[HB] == NEW_HB
    assert state_of(b) == commit_reference


@settings(max_examples=8, deadline=None, suppress_health_check=[HealthCheck.too_slow,
                                                               HealthCheck.function_scoped_fixture])
@given(delay=st.floats(min_value=0.0, max_value=0.8))
def test_random_sigkill_during_reopen_and_commit(template, reopen_reference, commit_reference, delay):
    """08e-12 (S4): SIGKILL at a random moment of the reopen, then of the commit; reruns end where the uninterrupted
    runs end."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        b = RF.copy(template, root)
        proc = subprocess.Popen(RF.argv(b.project, HB), env=base_env(), stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        time.sleep(delay)
        if proc.poll() is None:
            proc.send_signal(signal.SIGKILL)
        proc.communicate(timeout=120)
        again = RF.cli(b.project, HB)
        assert again.returncode == 10, again.stderr.decode()
        assert state_of(b) == reopen_reference

        choices = W.write_choices(root / "choices.json", RF.choose(b.project, RF.receipt_of(again), "manual",
                                                                    value=NEW_HB))
        proc = subprocess.Popen(W.cli_argv(b.project, choices), env=base_env(), stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        time.sleep(delay)
        if proc.poll() is None:
            proc.send_signal(signal.SIGKILL)
        proc.communicate(timeout=120)
        final = W.cli(b.project, choices, base_env())
        assert final.returncode == 0, final.stderr.decode()
        all_committed(b)
        assert state_of(b) == commit_reference
