"""WI-00d2 reproducer: the shipped card-writer must be able to return its structured result.

Host evidence (work/host-spike/00d1-probe-*, Claude Code 2.1.285): with ``--agent klode-card-writer`` the main
thread's tool set is exactly the agent's ``tools`` allowlist - ``[Read, Grep, Glob, Edit, Bash]`` - and the
``StructuredOutput`` tool that ``--json-schema`` adds (docs: agent-sdk/structured-outputs "Recognize the
StructuredOutput tool call") is filtered out. The writer then wrote a card that passes the gate but the result had
no ``structured_output``, so the bridge (correctly) classified the attempt ``parse_error`` and quarantined the card.

Rule: since the bridge always passes ``--json-schema`` for the writer, a writer definition that restricts its tools
must list ``StructuredOutput``. Paired real path: 00d2-01 / the init probe in tests/host/test_bridge_spike.py.
"""

from __future__ import annotations

import json

import pytest

from _support.flows import REPO
from klode_kit import bridge
from klode_kit.config import load

pytestmark = pytest.mark.unit


def test_shipped_writer_tools_include_structured_output(tmp_path):
    build = tmp_path / "builds" / "b-1"
    build.mkdir(parents=True)
    task = bridge.Task(task_id="t", build_dir=build, card="cards_content/c.md", instructions="x")
    argv, _ = bridge.build_argv(task, bridge.Options.from_config(load(), agents_dir=REPO / "agents"))
    writer = json.loads(argv[argv.index("--agents") + 1])["klode-card-writer"]
    assert "--json-schema" in argv
    assert "tools" not in writer or "StructuredOutput" in writer["tools"], writer.get("tools")
