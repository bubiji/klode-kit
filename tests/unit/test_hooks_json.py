"""WI-05b: ``hooks/hooks.json`` wires ``privacy_guard`` as a PreToolUse hook on Bash cloud calls.

Format per the local docs mirror (checked 2026-10-23):
- plugin hooks live in ``hooks/hooks.json`` at the plugin root, under a top-level ``"hooks"`` key, in the same shape
  as the ``hooks`` object of settings; an optional top-level ``description`` is allowed; a file holding only the
  event map without the wrapper fails to load (https://code.claude.com/docs/en/plugins/components#hooks,
  https://code.claude.com/docs/en/plugins/manifest-reference, https://code.claude.com/docs/en/hooks "Plugin scripts");
- handler fields ``type``, ``if`` (one permission rule, e.g. ``Bash(git *)``), ``command`` + ``args`` (exec form, no
  shell; ``${CLAUDE_PLUGIN_ROOT}`` substituted in ``command`` and each arg), ``timeout``
  (https://code.claude.com/docs/en/hooks "Common fields", "Exec form and shell form").
"""

from __future__ import annotations

import json

import pytest

from _support.flows import REPO

pytestmark = pytest.mark.unit

HOOKS = REPO / "hooks" / "hooks.json"


def test_hooks_json_shape():
    data = json.loads(HOOKS.read_text(encoding="utf-8"))
    assert set(data) <= {"description", "hooks"} and isinstance(data["hooks"], dict)
    assert set(data["hooks"]) == {"PreToolUse"}
    groups = data["hooks"]["PreToolUse"]
    assert [g["matcher"] for g in groups] == ["Bash"]
    handlers = groups[0]["hooks"]
    assert {h["if"] for h in handlers} == {"Bash(claude *)", "Bash(codex *)"}
    for h in handlers:
        assert set(h) <= {"type", "if", "command", "args", "timeout", "statusMessage"}
        assert h["type"] == "command"
        assert h["command"] == "${CLAUDE_PLUGIN_ROOT}/bin/klode-kit"
        assert h["args"] == ["hook", "privacy-guard"]
        assert isinstance(h["timeout"], int) and 0 < h["timeout"] <= 600


def test_plugin_manifest_does_not_redeclare_hooks():
    """``hooks/hooks.json`` loads by default; the manifest must not point at it again."""
    manifest = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert "hooks" not in manifest
