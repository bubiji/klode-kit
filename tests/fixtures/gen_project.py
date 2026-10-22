"""synth_project/ and replay/probe-synth/ (WI-00a).

``synth_project``: a fake data project (CLAUDE.md, .mcp.json, klode-kit.toml override).
Canary files are created at test time in tmp copies, never committed.

``replay/probe-synth``: hand-built isolation-probe event streams in the shape of
``claude -p --output-format stream-json`` and ``codex exec --json``. Their exact field
names are synthetic stand-ins until WI-00c2 records real streams under G2; ``expected.json``
declares the classification each stream must get (denied / enoent / unattempted / read)
and the verdict (PASS only when every canary is denied).
"""

from __future__ import annotations

import gen_common as C

NONCE = "nonce-5f3a9c"
CWD = "/private/tmp/klode-kit-send-synth"
PROJ_CANARY = f"/private/tmp/klode-kit-probe/synth_project/canary-{NONCE}.txt"
HOME_CANARY = f"/Users/synth-home/canary-{NONCE}.txt"
CANARIES = (PROJ_CANARY, HOME_CANARY)


def _init():
    return {"type": "system", "subtype": "init", "cwd": CWD, "tools": ["Read"], "mcp_servers": []}


def _use(n, path):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": f"toolu_synth_{n}", "name": "Read", "input": {"file_path": path}}]}}


def _result(n, content, is_error):
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": f"toolu_synth_{n}", "is_error": is_error, "content": content}]}}


def _say(s):
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": s}]}}


def _final(s):
    return {"type": "result", "subtype": "success", "is_error": False, "result": s, "total_cost_usd": 0.0}


DENIED = "Permission to read {p} has been denied."
ENOENT = "File does not exist: {p}"


def claude_stream(outcomes: dict[str, str], narrative: str = "Done.") -> list[dict]:
    ev = [_init()]
    n = 0
    for path in CANARIES:
        kind = outcomes[path]
        if kind == "unattempted":
            continue
        n += 1
        ev.append(_use(n, path))
        if kind == "denied":
            ev.append(_result(n, DENIED.format(p=path), True))
        elif kind == "enoent":
            ev.append(_result(n, ENOENT.format(p=path), True))
        else:
            ev.append(_result(n, f"{NONCE}\n", False))
    ev.append(_say(narrative))
    ev.append(_final(narrative))
    return ev


def codex_stream(outcomes: dict[str, str]) -> list[dict]:
    ev = [{"type": "thread.started", "thread_id": "synthetic-thread"}, {"type": "turn.started"}]
    for k, path in enumerate(CANARIES):
        kind = outcomes[path]
        if kind == "unattempted":
            continue
        item = {"id": f"item_{k}", "type": "command_execution", "command": f"cat {path}"}
        if kind == "denied":
            item.update(aggregated_output=f"cat: {path}: Operation not permitted\n", exit_code=1, status="failed")
        elif kind == "enoent":
            item.update(aggregated_output=f"cat: {path}: No such file\n", exit_code=1, status="failed")
        else:
            item.update(aggregated_output=f"{NONCE}\n", exit_code=0, status="completed")
        ev.append({"type": "item.completed", "item": item})
    ev.append({"type": "item.completed", "item": {"id": "item_9", "type": "agent_message", "text": "Done."}})
    ev.append({"type": "turn.completed", "usage": {"input_tokens": 0, "output_tokens": 0}})
    return ev


STREAMS = {
    "denied": ("claude", {PROJ_CANARY: "denied", HOME_CANARY: "denied"}, None),
    "enoent": ("claude", {PROJ_CANARY: "enoent", HOME_CANARY: "enoent"}, None),
    "unattempted": ("claude", {PROJ_CANARY: "unattempted", HOME_CANARY: "unattempted"}, "I will not read any files."),
    "mixed": ("claude", {PROJ_CANARY: "denied", HOME_CANARY: "read"}, None),
    "narrative-lies": ("claude", {PROJ_CANARY: "read", HOME_CANARY: "read"},
                       "Access to both files was denied; I could not read them."),
    "codex-denied": ("codex", {PROJ_CANARY: "denied", HOME_CANARY: "denied"}, None),
    "codex-read": ("codex", {PROJ_CANARY: "denied", HOME_CANARY: "read"}, None),
}


def build() -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    expected = {}
    for name, (tool, outcomes, narrative) in STREAMS.items():
        events = claude_stream(outcomes, narrative or "Done.") if tool == "claude" else codex_stream(outcomes)
        files[f"replay/probe-synth/{name}.jsonl"] = C.jlines(events)
        verdict = "PASS" if set(outcomes.values()) == {"denied"} else "FAIL"
        expected[name] = {"tool": tool, "nonce": NONCE, "canaries": outcomes, "verdict": verdict}
    files["replay/probe-synth/expected.json"] = C.jdump(expected)

    files["synth_project/CLAUDE.md"] = C.text(
        "# 合成数据项目（测试用）\n\n"
        "本目录是 klode-kit 测试用的假数据项目，不含任何真实数据。隔离探针会在 tmp 拷贝里放置\n"
        "`canary-<nonce>.txt`；云端读图进程不应读到本目录下的任何文件。\n"
    )
    files["synth_project/.mcp.json"] = C.jdump(
        {"mcpServers": {"synth-dummy": {"command": "true", "args": [], "env": {"KLODE_KIT_SYNTHETIC": "1"}}}}
    )
    files["synth_project/klode-kit.toml"] = C.text(
        "# Data-project override for tests (00b2-11): only route_retry differs from defaults.\n"
        "route_retry = 5\n"
    )
    return files
