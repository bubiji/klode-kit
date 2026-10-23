"""WI-00d2 reproducer (finding F-1, fix approved by the user 2026-10-23).

Claude Code 2.1.285 rejects a --json-schema document declaring ``"$schema": ".../draft/2020-12/schema"``
(exit 1 before any API call: "no schema with key or ref"). The bridge therefore passes the writer_receipt schema
with the top-level ``$schema`` and ``$id`` removed; everything else is unchanged, and the repository schema file
keeps its 2020-12 declaration. Paired real path: 00d2-01/02.
"""

from __future__ import annotations

import json

import pytest

from klode_kit import bridge
from klode_kit.config import load
from klode_kit.contract import schema as contract

pytestmark = pytest.mark.unit


def test_json_schema_value_has_no_dialect_keys(tmp_path):
    build = tmp_path / "builds" / "b-1"
    build.mkdir(parents=True)
    task = bridge.Task(task_id="t", build_dir=build, card="cards_content/c.md", instructions="x")
    argv, _ = bridge.build_argv(task, bridge.Options.from_config(load()))
    sent = json.loads(argv[argv.index("--json-schema") + 1])
    original = contract.load("writer_receipt")
    assert "$schema" in original and "$id" in original  # the repository file keeps its declaration
    assert "$schema" not in sent and "$id" not in sent
    assert sent == {k: v for k, v in original.items() if k not in ("$schema", "$id")}
    assert sent["properties"]["card"]["pattern"] == original["properties"]["card"]["pattern"]
