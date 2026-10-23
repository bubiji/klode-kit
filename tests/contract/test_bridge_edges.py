"""WI-00d1 (added cases): bridge outcome classification, kill escalation, quarantine, CLI exit codes.

Same doubles as test_bridge_run.py (fake ``claude`` behaviour scenarios); paired real path 00d2-01/02.
"""

from __future__ import annotations

import json
import signal
import time
from pathlib import Path

import pytest

from _support.replay import log_entries
from klode_kit import bridge
from klode_kit.config import load

pytestmark = pytest.mark.contract_replay


def _result(structured: dict | None = None, **top) -> str:
    obj = {"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.01}
    if structured is not None:
        obj["structured_output"] = structured
    obj.update(top)
    return json.dumps(obj)


WRITTEN = {"card": "{card}", "status": "written", "anchors_full": 5, "aliases": 10}


def _scenario(tmp: Path, monkeypatch, steps: list[dict]) -> None:
    path = tmp / "scenario.json"
    path.write_text(json.dumps({"steps": steps}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("KLODE_KIT_FAKE_SCENARIO", str(path))


def _task(tmp: Path, task_id: str = "t-000", card: str = "cards_content/c000.md") -> bridge.Task:
    build = tmp / "builds" / "b-1"
    build.mkdir(parents=True, exist_ok=True)
    return bridge.Task(task_id=task_id, build_dir=build, card=card, instructions="合成章节")


def _opts(**kw) -> bridge.Options:
    return bridge.Options.from_config(load(), **kw)


def _one(tmp, **kw) -> dict:
    task = _task(tmp)
    return bridge.run([task], _opts(**kw))["tasks"][0]


@pytest.mark.parametrize("steps, status, error_part", [
    ([{"stdout": _result(WRITTEN, is_error=True, subtype="error_max_budget_usd")}], "failed", "is_error"),
    ([{"stdout": _result({**WRITTEN, "status": "failed"}), "write_files_after": {"{card}": "x"}}], "failed", "writer status"),
    ([{"stdout": _result({**WRITTEN, "card": "cards_content/other.md"})}], "parse_error", "names cards_content/other.md"),
    ([{"stdout": _result(WRITTEN)}], "failed", "card_missing"),
    ([{"stdout": "[1, 2]"}], "parse_error", "not an object"),
    ([{"stdout": _result(None)}], "parse_error", "structured_output"),
])
def test_outcome_classification(tmp_path, monkeypatch, fake_log, steps, status, error_part):
    """Every non-done outcome is retried up to max_rounds and named in the receipt."""
    _scenario(tmp_path, monkeypatch, steps)

    rec = _one(tmp_path, max_rounds=2)

    assert rec["status"] == status
    assert error_part in rec["error"]
    assert rec["attempts"] == 2 == len(log_entries(fake_log))
    assert rec["writer"] is None
    assert rec["cost_usd"] == pytest.approx(0.02 if steps[0]["stdout"].startswith("{") else 0.0)


def test_failed_attempt_quarantines_card_and_restores_previous(tmp_path, monkeypatch):
    """A card changed by an attempt that did not finish done is moved aside; the earlier content comes back."""
    _scenario(tmp_path, monkeypatch, [{"write_files": {"{card}": "半截内容"}, "stdout": "", "exit_code": 1}])
    task = _task(tmp_path)
    card = task.build_dir / task.card
    card.parent.mkdir(parents=True)
    card.write_text("旧卡", encoding="utf-8")

    rec = bridge.run([task], _opts(max_rounds=1))["tasks"][0]

    assert rec["status"] == "failed"
    assert card.read_text(encoding="utf-8") == "旧卡"
    moved = list((task.build_dir / "_quarantine").iterdir())
    assert [p.read_text(encoding="utf-8") for p in moved] == ["半截内容"]


def test_failed_attempt_leaves_untouched_card_alone(tmp_path, monkeypatch):
    _scenario(tmp_path, monkeypatch, [{"stdout": "", "exit_code": 1}])
    task = _task(tmp_path)
    card = task.build_dir / task.card
    card.parent.mkdir(parents=True)
    card.write_text("旧卡", encoding="utf-8")

    bridge.run([task], _opts(max_rounds=1))

    assert card.read_text(encoding="utf-8") == "旧卡"
    assert not (task.build_dir / "_quarantine").exists()


def test_timeout_escalates_to_sigkill_when_sigterm_ignored(tmp_path, monkeypatch):
    """A group that ignores SIGTERM is SIGKILLed after kill_grace_s; child and grandchild both gone."""
    child_file, grand_file = tmp_path / "child.pids", tmp_path / "grand.pids"
    _scenario(tmp_path, monkeypatch, [{"ignore_sigterm": True, "pid_file": str(child_file),
                                       "spawn_child_pid_file": str(grand_file), "sleep_s": 3600}])
    task = _task(tmp_path)

    t0 = time.monotonic()
    rec = bridge.run([task], _opts(timeout_s=1, kill_grace_s=0.5))["tasks"][0]

    assert rec["status"] == "timeout"
    assert time.monotonic() - t0 < 10
    pids = [int(x) for x in child_file.read_text().split() + grand_file.read_text().split()]
    assert len(pids) == 2
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and any(_alive(p) for p in pids):
        time.sleep(0.05)
    assert not any(_alive(p) for p in pids)


def _alive(pid: int) -> bool:
    import os

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


# --------------------------------------------------------------------------- CLI


@pytest.fixture
def keep_sigterm():
    old = signal.getsignal(signal.SIGTERM)
    yield
    signal.signal(signal.SIGTERM, old)


def _tasks_file(tmp: Path, tasks: list[bridge.Task]) -> Path:
    spec = tmp / "tasks.json"
    spec.write_text(json.dumps({"tasks": [{"task_id": t.task_id, "build_dir": str(t.build_dir), "card": t.card,
                                           "instructions": t.instructions} for t in tasks]}), encoding="utf-8")
    return spec


def test_cli_exit_codes(tmp_path, monkeypatch, keep_sigterm):
    """0 all done; 20 a task not done (receipt still written); 2 usage; 40 configuration."""
    ok, bad = _task(tmp_path, "t-ok", "cards_content/ok.md"), _task(tmp_path, "t-bad", "cards_content/bad.md")
    scen = tmp_path / "scenario.json"
    scen.write_text(json.dumps({"by_card": {
        ok.card: [{"stdout": _result(WRITTEN), "write_files_after": {"{card}": "卡"}}],
        bad.card: [{"stdout": "", "exit_code": 1}]}}), encoding="utf-8")
    monkeypatch.setenv("KLODE_KIT_FAKE_SCENARIO", str(scen))
    receipt = tmp_path / "out" / "r.json"

    assert bridge.main(["run", "--tasks", str(_tasks_file(tmp_path, [ok])), "--receipt", str(receipt)]) == 0
    assert json.loads(receipt.read_text())["status"] == "done"

    code = bridge.main(["run", "--tasks", str(_tasks_file(tmp_path, [ok, bad])), "--receipt", str(receipt),
                        "--max-rounds", "1", "--concurrency", "2", "--max-budget-usd", "1.5"])
    assert code == 20
    written = json.loads(receipt.read_text())
    assert written["status"] == "partial" and written["max_budget_usd"] == 1.5

    assert bridge.main(["run"]) == 2
    missing = tmp_path / "nope.json"
    assert bridge.main(["run", "--tasks", str(missing), "--receipt", str(receipt)]) == 2
    dup = _tasks_file(tmp_path, [ok, ok])
    assert bridge.main(["run", "--tasks", str(dup), "--receipt", str(receipt)]) == 2
    project = tmp_path / "proj"
    project.mkdir()
    (project / "klode-kit.toml").write_text("[cards]\nwriter_concurrency = \"four\"\n", encoding="utf-8")
    assert bridge.main(["run", "--tasks", str(_tasks_file(tmp_path, [ok])), "--receipt", str(receipt),
                        "--project", str(project)]) == 40
