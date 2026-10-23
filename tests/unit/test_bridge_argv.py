"""WI-00d1: execution bridge, deterministic argv contract (matrix 00d1-01, 00d1-02, 00d1-11).

The argv is checked as data (L1): it is what the real ``claude -p`` receives (paired host test 00d2-01/02).
Flag semantics checked against the Claude Code docs (cli-reference, permissions "Read and Edit", sub-agents
"CLI-defined subagents"); see the WI-00d1 report.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from _support.replay import log_entries
from klode_kit import bridge
from klode_kit.config import load
from klode_kit.contract import schema as contract

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]


def _task(tmp_path: Path, task_id: str = "t-001", card: str = "cards_content/ch01-intro.md") -> bridge.Task:
    build = tmp_path / "builds" / "b-20261023-0001"
    build.mkdir(parents=True, exist_ok=True)
    return bridge.Task(task_id=task_id, build_dir=build, card=card, instructions="合成章节 1：示例片 10mg")


def _opts(**kw) -> bridge.Options:
    return bridge.Options.from_config(load(), **kw)


def _value(argv: list[str], flag: str) -> str:
    assert argv.count(flag) == 1, f"{flag} must appear exactly once: {argv}"
    return argv[argv.index(flag) + 1]


def test_argv_contract(tmp_path):
    """00d1-01: the CLI-bridge argv of design r2.5 as amended by R2-06; cwd is builds/<build_id>/."""
    task = _task(tmp_path)
    argv, cwd = bridge.build_argv(task, _opts())

    assert argv[0] == "claude"
    assert argv[1] == "-p"
    agents = json.loads(_value(argv, "--agents"))
    writer = agents["klode-card-writer"]
    assert writer["description"].strip()
    assert "cards_content/" in writer["prompt"]
    assert _value(argv, "--agent") == "klode-card-writer"
    assert _value(argv, "--permission-mode") == "dontAsk"
    assert _value(argv, "--allowedTools") == "Read,Grep,Glob,Edit(cards_content/**),Bash(*/bin/klode-kit *)"
    assert _value(argv, "--disallowedTools") == "Edit(library/**),Edit(src/**),Edit(.claude/**)"
    assert _value(argv, "--output-format") == "json"
    # approved change 2026-10-23 (WI-00d2 F-1): the CLI rejects the draft 2020-12 declaration, so $schema/$id are dropped
    assert json.loads(_value(argv, "--json-schema")) == {k: v for k, v in contract.load("writer_receipt").items()
                                                         if k not in ("$schema", "$id")}
    budget = float(_value(argv, "--max-budget-usd"))
    assert budget > 0
    assert argv.count("--no-session-persistence") == 1
    # the prompt is the last argument and comes after a boolean flag, so no variadic tool list swallows it
    assert argv[-2] == "--no-session-persistence"
    assert task.card in argv[-1] and task.task_id in argv[-1]
    assert Path(cwd) == task.build_dir
    assert Path(cwd).parent.name == "builds"
    assert not any("--force" in a for a in argv)


def test_argv_agents_json_generated_from_agents_dir(tmp_path):
    """00d1-01 (added): --agents is generated from agents/*.md, not hard-coded - a second agent file appears."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "klode-card-writer.md").write_text(
        "---\nname: klode-card-writer\ndescription: synthetic writer\ntools: Read, Edit\nmodel: opus\n---\n\nWrite cards_content/ only.\n",
        encoding="utf-8")
    (agents_dir / "other-helper.md").write_text(
        "---\nname: other-helper\ndescription: synthetic helper\n---\nHelp.\n", encoding="utf-8")

    argv, _ = bridge.build_argv(_task(tmp_path), _opts(agents_dir=agents_dir))

    agents = json.loads(_value(argv, "--agents"))
    assert set(agents) == {"klode-card-writer", "other-helper"}
    assert agents["klode-card-writer"] == {"description": "synthetic writer", "prompt": "Write cards_content/ only.",
                                           "tools": ["Read", "Edit"], "model": "opus"}
    assert agents["other-helper"] == {"description": "synthetic helper", "prompt": "Help."}


def test_allowed_tools_use_edit_not_write(tmp_path):
    """00d1-02: no Write( path rule (H11 - never consulted); every checker call goes through the launcher."""
    argv, _ = bridge.build_argv(_task(tmp_path), _opts())

    joined = "\n".join(argv)
    assert "Write(" not in joined
    rules = _value(argv, "--allowedTools").split(",") + _value(argv, "--disallowedTools").split(",")
    bash_rules = [r for r in rules if r.startswith("Bash(")]
    assert bash_rules == ["Bash(*/bin/klode-kit *)"]
    assert "python3 -m" not in joined and "python -m" not in joined
    assert ".venv/bin/" not in joined
    prompt = argv[-1]
    launcher_mentions = [w for w in prompt.split() if "klode-kit" in w]
    assert launcher_mentions, prompt
    assert all(w.strip("\"'`").endswith("/bin/klode-kit") for w in launcher_mentions), launcher_mentions


@pytest.mark.parametrize("card", ["library/x.md", "cards_content/../library/x.md", "cards_content/x.txt", "/abs/cards_content/x.md"])
def test_task_card_outside_cards_content_rejected(tmp_path, card):
    """Guard (added): a task may only name a card under cards_content/ (writer_receipt card pattern)."""
    with pytest.raises(bridge.BridgeUsageError):
        _task(tmp_path, card=card)


def test_task_build_dir_must_be_under_builds(tmp_path):
    """Guard (added): R2-06 path rules are relative to cwd, which must be builds/<build_id>/."""
    other = tmp_path / "elsewhere" / "b-1"
    other.mkdir(parents=True)
    with pytest.raises(bridge.BridgeUsageError):
        bridge.Task(task_id="t-1", build_dir=other, card="cards_content/x.md", instructions="x")


def test_options_from_config_defaults():
    """r2.6 defaults reach the bridge: writer_max_rounds 3, writer_concurrency 4, writer_timeout_s 600."""
    opts = _opts()
    assert (opts.max_rounds, opts.concurrency, opts.timeout_s) == (3, 4, 600)


@pytest.mark.parametrize("bad", [{"concurrency": 0}, {"max_rounds": 0}, {"timeout_s": 0}, {"max_budget_usd": 0}])
def test_options_reject_non_positive(bad):
    with pytest.raises(bridge.BridgeUsageError):
        _opts(**bad)


def test_duplicate_task_ids_rejected(tmp_path, fake_log):
    t1 = _task(tmp_path, "t-1", "cards_content/a.md")
    t2 = _task(tmp_path, "t-1", "cards_content/b.md")
    with pytest.raises(bridge.BridgeUsageError):
        bridge.run([t1, t2], _opts())
    assert log_entries(fake_log) == []


def test_zero_tasks(tmp_path, fake_log):
    """00d1-11: empty task list -> immediate return, task count 0, no subprocess (fake log empty)."""
    receipt_path = tmp_path / "bridge.json"
    receipt = bridge.run([], _opts(), receipt_path=receipt_path)

    assert receipt["task_count"] == 0
    assert receipt["tasks"] == []
    assert receipt["status"] == "done"
    assert json.loads(receipt_path.read_text(encoding="utf-8")) == receipt
    assert log_entries(fake_log) == []
