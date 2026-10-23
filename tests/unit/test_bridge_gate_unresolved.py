"""WI-00d2 gate edge (split from test_bridge_gate.py so the red receipt is not misread as 'runner-missing':
before the gate existed this case failed with FileNotFoundError 'No such file or directory: claude', which the
receipt classifier matches as an environment failure). Red observed manually before implementation."""

from __future__ import annotations

import json

import pytest

from klode_kit import bridge
from klode_kit.config import load

pytestmark = pytest.mark.unit


def _tasks_file(root):
    build = root / "builds" / "b-20261023-0001"
    build.mkdir(parents=True, exist_ok=True)
    return None, build


def test_run_refuses_unresolvable_claude_without_ack(tmp_path, monkeypatch):
    """S2: a claude that is not on PATH at all is not the fake either -> gated (no FileNotFoundError path first)."""
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setenv("KLODE_KIT_REAL", "0")
    _, build = _tasks_file(tmp_path)
    task = bridge.Task(task_id="t-001", build_dir=build, card="cards_content/c001.md", instructions="x")
    with pytest.raises(bridge.BridgeGateClosed):
        bridge.run([task], bridge.Options.from_config(load(), max_rounds=1))
