"""The two probes behind ``u5_ready`` (WI-11b; design 02 r4.2, R4-04; matrix 11b-07 ... 11b-09).

    klode-kit u5-probe --kb kb/<id> [--build ID] [--project DIR] [--server NAME] [--host] [--check] [--receipt F]

**Direct probe** (``direct_probe``): reads the entry ``mcpServers.<kb>-klode`` of ``<project>/.mcp.json`` and starts
*that* command with *those* args, the entry's ``env`` on top of the caller's environment, and the project directory as
cwd - exactly what the host does - then speaks MCP over stdio: ``initialize``, ``notifications/initialized``,
``tools/call kb_version``; the ``build_id`` of the answer is what the wrapper serves right now. The server is then
asked to leave (stdin closed, then SIGTERM, then SIGKILL) so its reader lease goes with it.

**Host probe** (``host_probe``, *manual gate G2*: a real model call; also G5 trust of the data project): ``claude -p
"<ask for kb_version once>" --output-format stream-json --verbose --allowedTools mcp__<server>__kb_version
--max-budget-usd N`` in the project directory. The evidence is read from the event stream by ``tool_use`` id
(``parse_host_stream``, R4-04): the ``mcp_servers`` status of the ``system/init`` event gives the host state, the
``tool_use`` block that calls ``mcp__<server>__kb_version`` gives an id, and only the ``tool_result`` block with that
``tool_use_id`` is read - the model's prose, the closing ``result`` text and other tools' results are not evidence. When
the model called it more than once, the last answered call counts. A ``claude`` that is not the replay fake in
``tests/fakes/bin`` is started only through the shared G2 gate (``klode_kit.gate.vet``: ``KLODE_KIT_REAL=1`` and a G2
acknowledgement), exit 61 otherwise, zero calls. Stream shapes: https://code.claude.com/docs/en/headless and
https://code.claude.com/docs/en/agent-sdk/typescript (``system``/``init`` ``mcp_servers[{name,status}]``).

**Decision** (``decide``): ``u5_ready`` only when the direct probe read the target build and the host probe ran, sees the
server ``connected`` and read the target build too. A probe that ran and did not read the target build -> exit 21
(``u5_ready:false``); a direct probe only, reading the target -> exit 0 and ``u5_ready:false`` (``host_state:
not_run``). With ``--check`` the findings go to the real ``kb-check`` (``--host-state`` / ``--probe-build-id``, WI-10a):
``check_receipt.json`` of the build is rewritten with ``mcp_host_visible`` and ``u5_ready`` (the probe id handed over is the
target when every probe that ran read it, else the dissenting one), and a failing check keeps its own exit code.

Exit codes: 0 no probe disagrees, 2 usage, 21 a probe read another build / could not read one / the host is not
connected, 40 no ``claude`` (or the kb-check's code), 61 the G2 gate is closed. stdout: one JSON line
``klode-kit.u5_probe/1`` (also written to ``--receipt``).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import select
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

from klode_kit import gate
from klode_kit.pipeline import EXIT_CODE, Reason
from klode_kit.registry_write import BUILD_RE, atomic_write

SCHEMA = "klode-kit.u5_probe/1"
PROTOCOL = "2025-06-18"
HOST_STATES = ("connected", "pending", "failed", "not_run")
DIRECT_TIMEOUT = 60.0
HOST_TIMEOUT = 300.0
LEAVE_GRACE = 3.0


REASON_OF = {0: Reason.ok, 21: Reason.post_publish_failed, 20: Reason.content_gate, 30: Reason.contract_violation,
             40: Reason.environment, 50: Reason.no_progress}


class ProbeEnvironmentError(Exception):
    """The probe cannot run at all (no ``claude`` on PATH): exit 40."""


def _env_of(env: Mapping[str, str] | None) -> dict[str, str]:
    return dict(os.environ if env is None else env)


# --------------------------------------------------------------------------- the direct probe


def _entry_of(project: Path, server: str) -> tuple[dict | None, str | None]:
    path = project / ".mcp.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, f".mcp.json not found in {project}"
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        return None, f".mcp.json cannot be read as JSON ({exc})"
    servers = doc.get("mcpServers") if isinstance(doc, dict) else None
    entry = servers.get(server) if isinstance(servers, dict) else None
    if not isinstance(entry, dict):
        return None, f"no entry {server!r} in .mcp.json"
    if not isinstance(entry.get("command"), str) or not entry["command"]:
        return None, f"the .mcp.json entry {server!r} has no command"
    return entry, None


class _Pipe:
    """JSON lines over a child's stdout, with a deadline."""

    def __init__(self, proc: subprocess.Popen):
        self.proc, self.buf = proc, b""

    def line(self, deadline: float) -> bytes | None:
        fd = self.proc.stdout.fileno()  # type: ignore[union-attr]
        while b"\n" not in self.buf:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError
            if not select.select([fd], [], [], left)[0]:
                raise TimeoutError
            chunk = os.read(fd, 65536)
            if not chunk:
                return None
            self.buf += chunk
        line, _, self.buf = self.buf.partition(b"\n")
        return line

    def answer(self, rid: int, deadline: float) -> dict | None:
        """The response with ``id == rid`` (other lines - noise, notifications - are skipped); None at EOF."""
        while True:
            raw = self.line(deadline)
            if raw is None:
                return None
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if isinstance(msg, dict) and msg.get("id") == rid and ("result" in msg or "error" in msg):
                return msg


def _send(proc: subprocess.Popen, msg: Mapping) -> None:
    proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))  # type: ignore[union-attr]
    proc.stdin.flush()  # type: ignore[union-attr]


def _leave(proc: subprocess.Popen) -> None:
    """Let the server go: EOF on stdin, then SIGTERM, then SIGKILL (its lease is released on EOF/SIGTERM)."""
    with contextlib.suppress(OSError):  # closing twice is fine; a broken pipe on the last flush is the server's own exit
        proc.stdin.close()  # type: ignore[union-attr]
    for sig in (None, "terminate", "kill"):
        if sig:
            getattr(proc, sig)()
        try:
            proc.wait(timeout=LEAVE_GRACE)
            return
        except subprocess.TimeoutExpired:
            continue


def _version_of(text: str) -> tuple[str | None, str | None]:
    """``(build_id, error)`` from the text of a ``kb_version`` answer."""
    try:
        doc = json.loads(text)
    except ValueError:
        return None, f"the kb_version answer is not JSON: {text[:120]!r}"
    if not isinstance(doc, dict) or "build_id" not in doc:
        return None, "the kb_version answer has no build_id"
    bid = doc["build_id"]
    if not isinstance(bid, str) or not BUILD_RE.fullmatch(bid):
        return None, f"build_id {str(bid)[:60]!r} is not a build id"
    return bid, None


def direct_probe(project: str | os.PathLike, server: str, *, env: Mapping[str, str] | None = None,
                 timeout: float = DIRECT_TIMEOUT) -> dict[str, Any]:
    """Start the ``.mcp.json`` entry of ``server`` as the host would and read its ``kb_version``."""
    project = Path(project)
    out: dict[str, Any] = {"ok": False, "build_id": None, "error": None, "argv": [], "cwd": str(project)}
    entry, problem = _entry_of(project, server)
    if entry is None:
        out["error"] = problem
        return out
    argv = [entry["command"], *[str(a) for a in entry.get("args", [])]]
    out["argv"] = argv
    penv = _env_of(env)
    penv.update({str(k): str(v) for k, v in (entry.get("env") or {}).items()})
    with tempfile.TemporaryFile() as err:
        try:
            proc = subprocess.Popen(argv, cwd=str(project), env=penv, stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=err)
        except OSError as exc:
            out["error"] = f"the server cannot be started: {exc}"
            return out
        deadline = time.monotonic() + timeout
        pipe = _Pipe(proc)
        try:
            _send(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                         "params": {"protocolVersion": PROTOCOL, "capabilities": {},
                                    "clientInfo": {"name": "klode-kit-u5-probe", "version": "1"}}})
            if pipe.answer(1, deadline) is None:
                raise EOFError
            _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
            _send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                         "params": {"name": "kb_version", "arguments": {}}})
            msg = pipe.answer(2, deadline)
            if msg is None:
                raise EOFError
        except TimeoutError:
            out["error"] = f"timed out after {timeout:g}s waiting for the server"
            return out
        except (EOFError, BrokenPipeError):
            err.seek(0)
            said = err.read()[-300:].decode("utf-8", "replace").strip()
            out["error"] = "the server closed its output before answering" + (f": {said}" if said else "")
            return out
        finally:
            _leave(proc)
        result = msg.get("result")
        text = _text_of(result.get("content")) if isinstance(result, dict) else str(msg.get("error"))
        if not isinstance(result, dict) or result.get("isError"):
            out["error"] = f"kb_version returned isError: {text[:200]}"
            return out
        bid, error = _version_of(text)
        out["build_id"], out["error"], out["ok"] = bid, error, bid is not None
    return out


# --------------------------------------------------------------------------- the host probe


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def tool_name(server: str) -> str:
    return f"mcp__{server}__kb_version"


def parse_host_stream(stream: str | Iterable[str], server: str) -> dict[str, Any]:
    """Read ``claude -p --output-format stream-json --verbose`` output: host state of ``server`` and the build id from
    the ``tool_result`` that answers the ``kb_version`` ``tool_use`` (by id). Never raises on odd input."""
    lines = stream.splitlines() if isinstance(stream, str) else list(stream)
    wanted = tool_name(server)
    status: str | None = None
    saw_init = False
    calls: list[str] = []
    results: dict[str, tuple[str, bool]] = {}
    cost: float | None = None
    for raw in lines:
        try:
            event = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            saw_init = True
            for item in event.get("mcp_servers") or []:
                if isinstance(item, dict) and item.get("name") == server:
                    status = str(item.get("status"))
        elif kind == "result" and isinstance(event.get("total_cost_usd"), (int, float)):
            cost = float(event["total_cost_usd"])
        elif kind in ("assistant", "user"):
            message = event.get("message")
            blocks = message.get("content") if isinstance(message, dict) else None
            for block in blocks if isinstance(blocks, list) else []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use" and block.get("name") == wanted and isinstance(block.get("id"), str):
                    calls.append(block["id"])
                elif block.get("type") == "tool_result" and isinstance(block.get("tool_use_id"), str):
                    results[block["tool_use_id"]] = (_text_of(block.get("content")), bool(block.get("is_error")))
    host_state = "failed" if status is None else status if status in ("connected", "pending") else "failed"
    out: dict[str, Any] = {"host_state": host_state, "build_id": None, "tool_use_id": None, "error": None,
                           "cost_usd": cost}
    if not saw_init:
        out["error"] = "no system/init message in the stream"
        return out
    if not calls:
        out["error"] = f"no kb_version call ({wanted}) in the stream"
        return out
    answered = [i for i in calls if i in results]
    if not answered:
        out["tool_use_id"] = calls[-1]
        out["error"] = f"no tool_result for the kb_version tool_use {calls[-1]}"
        return out
    use_id = answered[-1]
    text, is_error = results[use_id]
    out["tool_use_id"] = use_id
    if is_error:
        out["error"] = f"the tool_result has is_error: {text[:200]}"
        return out
    out["build_id"], out["error"] = _version_of(text)
    return out


def host_probe(project: str | os.PathLike, server: str, *, env: Mapping[str, str] | None = None,
               claude: str | None = None, timeout: float = HOST_TIMEOUT, max_budget_usd: float = 1.0,
               model: str | None = None) -> dict[str, Any]:
    """One ``claude -p`` run in the data project (G2: raises ``gate.GateClosed`` for a real ``claude`` unless the gate
    is open; ``ProbeEnvironmentError`` without a ``claude``)."""
    project = Path(project)
    penv = _env_of(env)
    prog = claude or "claude"
    resolved = gate.vet(prog, path=penv.get("PATH"), env=penv)
    exe = shutil.which(prog, path=penv.get("PATH"))
    if resolved is None or exe is None:
        raise ProbeEnvironmentError(f"{prog}: not found on PATH")
    prompt = (f"Call the MCP tool {tool_name(server)} exactly once with no arguments, then reply with the single "
              "word DONE.")
    argv = [prog, "-p", prompt, "--output-format", "stream-json", "--verbose", "--allowedTools", tool_name(server),
            "--max-budget-usd", str(max_budget_usd)]
    if model:
        argv += ["--model", model]
    out: dict[str, Any] = {"argv": argv, "cwd": str(project), "exit_code": None}
    try:
        proc = subprocess.run([exe, *argv[1:]], cwd=str(project), env=penv, capture_output=True, timeout=timeout,
                              stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        out.update(host_state="failed", build_id=None, tool_use_id=None, cost_usd=None,
                   error=f"claude timed out after {timeout:g}s")
        return out
    except OSError as exc:
        raise ProbeEnvironmentError(f"{prog}: cannot be started: {exc}") from exc
    parsed = parse_host_stream(proc.stdout.decode("utf-8", "replace"), server)
    out.update(parsed, exit_code=proc.returncode)
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", "replace").strip()[-200:]
        out["error"] = f"claude exit {proc.returncode}" + (f": {tail}" if tail else "") + (
            f"; {parsed['error']}" if parsed["error"] else "")
    return out


# --------------------------------------------------------------------------- the decision, the command


def decide(target: str, direct_id: str | None, host_ran: bool, host_state: str, host_id: str | None) -> tuple[bool, int]:
    """``(u5_ready, exit code)``. A probe that ran and did not read ``target`` is exit 21; ``u5_ready`` needs both."""
    direct_bad = direct_id != target
    host_bad = host_ran and (host_state != "connected" or host_id != target)
    ready = (not direct_bad) and host_ran and not host_bad
    return ready, EXIT_CODE[Reason.post_publish_failed] if direct_bad or host_bad else 0


def _body(kb: Path, build_id: str | None, reason: Reason, **extra: Any) -> dict[str, Any]:
    return {"schema": SCHEMA, "kb_id": kb.name, "build_id": build_id, "u5_ready": False, "exit_code": EXIT_CODE[reason],
            "reason": reason.value, **extra}


def _current_of(kb: Path) -> str | None:
    link = kb / "current"
    if not link.is_symlink():
        return None
    name = os.path.basename(os.readlink(link))
    return name if BUILD_RE.fullmatch(name) else None


def run(kb: str | os.PathLike, build_id: str | None = None, project: str | os.PathLike | None = None, *,
        server: str | None = None, host: bool = False, check: bool = False, env: Mapping[str, str] | None = None,
        timeout: float = DIRECT_TIMEOUT, host_timeout: float = HOST_TIMEOUT, claude: str | None = None,
        max_budget_usd: float = 1.0, model: str | None = None) -> tuple[int, dict[str, Any]]:
    kb = Path(kb)
    target = build_id if build_id is not None else _current_of(kb)
    if target is None or not BUILD_RE.fullmatch(target):
        return 2, _body(kb, target, Reason.usage, detail="no build to probe: give --build (a build id) or point current")
    proj = Path(project) if project is not None else (kb.parent.parent if kb.parent.name == "kb" else None)
    if proj is None:
        return 2, _body(kb, target, Reason.usage, detail="no --project and the kb is not below <project>/kb/")
    build_dir = kb / "builds" / target
    if not build_dir.is_dir():
        return 2, _body(kb, target, Reason.usage, detail=f"builds/{target} does not exist")
    name = server or f"{kb.name}-klode"
    direct = direct_probe(proj, name, env=env, timeout=timeout)
    host_res: dict[str, Any] | None = None
    if host:
        try:
            host_res = host_probe(proj, name, env=env, claude=claude, timeout=host_timeout,
                                  max_budget_usd=max_budget_usd, model=model)
        except gate.GateClosed as exc:
            return 61, _body(kb, target, Reason.gate_closed, server=name, direct=direct, host=None,
                             host_state="not_run", probe_build_id=direct["build_id"], detail=str(exc))
        except ProbeEnvironmentError as exc:
            return 40, _body(kb, target, Reason.environment, server=name, direct=direct, host=None,
                             host_state="not_run", probe_build_id=direct["build_id"], detail=str(exc))
    host_state = host_res["host_state"] if host_res else "not_run"
    host_id = host_res["build_id"] if host_res else None
    ready, code = decide(target, direct["build_id"], host_res is not None, host_state, host_id)
    extra: dict[str, Any] = {}
    if check:
        from klode_kit.check import kb_check

        cfg_project = proj if (proj / "klode-kit.toml").is_file() else None
        # kb-check knows one probe id: the target when every probe that ran read it, else the dissenting one
        seen = [direct["build_id"], *([host_id] if host_res is not None else [])]
        dissent = [i for i in seen if i != target]
        check_code, doc = kb_check.run_check(build_dir, entry="u5-probe", project=cfg_project, host=host_state,
                                             probe_build_id=dissent[0] if dissent else target)
        ready = ready and bool(doc["u5_ready"])
        extra["check"] = {"status": doc["status"], "u5_ready": doc["u5_ready"], "exit_code": check_code}
        if code == 0 and check_code != 0:
            code = check_code
    reason = REASON_OF.get(code, Reason.environment)
    body = _body(kb, target, reason, server=name, project=str(proj), host_state=host_state,
                 probe_build_id=direct["build_id"], direct=direct, host=host_res, **extra)
    body["u5_ready"] = ready
    return code, body


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog="klode-kit u5-probe", description="the probes behind u5_ready")
    parser.add_argument("--kb", type=Path, required=True)
    parser.add_argument("--build", default=None, help="target build id (default: what current points to)")
    parser.add_argument("--project", type=Path, default=None)
    parser.add_argument("--server", default=None, help="MCP server name (default: <kb>-klode)")
    parser.add_argument("--host", action="store_true", help="also run the host probe (real claude, manual gate G2)")
    parser.add_argument("--check", action="store_true", help="hand the findings to kb-check (rewrites check_receipt)")
    parser.add_argument("--receipt", type=Path, default=None)
    parser.add_argument("--timeout", type=float, default=DIRECT_TIMEOUT)
    parser.add_argument("--host-timeout", type=float, default=HOST_TIMEOUT)
    parser.add_argument("--budget-usd", type=float, default=1.0)
    parser.add_argument("--model", default=None)
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    code, body = run(args.kb, args.build, args.project, server=args.server, host=args.host, check=args.check,
                     timeout=args.timeout, host_timeout=args.host_timeout, max_budget_usd=args.budget_usd,
                     model=args.model)
    text = json.dumps(body, ensure_ascii=False) + "\n"
    if args.receipt is not None:
        atomic_write(args.receipt, text.encode("utf-8"), tmp_point="u5.receipt_tmp", done_point="u5.receipt_written")
    sys.stdout.write(text)
    return code
