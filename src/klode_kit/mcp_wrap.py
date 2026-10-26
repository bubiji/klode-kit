"""``klode-kit mcp --kb <kb_root>``: the MCP server of one library that follows ``kb/<id>/current`` (WI-11a; design 02
r4.2 "自动版本交接的 MCP 包装" / "热交接" / "kb_version" / "降级" / "读者租约").

``.mcp.json`` points at this wrapper, never at a build. The wrapper reads newline-delimited JSON-RPC on stdin like
``klode-mcp`` and answers on stdout (stdout carries protocol JSON and nothing else; diagnostics go to stderr).

**Hand-over.** Before each ``tools/call`` it does one ``readlink(kb/current)``. A build id it has not loaded yet is
loaded - lease first, then the build - and the previous build is let go. A call that is under way when ``current``
moves finishes on the build it started on (a build never changes); the next call uses the new one. ``current`` missing
or not ours -> that call answers ``isError: true`` ("kb not published"), the process lives on. After every call the
pointer is looked at once more: when it has moved on, the lease of the build just served is removed at once and the
next call loads the new one.

**The reader lease** is a file ``kb/<id>/.readers/<build_id>/<pid>-<nonce>`` (content: the start time). A published
build is read-only all the way down, its top directory included, so a running reader cannot create anything inside it
(design 02 put the lease there; WI-11a moved it beside the builds). ``kb-publish --prune`` reads this place
(``publish.build_leases``): a build with a live lease is never moved; leases of dead pids are removed by prune. The lease is
written *before* the build is opened - the file system event order is the proof: lease, ``mcp.after_resolve`` (the pause
point of the tests), then the first file of the build. Between the readlink and the lease a prune may have moved the
build away: the wrapper looks again after writing the lease and starts over if ``builds/<id>/library.toml`` is gone.

**Three ways to serve a build** (the first that works; the level is reported by ``kb_version.mode``):

0. ``in-process`` - ``from klode.lib import mcp_server`` and a ``KBPool.single`` of the build's ``Config``;
   ``initialize`` / ``ping`` go to klode's ``handle`` (which writes its answer itself and returns ``None``), so does every
   tool call. ``tools/list`` is the wrapper's own: klode's ``TOOLS`` plus ``kb_version``.
1. ``subprocess-proxy`` - that import fails: a ``klode-mcp -c builds/<id>/library.toml`` child per build, restarted when
   the build changes (``initialize`` replayed), calls forwarded line by line.
2. ``cli`` - no ``klode-mcp`` that answers either: every call runs ``klode --json -c builds/<id>/library.toml <verb>``
   and ``mcp_render`` writes the text klode's server would have written.

A level that turns out unusable at the first load hands over to the next one (for good). Children are started with
``PYTHONPATH`` and friends removed: they are other programs with their own environment.

``kb_version`` (the wrapper's own tool) returns one JSON text ``{kb_id, build_id, library_toml, published_at,
wrapper_version, klode_version, mode}``; it resolves ``current`` like any call, so its ``build_id`` is the readlink
and the build the next query will use. It is the only evidence of ``u5_ready``.

Crash / pause point: ``mcp.after_resolve``.
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import secrets
import select
import shutil
import signal
import subprocess
import sys
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, TextIO

from klode_kit import __version__, mcp_render
from klode_kit.publish import LOG_NAME, current_build, reader_lease_dir
from klode_kit.seams import crashpoint, startup

PROTOCOL_VERSION = "2025-06-18"
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR = -32700, -32600, -32601, -32602, -32603
USAGE_EXIT = 2
KB_VERSION = "kb_version"
KB_VERSION_TOOL = {
    "name": KB_VERSION,
    "description": ("Which build of this library the server is serving right now: kb_id, build_id (the build "
                    "`current` points to), library_toml, published_at, wrapper_version, klode_version, mode."),
    "inputSchema": {"type": "object", "properties": {}},
}
HANDSHAKE_TIMEOUT = 30.0  # seconds a klode-mcp child gets to answer initialize (KLODE_KIT_MCP_HANDSHAKE_TIMEOUT)
CALL_TIMEOUT = 600.0  # seconds one forwarded / CLI call may take (KLODE_KIT_MCP_CALL_TIMEOUT)
CLI_VERSION_TIMEOUT = 30.0
MAX_LOOKS = 5  # a build can move away under us this many times in a row before the call gives up
LEVEL_NAMES = {0: "in-process", 1: "subprocess-proxy", 2: "cli"}
_SCRUBBED_ENV = ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONSAFEPATH")


class NotPublished(Exception):
    """``current`` is missing or does not point to one of our builds."""


class BackendUnavailable(Exception):
    """The proxy level cannot serve at all (nothing started / no answer to initialize): hand over to the CLI level."""


class CallFailed(Exception):
    """One call could not be answered by the backend; the call gets ``isError: true``."""


@functools.cache
def klode_cli_version(exe: str | None) -> str | None:
    """The installed klode's version. klode 0.5.0 has no ``--version``: ask the interpreter its script runs on."""
    try:
        first = Path(exe or "").read_text(encoding="utf-8", errors="replace").splitlines()[0]
        interpreter = first[2:].strip() if first.startswith("#!") else ""
        ran = subprocess.run([interpreter, "-c", "import importlib.metadata as m; print(m.version('klode'))"],
                             capture_output=True, text=True, timeout=CLI_VERSION_TIMEOUT, env=_child_env())
    except (OSError, IndexError, subprocess.TimeoutExpired, ValueError):
        return None
    return (ran.stdout.strip() or None) if ran.returncode == 0 else None


def _seconds(name: str, default: float) -> float:
    """A timeout in seconds, from the environment variable ``name`` when it holds a positive number."""
    try:
        value = float(os.environ.get(name, ""))
    except ValueError:
        return default
    return value if value > 0 else default


def _child_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in _SCRUBBED_ENV}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _tool_result(req_id: Any, text: str, is_error: bool = False) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": text}],
                                                       "isError": is_error}}


def _error(req_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _initialize_result(params: Any, version: str | None) -> dict:
    cv = params.get("protocolVersion") if isinstance(params, dict) else None
    return {"protocolVersion": cv if isinstance(cv, str) and cv else PROTOCOL_VERSION,
            "capabilities": {"tools": {}}, "serverInfo": {"name": "klode", "version": version or "unknown"}}


def _library_facts(build: Path, fallback_id: str) -> tuple[str, str]:
    """``(id, description)`` of a build's ``library.toml`` (``fallback_id`` and ``""`` when it cannot be read)."""
    try:
        lib = tomllib.loads((build / "library.toml").read_text(encoding="utf-8")).get("library", {})
    except (OSError, ValueError):
        return fallback_id, ""
    return str(lib.get("id") or fallback_id), str(lib.get("description") or "")


def _published_at(kb: Path) -> str | None:
    """The ``at`` of the last row of ``PUBLISH.log`` (``None``: no log, or a last line that is not a row)."""
    try:
        lines = (kb / LOG_NAME).read_text(encoding="utf-8").splitlines()
        row = json.loads(lines[-1])
    except (OSError, ValueError, IndexError):
        return None
    at = row.get("at") if isinstance(row, dict) else None
    return at if isinstance(at, str) else None


# --------------------------------------------------------------------------- the three ways to serve a build


class InProcess:
    level = 0

    def __init__(self, server: Any, pool_cls: Any, config_cls: Any):
        self.server, self.pool_cls, self.config_cls = server, pool_cls, config_cls
        self.pool: Any = None

    def open(self, build: Path, client_init: Any) -> None:
        self.pool = self.pool_cls.single(self.config_cls.load(build / "library.toml"))

    def close(self) -> None:
        self.pool = None

    def tools(self) -> list[dict]:
        return list(self.server.TOOLS)

    def version(self) -> str | None:
        return str(self.server.SERVER_VERSION)

    def meta(self, msg: dict) -> None:
        """``initialize`` / ``ping``: klode's ``handle`` answers on stdout itself (it returns ``None``)."""
        self.server.handle(None, msg)

    def call(self, msg: dict) -> dict | None:
        """``tools/call``: klode's ``handle`` writes the response itself."""
        self.server.handle(self.pool, msg)
        return None


class _Lines:
    """Newline-delimited reads from a child's stdout with a deadline (unbuffered fd, own buffer)."""

    def __init__(self, fd: int):
        self.fd, self.buf = fd, b""

    def readline(self, timeout: float) -> bytes | None:
        deadline = time.monotonic() + timeout
        while b"\n" not in self.buf:
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([self.fd], [], [], left)[0]:
                return None
            chunk = os.read(self.fd, 65536)
            if not chunk:
                return None
            self.buf += chunk
        line, _, self.buf = self.buf.partition(b"\n")
        return line


class Proxy:
    level = 1

    def __init__(self, exe: str):
        self.exe = exe
        self.proc: subprocess.Popen | None = None
        self.lines: _Lines | None = None
        self.klode_version: str | None = None

    def open(self, build: Path, client_init: Any) -> None:
        self.close()
        try:
            self.proc = subprocess.Popen([self.exe, "-c", str(build / "library.toml")], stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE, stderr=None, bufsize=0, env=_child_env())
        except OSError as exc:
            raise BackendUnavailable(f"{self.exe}: {exc}") from exc
        assert self.proc.stdout is not None
        self.lines = _Lines(self.proc.stdout.fileno())
        params = dict(client_init) if isinstance(client_init, dict) else {}
        params.setdefault("protocolVersion", PROTOCOL_VERSION)
        params.setdefault("capabilities", {})
        params.setdefault("clientInfo", {"name": "klode-kit-mcp", "version": __version__})
        try:
            answer = self._exchange({"jsonrpc": "2.0", "id": "klode-kit-init", "method": "initialize",
                                     "params": params}, _seconds("KLODE_KIT_MCP_HANDSHAKE_TIMEOUT", HANDSHAKE_TIMEOUT))
            info = answer.get("result", {}).get("serverInfo", {}) if isinstance(answer, dict) else {}
            self._write({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except (CallFailed, OSError, AttributeError) as exc:
            self.close()
            raise BackendUnavailable(f"{self.exe} did not answer initialize: {exc}") from exc
        self.klode_version = info.get("version") if isinstance(info, dict) else None

    def _write(self, payload: Mapping) -> None:
        assert self.proc is not None and self.proc.stdin is not None
        self.proc.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))

    def _exchange(self, payload: dict, timeout: float) -> dict:
        assert self.lines is not None
        try:
            self._write(payload)
        except OSError as exc:
            raise CallFailed(f"klode-mcp is gone: {exc}") from exc
        while True:
            raw = self.lines.readline(timeout)
            if raw is None:
                raise CallFailed("klode-mcp gave no answer (exited or timed out)")
            try:
                doc = json.loads(raw)
            except ValueError:
                continue  # not protocol JSON: not ours to forward
            if isinstance(doc, dict) and doc.get("id") == payload["id"]:
                return doc

    def close(self) -> None:
        proc, self.proc, self.lines = self.proc, None, None
        if proc is None:
            return
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.close()  # EOF: a klode-mcp that works exits by itself
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        proc.stdout.close()

    def tools(self) -> list[dict]:
        return list(mcp_render.snapshot()["tools"])

    def version(self) -> str | None:
        return self.klode_version or klode_cli_version(shutil.which("klode"))

    def meta(self, msg: dict) -> dict:
        return {"jsonrpc": "2.0", "id": msg["id"],
                "result": {} if msg.get("method") == "ping" else _initialize_result(msg.get("params"), self.version())}

    def call(self, msg: dict) -> dict | None:
        try:
            return self._exchange(msg, _seconds("KLODE_KIT_MCP_CALL_TIMEOUT", CALL_TIMEOUT))
        except CallFailed:
            self.close()
            raise


class Cli:
    level = 2

    def __init__(self, exe: str):
        self.exe = exe
        self.library: Path | None = None
        self.kb_id, self.description = "", ""

    def open(self, build: Path, client_init: Any) -> None:
        self.library = build / "library.toml"
        self.kb_id, self.description = _library_facts(build, build.parent.parent.name)

    def close(self) -> None:
        self.library = None

    def tools(self) -> list[dict]:
        return list(mcp_render.snapshot()["tools"])

    def version(self) -> str | None:
        return klode_cli_version(self.exe)

    def meta(self, msg: dict) -> dict:
        return {"jsonrpc": "2.0", "id": msg["id"],
                "result": {} if msg.get("method") == "ping" else _initialize_result(msg.get("params"), self.version())}

    def call(self, msg: dict) -> dict | None:
        assert self.library is not None
        params = msg["params"]
        name, args = params["name"], params.get("arguments") or {}
        try:
            if name == "list_kbs":
                return _tool_result(msg["id"], mcp_render.list_kbs_text(self.kb_id, self.description))
            argv = mcp_render.cli_argv(name, args)
        except mcp_render.Local as local:  # answered here, tagged like the rendered answers of klode (unless an error)
            text = local.text if local.is_error else mcp_render.tagged(name, args, self.kb_id, local.text)
            return _tool_result(msg["id"], text, local.is_error)
        try:
            ran = subprocess.run([self.exe, "--json", "-c", str(self.library), *argv], capture_output=True,
                                 timeout=_seconds("KLODE_KIT_MCP_CALL_TIMEOUT", CALL_TIMEOUT), env=_child_env(),
                                 stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CallFailed(f"klode did not run: {exc}") from exc
        try:
            result = json.loads(ran.stdout.decode("utf-8"))
            text = mcp_render.render(name, args, result)
        except (ValueError, KeyError, TypeError):
            sys.stderr.write(ran.stderr.decode("utf-8", "replace"))
            return _tool_result(msg["id"], f"Tool {name!r} failed (see server logs).", True)
        return _tool_result(msg["id"], text)


def detect(env_path: str | None = None) -> InProcess | Proxy | Cli:
    """The best level available now: the klode module, else the ``klode-mcp`` command, else the ``klode`` command."""
    try:
        from klode.lib import mcp_server
        from klode.lib.config import Config
        from klode.lib.pool import KBPool
    except Exception:  # noqa: BLE001 - any failure to import the module is the cue to degrade
        mcp_server = None
    else:
        return InProcess(mcp_server, KBPool, Config)
    return _next_level(0, env_path)


def _next_level(after: int, env_path: str | None = None) -> Proxy | Cli:
    """The level below ``after`` that has its command on PATH; ``Cli`` (which then fails per call) when none has."""
    if after < 1 and (exe := shutil.which("klode-mcp", path=env_path)):
        return Proxy(exe)
    exe = shutil.which("klode", path=env_path)
    return Cli(exe or "klode")


# --------------------------------------------------------------------------- the wrapper


class Wrapper:
    def __init__(self, kb: Path, out: TextIO | None = None):
        self.kb = kb
        self.out = out
        self.backend = detect()
        self.cached: str | None = None
        self.lease: Path | None = None
        self.client_init: Any = None
        self.facts: tuple[str, str] = (kb.name, "")

    # ---- output
    def send(self, payload: Mapping) -> None:
        out = self.out or sys.stdout
        out.write(json.dumps(payload, ensure_ascii=False) + "\n")
        out.flush()

    # ---- leases
    @staticmethod
    def _create_lease(directory: Path, name: str) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        crashpoint("mcp.lease_dir")
        fd = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        try:
            os.write(fd, (_now() + "\n").encode("ascii"))
        finally:
            os.close(fd)
        return directory / name

    def _acquire(self, build_id: str) -> Path:
        directory, name = reader_lease_dir(self.kb, build_id), f"{os.getpid()}-{secrets.token_hex(4)}"
        try:
            return self._create_lease(directory, name)
        except FileNotFoundError:  # a prune took the (empty) directory away between mkdir and open: once more
            return self._create_lease(directory, name)

    @staticmethod
    def _drop(lease: Path | None) -> None:
        if lease is None:
            return
        try:
            os.unlink(lease)
        except FileNotFoundError:
            pass
        try:
            os.rmdir(lease.parent)  # only when nobody else holds a lease of this build
        except OSError:
            pass

    # ---- the pointer
    def _open(self, build: Path) -> None:
        """Open the build on the backend; a proxy whose ``klode-mcp`` does not work hands over to the CLI level."""
        try:
            self.backend.open(build, self.client_init)
        except BackendUnavailable as exc:
            sys.stderr.write(f"klode-kit mcp: level {self.backend.level} unusable ({exc}); using the next\n")
            self.backend.close()
            self.backend = _next_level(self.backend.level)
            self.backend.open(build, self.client_init)

    def ensure(self) -> str:
        """The build id this call serves; loads it (lease first) when it is not the one already loaded."""
        for _ in range(MAX_LOOKS):
            build_id = current_build(self.kb)
            if build_id is None:
                self.invalidate()
                raise NotPublished(f"kb not published: {self.kb / 'current'} is missing or not a build link")
            if build_id == self.cached:
                return build_id
            lease = self._acquire(build_id)
            crashpoint("mcp.after_resolve")
            build = self.kb / "builds" / build_id
            if not (build / "library.toml").is_file():  # pruned between the readlink and the lease: look again
                self._drop(lease)
                continue
            try:
                self._open(build)
            except BaseException:
                self._drop(lease)
                raise
            self._drop(self.lease)
            self.lease, self.cached = lease, build_id
            self.facts = _library_facts(build, self.kb.name)
            return build_id
        raise CallFailed(f"the build moved away {MAX_LOOKS} times while it was being loaded")

    def invalidate(self) -> None:
        self.backend.close()
        self._drop(self.lease)
        self.lease, self.cached = None, None

    def settle(self) -> None:
        """After a call: when ``current`` has moved on, let go of the build just served (and its lease)."""
        if self.cached is not None and current_build(self.kb) != self.cached:
            self.invalidate()

    def close(self) -> None:
        self.invalidate()

    # ---- requests
    def kb_version(self, req_id: Any, build_id: str) -> dict:
        info = {"kb_id": self.facts[0], "build_id": build_id,
                "library_toml": str(self.kb / "builds" / build_id / "library.toml"),
                "published_at": _published_at(self.kb), "wrapper_version": __version__,
                "klode_version": self.backend.version() or "unknown", "mode": LEVEL_NAMES[self.backend.level]}
        return _tool_result(req_id, json.dumps(info, ensure_ascii=False, sort_keys=True))

    def tools_call(self, msg: dict) -> None:
        req_id, params = msg["id"], msg.get("params")
        if not isinstance(params, dict):
            return self.send(_error(req_id, INVALID_PARAMS, "params must be an object"))
        name, args = params.get("name"), params.get("arguments") or {}
        if not isinstance(name, str) or not name:
            return self.send(_error(req_id, INVALID_PARAMS, "tool name must be a non-empty string"))
        if not isinstance(args, dict):
            return self.send(_error(req_id, INVALID_PARAMS, "tool arguments must be an object"))
        try:
            build_id = self.ensure()
            if name == KB_VERSION:
                response: dict | None = self.kb_version(req_id, build_id)
            else:
                response = self.backend.call(msg)
        except NotPublished as exc:
            response = _tool_result(req_id, str(exc), True)
        except CallFailed as exc:
            self.invalidate()
            response = _tool_result(req_id, f"Tool {name!r} failed: {exc}", True)
        except Exception as exc:  # noqa: BLE001 - a failing tool is reported to the caller, never raised
            self.invalidate()
            print(f"klode-kit mcp: {name!r}: {type(exc).__name__}: {exc}", file=sys.stderr)
            response = _tool_result(req_id, f"Tool {name!r} failed (see server logs).", True)
        if response is not None:
            self.send(response)
        self.settle()

    def handle(self, msg: dict) -> None:
        """One JSON-RPC message; notifications (no ``id``) never get an answer."""
        if "id" not in msg:
            return
        method = msg.get("method")
        if method in ("initialize", "ping"):
            if method == "initialize":
                self.client_init = msg.get("params")
            response = self.backend.meta(msg)
            if response is not None:
                self.send(response)
        elif method == "tools/list":
            self.send({"jsonrpc": "2.0", "id": msg["id"],
                       "result": {"tools": [*self.backend.tools(), KB_VERSION_TOOL]}})
        elif method == "tools/call":
            self.tools_call(msg)
        else:
            self.send(_error(msg["id"], METHOD_NOT_FOUND, f"Method not found: {method}"))

    def serve(self, stream: TextIO) -> int:
        try:
            for line in stream:
                self._serve_line(line.strip())
        except BrokenPipeError:  # the client is gone: nobody to answer (and nothing to flush at exit)
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0

    def _serve_line(self, line: str) -> None:
        if not line:
            return
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            return self.send(_error(None, PARSE_ERROR, f"Parse error: {exc}"))
        if not isinstance(msg, dict):
            return self.send(_error(None, INVALID_REQUEST, "Request must be a JSON object"))
        try:
            self.handle(msg)
        except BrokenPipeError:
            raise
        except Exception as exc:  # noqa: BLE001  # pragma: no cover - safety net: the server outlives any one message
            print(f"klode-kit mcp: {type(exc).__name__}: {exc}", file=sys.stderr)
            if "id" in msg:
                self.send(_error(msg["id"], INTERNAL_ERROR, "Internal server error"))


def _terminate(signum: int, _frame: Any) -> None:
    raise SystemExit(128 + signum)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="klode-kit mcp", description="MCP server of one library, following kb/<id>/current")
    parser.add_argument("--kb", type=Path, required=True, help="library root kb/<id> (absolute path)")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return USAGE_EXIT if exc.code else 0
    if not args.kb.is_dir():
        print(f"klode-kit mcp: --kb is not a directory: {args.kb}", file=sys.stderr)
        return USAGE_EXIT
    startup()
    for stream in (sys.stdin, sys.stdout):
        stream.reconfigure(encoding="utf-8")
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, _terminate)
    wrapper = Wrapper(args.kb.resolve())
    try:
        return wrapper.serve(sys.stdin)
    finally:
        wrapper.close()


if __name__ == "__main__":  # pragma: no cover - entry is ``klode-kit mcp``
    sys.exit(main())
