"""Helpers for the ``u5_ready`` probe tests (WI-11b; matrix 11b-07 ... 11b-09).

- ``stub_server(tmp, ...)``: a tiny stand-in MCP server (stdio, JSON lines) that answers ``initialize`` and the
  ``kb_version`` tool, and records its own argv, cwd and the marker env into a file - so a test can see that the
  probe started it with the argv and cwd of ``.mcp.json``. ``mode`` makes it misbehave.
- ``mcp_json(project, ...)``: a ``.mcp.json`` with the entry of a server.
- ``stream(...)``: the ``claude -p --output-format stream-json --verbose`` lines of a session in which the model calls
  ``mcp__<server>__kb_version``: ``system/init`` (with ``mcp_servers``), assistant ``tool_use`` blocks, user
  ``tool_result`` blocks matched by ``tool_use_id``, the closing ``result`` line (shapes: Claude Code headless and
  Agent SDK documentation, https://code.claude.com/docs/en/headless and
  https://code.claude.com/docs/en/agent-sdk/typescript).
- ``fake_claude_env(tmp, lines, exit_code)``: environment that makes the replay fake ``claude`` (tests/fakes/bin) print
  ``lines``.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from typing import Mapping, Sequence

STUB = r'''
import json, os, sys, time
mode = os.environ.get("STUB_MODE", "ok")
with open(os.environ["STUB_RECORD"], "w", encoding="utf-8") as fh:
    json.dump({"argv": sys.argv[1:], "cwd": os.path.realpath(os.getcwd()), "marker": os.environ.get("KLODE_KIT_BUILD")}, fh)
if mode == "crash":
    sys.exit(3)
for line in sys.stdin:
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    if "id" not in msg:
        continue
    if msg["method"] == "initialize":
        out = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "stub", "version": "0"}}
    elif msg["method"] == "tools/call" and msg["params"]["name"] == "kb_version":
        if mode == "hang":
            time.sleep(600)
        bid = os.environ.get("STUB_BUILD", "")
        if mode == "iserror":
            out = {"content": [{"type": "text", "text": "no current"}], "isError": True}
        elif mode == "nojson":
            out = {"content": [{"type": "text", "text": "hello"}], "isError": False}
        elif mode == "nobuild":
            out = {"content": [{"type": "text", "text": json.dumps({"kb_id": "x"})}], "isError": False}
        else:
            out = {"content": [{"type": "text", "text": json.dumps({"build_id": bid, "kb_id": "x", "mode": "stub"})}], "isError": False}
    else:
        out = {}
    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": out}), flush=True)
'''


def stub_server(tmp: Path) -> Path:
    path = tmp / "stub_mcp.py"
    path.write_text(STUB, encoding="utf-8")
    return path


def mcp_json(project: Path, server: str, command: str, args: Sequence[str], env: Mapping[str, str] | None = None,
             extra: Mapping | None = None) -> None:
    entry: dict = {"command": command, "args": list(args)}
    if env is not None:
        entry["env"] = dict(env)
    doc = {"mcpServers": {server: entry, **(extra or {})}}
    (project / ".mcp.json").write_text(json.dumps(doc), encoding="utf-8")


def stub_entry(project: Path, tmp: Path, server: str, *, build: str, mode: str = "ok") -> Path:
    """``.mcp.json`` with ``server`` -> the stub; returns the record file the stub writes at start."""
    record = tmp / "stub_record.json"
    mcp_json(project, server, sys.executable, [str(stub_server(tmp)), "--kb", "KB"],
             env={"STUB_BUILD": build, "STUB_MODE": mode, "STUB_RECORD": str(record), "KLODE_KIT_BUILD": build})
    return record


# --------------------------------------------------------------------------- stream-json lines


def j(obj: Mapping) -> str:
    return json.dumps(obj, ensure_ascii=False)


def init(server: str, status: str = "connected", others: Sequence[Mapping] = ()) -> dict:
    return {"type": "system", "subtype": "init", "session_id": "s-1", "cwd": "/synthetic/project",
            "tools": [f"mcp__{server}__kb_version"], "mcp_servers": [{"name": server, "status": status}, *others],
            "model": "synthetic", "permissionMode": "default"}


def tool_use(tool_id: str, name: str, input_: Mapping | None = None) -> dict:
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": tool_id, "name": name, "input": dict(input_ or {})}]}}


def tool_result(tool_id: str, text: str, *, is_error: bool = False, as_string: bool = False) -> dict:
    content = text if as_string else [{"type": "text", "text": text}]
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tool_id, "content": content, "is_error": is_error}]}}


def say(text: str) -> dict:
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def result_line(text: str, *, is_error: bool = False, cost: float = 0.01) -> dict:
    return {"type": "result", "subtype": "success", "is_error": is_error, "result": text, "total_cost_usd": cost,
            "num_turns": 2}


def version_text(build_id: str) -> str:
    return json.dumps({"build_id": build_id, "kb_id": "demo", "mode": "in-process"})


def stream(server: str, build_id: str | None, *, status: str = "connected", tool_id: str = "toolu_real") -> list[str]:
    """A session in which the model calls ``kb_version`` once and the server answers ``build_id``."""
    lines = [init(server, status)]
    if build_id is not None:
        lines += [say("I will ask the library which build it serves."),
                  tool_use(tool_id, f"mcp__{server}__kb_version"),
                  tool_result(tool_id, version_text(build_id)),
                  say(f"The library serves {build_id}."), result_line(f"build {build_id}")]
    else:
        lines += [result_line("I could not reach the tool.")]
    return [j(x) for x in lines]


def fake_claude_env(tmp: Path, lines: Sequence[str], exit_code: int = 0, name: str = "claude-scenario") -> dict[str, str]:
    """``KLODE_KIT_FAKE_SCENARIO`` for the replay fake ``claude``: print ``lines`` (one JSON object per line)."""
    scenario = tmp / f"{name}.json"
    scenario.write_text(json.dumps({"steps": [{"stdout": "\n".join(lines) + ("\n" if lines else ""),
                                               "exit_code": exit_code}]}), encoding="utf-8")
    return {"KLODE_KIT_FAKE_SCENARIO": str(scenario)}


def executable(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path
