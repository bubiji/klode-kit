"""WI-00c1b: reproducer for WI-00c2 finding F2 - the guard denied every ``StructuredOutput`` call.

``--json-schema`` adds a ``StructuredOutput`` tool to the session; the agent calls it to return its
answer (https://code.claude.com/docs/en/agent-sdk/structured-outputs, "Recognize the
StructuredOutput tool call"; init event ``tools: ["Read", "StructuredOutput"]`` in the WI-00c2 run).
The pinned hook matcher is ``*``, so the guard sees that call; its ``tool_input`` is the
schema object (``{"lines": [...]}``), which has no ``file_path``. Before the fix the guard
failed closed on it (exit 2) and every structured result was rejected.

Contract after WI-00c1b:
- ``tool_name == "StructuredOutput"`` (exact) in a well-formed ``PreToolUse`` event with an absolute
  ``cwd`` -> exit 0, one log line ``decision: allow`` naming the tool;
- every other tool without a ``file_path`` - including near-miss names - still exit 2 (fail closed);
- a ``StructuredOutput`` event that is otherwise unjudgeable (wrong event, relative cwd) still exit 2.

Payloads mirror the real PreToolUse input (docs: https://code.claude.com/docs/en/hooks, "Common input
fields", "PreToolUse input": ``tool_name``, ``tool_input``, ``tool_use_id``).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from _support.flows import base_env

pytestmark = pytest.mark.contract_replay

GUARD = [sys.executable, "-m", "klode_kit.ocr.isolation_guard"]
STRUCTURED_INPUT = {"lines": [{"text": "Hemoglobin 13.2 g/dL", "uncertain": False},
                              {"text": "WBC 6.1", "uncertain": True}]}


def event(cwd: Path | str, tool_name: str, tool_input: dict, *, hook_event_name: str = "PreToolUse") -> bytes:
    return json.dumps({
        "session_id": "f59ff10b-0000-4000-8000-000000000000",
        "transcript_path": "/dev/null",
        "cwd": str(cwd),
        "permission_mode": "dontAsk",
        "hook_event_name": hook_event_name,
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_use_id": "toolu_01UhpS3Yc2tExxffsSai7GjW",
    }).encode("utf-8")


def run_guard(stdin: bytes, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(GUARD, input=stdin, cwd=cwd, env=base_env(), capture_output=True, timeout=60)


def log_entries(cwd: Path) -> list[dict]:
    p = Path(str(cwd.resolve()) + ".guard.jsonl")
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


@pytest.fixture
def cwd(tmp_path) -> Path:
    d = tmp_path / "klode-kit-send-test"
    d.mkdir(mode=0o700)
    return d


@pytest.mark.parametrize("tool_input", [STRUCTURED_INPUT, {"lines": []}], ids=["lines", "empty-lines"])
def test_structured_output_call_allowed(cwd, tool_input):
    """F2 reproducer: a real-shaped StructuredOutput PreToolUse event -> exit 0, logged as allow."""
    res = run_guard(event(cwd, "StructuredOutput", tool_input), cwd)

    assert res.returncode == 0, res.stderr
    entries = log_entries(cwd)
    assert len(entries) == 1
    assert entries[0]["decision"] == "allow"
    assert entries[0]["tool_name"] == "StructuredOutput"


@pytest.mark.parametrize("tool_name,tool_input", [
    ("Bash", {"command": "cat ~/canary.txt"}),
    ("WebFetch", {"url": "https://example.invalid", "prompt": "x"}),
    ("Agent", {"description": "d", "prompt": "p"}),
    ("Glob", {"pattern": "**/*"}),
    ("structuredoutput", STRUCTURED_INPUT),
    ("StructuredOutput ", STRUCTURED_INPUT),
    ("mcp__x__StructuredOutput", STRUCTURED_INPUT),
    ("Read", STRUCTURED_INPUT),
])
def test_other_pathless_tools_still_denied(cwd, tool_name, tool_input):
    """Only the exact StructuredOutput name is exempt; every other path-less call stays fail-closed."""
    res = run_guard(event(cwd, tool_name, tool_input), cwd)

    assert res.returncode == 2, (tool_name, res.stderr)
    entries = log_entries(cwd)
    assert [e["decision"] for e in entries] == ["deny"]


def test_structured_output_unjudgeable_event_still_denied(cwd):
    """The exemption does not bypass the event checks: a non-PreToolUse event or a relative cwd -> exit 2."""
    wrong_event = run_guard(event(cwd, "StructuredOutput", STRUCTURED_INPUT, hook_event_name="PostToolUse"), cwd)
    relative = run_guard(event("relative/dir", "StructuredOutput", STRUCTURED_INPUT), cwd)

    assert wrong_event.returncode == 2
    assert relative.returncode == 2
    assert [e["decision"] for e in log_entries(cwd)] == ["deny", "deny"]
