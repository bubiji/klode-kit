"""WI-05b: ``privacy_guard`` decision edges, through its public functions ``simple_commands`` and ``decide``
(the hook process itself is exercised in tests/contract/test_privacy_guard_hook.py). Added after the first green
run, for branch coverage."""

from __future__ import annotations

import subprocess
import sys

import pytest

from _support.flows import base_env
from _support.send_fake import FIXTURE_PNG, masked_project
from klode_kit.ocr import privacy_guard as PG

pytestmark = pytest.mark.unit


def event(command, cwd):
    return {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)}


@pytest.mark.parametrize("command, expected", [
    ("", []),
    ("; ;", []),
    ("a b && c", [["a", "b"], ["c"]]),
    ("a\nb 'x\ny'", [["a"], ["b", "x\ny"]]),
    ("(a | b) & c", [["a"], ["b"], ["c"]]),
])
def test_simple_commands(command, expected):
    assert PG.simple_commands(command) == expected


def test_assignments_only_and_cloud_call_without_images_pass(tmp_path):
    PG.decide(event("FOO=1 BAR=2; codex exec 'say hi'; claude -p hello", tmp_path), {})


def test_named_image_that_is_a_directory_blocked(tmp_path):
    (tmp_path / "x.png").mkdir()
    with pytest.raises(PG.Deny, match="not a regular file"):
        PG.decide(event("codex exec -i x.png q", tmp_path), {"CLAUDE_PROJECT_DIR": str(tmp_path)})


def test_two_cloud_calls_each_checked(tmp_path):
    """The whitelist is read once and applied to every cloud call: the second call's image is still checked."""
    project, row = masked_project(tmp_path / "p")
    good = project / "_masked" / row["path"]
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"unregistered")
    env = {"CLAUDE_PROJECT_DIR": str(project)}
    PG.decide(event(f"codex exec -i {good} a; codex exec -i {FIXTURE_PNG} b", tmp_path), env)
    with pytest.raises(PG.Deny, match="not registered"):
        PG.decide(event(f"codex exec -i {good} a && env -i codex exec -i {bad} b", tmp_path), env)


def test_relative_cwd_falls_back_to_process_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "loose.png").write_bytes(b"unregistered")
    ev = event("codex exec -i loose.png q", tmp_path)
    ev["cwd"] = "relative/dir"
    with pytest.raises(PG.Deny):
        PG.decide(ev, {"CLAUDE_PROJECT_DIR": str(tmp_path)})


def test_module_entry_point(tmp_path):
    """``python -m klode_kit.ocr.privacy_guard`` is the same hook (non-cloud command -> exit 0)."""
    res = subprocess.run([sys.executable, "-m", "klode_kit.ocr.privacy_guard"],
                         input=b'{"hook_event_name": "PreToolUse", "tool_input": {"command": "ls"}, "cwd": "/"}',
                         env=base_env(), capture_output=True, timeout=60)
    assert res.returncode == 0, res.stderr.decode()
