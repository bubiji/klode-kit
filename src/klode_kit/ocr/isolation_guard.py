"""PreToolUse hook for the cloud Claude route: file reads must stay inside cwd (WI-00c1; 02 r4.3 (a)/(b)).

Wired by the pinned ``config/cloud_settings.json`` as an exec-form command hook,
``{"command": "klode-kit", "args": ["hook", "isolation-guard"]}``; exec form resolves ``command``
on PATH and passes ``args`` without a shell (https://code.claude.com/docs/en/hooks, "Exec form").
Also runnable as ``python -m klode_kit.ocr.isolation_guard``.

Protocol (https://code.claude.com/docs/en/hooks): stdin is one JSON object with the common
fields (``cwd``: "Current working directory when the hook is invoked", ``hook_event_name``) and
``tool_input``; for Read/Edit/Write ``tool_input.file_path`` is absolute. Exit code 2 on
``PreToolUse`` "Blocks the tool call", with stderr as the reason, and a PreToolUse hook exiting 2
stops the call before permission rules are evaluated (02 r4.0 H19, docs: permissions).

Decision:
- ``realpath(file_path)`` (relative paths joined to ``cwd``) inside ``realpath(cwd)`` by path
  components -> exit 0, ``allow``;
- outside -> exit 2, ``deny``;
- ``tool_name == "StructuredOutput"`` (exact name) in an otherwise well-formed ``PreToolUse`` event
  -> exit 0, ``allow`` (WI-00c1b, WI-00c2 finding F2). ``--json-schema`` adds this tool to the
  session and the agent calls it to return its answer; its ``tool_input`` is the schema object, it
  has no ``file_path`` and touches no file
  (https://code.claude.com/docs/en/agent-sdk/structured-outputs, "Recognize the StructuredOutput
  tool call"). Denying it rejected every structured result. MCP tools are named ``mcp__<server>__*``
  and so never match the exact name;
- anything that cannot be judged - unreadable / non-JSON / non-object stdin, a relative or missing
  ``cwd``, an event other than ``PreToolUse``, any other ``tool_input`` without a string
  ``file_path``, a path that cannot be resolved - -> exit 2, ``deny`` (fail closed). ``--tools Read``
  leaves Read as the only built-in tool besides ``StructuredOutput``, so denying the remaining
  path-less tool calls costs nothing.

Log: every decision appends exactly one JSON line to ``<realpath(cwd)>.guard.jsonl`` - next to
the send directory, outside it, so the model cannot read it and the sender can copy it into the
receipt directory. When stdin carries no usable ``cwd`` the process cwd is used. Lines are
written with one ``write`` on an ``O_APPEND`` descriptor under an exclusive ``flock`` so
concurrent hooks never interleave (00c1-17).
"""

from __future__ import annotations

import fcntl
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ALLOW_EXIT = 0
DENY_EXIT = 2
LOG_SUFFIX = ".guard.jsonl"
STRUCTURED_OUTPUT_TOOL = "StructuredOutput"


def _decide(raw: bytes) -> tuple[bool, str, dict[str, Any]]:
    """(allowed, reason, log fields). Never raises."""
    fields: dict[str, Any] = {"file_path": None, "tool_name": None, "cwd": None}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return False, "stdin is not a JSON object", fields
    if not isinstance(data, dict):
        return False, "stdin is not a JSON object", fields
    cwd, tool_input = data.get("cwd"), data.get("tool_input")
    fields["tool_name"] = data.get("tool_name") if isinstance(data.get("tool_name"), str) else None
    if not isinstance(cwd, str) or not os.path.isabs(cwd):
        return False, "cwd missing or not absolute", fields
    fields["cwd"] = cwd
    if data.get("hook_event_name") != "PreToolUse":
        return False, f"unexpected hook_event_name {data.get('hook_event_name')!r}", fields
    if fields["tool_name"] == STRUCTURED_OUTPUT_TOOL:
        return True, "structured output (no file access)", fields
    file_path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    if not isinstance(file_path, str) or not file_path:
        return False, "tool_input.file_path missing", fields
    fields["file_path"] = file_path
    try:
        root = Path(os.path.realpath(cwd))
        target = Path(os.path.realpath(os.path.join(cwd, file_path)))
    except (ValueError, OSError) as exc:
        return False, f"cannot resolve path: {exc}", fields
    fields["resolved"] = str(target)
    if target == root or target.is_relative_to(root):
        return True, "inside cwd", fields
    return False, "outside cwd", fields


def _log_path(cwd: str | None) -> Path:
    base = cwd if cwd is not None else os.getcwd()
    return Path(os.path.realpath(base) + LOG_SUFFIX)


def _append(path: Path, entry: dict[str, Any]) -> None:
    line = (json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, line)
    finally:
        os.close(fd)


def main(argv: list[str] | None = None) -> int:
    try:
        raw = sys.stdin.buffer.read()
    except OSError:
        raw = b""
    allowed, reason, fields = _decide(raw)
    decision = "allow" if allowed else "deny"
    entry = {"at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "decision": decision,
             "reason": reason, "pid": os.getpid(), **fields}
    try:
        _append(_log_path(fields["cwd"]), entry)
    except OSError as exc:
        print(f"isolation_guard: cannot write log: {exc}", file=sys.stderr)
        allowed = False  # an unlogged allow is not evidence; refuse
        decision = "deny"
    if allowed:
        return ALLOW_EXIT
    print(f"isolation_guard: {decision} {fields.get('file_path')!r}: {reason}", file=sys.stderr)
    return DENY_EXIT


if __name__ == "__main__":
    sys.exit(main())
