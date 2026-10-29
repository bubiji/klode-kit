"""WI-15l: ``u5_probe`` pinned exactly (mutation-gate survivors, score 0.733 -> >= 0.85; critical path S4).

The existing u5_probe tests (``test_u5_probe.py``, ``test_u5_probe_edges.py``, ``tests/cli/test_u5_probe_*.py``) check
that the probes read the right build and that a wrong one is exit 21. They do not pin what the probe *sends*, what its
failure results *say*, the shape of every result dictionary, how a stream is read when something else than a clean
session surrounds it, the ``claude`` command line and its exit/stderr handling, the hand-over to ``kb-check``, the
usage bodies of ``run`` and the argparse wiring of ``main``. This file does (S1/S2/S3 pins plus one round trip).

How it is driven, in process:

- a stand-in MCP server (a Python script with a shebang, started through the ``.mcp.json`` entry exactly as the probe
  starts the real wrapper) logs every line it is sent and the argv / cwd / environment it was started with, and answers
  per ``SRV_MODE``;
- a stand-in ``claude`` (same idea; the G2 gate is *opened* in the environment handed to the probe - the program started
  is this script, never a model) logs argv / cwd / environment and plays stdout / stderr / exit code / delay;
- ``_Pipe`` / ``_leave`` are driven with real pipes and a recording fake process; clocks, ``os.read`` and
  ``select.select`` are watched through small proxies;
- ``kb_check.run_check`` is watched with a recorder (and, once, run for real on a bare build directory; the real
  ``kb-check`` hand-over is in ``tests/cli/test_u5_probe_cli.py``).

``CasePath`` is a ``Path`` that is case-sensitive about the names it opens: the build volume of a Mac is not, and a
mutant that opens ``.MCP.JSON`` / ``CURRENT`` / ``BUILDS`` / ``KLODE-KIT.TOML`` would otherwise find the lower-case file.

The parts that need a child process of their own (the C locale, a process killed at a crash point) are in
``tests/contract/test_u5_probe_wi15l.py``.

Law (S4/S5): ``parse_host_stream`` - the answer is the ``tool_result`` of the ``kb_version`` call whatever surrounds it
(``test_pairing_survives_any_event_chatter``); ``_Pipe`` - whatever the bytes are cut into by the reads, the lines out
are the lines in (``test_pipe_lines_round_trip_for_any_cut``). The rest has no law (texts, key names, defaults): S1-S3.

Mutants left alone as equivalent, with the reason:
``read_text(encoding="UTF-8")``, ``.encode("UTF-8")``, ``.decode("UTF-8", ...)`` are the same codecs as ``"utf-8"``;
``parse_host_stream``: ``status = ""`` and ``saw_init = None`` as the initial values (``""`` is no state in the tuple,
``None`` is falsy like ``False``; a stream without ``init`` ends in the same ``failed`` / error text) and
``(status is None) and False`` (``None`` is not in the state tuple either, so ``failed`` again);
``host_probe``: ``resolved is None and exe is None`` - both come from the same PATH lookup, so they are None together;
``json.dumps(..., ensure_ascii=None)`` is falsy like ``False``; argparse: dropping ``default=None`` (``--build``,
``--server``, ``--project``, ``--model``) is the default of argparse.
"""

from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from _support import publish_fx as F
from _support import u5_fx as U
from klode_kit import gate
from klode_kit import registry_write
from klode_kit import u5_probe as P

pytestmark = pytest.mark.unit

T = F.build_id(1)
OLD = F.build_id(0)
SERVER = "demo-klode"
ACK = "G2-20261026"


# --------------------------------------------------------------------------- helpers


class CasePath(type(Path())):
    """A ``Path`` that opens a name only when it is spelled exactly (a case-insensitive volume would not)."""

    def _exact(self) -> bool:
        for part in (self, *self.parents):
            if part.parent == part:
                return True
            try:
                if part.name not in os.listdir(part.parent):
                    return False
            except OSError:
                return False
        return True

    def is_file(self) -> bool:
        return self._exact() and super().is_file()

    def is_dir(self) -> bool:
        return self._exact() and super().is_dir()

    def is_symlink(self) -> bool:
        return self._exact() and super().is_symlink()

    def read_text(self, *args, **kwargs):
        if not self._exact():
            raise FileNotFoundError(str(self))
        return super().read_text(*args, **kwargs)


class Proxy:
    """Delegates to ``real`` except for the attributes overridden."""

    def __init__(self, real, **over):
        self._real, self._over = real, over

    def __getattr__(self, name):
        return self._over[name] if name in self._over else getattr(self._real, name)


SRV = r'''#!%(python)s
import json, os, sys
rec = os.environ.get("SRV_REC")
mode = os.environ.get("SRV_MODE", "ok")
lines = []


def dump():
    if rec:
        with open(rec, "w", encoding="utf-8") as fh:
            json.dump({"lines": lines, "argv": sys.argv[1:], "cwd": os.path.realpath(os.getcwd()),
                       "env": dict(os.environ)}, fh)


def say(doc):
    sys.stdout.write(json.dumps(doc) + "\n")
    sys.stdout.flush()


dump()
if mode == "eof":
    err = os.environ.get("SRV_ERR_HEX")
    if err:
        sys.stderr.buffer.write(bytes.fromhex(err))
        sys.stderr.buffer.flush()
    sys.exit(0)
for raw in sys.stdin.buffer:
    lines.append(raw.decode("utf-8"))
    dump()
    msg = json.loads(raw)
    if "id" not in msg:
        continue
    if msg["method"] == "initialize":
        say({"jsonrpc": "2.0", "id": msg["id"], "result": {"protocolVersion": "2025-06-18", "capabilities": {},
                                                           "serverInfo": {"name": "s", "version": "0"}}})
        continue
    if mode == "noid":
        say({"jsonrpc": "2.0", "id": msg["id"]})
    if mode == "error":
        say({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32000, "message": "boom"}})
        continue
    text = os.environ.get("SRV_TEXT")
    if text is None:
        text = json.dumps({"build_id": os.environ["SRV_BUILD"], "kb_id": "x"})
    say({"jsonrpc": "2.0", "id": msg["id"], "result": {"content": [{"type": "text", "text": text}],
                                                       "isError": mode == "iserror"}})
'''

CLAUDE = r'''#!%(python)s
import json, os, sys, time
with open(os.environ["CL_REC"], "w", encoding="utf-8") as fh:
    json.dump({"argv": sys.argv[1:], "cwd": os.path.realpath(os.getcwd()), "env": dict(os.environ)}, fh)
nap = float(os.environ.get("CL_SLEEP", "0"))
if nap:
    time.sleep(nap)
sys.stdout.buffer.write(bytes.fromhex(os.environ.get("CL_OUT_HEX", "")))
sys.stdout.buffer.flush()
sys.stderr.buffer.write(bytes.fromhex(os.environ.get("CL_ERR_HEX", "")))
sys.stderr.buffer.flush()
sys.exit(int(os.environ.get("CL_EXIT", "0")))
'''


def script(path: Path, template: str) -> Path:
    return U.executable(path, template % {"python": sys.executable})


def base_env(tmp: Path, **extra: str) -> dict[str, str]:
    """The caller's environment: no gate variables, a private fake log."""
    out = dict(os.environ)
    for k in ("KLODE_KIT_REAL", "KLODE_KIT_GATE_ACK"):
        out.pop(k, None)
    out["KLODE_KIT_FAKE_LOG"] = str(tmp / "fake.log")
    out.update(extra)
    return out


def serve(tmp: Path, *, mode: str = "ok", build: str = T, text: str | None = None, err_hex: str | None = None,
          entry_env: dict | None = None, with_args: bool = True, name: str = SERVER) -> tuple[Path, Path]:
    """``<tmp>/proj`` with a ``.mcp.json`` whose entry is the stand-in server; returns (project, record file)."""
    project = tmp / "proj"
    project.mkdir(exist_ok=True)
    rec = tmp / "srv_rec.json"
    exe = script(tmp / "srv.py", SRV)
    env = {"SRV_REC": str(rec), "SRV_MODE": mode, "SRV_BUILD": build, **(entry_env or {})}
    if text is not None:
        env["SRV_TEXT"] = text
    if err_hex is not None:
        env["SRV_ERR_HEX"] = err_hex
    entry: dict = {"command": str(exe), "env": env}
    if with_args:
        entry["args"] = ["--kb", "KB"]
    (project / ".mcp.json").write_text(json.dumps({"mcpServers": {name: entry}}), encoding="utf-8")
    return project, rec


def seen(rec: Path) -> dict:
    return json.loads(rec.read_text(encoding="utf-8"))


def kb_of(tmp: Path, build_id: str = T, name: str = "demo") -> tuple[Path, Path]:
    project = tmp / "proj"
    project.mkdir(exist_ok=True)
    kb = project / "kb" / name
    (kb / "builds" / build_id).mkdir(parents=True, exist_ok=True)
    return project, kb


def gate_open_env(tmp: Path, *, on_path: bool = False, **cl: str) -> dict[str, str]:
    """An environment with the G2 gate *opened* for the stand-in ``claude`` script (the program is this script)."""
    env = base_env(tmp, KLODE_KIT_REAL="1", KLODE_KIT_GATE_ACK=ACK, CL_REC=str(tmp / "cl_rec.json"), **cl)
    if on_path:
        script(tmp / "bin" / "claude", CLAUDE)
        env["PATH"] = str(tmp / "bin") + os.pathsep + env["PATH"]
    return env


def hexed(lines) -> str:
    return ("\n".join(lines) + "\n").encode("utf-8").hex()


def j(obj) -> str:
    return U.j(obj)


PROMPT = ("Call the MCP tool mcp__demo-klode__kb_version exactly once with no arguments, then reply with the single "
          "word DONE.")


def argv_of(budget: str = "1.0", model: str | None = None, prog: str = "claude") -> list[str]:
    out = [prog, "-p", PROMPT, "--output-format", "stream-json", "--verbose", "--allowedTools",
           "mcp__demo-klode__kb_version", "--max-budget-usd", budget]
    return out + (["--model", model] if model else [])


# --------------------------------------------------------------------------- _Pipe


def pipe_of(data: bytes = b""):
    r, w = os.pipe()
    os.write(w, data)
    return SimpleNamespace(stdout=SimpleNamespace(fileno=lambda: r)), r, w


@pytest.fixture()
def fds():
    opened: list[int] = []
    yield opened
    for fd in opened:
        try:
            os.close(fd)
        except OSError:
            pass


def test_pipe_deadline_equal_to_now_is_a_timeout_even_when_data_is_waiting(monkeypatch, fds):
    proc, r, w = pipe_of(b"line\n")
    fds += [r, w]
    monkeypatch.setattr(P, "time", Proxy(time, monotonic=lambda: 100.0))
    with pytest.raises(TimeoutError):
        P._Pipe(proc).line(100.0)


def test_pipe_with_time_left_reads_and_hands_the_remaining_time_to_select(monkeypatch, fds):
    proc, r, w = pipe_of(b"line\nrest")
    fds += [r, w]
    waits = []

    def spy(rl, wl, xl, timeout):
        waits.append(timeout)
        return select_real(rl, wl, xl, timeout)

    import select as select_real_mod
    select_real = select_real_mod.select
    monkeypatch.setattr(P, "time", Proxy(time, monotonic=lambda: 100.0))
    monkeypatch.setattr(P, "select", Proxy(select_real_mod, select=spy))
    pipe = P._Pipe(proc)
    assert pipe.line(100.25) == b"line"
    assert waits == [0.25] and pipe.buf == b"rest"


def test_pipe_one_second_left_is_still_time_to_read(fds):
    proc, r, w = pipe_of(b"x\n")
    fds += [r, w]
    assert P._Pipe(proc).line(time.monotonic() + 0.5) == b"x"


def test_pipe_with_nothing_to_read_times_out_after_the_wait(fds):
    proc, r, w = pipe_of(b"")
    fds += [r, w]
    t0 = time.monotonic()
    with pytest.raises(TimeoutError):
        P._Pipe(proc).line(t0 + 0.3)
    assert 0.2 < time.monotonic() - t0 < 5


def test_pipe_reads_at_most_64_kib_at_a_time(monkeypatch, fds):
    proc, r, w = pipe_of(b"abc\n")
    fds += [r, w]
    sizes = []

    def spy(fd, n):
        sizes.append(n)
        return os.read(fd, n)

    monkeypatch.setattr(P, "os", Proxy(os, read=spy))
    assert P._Pipe(proc).line(time.monotonic() + 5) == b"abc"
    assert sizes == [65536]


def test_pipe_keeps_what_was_read_across_reads(monkeypatch, fds):
    proc, r, w = pipe_of(b'{"id": 1, "result": {}}\n')
    fds += [r, w]
    monkeypatch.setattr(P, "os", Proxy(os, read=lambda fd, n: os.read(fd, 3)))
    assert P._Pipe(proc).line(time.monotonic() + 5) == b'{"id": 1, "result": {}}'


def test_pipe_eof_is_none_and_the_partial_line_is_not_a_line(fds):
    proc, r, w = pipe_of(b"partial")
    fds += [r]
    os.close(w)
    assert P._Pipe(proc).line(time.monotonic() + 5) is None


@settings(max_examples=80, deadline=None)
@given(lines=st.lists(st.binary(max_size=40).filter(lambda b: b"\n" not in b), min_size=1, max_size=5),
       cut=st.integers(1, 9))
def test_pipe_lines_round_trip_for_any_cut(lines, cut):
    """Law (S5): the lines out are the lines in, whatever size the reads are cut to."""
    r, w = os.pipe()
    os.write(w, b"".join(x + b"\n" for x in lines))
    proc = SimpleNamespace(stdout=SimpleNamespace(fileno=lambda: r))
    pipe = P._Pipe(proc)
    real_read = os.read
    orig = P.os
    P.os = Proxy(os, read=lambda fd, n: real_read(fd, cut))
    try:
        got = [pipe.line(time.monotonic() + 5) for _ in lines]
    finally:
        P.os = orig
        os.close(r)
        os.close(w)
    assert got == lines


def test_answer_takes_the_response_with_the_id_and_skips_everything_else(fds):
    data = b"\n".join([
        b"plain noise",
        b'{"jsonrpc": "2.0", "id": 1}',                                   # an id but neither result nor error
        b'{"jsonrpc": "2.0", "id": 9, "result": {}}',                      # another id
        b'[1, 2]',                                                         # JSON, no object
        b'{"jsonrpc": "2.0", "id": 1, "error": {"code": -1, "message": "m"}}',
        b'{"jsonrpc": "2.0", "id": 1, "result": {"late": true}}',
    ]) + b"\n"
    proc, r, w = pipe_of(data)
    fds += [r, w]
    msg = P._Pipe(proc).answer(1, time.monotonic() + 5)
    assert msg == {"jsonrpc": "2.0", "id": 1, "error": {"code": -1, "message": "m"}}


def test_answer_at_eof_is_none(fds):
    proc, r, w = pipe_of(b"noise\n")
    fds += [r]
    os.close(w)
    assert P._Pipe(proc).answer(1, time.monotonic() + 5) is None


# --------------------------------------------------------------------------- _leave


class FakeStdin:
    def __init__(self, log, raises):
        self.log, self.raises = log, raises

    def close(self):
        self.log.append("close")
        if self.raises:
            raise BrokenPipeError("flush on close")


class FakeProc:
    """Dies at EOF, at SIGTERM or only at SIGKILL (``dies_on``: eof / terminate / kill)."""

    def __init__(self, dies_on: str, raises: bool = False):
        self.log: list = []
        self.stdin = FakeStdin(self.log, raises)
        self.dies_on, self.dead = dies_on, dies_on == "eof"

    def terminate(self):
        self.log.append("terminate")
        self.dead = self.dead or self.dies_on == "terminate"

    def kill(self):
        self.log.append("kill")
        self.dead = True

    def wait(self, timeout=None):
        self.log.append(("wait", timeout))
        if not self.dead:
            raise subprocess.TimeoutExpired("fake", timeout)
        return 0


@pytest.mark.parametrize("dies_on,expected", [
    ("eof", ["close", ("wait", 3.0)]),
    ("terminate", ["close", ("wait", 3.0), "terminate", ("wait", 3.0)]),
    ("kill", ["close", ("wait", 3.0), "terminate", ("wait", 3.0), "kill", ("wait", 3.0)]),
])
def test_leave_asks_eof_then_sigterm_then_sigkill_each_with_the_grace(dies_on, expected):
    proc = FakeProc(dies_on)
    P._leave(proc)
    assert proc.log == expected and P.LEAVE_GRACE == 3.0


def test_leave_survives_a_stdin_that_cannot_be_closed_cleanly():
    proc = FakeProc("eof", raises=True)
    P._leave(proc)
    assert proc.log == ["close", ("wait", 3.0)]


def test_leave_really_kills_a_server_that_ignores_eof_and_sigterm(monkeypatch):
    monkeypatch.setattr(P, "LEAVE_GRACE", 0.4)
    code = ("import signal, sys, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "print('ready', flush=True)\ntime.sleep(60)\n")
    proc = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    assert proc.stdout.readline() == b"ready\n"
    P._leave(proc)
    assert proc.returncode == -signal.SIGKILL
    proc.stdout.close()


# --------------------------------------------------------------------------- _version_of / _text_of


def test_version_of_texts_are_exact():
    assert P._version_of(json.dumps({"build_id": T})) == (T, None)
    long = "z" * 200
    assert P._version_of(long) == (None, f"the kb_version answer is not JSON: {long[:120]!r}")
    assert P._version_of("{}") == (None, "the kb_version answer has no build_id")
    assert P._version_of("[1]") == (None, "the kb_version answer has no build_id")
    assert P._version_of(json.dumps({"build_id": "q" * 100})) == (None, f"build_id {'q' * 60!r} is not a build id")
    assert P._version_of(json.dumps({"build_id": 12345})) == (None, "build_id '12345' is not a build id")
    assert P._version_of(json.dumps({"build_id": T + "x"}))[0] is None


def test_text_of_joins_only_the_text_blocks_and_anything_else_is_empty():
    assert P._text_of("plain") == "plain"
    blocks = [{"type": "text", "text": "a"}, {"type": "text"}, {"type": "image", "text": "no"}, "s", None,
              {"type": "text", "text": "b"}]
    assert P._text_of(blocks) == "ab"
    assert P._text_of([{"type": "text"}]) == ""
    assert P._text_of(None) == ""
    assert P._text_of(5) == ""
    assert P._text_of({"type": "text", "text": "x"}) == ""


# --------------------------------------------------------------------------- direct_probe: what is sent, how it starts


def test_direct_probe_sends_exactly_initialize_initialized_and_the_kb_version_call(tmp_path):
    project, rec = serve(tmp_path)
    out = P.direct_probe(project, SERVER, env=base_env(tmp_path))
    assert out["ok"] is True
    lines = seen(rec)["lines"]
    assert all(x.endswith("\n") and x.count("\n") == 1 for x in lines)
    assert [json.loads(x) for x in lines] == [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "klode-kit-u5-probe", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "kb_version", "arguments": {}}},
    ]


def test_direct_probe_result_dict_on_success_is_exact(tmp_path):
    project, rec = serve(tmp_path)
    out = P.direct_probe(project, SERVER, env=base_env(tmp_path))
    exe = str(tmp_path / "srv.py")
    assert out == {"ok": True, "build_id": T, "error": None, "argv": [exe, "--kb", "KB"], "cwd": str(project)}
    got = seen(rec)
    assert got["argv"] == ["--kb", "KB"] and got["cwd"] == os.path.realpath(project)


def test_direct_probe_entry_without_args_is_started_with_the_command_alone(tmp_path):
    project, rec = serve(tmp_path, with_args=False)
    out = P.direct_probe(project, SERVER, env=base_env(tmp_path))
    assert out["ok"] is True and out["argv"] == [str(tmp_path / "srv.py")] and seen(rec)["argv"] == []


def test_direct_probe_environment_is_the_callers_plus_the_entrys_not_the_processs(tmp_path, monkeypatch):
    monkeypatch.setenv("U5_AMBIENT", "from-process")
    project, rec = serve(tmp_path, entry_env={"SRV_OVERRIDE": "entry", "SRV_NUMBER": 5})
    out = P.direct_probe(project, SERVER, env={"ONLY_IN_ENV": "1", "SRV_OVERRIDE": "caller"})
    assert out["ok"] is True
    env = seen(rec)["env"]
    assert env["ONLY_IN_ENV"] == "1" and "U5_AMBIENT" not in env
    assert env["SRV_OVERRIDE"] == "entry" and env["SRV_NUMBER"] == "5"


def test_direct_probe_without_env_uses_the_process_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("U5_AMBIENT", "from-process")
    project, rec = serve(tmp_path)
    assert P.direct_probe(project, SERVER)["ok"] is True
    assert seen(rec)["env"]["U5_AMBIENT"] == "from-process"


# --------------------------------------------------------------------------- direct_probe: failures, exact results


def failed(project: Path, error: str, argv: list[str] | None = None) -> dict:
    return {"ok": False, "build_id": None, "error": error, "argv": argv or [], "cwd": str(project)}


def test_direct_probe_no_mcp_json_result_is_exact(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    assert P.direct_probe(project, SERVER, env=base_env(tmp_path)) == failed(project, f".mcp.json not found in {project}")


@pytest.mark.parametrize("doc", ["[]", "5", "null", '"text"', '{"mcpServers": []}', '{"mcpServers": "x"}',
                                 '{"mcpServers": null}', '{"mcpServers": {"demo-klode": []}}',
                                 '{"mcpServers": {"other": {"command": "x"}}}'])
def test_direct_probe_mcp_json_of_the_wrong_shape_has_no_entry(tmp_path, doc):
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".mcp.json").write_text(doc, encoding="utf-8")
    assert P.direct_probe(project, SERVER, env=base_env(tmp_path)) == failed(project, "no entry 'demo-klode' in .mcp.json")


@pytest.mark.parametrize("command", [None, "", 5])
def test_direct_probe_entry_without_a_usable_command(tmp_path, command):
    project = tmp_path / "proj"
    project.mkdir()
    entry = {"args": []} if command is None else {"command": command}
    (project / ".mcp.json").write_text(json.dumps({"mcpServers": {SERVER: entry}}), encoding="utf-8")
    assert P.direct_probe(project, SERVER, env=base_env(tmp_path)) == failed(
        project, "the .mcp.json entry 'demo-klode' has no command")


def test_direct_probe_broken_json_says_why(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".mcp.json").write_text("{ broken", encoding="utf-8")
    out = P.direct_probe(project, SERVER, env=base_env(tmp_path))
    assert out["ok"] is False and out["argv"] == [] and out["error"].startswith(".mcp.json cannot be read as JSON (")
    assert out["error"].endswith(")") and "Expecting property name" in out["error"]


def test_direct_probe_mcp_json_that_is_not_utf8_cannot_be_read(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".mcp.json").write_bytes(b'{"mcpServers": "\xff\xfe"}')
    out = P.direct_probe(project, SERVER, env=base_env(tmp_path))
    assert out["ok"] is False and out["error"].startswith(".mcp.json cannot be read as JSON (")


def test_direct_probe_the_command_that_cannot_be_started_keeps_its_argv(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".mcp.json").write_text(json.dumps({"mcpServers": {SERVER: {"command": "/nonexistent/server",
                                                                         "args": ["a", 7]}}}), encoding="utf-8")
    out = P.direct_probe(project, SERVER, env=base_env(tmp_path))
    assert out["argv"] == ["/nonexistent/server", "a", "7"] and out["ok"] is False and out["build_id"] is None
    assert out["error"].startswith("the server cannot be started: ") and "/nonexistent/server" in out["error"]
    assert set(out) == {"ok", "build_id", "error", "argv", "cwd"}


def test_direct_probe_mcp_json_is_opened_by_its_exact_name(tmp_path, monkeypatch):
    project, _ = serve(tmp_path)
    (project / ".mcp.json").rename(project / "mcp-original")
    (project / "mcp-original").rename(project / ".MCP.JSON")  # not the name the probe reads
    monkeypatch.setattr(P, "Path", CasePath)
    out = P.direct_probe(project, SERVER, env=base_env(tmp_path))
    assert out["ok"] is False and out["error"] == f".mcp.json not found in {project}"


def test_direct_probe_server_that_closes_at_once_without_stderr(tmp_path):
    project, _ = serve(tmp_path, mode="eof")
    out = P.direct_probe(project, SERVER, env=base_env(tmp_path))
    assert out == failed(project, "the server closed its output before answering",
                         [str(tmp_path / "srv.py"), "--kb", "KB"])


def test_direct_probe_stderr_of_a_closed_server_is_quoted_from_its_start_when_short(tmp_path):
    project, _ = serve(tmp_path, mode="eof", err_hex=b"server: going away\n".hex())
    out = P.direct_probe(project, SERVER, env=base_env(tmp_path))
    assert out["error"] == "the server closed its output before answering: server: going away"


def test_direct_probe_stderr_is_cut_to_its_last_300_bytes(tmp_path):
    project, _ = serve(tmp_path, mode="eof", err_hex=(b"X" + b"Y" * 300 + b"\n").hex())
    out = P.direct_probe(project, SERVER, env=base_env(tmp_path))
    assert out["error"] == "the server closed its output before answering: " + "Y" * 299  # 300 bytes, the last a newline


def test_direct_probe_stderr_that_is_not_utf8_is_replaced_not_fatal(tmp_path):
    project, _ = serve(tmp_path, mode="eof", err_hex=b"bad\xff\xfeend".hex())
    out = P.direct_probe(project, SERVER, env=base_env(tmp_path))
    assert out["error"] == "the server closed its output before answering: bad��end"


def test_direct_probe_error_response_is_read_as_an_error_with_its_text(tmp_path):
    project, _ = serve(tmp_path, mode="error")
    out = P.direct_probe(project, SERVER, env=base_env(tmp_path))
    assert out == failed(project, "kb_version returned isError: {'code': -32000, 'message': 'boom'}",
                         [str(tmp_path / "srv.py"), "--kb", "KB"])


def test_direct_probe_iserror_text_is_cut_to_200_characters(tmp_path):
    project, _ = serve(tmp_path, mode="iserror", text="t" * 300)
    out = P.direct_probe(project, SERVER, env=base_env(tmp_path))
    assert out["ok"] is False and out["error"] == "kb_version returned isError: " + "t" * 200


def test_direct_probe_response_without_result_or_error_for_the_id_is_skipped(tmp_path):
    project, _ = serve(tmp_path, mode="noid")
    out = P.direct_probe(project, SERVER, env=base_env(tmp_path))
    assert out["ok"] is True and out["build_id"] == T and out["error"] is None


def test_direct_probe_answer_texts_of_the_version_are_exact(tmp_path):
    project, _ = serve(tmp_path, text="x" * 150)
    out = P.direct_probe(project, SERVER, env=base_env(tmp_path))
    assert out["ok"] is False and out["build_id"] is None
    assert out["error"] == f"the kb_version answer is not JSON: {('x' * 150)[:120]!r}"


def test_direct_probe_deadline_text_uses_the_timeout_given(tmp_path):
    project, _ = serve(tmp_path)
    out = P.direct_probe(project, SERVER, env=base_env(tmp_path), timeout=0.0)
    assert out == failed(project, "timed out after 0s waiting for the server", [str(tmp_path / "srv.py"), "--kb", "KB"])


# --------------------------------------------------------------------------- parse_host_stream


def parse(events, server=SERVER):
    return P.parse_host_stream([j(e) if not isinstance(e, str) else e for e in events], server)


def call(tool_id="t"):
    return U.tool_use(tool_id, "mcp__demo-klode__kb_version")


def test_stream_clean_session_result_is_exact_with_cost():
    got = P.parse_host_stream(U.stream(SERVER, T), SERVER)
    assert got == {"host_state": "connected", "build_id": T, "tool_use_id": "toolu_real", "error": None,
                   "cost_usd": 0.01}


def test_stream_no_init_result_is_exact_and_keeps_the_cost_of_the_result_line():
    got = parse([U.say("hi"), U.result_line("x", cost=0.5)])
    assert got == {"host_state": "failed", "build_id": None, "tool_use_id": None,
                   "error": "no system/init message in the stream", "cost_usd": 0.5}
    assert parse([])["cost_usd"] is None


def test_stream_cost_is_a_float_and_only_a_number_of_a_result_line():
    assert parse([U.init(SERVER), {"type": "result", "total_cost_usd": 2}])["cost_usd"] == 2.0
    assert isinstance(parse([U.init(SERVER), {"type": "result", "total_cost_usd": 2}])["cost_usd"], float)
    assert parse([U.init(SERVER), {"type": "result", "total_cost_usd": "0.5"}])["cost_usd"] is None
    assert parse([U.init(SERVER), {"type": "result"}])["cost_usd"] is None
    # numbers on events that are not result lines are not the cost
    assert parse([U.init(SERVER), {"type": "system", "subtype": "note", "total_cost_usd": 3}])["cost_usd"] is None
    assert parse([{"type": "weird", "total_cost_usd": 3}, U.init(SERVER)])["cost_usd"] is None
    assert parse([U.init(SERVER), {"type": "RESULT", "total_cost_usd": 3}])["cost_usd"] is None


def test_stream_an_assistant_event_with_a_cost_field_is_still_read_for_its_blocks():
    event = call()
    event["total_cost_usd"] = 0.5
    got = parse([U.init(SERVER), event, U.tool_result("t", U.version_text(T))])
    assert got["build_id"] == T and got["cost_usd"] is None


def test_stream_init_means_system_and_init_together():
    only_system = {"type": "system", "subtype": "hook_started", "mcp_servers": [{"name": SERVER, "status": "connected"}]}
    only_subtype = {"type": "assistant", "subtype": "init", "mcp_servers": [{"name": SERVER, "status": "connected"}]}
    for event in (only_system, only_subtype):
        got = parse([event])
        assert got["host_state"] == "failed" and got["error"] == "no system/init message in the stream"
    # a later system event does not overwrite what init said
    later = {"type": "system", "subtype": "status", "mcp_servers": [{"name": SERVER, "status": "pending"}]}
    assert parse([U.init(SERVER), later])["host_state"] == "connected"


def test_stream_no_call_result_is_exact():
    got = parse([U.init(SERVER, "pending"), U.say("no tool")])
    assert got == {"host_state": "pending", "build_id": None, "tool_use_id": None,
                   "error": "no kb_version call (mcp__demo-klode__kb_version) in the stream", "cost_usd": None}


def test_stream_call_without_result_is_exact_and_names_the_last_call():
    got = parse([U.init(SERVER), call("t1"), call("t2"), U.say("x"), U.result_line("y", cost=0.25)])
    assert got == {"host_state": "connected", "build_id": None, "tool_use_id": "t2",
                   "error": "no tool_result for the kb_version tool_use t2", "cost_usd": 0.25}


def test_stream_error_result_is_exact_and_cut_to_200_characters():
    got = parse([U.init(SERVER), call(), U.tool_result("t", "e" * 300, is_error=True)])
    assert got == {"host_state": "connected", "build_id": None, "tool_use_id": "t",
                   "error": "the tool_result has is_error: " + "e" * 200, "cost_usd": None}


def test_stream_a_block_that_is_no_tool_result_is_not_a_result_even_with_a_tool_use_id():
    fake = {"type": "user", "message": {"content": [
        {"type": "text", "tool_use_id": "t", "content": [{"type": "text", "text": U.version_text(T)}]}]}}
    got = parse([U.init(SERVER), call(), fake])
    assert got["build_id"] is None and got["error"] == "no tool_result for the kb_version tool_use t"


def test_stream_odd_blocks_before_the_call_in_the_same_message_do_not_hide_it():
    event = {"type": "assistant", "message": {"content": [
        "text", None, 3, {"type": "tool_use", "id": "t", "name": "mcp__demo-klode__kb_version", "input": {}}]}}
    got = parse([U.init(SERVER), event, U.tool_result("t", U.version_text(T))])
    assert got["build_id"] == T and got["tool_use_id"] == "t"


def test_stream_odd_blocks_before_a_result_in_the_same_message_do_not_hide_it():
    event = {"type": "user", "message": {"content": [
        None, "s", {"type": "tool_result", "tool_use_id": "t", "content": U.version_text(T)}]}}
    assert parse([U.init(SERVER), call(), event])["build_id"] == T


def test_stream_server_named_in_init_among_others_and_unnamed_ones():
    others = [{"name": "other", "status": "failed"}, "junk", None]
    event = {"type": "system", "subtype": "init", "mcp_servers": [*others, {"name": SERVER, "status": "pending"}]}
    assert parse([event])["host_state"] == "pending"
    assert parse([{"type": "system", "subtype": "init", "mcp_servers": None}])["host_state"] == "failed"
    assert parse([{"type": "system", "subtype": "init", "mcp_servers": [{"name": SERVER}]}])["host_state"] == "failed"


@settings(max_examples=60, deadline=None)
@given(noise=st.lists(st.sampled_from(["say", "other_tool", "orphan", "system", "junk", "result"]), max_size=8),
       where=st.integers(0, 8))
def test_pairing_survives_any_event_chatter(noise, where):
    """Law (S4): the build id is the answer of the kb_version call - whatever events surround the call and its result."""
    def make(kind, i):
        return {
            "say": U.say(f"prose {OLD}"),
            "other_tool": U.tool_use(f"o{i}", "Read"),
            "orphan": U.tool_result(f"never{i}", U.version_text(OLD)),
            "system": {"type": "system", "subtype": "status", "total_cost_usd": 9, "mcp_servers": []},
            "junk": "not json",
            "result": U.result_line(f"build {OLD}", cost=0.5),
        }[kind]

    chatter = [make(k, i) for i, k in enumerate(noise)]
    cut = min(where, len(chatter))
    events = [U.init(SERVER), *chatter[:cut], call("real"), U.tool_result("real", U.version_text(T)), *chatter[cut:]]
    got = parse(events)
    assert (got["build_id"], got["tool_use_id"], got["host_state"], got["error"]) == (T, "real", "connected", None)


# --------------------------------------------------------------------------- host_probe: the claude command line


def run_host(tmp, *, out_lines=None, out_hex=None, err_hex="", exit_code=0, nap=0.0, on_path=True, **kw):
    project = tmp / "proj"
    project.mkdir(exist_ok=True)
    env = gate_open_env(tmp, on_path=on_path, CL_OUT_HEX=out_hex if out_hex is not None else hexed(out_lines or []),
                        CL_ERR_HEX=err_hex, CL_EXIT=str(exit_code), CL_SLEEP=str(nap))
    return project, env, P.host_probe(project, SERVER, env=env, **kw)


def recorded(tmp) -> dict:
    return json.loads((tmp / "cl_rec.json").read_text(encoding="utf-8"))


def test_host_probe_command_line_and_result_are_exact(tmp_path):
    project, env, out = run_host(tmp_path, out_lines=U.stream(SERVER, T))
    argv = argv_of()
    assert out == {"argv": argv, "cwd": str(project), "exit_code": 0, "host_state": "connected", "build_id": T,
                   "tool_use_id": "toolu_real", "error": None, "cost_usd": 0.01}
    got = recorded(tmp_path)
    assert got["argv"] == argv[1:] and got["cwd"] == os.path.realpath(project)


def test_host_probe_budget_and_model_are_part_of_the_command_line(tmp_path):
    _, _, out = run_host(tmp_path, out_lines=U.stream(SERVER, T), max_budget_usd=0.25, model="synthetic-model")
    assert out["argv"] == argv_of("0.25", "synthetic-model") and recorded(tmp_path)["argv"] == out["argv"][1:]


def test_host_probe_environment_is_the_callers_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("U5_AMBIENT", "from-process")
    project = tmp_path / "proj"
    project.mkdir()
    exe = script(tmp_path / "bin" / "claude", CLAUDE)
    env = {"PATH": str(exe.parent), "KLODE_KIT_REAL": "1", "KLODE_KIT_GATE_ACK": ACK, "ONLY_IN_ENV": "1",
           "CL_REC": str(tmp_path / "cl_rec.json"), "CL_OUT_HEX": hexed(U.stream(SERVER, T))}
    assert P.host_probe(project, SERVER, env=env)["build_id"] == T
    got = recorded(tmp_path)["env"]
    assert {k: got[k] for k in env} == env and "U5_AMBIENT" not in got


def test_host_probe_claude_argument_names_the_program(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    exe = script(tmp_path / "elsewhere" / "myclaude", CLAUDE)
    env = gate_open_env(tmp_path, CL_OUT_HEX=hexed(U.stream(SERVER, T)))
    out = P.host_probe(project, SERVER, env=env, claude=str(exe))
    assert out["argv"] == argv_of(prog=str(exe)) and out["build_id"] == T
    assert recorded(tmp_path)["argv"] == argv_of()[1:]


def test_host_probe_starts_claude_without_a_stdin_and_waits_for_the_given_time(tmp_path, monkeypatch):
    calls = []
    real = subprocess.run

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(P, "subprocess", Proxy(subprocess, run=spy))
    _, env, _ = run_host(tmp_path, out_lines=U.stream(SERVER, T), timeout=77.0)
    (args, kwargs), = calls
    assert kwargs["stdin"] is subprocess.DEVNULL and kwargs["timeout"] == 77.0 and kwargs["capture_output"] is True
    assert kwargs["env"] == env and kwargs["cwd"] == str(tmp_path / "proj")
    assert args[0][0] == str(tmp_path / "bin" / "claude") and args[0][1:] == argv_of()[1:]


def test_host_probe_default_timeout_is_five_minutes(tmp_path, monkeypatch):
    calls = []
    real = subprocess.run
    monkeypatch.setattr(P, "subprocess", Proxy(subprocess, run=lambda *a, **k: (calls.append(k), real(*a, **k))[1]))
    run_host(tmp_path, out_lines=U.stream(SERVER, T))
    assert calls[0]["timeout"] == 300.0 and P.HOST_TIMEOUT == 300.0 and P.DIRECT_TIMEOUT == 60.0


def test_host_probe_timeout_result_is_exact(tmp_path):
    project, _, out = run_host(tmp_path, out_lines=U.stream(SERVER, T), nap=5, timeout=0.5)
    assert out == {"argv": argv_of(), "cwd": str(project), "exit_code": None, "host_state": "failed",
                   "build_id": None, "tool_use_id": None, "cost_usd": None, "error": "claude timed out after 0.5s"}


def test_host_probe_without_claude_names_the_program(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    env = base_env(tmp_path, KLODE_KIT_REAL="1", KLODE_KIT_GATE_ACK=ACK, PATH=str(tmp_path / "empty"))
    with pytest.raises(P.ProbeEnvironmentError) as exc:
        P.host_probe(project, SERVER, env=env)
    assert str(exc.value) == "claude: not found on PATH"
    with pytest.raises(P.ProbeEnvironmentError) as exc:
        P.host_probe(project, SERVER, env=env, claude="otherclaude")
    assert str(exc.value) == "otherclaude: not found on PATH"


def test_host_probe_stdout_that_is_not_utf8_is_replaced_not_fatal(tmp_path):
    raw = ("\n".join(U.stream(SERVER, T)) + "\n").encode("utf-8") + b"\xff\xfe not text\n"
    _, _, out = run_host(tmp_path, out_hex=raw.hex())
    assert out["build_id"] == T and out["error"] is None


@pytest.mark.parametrize("stream,err,expected", [
    ("none", b"boom\n", "claude exit 3: boom; no system/init message in the stream"),
    ("full", b"", "claude exit 3"),
    ("full", b"boom", "claude exit 3: boom"),
    ("none", b"", "claude exit 3; no system/init message in the stream"),
    ("none", b"  \n boom \n\n", "claude exit 3: boom; no system/init message in the stream"),
])
def test_host_probe_nonzero_exit_error_text_is_exact(tmp_path, stream, err, expected):
    lines = U.stream(SERVER, T) if stream == "full" else []
    _, _, out = run_host(tmp_path, out_lines=lines, err_hex=err.hex(), exit_code=3)
    assert out["error"] == expected and out["exit_code"] == 3


def test_host_probe_stderr_tail_is_the_last_200_characters_of_the_stripped_text(tmp_path):
    err = b"S" + b"T" * 250 + b"\n\n"
    _, _, out = run_host(tmp_path, out_lines=U.stream(SERVER, T), err_hex=err.hex(), exit_code=3)
    assert out["error"] == "claude exit 3: " + "T" * 200


def test_host_probe_stderr_that_is_not_utf8_is_replaced(tmp_path):
    _, _, out = run_host(tmp_path, out_lines=U.stream(SERVER, T), err_hex=b"bad\xffend".hex(), exit_code=3)
    assert out["error"] == "claude exit 3: bad�end"


def test_host_probe_exit_zero_with_a_clean_stream_has_no_error_and_records_the_exit(tmp_path):
    _, _, out = run_host(tmp_path, out_lines=U.stream(SERVER, T), err_hex=b"noise on stderr".hex())
    assert out["error"] is None and out["exit_code"] == 0


# --------------------------------------------------------------------------- decide (the table is in test_u5_probe.py)


def test_decide_exit_codes_are_the_pipeline_codes():
    assert P.decide(T, T, True, "connected", T) == (True, 0)
    assert P.decide(T, OLD, True, "connected", T) == (False, 21)
    assert P.REASON_OF[21].value == "post_publish_failed" and P.REASON_OF[0].value == "ok"
    assert P.REASON_OF[30].value == "contract_violation" and P.REASON_OF[40].value == "environment"


# --------------------------------------------------------------------------- run: usage bodies


def usage_body(kb: Path, build_id, detail: str) -> dict:
    return {"schema": "klode-kit.u5_probe/1", "kb_id": kb.name, "build_id": build_id, "u5_ready": False,
            "exit_code": 2, "reason": "usage", "detail": detail}


def test_run_without_a_build_to_probe(tmp_path):
    project, kb = kb_of(tmp_path)
    code, body = P.run(kb, None, project=project, env=base_env(tmp_path))
    assert code == 2 and body == usage_body(kb, None, "no build to probe: give --build (a build id) or point current")


def test_run_with_a_build_that_is_no_build_id_reports_it_as_given(tmp_path):
    project, kb = kb_of(tmp_path)
    code, body = P.run(kb, "latest", project=project, env=base_env(tmp_path))
    assert code == 2 and body == usage_body(kb, "latest", "no build to probe: give --build (a build id) or point current")


def test_run_without_a_derivable_project(tmp_path):
    kb = tmp_path / "elsewhere" / "demo"
    (kb / "builds" / T).mkdir(parents=True)
    code, body = P.run(kb, T, env=base_env(tmp_path))
    assert code == 2 and body == usage_body(kb, T, "no --project and the kb is not below <project>/kb/")


def test_run_with_a_missing_build_directory(tmp_path):
    project, kb = kb_of(tmp_path)
    code, body = P.run(kb, F.build_id(9), project=project, env=base_env(tmp_path))
    assert code == 2 and body == usage_body(kb, F.build_id(9), f"builds/{F.build_id(9)} does not exist")


def test_run_build_directory_is_looked_up_as_builds_exactly(tmp_path, monkeypatch):
    project, kb = kb_of(tmp_path)
    serve(tmp_path)
    monkeypatch.setattr(P, "Path", CasePath)
    (kb / "builds").rename(kb / "BUILDS")  # not the name the probe reads
    code, body = P.run(kb, T, project=project, env=base_env(tmp_path))
    assert code == 2 and body["detail"] == f"builds/{T} does not exist"


# --------------------------------------------------------------------------- run: the current link


@pytest.mark.parametrize("link", ["builds/" + T, "/somewhere/else/builds/" + T])
def test_run_current_link_is_read_by_its_last_component(tmp_path, link):
    project, kb = kb_of(tmp_path)
    os.symlink(link, kb / "current")
    assert P._current_of(kb) == T


def test_current_link_is_looked_up_as_current_exactly(tmp_path):
    _, kb = kb_of(tmp_path)
    os.symlink("builds/" + T, kb / "current")
    assert P._current_of(CasePath(kb)) == T
    (kb / "current").rename(kb / "CURRENT")  # not the name the probe reads
    assert P._current_of(CasePath(kb)) is None


def test_current_that_is_no_symlink_or_points_at_no_build_id_is_nothing(tmp_path):
    project, kb = kb_of(tmp_path)
    (kb / "current").mkdir()
    assert P._current_of(kb) is None


def test_run_current_pointing_at_a_non_build_name_is_a_usage_error_with_no_build(tmp_path):
    project, kb = kb_of(tmp_path)
    os.symlink("builds/latest", kb / "current")
    assert P._current_of(kb) is None
    code, body = P.run(kb, None, project=project, env=base_env(tmp_path))
    assert code == 2 and body == usage_body(kb, None, "no build to probe: give --build (a build id) or point current")


# --------------------------------------------------------------------------- run: the project, the probes, the body


def test_run_given_project_wins_over_the_one_above_kb(tmp_path):
    _, kb = kb_of(tmp_path)  # the project above kb/ has no .mcp.json
    (tmp_path / "real").mkdir()
    real_project, _ = serve(tmp_path / "real")
    code, body = P.run(kb, T, project=real_project, env=base_env(tmp_path))
    assert code == 0 and body["project"] == str(real_project) and body["direct"]["ok"] is True


def test_run_project_defaults_to_the_directory_above_kb(tmp_path):
    project, kb = kb_of(tmp_path)
    serve(tmp_path)
    code, body = P.run(kb, T, env=base_env(tmp_path))
    assert code == 0 and body["project"] == str(project) and body["direct"]["ok"] is True


def test_run_body_of_a_direct_only_probe_is_exact(tmp_path):
    project, kb = kb_of(tmp_path)
    _, rec = serve(tmp_path)
    code, body = P.run(kb, T, project=project, env=base_env(tmp_path))
    assert code == 0
    assert body == {"schema": "klode-kit.u5_probe/1", "kb_id": "demo", "build_id": T, "u5_ready": False, "exit_code": 0,
                    "reason": "ok", "server": SERVER, "project": str(project), "host_state": "not_run",
                    "probe_build_id": T, "direct": {"ok": True, "build_id": T, "error": None,
                                                    "argv": [str(tmp_path / "srv.py"), "--kb", "KB"],
                                                    "cwd": str(project)}, "host": None}


def test_run_body_of_a_mismatch_says_post_publish_failed(tmp_path):
    project, kb = kb_of(tmp_path)
    serve(tmp_path, build=OLD)
    code, body = P.run(kb, T, project=project, env=base_env(tmp_path))
    assert (code, body["exit_code"], body["reason"], body["probe_build_id"]) == (21, 21, "post_publish_failed", OLD)


def test_run_server_name_is_the_one_given_or_the_kb_name_plus_klode(tmp_path):
    project, kb = kb_of(tmp_path)
    serve(tmp_path, name="custom")
    code, body = P.run(kb, T, project=project, server="custom", env=base_env(tmp_path))
    assert code == 0 and body["server"] == "custom"
    code, body = P.run(kb, T, project=project, env=base_env(tmp_path))
    assert code == 21 and body["server"] == SERVER and "no entry 'demo-klode'" in body["direct"]["error"]


def test_run_hands_its_env_and_timeout_to_the_direct_probe(tmp_path, monkeypatch):
    monkeypatch.setenv("U5_AMBIENT", "from-process")
    project, kb = kb_of(tmp_path)
    _, rec = serve(tmp_path)
    code, body = P.run(kb, T, project=project, env={"ONLY_IN_ENV": "1"})
    assert code == 0 and seen(rec)["env"]["ONLY_IN_ENV"] == "1" and "U5_AMBIENT" not in seen(rec)["env"]
    code, body = P.run(kb, T, project=project, env=base_env(tmp_path), timeout=0.0)
    assert code == 21 and body["direct"]["error"] == "timed out after 0s waiting for the server"


def test_run_without_check_does_not_call_kb_check_or_write_a_receipt(tmp_path):
    project, kb = kb_of(tmp_path)
    serve(tmp_path)
    code, body = P.run(kb, T, project=project, env=base_env(tmp_path))
    assert "check" not in body and not (kb / "builds" / T / "check_receipt.json").exists()


# --------------------------------------------------------------------------- run: the host probe


def test_run_hands_claude_budget_model_and_timeout_to_the_host_probe(tmp_path):
    project, kb = kb_of(tmp_path)
    serve(tmp_path)
    exe = script(tmp_path / "elsewhere" / "myclaude", CLAUDE)
    env = gate_open_env(tmp_path, CL_OUT_HEX=hexed(U.stream(SERVER, T)))
    code, body = P.run(kb, T, project=project, host=True, env=env, claude=str(exe), max_budget_usd=0.25, model="m1")
    assert code == 0 and body["u5_ready"] is True and body["host"]["argv"] == argv_of("0.25", "m1", str(exe))
    assert body["host"]["exit_code"] == 0 and body["host_state"] == "connected"


def test_run_default_budget_is_one_dollar_and_there_is_no_model(tmp_path):
    project, kb = kb_of(tmp_path)
    serve(tmp_path)
    env = gate_open_env(tmp_path, on_path=True, CL_OUT_HEX=hexed(U.stream(SERVER, T)))
    code, body = P.run(kb, T, project=project, host=True, env=env)
    assert code == 0 and body["host"]["argv"] == argv_of()


def test_run_host_timeout_applies_to_the_host_probe(tmp_path):
    project, kb = kb_of(tmp_path)
    serve(tmp_path)
    env = gate_open_env(tmp_path, on_path=True, CL_OUT_HEX=hexed(U.stream(SERVER, T)), CL_SLEEP="5")
    code, body = P.run(kb, T, project=project, host=True, env=env, host_timeout=0.5)
    assert code == 21 and body["host"]["error"] == "claude timed out after 0.5s" and body["host_state"] == "failed"


def closed_gate_env(tmp: Path) -> dict[str, str]:
    U.executable(tmp / "bin" / "claude", f"#!/bin/sh\ntouch '{tmp / 'started'}'\n")
    env = base_env(tmp)
    env["PATH"] = str(tmp / "bin") + os.pathsep + env["PATH"]
    return env


def test_run_closed_gate_body_is_exact(tmp_path):
    project, kb = kb_of(tmp_path)
    serve(tmp_path)
    env = closed_gate_env(tmp_path)
    with pytest.raises(gate.GateClosed) as why:
        P.host_probe(project, SERVER, env=env)
    code, body = P.run(kb, T, project=project, host=True, env=env)
    assert code == 61 and not (tmp_path / "started").exists()
    assert body == {"schema": "klode-kit.u5_probe/1", "kb_id": "demo", "build_id": T, "u5_ready": False,
                    "exit_code": 61, "reason": "gate_closed", "server": SERVER,
                    "direct": {"ok": True, "build_id": T, "error": None,
                               "argv": [str(tmp_path / "srv.py"), "--kb", "KB"], "cwd": str(project)},
                    "host": None, "host_state": "not_run", "probe_build_id": T, "detail": str(why.value)}
    assert "G2" in body["detail"]


def test_run_without_claude_body_is_exact(tmp_path):
    project, kb = kb_of(tmp_path)
    serve(tmp_path)
    env = base_env(tmp_path, KLODE_KIT_REAL="1", KLODE_KIT_GATE_ACK=ACK)
    claude = tmp_path / "bin" / "claude"
    U.executable(claude, "#!/nonexistent/interpreter\n")
    env["PATH"] = str(tmp_path / "bin") + os.pathsep + env["PATH"]
    with pytest.raises(P.ProbeEnvironmentError) as why:
        P.host_probe(project, SERVER, env=env)
    code, body = P.run(kb, T, project=project, host=True, env=env)
    assert code == 40
    assert body == {"schema": "klode-kit.u5_probe/1", "kb_id": "demo", "build_id": T, "u5_ready": False,
                    "exit_code": 40, "reason": "environment", "server": SERVER,
                    "direct": {"ok": True, "build_id": T, "error": None,
                               "argv": [str(tmp_path / "srv.py"), "--kb", "KB"], "cwd": str(project)},
                    "host": None, "host_state": "not_run", "probe_build_id": T, "detail": str(why.value)}
    assert "cannot be started" in body["detail"]


# --------------------------------------------------------------------------- run --check


class CheckSpy:
    def __init__(self, code=0, doc=None):
        self.code, self.doc = code, doc if doc is not None else {"status": "PASS", "u5_ready": False}
        self.calls: list[tuple] = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.code, self.doc


@pytest.fixture()
def spy(monkeypatch):
    from klode_kit.check import kb_check

    def install(code=0, doc=None):
        s = CheckSpy(code, doc)
        monkeypatch.setattr(kb_check, "run_check", s)
        return s

    return install


def test_check_is_handed_the_build_the_entry_the_host_and_the_target_as_probe_id(tmp_path, spy):
    project, kb = kb_of(tmp_path)
    serve(tmp_path)
    s = spy(0, {"status": "WARN", "u5_ready": False})
    code, body = P.run(kb, T, project=project, check=True, env=base_env(tmp_path))
    (args, kwargs), = s.calls
    assert args == (kb / "builds" / T,) and kwargs == {"entry": "u5-probe", "project": None, "host": "not_run",
                                                      "probe_build_id": T}
    assert code == 0 and body["check"] == {"status": "WARN", "u5_ready": False, "exit_code": 0}
    assert body["u5_ready"] is False


def test_check_gets_the_project_only_when_it_holds_a_klode_kit_toml(tmp_path, spy, monkeypatch):
    project, kb = kb_of(tmp_path)
    serve(tmp_path)
    monkeypatch.setattr(P, "Path", CasePath)
    s = spy()
    P.run(kb, T, project=project, check=True, env=base_env(tmp_path))
    assert s.calls[-1][1]["project"] is None
    (project / "klode-kit.toml").write_text("", encoding="utf-8")
    P.run(kb, T, project=project, check=True, env=base_env(tmp_path))
    assert s.calls[-1][1]["project"] == project
    (project / "klode-kit.toml").rename(project / "KLODE-KIT.TOML")
    P.run(kb, T, project=project, check=True, env=base_env(tmp_path))
    assert s.calls[-1][1]["project"] is None


def host_run(tmp, kb, project, build, **kw):
    env = gate_open_env(tmp, on_path=True, CL_OUT_HEX=hexed(U.stream(SERVER, build)))
    return P.run(kb, T, project=project, host=True, check=True, env=env, **kw)


@pytest.mark.parametrize("direct,host,state,probe_id", [
    (T, T, "connected", T),
    (T, OLD, "connected", OLD),
    (OLD, T, "connected", OLD),
    (OLD, OLD, "connected", OLD),
    (T, None, "connected", None),
])
def test_check_probe_id_is_the_target_when_every_probe_read_it_else_the_first_dissenter(tmp_path, spy, direct, host,
                                                                                         state, probe_id):
    project, kb = kb_of(tmp_path)
    serve(tmp_path, build=direct)
    s = spy()
    host_run(tmp_path, kb, project, host)
    kwargs = s.calls[0][1]
    assert kwargs["host"] == state and kwargs["probe_build_id"] == probe_id


def test_check_with_a_direct_probe_that_read_nothing_hands_over_none(tmp_path, spy):
    project, kb = kb_of(tmp_path)
    serve(tmp_path, mode="error")
    s = spy()
    P.run(kb, T, project=project, check=True, env=base_env(tmp_path))
    assert s.calls[0][1]["probe_build_id"] is None


@pytest.mark.parametrize("probe_code,check_code,expected_code,reason", [
    (0, 0, 0, "ok"),
    (0, 30, 30, "contract_violation"),
    (0, 1, 1, "environment"),
    (0, 40, 40, "environment"),
    (0, 21, 21, "post_publish_failed"),
    (21, 0, 21, "post_publish_failed"),
    (21, 1, 21, "post_publish_failed"),
    (21, 30, 21, "post_publish_failed"),
])
def test_check_exit_code_rules(tmp_path, spy, probe_code, check_code, expected_code, reason):
    """A failing check keeps its own code only when the probes themselves had no complaint."""
    project, kb = kb_of(tmp_path)
    serve(tmp_path, build=T if probe_code == 0 else OLD)
    spy(check_code, {"status": "PASS" if check_code == 0 else "FAIL", "u5_ready": False})
    code, body = P.run(kb, T, project=project, check=True, env=base_env(tmp_path))
    # the body's exit_code is the one of its reason (a check code outside the table is reported as an environment error)
    assert (code, body["exit_code"], body["reason"]) == (expected_code, 40 if reason == "environment" else expected_code, reason)
    assert body["check"]["exit_code"] == check_code


@pytest.mark.parametrize("doc_ready,expected", [(True, True), (False, False)])
def test_check_u5_ready_needs_the_check_to_agree(tmp_path, spy, doc_ready, expected):
    project, kb = kb_of(tmp_path)
    serve(tmp_path)
    spy(0, {"status": "PASS", "u5_ready": doc_ready})
    code, body = host_run(tmp_path, kb, project, T)
    assert code == 0 and body["u5_ready"] is expected
    assert body["check"] == {"status": "PASS", "u5_ready": doc_ready, "exit_code": 0}


def test_check_for_real_on_a_bare_build_directory_writes_the_receipt(tmp_path):
    project, kb = kb_of(tmp_path)
    serve(tmp_path)
    code, body = host_run(tmp_path, kb, project, T)
    receipt = json.loads((kb / "builds" / T / "check_receipt.json").read_text(encoding="utf-8"))
    assert code == 0 and body["u5_ready"] is True and body["check"]["u5_ready"] is True
    assert body["check"]["status"] == receipt["status"] and receipt["probe"] == {"build_id": T}
    assert receipt["mcp"]["mcp_host_visible"] == "connected" and receipt["plugin"]["entry"] == "u5-probe"


# --------------------------------------------------------------------------- main


@pytest.fixture()
def recorder(monkeypatch):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return 7, {"schema": "klode-kit.u5_probe/1", "seen": True}

    monkeypatch.setattr(P, "run", fake_run)
    return calls


def test_main_hands_every_option_to_run(recorder, capsys):
    code = P.main(["--kb", "/x/kb/demo", "--build", T, "--project", "/p", "--server", "s", "--host", "--check",
                   "--timeout", "7", "--host-timeout", "9", "--budget-usd", "0.5", "--model", "m"])
    (args, kwargs), = recorder
    assert code == 7
    assert args == (Path("/x/kb/demo"), T, Path("/p"))
    assert kwargs == {"server": "s", "host": True, "check": True, "timeout": 7.0, "host_timeout": 9.0,
                      "max_budget_usd": 0.5, "model": "m"}
    assert all(isinstance(kwargs[k], float) for k in ("timeout", "host_timeout", "max_budget_usd"))
    assert json.loads(capsys.readouterr().out) == {"schema": "klode-kit.u5_probe/1", "seen": True}


def test_main_defaults(recorder):
    P.main(["--kb", "/x/kb/demo"])
    (args, kwargs), = recorder
    assert args == (Path("/x/kb/demo"), None, None)
    assert kwargs == {"server": None, "host": False, "check": False, "timeout": 60.0, "host_timeout": 300.0,
                      "max_budget_usd": 1.0, "model": None}


def test_main_without_argv_reads_sys_argv_after_the_program_name(recorder, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["klode-kit-u5", "--kb", "/x/kb/demo", "--build", T])
    assert P.main() == 7
    assert recorder[0][0] == (Path("/x/kb/demo"), T, None)


def test_main_help_exits_zero_and_describes_every_option(capsys, monkeypatch):
    monkeypatch.setenv("COLUMNS", "300")
    assert P.main(["--help"]) == 0
    text = " ".join(capsys.readouterr().out.split())
    assert text.startswith("usage: klode-kit u5-probe [-h] --kb KB [--build BUILD] [--project PROJECT]")
    assert " the probes behind u5_ready " in text  # the description, as a whole
    assert ("--kb KB --build BUILD target build id (default: what current points to) --project PROJECT "
            "--server SERVER MCP server name (default: <kb>-klode) "
            "--host also run the host probe (real claude, manual gate G2) "
            "--check hand the findings to kb-check (rewrites check_receipt) --receipt RECEIPT --timeout TIMEOUT "
            "--host-timeout HOST_TIMEOUT --budget-usd BUDGET_USD --model MODEL") in text
    assert P.main(["-h"]) == 0


def test_main_usage_errors_are_exit_2_with_the_program_name_on_stderr(capsys):
    assert P.main([]) == 2
    err = capsys.readouterr().err
    assert err.startswith("usage: klode-kit u5-probe ") and "--kb" in err
    assert P.main(["--kb", "k", "--timeout", "soon"]) == 2
    assert P.main(["--kb", "k", "--budget-usd", "x"]) == 2
    assert P.main(["--kb", "k", "--host-timeout", "x"]) == 2


def test_main_receipt_is_written_through_atomic_write_with_its_crash_points(tmp_path, monkeypatch, capsys):
    project, kb = kb_of(tmp_path)
    serve(tmp_path, build=OLD)
    names, paths = [], []
    monkeypatch.setattr(registry_write, "crashpoint", names.append)
    real = P.atomic_write
    monkeypatch.setattr(P, "atomic_write", lambda path, data, **kw: (paths.append(path), real(path, data, **kw))[1])
    receipt = tmp_path / "out" / "deep" / "u5.json"
    code = P.main(["--kb", str(kb), "--build", T, "--project", str(project), "--receipt", str(receipt)])
    out = capsys.readouterr().out
    assert code == 21 and out.endswith("\n") and out.count("\n") == 1
    assert receipt.read_bytes() == out.encode("utf-8") and stat.S_IMODE(receipt.stat().st_mode) == 0o644
    assert [n for n in names if n != "publish.mkdir"] == ["u5.receipt_tmp", "u5.receipt_written"]
    assert isinstance(paths[0], Path) and paths[0] == receipt
    assert sorted(x.name for x in receipt.parent.iterdir()) == ["u5.json"]


def test_main_without_receipt_writes_only_stdout(tmp_path, monkeypatch, capsys):
    project, kb = kb_of(tmp_path)
    serve(tmp_path, build=OLD)
    names = []
    monkeypatch.setattr(registry_write, "crashpoint", names.append)
    assert P.main(["--kb", str(kb), "--build", T, "--project", str(project)]) == 21
    assert names == [] and json.loads(capsys.readouterr().out)["exit_code"] == 21


def test_main_prints_non_ascii_as_it_is(tmp_path, capsys):
    project, kb = kb_of(tmp_path, name="kb-é")
    receipt = tmp_path / "u5.json"
    code = P.main(["--kb", str(kb), "--build", T, "--project", str(project), "--receipt", str(receipt)])
    out = capsys.readouterr().out
    assert code == 21 and '"server": "kb-é-klode"' in out and "\\u00e9" not in out
    assert receipt.read_bytes() == out.encode("utf-8")
