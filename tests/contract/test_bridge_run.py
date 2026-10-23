"""WI-00d1: execution bridge run - concurrency, timeout (process group), cancel, retry, receipt (00d1-03..10).

Real subprocesses: the fake ``claude`` (tests/fakes/bin, first on PATH) runs a behaviour scenario named by
``KLODE_KIT_FAKE_SCENARIO``. Process liveness is checked with ``os.kill(pid, 0)`` (L4), overlap with the
fake's own timestamps (L2), outcomes with the bridge receipt (L1). Paired real path: 00d2-01/02.

Law (matrix): running count <= concurrency limit (S4, 00d1-06); after a timeout or a cancel no child or
grandchild process survives (S3, 00d1-04/07).
"""

from __future__ import annotations

import itertools
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from _support.replay import log_entries
from klode_kit import bridge
from klode_kit.config import load
from klode_kit.contract import schema as contract

pytestmark = pytest.mark.contract_replay

REPO = Path(__file__).resolve().parents[2]
_seq = itertools.count()


def result_json(cost: float = 0.0123, anchors: int = 5, aliases: int = 10) -> str:
    """What `claude -p --output-format json --json-schema ...` prints on success (shape of tests/recordings)."""
    return json.dumps({
        "type": "result", "subtype": "success", "is_error": False, "num_turns": 4,
        "total_cost_usd": cost, "permission_denials": [],
        "result": "card written",
        "structured_output": {"card": "{card}", "status": "written", "anchors_full": anchors, "aliases": aliases},
    })


def ok_step(**extra) -> dict:
    step = {"write_files_after": {"{card}": "# 合成卡\n\n示例片 10mg 2026-09-18\n"}, "stdout": result_json()}
    step.update(extra)
    return step


MALFORMED = {"stdout": '{"type": "result", "structured_output": {"card": ', "exit_code": 0}


def scenario(tmp: Path, monkeypatch, data: dict) -> Path:
    path = tmp / f"scenario-{next(_seq)}.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("KLODE_KIT_FAKE_SCENARIO", str(path))
    return path


def tasks_in(root: Path, n: int, build: str = "b-20261023-0001") -> list[bridge.Task]:
    build_dir = root / "builds" / build
    build_dir.mkdir(parents=True, exist_ok=True)
    return [bridge.Task(task_id=f"t-{i:03d}", build_dir=build_dir, card=f"cards_content/c{i:03d}.md",
                        instructions=f"合成章节 {i}") for i in range(n)]


def opts(**kw) -> bridge.Options:
    return bridge.Options.from_config(load(), **kw)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def gone_within(pids: list[int], seconds: float = 5.0) -> list[int]:
    """Pids still alive after ``seconds`` (zombies are reaped by their parent / launchd meanwhile)."""
    deadline = time.monotonic() + seconds
    left = list(pids)
    while left and time.monotonic() < deadline:
        left = [p for p in left if alive(p)]
        if left:
            time.sleep(0.05)
    return left


def pids(path: Path) -> list[int]:
    return [int(x) for x in path.read_text().split()] if path.exists() else []


def by_id(receipt: dict) -> dict[str, dict]:
    return {t["task_id"]: t for t in receipt["tasks"]}


# --------------------------------------------------------------------------- success


def test_success_writes_receipt_with_cost(tmp_path, monkeypatch):
    """00d1-03: done; the writer receipt passes the WI-00b1 writer_receipt schema; cost_usd numeric; card exists."""
    scenario(tmp_path, monkeypatch, {"steps": [ok_step()]})
    [task] = tasks_in(tmp_path, 1)
    receipt_path = tmp_path / "work" / "bridge.json"

    receipt = bridge.run([task], opts(), receipt_path=receipt_path)

    rec = by_id(receipt)[task.task_id]
    assert rec["status"] == "done"
    assert rec["attempts"] == 1
    assert rec["exit_code"] == 0
    contract.validate("writer_receipt", rec["writer"])
    assert rec["writer"]["card"] == task.card
    assert isinstance(rec["cost_usd"], float) and rec["cost_usd"] == pytest.approx(0.0123)
    assert isinstance(receipt["cost_usd"], float) and receipt["cost_usd"] == pytest.approx(0.0123)
    assert (task.build_dir / task.card).is_file()
    assert receipt["task_count"] == 1 and receipt["status"] == "done"
    assert json.loads(receipt_path.read_text(encoding="utf-8")) == receipt


def test_success_runs_in_build_dir(tmp_path, monkeypatch, fake_log):
    """The writer process runs with cwd = builds/<build_id>/ (R2-06 relative path rules)."""
    scenario(tmp_path, monkeypatch, {"steps": [ok_step()]})
    [task] = tasks_in(tmp_path, 1)

    bridge.run([task], opts())

    calls = log_entries(fake_log)
    assert [Path(c["cwd"]).resolve() for c in calls] == [task.build_dir.resolve()]
    assert not any("--force" in a for c in calls for a in c["argv"])


# --------------------------------------------------------------------------- timeout


def test_timeout_kills_process_group(tmp_path, monkeypatch):
    """00d1-04: on timeout the whole process group dies - child and grandchild are ESRCH; receipt timeout."""
    child_file, grand_file = tmp_path / "child.pids", tmp_path / "grand.pids"
    scenario(tmp_path, monkeypatch, {"steps": [{"pid_file": str(child_file), "spawn_child_pid_file": str(grand_file),
                                                "sleep_s": 3600}]})
    [task] = tasks_in(tmp_path, 1)

    t0 = time.monotonic()
    receipt = bridge.run([task], opts(timeout_s=1))
    elapsed = time.monotonic() - t0

    assert by_id(receipt)[task.task_id]["status"] == "timeout"
    assert by_id(receipt)[task.task_id]["attempts"] == 1
    assert elapsed < 15
    children, grandchildren = pids(child_file), pids(grand_file)
    assert len(children) == 1 and len(grandchildren) == 1
    assert gone_within(children + grandchildren) == []


def test_timeout_boundary(tmp_path, monkeypatch):
    """00d1-05: timeout 1 s - a 0.5 s writer is done, a 1.5 s writer is timeout."""
    fast, slow = tasks_in(tmp_path, 2)
    scenario(tmp_path, monkeypatch, {"by_card": {fast.card: [ok_step(sleep_s=0.5)],
                                                 slow.card: [ok_step(sleep_s=1.5)]}})

    receipt = bridge.run([fast, slow], opts(timeout_s=1))

    assert by_id(receipt)[fast.task_id]["status"] == "done"
    assert by_id(receipt)[slow.task_id]["status"] == "timeout"
    assert not (slow.build_dir / slow.card).exists()


# --------------------------------------------------------------------------- concurrency (law, S4)


def max_overlap(stamps: Path) -> int:
    events = [json.loads(line) for line in stamps.read_text().splitlines()]
    timeline = sorted(((e["t"], 1 if e["event"] == "start" else -1) for e in events), key=lambda x: (x[0], x[1]))
    cur = best = 0
    for _, delta in timeline:
        cur += delta
        best = max(best, cur)
    return best


@settings(max_examples=8, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(n=st.integers(min_value=1, max_value=12), limit=st.integers(min_value=1, max_value=4))
def test_concurrency_bound(tmp_path, monkeypatch, n, limit):
    """00d1-06: max overlap <= limit; with n >= limit the overlap reaches the limit (not serial)."""
    root = tmp_path / f"ex-{next(_seq)}"
    root.mkdir()
    stamps = root / "stamps.jsonl"
    scenario(root, monkeypatch, {"steps": [ok_step(stamps=str(stamps), sleep_s=0.4)]})
    tasks = tasks_in(root, n)

    receipt = bridge.run(tasks, opts(concurrency=limit))

    assert [t["status"] for t in receipt["tasks"]] == ["done"] * n
    overlap = max_overlap(stamps)
    assert overlap <= limit
    assert overlap == min(n, limit)


# --------------------------------------------------------------------------- cancel


def test_cancel_propagates(tmp_path, monkeypatch):
    """00d1-07: SIGINT to the parent while 10 writers run -> every child and grandchild ESRCH, in-flight
    receipts cancelled, no half-written card left under cards_content/."""
    child_file, grand_file = tmp_path / "child.pids", tmp_path / "grand.pids"
    scenario(tmp_path, monkeypatch, {"steps": [{"pid_file": str(child_file), "spawn_child_pid_file": str(grand_file),
                                                "write_files": {"{card}": "# 合成卡（写了一半"}, "sleep_s": 3600}]})
    tasks = tasks_in(tmp_path, 10)
    spec = tmp_path / "tasks.json"
    spec.write_text(json.dumps({"tasks": [{"task_id": t.task_id, "build_dir": str(t.build_dir), "card": t.card,
                                           "instructions": t.instructions} for t in tasks]}, ensure_ascii=False))
    receipt_path = tmp_path / "work" / "bridge.json"
    env = dict(os.environ, PYTHONPATH=str(REPO / "src"))
    parent = subprocess.Popen([sys.executable, "-m", "klode_kit.bridge", "run", "--tasks", str(spec),
                               "--receipt", str(receipt_path), "--concurrency", "10", "--timeout-s", "600"],
                              env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 30
        while (len(pids(grand_file)) < 10 or len(pids(child_file)) < 10) and time.monotonic() < deadline:
            assert parent.poll() is None, parent.communicate()
            time.sleep(0.05)
        assert len(pids(child_file)) == 10 and len(pids(grand_file)) == 10
        time.sleep(0.2)  # the partial cards are on disk

        parent.send_signal(signal.SIGINT)
        out, err = parent.communicate(timeout=30)
    finally:
        if parent.poll() is None:
            parent.kill()

    assert parent.returncode == 130, err.decode()
    assert gone_within(pids(child_file) + pids(grand_file)) == []
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "cancelled"
    assert sorted(by_id(receipt)) == sorted(t.task_id for t in tasks)
    assert {t["status"] for t in receipt["tasks"]} == {"cancelled"}
    assert list((tasks[0].build_dir / "cards_content").rglob("*.md")) == []


# --------------------------------------------------------------------------- retry


def test_malformed_retry_exhausted(tmp_path, monkeypatch, fake_log):
    """00d1-08: malformed JSON every time -> attempts == writer_max_rounds (3), status parse_error."""
    scenario(tmp_path, monkeypatch, {"steps": [MALFORMED]})
    [task] = tasks_in(tmp_path, 1)

    receipt = bridge.run([task], opts())

    rec = by_id(receipt)[task.task_id]
    assert rec["status"] == "parse_error"
    assert rec["attempts"] == 3
    assert len(log_entries(fake_log)) == 3
    assert isinstance(rec["cost_usd"], float)
    assert rec["cost_complete"] is False


def test_malformed_then_success(tmp_path, monkeypatch):
    """00d1-09: malformed first, success second -> done after 2 attempts, exactly one card."""
    scenario(tmp_path, monkeypatch, {"steps": [MALFORMED, ok_step()]})
    [task] = tasks_in(tmp_path, 1)

    receipt = bridge.run([task], opts())

    rec = by_id(receipt)[task.task_id]
    assert rec["status"] == "done"
    assert rec["attempts"] == 2
    assert [a["status"] for a in rec["attempts_log"]] == ["parse_error", "done"]
    assert [p.name for p in (task.build_dir / "cards_content").rglob("*.md")] == ["c000.md"]


def test_structured_output_violating_writer_schema_is_parse_error(tmp_path, monkeypatch):
    """Added: valid JSON whose structured_output breaks writer_receipt (card outside cards_content) is retried."""
    bad = json.dumps({"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.01,
                      "structured_output": {"card": "library/x.md", "status": "written", "anchors_full": 1,
                                            "aliases": 1}})
    scenario(tmp_path, monkeypatch, {"steps": [{"stdout": bad}]})
    [task] = tasks_in(tmp_path, 1)

    rec = by_id(bridge.run([task], opts(max_rounds=2)))[task.task_id]

    assert (rec["status"], rec["attempts"]) == ("parse_error", 2)
    assert rec["cost_usd"] == pytest.approx(0.02)
    assert rec["cost_complete"] is True


def test_exit1_failure_recorded(tmp_path, monkeypatch, fake_log):
    """00d1-10: exit 1 -> status failed, exit_code 1; attempts never exceed the limit."""
    scenario(tmp_path, monkeypatch, {"steps": [{"stdout": "", "stderr": "boom\n", "exit_code": 1}]})
    [task] = tasks_in(tmp_path, 1)

    receipt = bridge.run([task], opts())

    rec = by_id(receipt)[task.task_id]
    assert rec["status"] == "failed"
    assert rec["exit_code"] == 1
    assert 1 <= rec["attempts"] <= 3
    assert len(log_entries(fake_log)) == rec["attempts"]
    assert receipt["status"] == "partial"
