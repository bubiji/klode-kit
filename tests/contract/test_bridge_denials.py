"""WI-00d2 (00d2-02 evidence path): the bridge keeps the CLI's ``permission_denials`` in its run receipt.

The host spike proves "writing library/x is refused" from ``permission_denials`` of the ``claude -p`` result
(design R2-06). The bridge receipt is the artifact the pipeline keeps, so every attempt record carries the list
the CLI reported (``[]`` when it reported none or printed no JSON), and the task record carries the union in
attempt order. Replay fake (tests/fakes/bin/claude, behaviour scenario); paired real path 00d2-02.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

from klode_kit import bridge
from klode_kit.config import load

pytestmark = pytest.mark.contract_replay

_seq = itertools.count()

DENIAL = {"tool_name": "Edit", "tool_use_id": "toolu_01", "tool_input": {"file_path": "library/x.md",
                                                                          "old_string": "PLACEHOLDER", "new_string": "EDITED"}}


def _result(denials: list | None, *, structured: bool = True) -> str:
    obj = {"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.01, "result": "ok"}
    if denials is not None:
        obj["permission_denials"] = denials
    if structured:
        obj["structured_output"] = {"card": "{card}", "status": "written", "anchors_full": 5, "aliases": 10}
    return json.dumps(obj)


def _scenario(tmp: Path, monkeypatch, steps: list[dict]) -> None:
    path = tmp / f"scenario-{next(_seq)}.json"
    path.write_text(json.dumps({"steps": steps}), encoding="utf-8")
    monkeypatch.setenv("KLODE_KIT_FAKE_SCENARIO", str(path))


def _task(tmp: Path) -> bridge.Task:
    build = tmp / "builds" / "b-20261023-0001"
    build.mkdir(parents=True)
    return bridge.Task(task_id="t-001", build_dir=build, card="cards_content/x.md", instructions="x")


def test_denials_recorded_on_done_attempt(tmp_path, monkeypatch):
    _scenario(tmp_path, monkeypatch, [{"write_files_after": {"{card}": "# x\n"}, "stdout": _result([DENIAL])}])
    receipt = bridge.run([_task(tmp_path)], bridge.Options.from_config(load()))
    rec = receipt["tasks"][0]
    assert rec["status"] == "done"
    assert rec["attempts_log"][0]["permission_denials"] == [DENIAL]
    assert rec["permission_denials"] == [DENIAL]


def test_denials_union_across_retries(tmp_path, monkeypatch):
    second = {**DENIAL, "tool_use_id": "toolu_02"}
    _scenario(tmp_path, monkeypatch, [
        {"stdout": _result([DENIAL], structured=False)},                       # parse_error -> retry
        {"stdout": "not json at all"},                                          # no JSON -> []
        {"write_files_after": {"{card}": "# x\n"}, "stdout": _result([second])},
    ])
    receipt = bridge.run([_task(tmp_path)], bridge.Options.from_config(load(), max_rounds=3))
    rec = receipt["tasks"][0]
    assert [a["permission_denials"] for a in rec["attempts_log"]] == [[DENIAL], [], [second]]
    assert rec["permission_denials"] == [DENIAL, second]


@pytest.mark.parametrize("value", [None, "Edit library/x", {"x": 1}], ids=["absent", "string", "object"])
def test_missing_or_malformed_denials_are_empty(tmp_path, monkeypatch, value):
    stdout = json.loads(_result(None))
    if value is not None:
        stdout["permission_denials"] = value
    _scenario(tmp_path, monkeypatch, [{"write_files_after": {"{card}": "# x\n"}, "stdout": json.dumps(stdout)}])
    rec = bridge.run([_task(tmp_path)], bridge.Options.from_config(load()))["tasks"][0]
    assert rec["permission_denials"] == []
    assert rec["attempts_log"][0]["permission_denials"] == []
