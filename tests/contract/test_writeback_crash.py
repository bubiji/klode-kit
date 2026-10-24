"""WI-08c: crash injection at every write point of the writeback (matrix 08c-13; section 0.3; plan WI-08c
"崩溃恢复").

The enumeration list is ``_support.flows.WRITEBACK_CRASHPOINTS`` - the same list 00s-16 proves complete (every
segment of the normal flow, head and tail included, makes at most one durable write). For each point the real CLI
``klode-kit kb-audit --writeback`` is SIGKILLed there (``KLODE_KIT_CRASH_AT``, test seams), then run again:

- right after the crash the records are readable and consistent (front matter legal, top status mirrors
  effective / latest candidate, every revision a record names has its body);
- after the rerun the products equal those of an uninterrupted run (wall-clock stamps normalised): the commit was
  either completed or never started - never half - and every commit_id has its end marker;
- the hypothesis case kills the process at a random moment instead (S4).

No mock: the crash is a real SIGKILL of a real process.
"""

from __future__ import annotations

import json
import signal
import subprocess
import tempfile
import time
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from _support import writeback_fx as W
from _support.flows import WRITEBACK_CRASHPOINTS, base_env, writeback_setup
from klode_kit.contract import schema

pytestmark = pytest.mark.contract_replay

_REFERENCE: dict[str, bytes] = {}


def reference(root: Path) -> dict[str, bytes]:
    """Products of one uninterrupted run (computed once per session; the inputs are fixed fixtures)."""
    if not _REFERENCE:
        project, b, _, choices = writeback_setup(root)
        res = W.cli(project, choices, base_env())
        assert res.returncode == 0, res.stderr.decode()
        _REFERENCE.update(W.state(project, b))
    return _REFERENCE


def consistent(project: Path, b: str) -> None:
    root = project / "records" / "_by_id"
    for path in sorted(root.glob("*.md")) if root.is_dir() else []:
        front = W.front(project, path.stem)
        assert schema.issues("record", front) == [], (path, schema.issues("record", front))
        eff = front.get("effective")
        assert front["status"] == (eff["status"] if eff else front["candidates"][-1]["status"])
        for c in front["candidates"]:
            body = W.body(project, path.stem, c["rev"])
            assert (body["rev"], body["audit_rev"]) == (c["rev"], c["audit_rev"])


def committed_all(project: Path, b: str) -> None:
    for adir in W.revisions(project / "work" / b):
        ends = {m["commit_id"] for m in W.commits(adir) if m["op"] == "end"}
        begins = {m["commit_id"] for m in W.commits(adir) if m["op"] == "begin"}
        assert {r["commit_id"] for r in W.fixes(adir)} <= ends
        assert begins == ends
    for path in (project / "records" / "_by_id").glob("*.md"):
        front = W.front(project, path.stem)
        assert front["status"] == front["effective"]["status"] == "verified"
    assert not [p for p in (project / "records").rglob("*") if p.name.endswith(".tmp")]


@pytest.mark.parametrize("point", sorted(WRITEBACK_CRASHPOINTS))
def test_crash_injection_consistency(tmp_path, point):
    """08c-13 (S5): crash at ``point`` (first pass), check consistency, rerun, compare with an uninterrupted run."""
    ref = reference(tmp_path / "ref")
    project, b, _, choices = writeback_setup(tmp_path / "crash")
    res = W.cli(project, choices, base_env({"KLODE_KIT_CRASH_AT": point}))
    assert res.returncode == -signal.SIGKILL, (point, res.returncode, res.stderr.decode())
    consistent(project, b)
    again = W.cli(project, choices, base_env())
    assert again.returncode == 0, again.stderr.decode()
    consistent(project, b)
    committed_all(project, b)
    assert W.state(project, b) == ref


def test_crash_recovered_by_any_writeback_run(tmp_path):
    """08c-13 companion (S5): a commit cut after its decisions were appended is completed by the next writeback of
    the batch even when that run carries no choices (ingest mode); the receipt names the recovered commit."""
    project, b, adir, choices = writeback_setup(tmp_path / "p")
    res = W.cli(project, choices, base_env({"KLODE_KIT_CRASH_AT": "writeback.fixes_appended"}))
    assert res.returncode == -signal.SIGKILL
    pending = {r["commit_id"] for r in W.fixes(adir)}
    assert len(pending) == 1
    again = W.cli(project, None, base_env(), batch_id=b)
    assert again.returncode in (0, 10), again.stderr.decode()
    receipt = json.loads(again.stdout.decode().strip().splitlines()[-1])
    assert receipt["recovered"] == sorted(pending)
    # user-approved 2026-10-24: ingest also writes never-written records as new candidates, so pending is a subset
    assert pending <= {m["commit_id"] for m in W.commits(adir) if m["op"] == "end"}
    consistent(project, b)


@settings(max_examples=8, deadline=None, suppress_health_check=[HealthCheck.too_slow,
                                                                HealthCheck.function_scoped_fixture])
@given(delay=st.floats(min_value=0.0, max_value=0.8))
def test_crash_random_sigkill(tmp_path, delay):
    """08c-13 (S4): SIGKILL at a random moment, then rerun: the products equal an uninterrupted run's."""
    ref = reference(tmp_path / "ref")
    with tempfile.TemporaryDirectory() as tmp:
        project, b, _, choices = writeback_setup(Path(tmp))
        proc = subprocess.Popen(W.cli_argv(project, choices), env=base_env(), stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        time.sleep(delay)
        if proc.poll() is None:
            proc.send_signal(signal.SIGKILL)
        proc.communicate(timeout=120)
        consistent(project, b)
        again = W.cli(project, choices, base_env())
        assert again.returncode == 0, again.stderr.decode()
        committed_all(project, b)
        assert W.state(project, b) == ref


def test_crash_recovered_then_ingest_completes_pending(tmp_path):
    """08c-13 companion (S5), added while implementing: same cut as above; the ingest run first completes the
    pending commit (its end marker appears, the record is verified by the human choice) and then ingests the
    batch's other record as a new commit of its own - the pending commit is never redone or duplicated."""
    project, b, adir, choices = writeback_setup(tmp_path / "p")
    res = W.cli(project, choices, base_env({"KLODE_KIT_CRASH_AT": "writeback.fixes_appended"}))
    assert res.returncode == -signal.SIGKILL
    pending = {r["commit_id"] for r in W.fixes(adir)}
    (rec_id,) = {r["field_id"].split("/")[0] for r in W.fixes(adir)}
    decisions = len(W.fixes(adir))
    again = W.cli(project, None, base_env(), batch_id=b)
    assert again.returncode == 10, again.stderr.decode()
    receipt = json.loads(again.stdout.decode().strip().splitlines()[-1])
    assert receipt["recovered"] == sorted(pending)
    ends = [m["commit_id"] for m in W.commits(adir) if m["op"] == "end"]
    assert pending <= set(ends) and len(ends) == len(set(ends)) == 2
    assert len(W.fixes(adir)) == decisions  # the other record's pending candidate adds no decision
    assert W.front(project, rec_id)["effective"]["verified_by"] == "human"
    others = [r for r in receipt["per_record"] if r["result"] == "committed"]
    assert [r["status"] for r in others] == ["pending_confirm"] and others[0]["rec_id"] != rec_id
    consistent(project, b)
