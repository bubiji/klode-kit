"""WI-15k: ``mcp_wrap`` pinned exactly - the in-process part (mutation-gate survivors, score 0.6875 -> >= 0.85).

The recorded tests (``tests/cli/test_mcp_wrap.py`` with the real klode, ``test_mcp_wrap_einval_retry.py``,
``test_reader_lease_places.py``) show that the wrapper hands a build over, degrades through the three levels and keeps
leases. They do not pin *what it says*: the JSON-RPC envelope of every answer and error (code, message, id), the
``initialize`` the proxy replays, the ``kb_version`` text byte for byte, the stderr lines, the lease file (mode, name,
content), the timeouts and the environment variables that set them, the order lease -> open -> drop, and the small
helpers (``resolve_current`` trailing slashes, ``_seconds``, ``_now``, ``_library_facts``, ``_published_at``, ``_Lines``).

Everything here runs in this process: the wrapper is driven with a recording ``FakeBackend`` (the ``detect`` seam is the
module's own), the file system is real (``tmp_path`` knowledge base with real ``builds/`` and a real ``current`` link),
and the pieces that start a process (the proxy against a stand-in ``klode-mcp``, the CLI level against a stand-in
``klode``, the C-locale runs) are in ``tests/contract/test_mcp_wrap_wi15k.py``.

``CasePath`` is a ``Path`` that is case-sensitive about the *name* it opens: the build volume of a Mac is not, and a
mutant that opens ``LIBRARY.TOML`` would otherwise find ``library.toml`` there.

Law (S4): ``_Lines`` is a round trip - whatever the bytes are cut into by the reads, the lines that come out are the
lines that went in (``test_lines_round_trip_for_any_cut``). The rest has no law of its own (envelopes and texts): S1-S3.

Mutants left alone as equivalent, with the reason:
``resolve_current``: ``range(RETRIES + 2)`` - the last attempt that is ever reached is ``RETRIES``, where an EINVAL
returns ``None`` anyway; ``_seconds``: ``environ.get(name, "XXXX")`` - ``float("XXXX")`` and ``float("")`` both fail;
``read_text(encoding="UTF-8")`` / ``.encode("UTF-8")`` / ``.decode("UTF-8")`` / ``.encode("ASCII")`` are the same codecs;
``json.dumps(..., ensure_ascii=None)`` is falsy like ``False``; ``Proxy.open``: ``stderr=None`` removed is the default,
``answer`` is always a dict (``isinstance(answer, dict) or True``), ``.get("serverInfo", None)`` / ``.get("serverInfo", )``
give ``None`` where ``{}`` would not change ``klode_version``; ``detect``: ``mcp_server = None`` is never read;
``main``: ``reconfigure(encoding="UTF-8")``.
"""

from __future__ import annotations

import errno
import io
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from klode_kit import __version__
from klode_kit import mcp_render
from klode_kit import mcp_wrap as W
from klode_kit.publish import LOG_NAME, reader_lease_dir

pytestmark = pytest.mark.unit

GOOD = "b-20261027T000000Z-0123abcd"
GOOD2 = "b-20261028T000000Z-89abcdef"
TOML = '[library]\nid = "血液库"\ndescription = "labs of the family"\n'


class CasePath(type(Path())):
    """A ``Path`` that opens a file only when the *name* is spelled exactly (a case-insensitive volume would not)."""

    def _exact(self) -> bool:
        return self.name in os.listdir(self.parent)

    def is_file(self) -> bool:
        return self._exact() and super().is_file()

    def read_text(self, *args, **kwargs):
        if not self._exact():
            raise FileNotFoundError(str(self))
        return super().read_text(*args, **kwargs)


# --------------------------------------------------------------------------- helpers


def make_kb(tmp_path, ids=(GOOD,), current=GOOD, toml=TOML, log=None, cls=CasePath):
    kb = cls(tmp_path / "kb" / "lib")
    for build_id in ids:
        build = kb / "builds" / build_id
        build.mkdir(parents=True)
        if toml is not None:
            (build / "library.toml").write_text(toml, encoding="utf-8")
    if current:
        os.symlink(f"builds/{current}", kb / "current")
    if log is not None:
        (kb / LOG_NAME).write_text(log, encoding="utf-8")
    return kb


def point(kb, build_id):
    """What ``publish.switch_pointer`` does: a fresh link renamed over ``current``."""
    os.symlink(f"builds/{build_id}", kb / "current.new")
    os.replace(kb / "current.new", kb / "current")


class FakeBackend:
    """A recording stand-in for the three levels (the wrapper's only collaborator)."""

    def __init__(self, level=0, version="0.5.0", responder=None, open_error=None, meta_result="echo"):
        self.level, self._version, self.responder, self.open_error = level, version, responder, open_error
        self.meta_result = meta_result
        self.opened, self.closed, self.calls, self.metas, self.seen = [], 0, [], [], []

    def open(self, build, client_init):
        self.opened.append((build, client_init))
        leases = reader_lease_dir(build.parent.parent, build.name)
        self.seen.append(sorted(p.name for p in leases.iterdir()) if leases.is_dir() else [])
        if self.open_error is not None:
            raise self.open_error

    def close(self):
        self.closed += 1

    def tools(self):
        return [{"name": "t1"}]

    def version(self):
        return self._version

    def meta(self, msg):
        self.metas.append(msg)
        if self.meta_result == "echo":
            return {"jsonrpc": "2.0", "id": msg["id"], "result": {"meta": msg["method"]}}
        return self.meta_result

    def call(self, msg):
        self.calls.append(msg)
        if self.responder is None:
            return {"jsonrpc": "2.0", "id": msg["id"], "result": {"served": True}}
        out = self.responder(msg)
        if isinstance(out, Exception):
            raise out
        return out


def make(tmp_path, monkeypatch, backend=None, **kw):
    kb = make_kb(tmp_path, **kw)
    backend = backend or FakeBackend()
    monkeypatch.setattr(W, "detect", lambda: backend)
    out = io.StringIO()
    return W.Wrapper(kb, out), backend, out, kb


def sent(out):
    return [json.loads(line) for line in out.getvalue().splitlines()]


def call_msg(name, args=None, rid=1):
    params = {"name": name} if args is None else {"name": name, "arguments": args}
    return {"jsonrpc": "2.0", "id": rid, "method": "tools/call", "params": params}


def crashes(monkeypatch):
    seen = []
    monkeypatch.setattr(W, "crashpoint", seen.append)
    return seen


# --------------------------------------------------------------------------- resolve_current


def test_resolve_current_reads_the_link_called_current_in_the_given_kb():
    paths = []

    def read(path):
        paths.append(path)
        return f"builds/{GOOD}"

    assert W.resolve_current(Path("/x/kb/lib"), _readlink=read) == GOOD
    assert paths == [os.path.join(Path("/x/kb/lib"), "current")]
    assert paths == ["/x/kb/lib/current"]


@pytest.mark.parametrize("suffix", ["/", "///"])
def test_resolve_current_ignores_trailing_slashes_of_the_target(suffix):
    assert W.resolve_current("/kb", _readlink=lambda p: f"builds/{GOOD}{suffix}") == GOOD
    assert W.resolve_current("/kb", _readlink=lambda p: f"/abs/builds/{GOOD}{suffix}") == GOOD


def test_resolve_current_strips_only_slashes_from_the_end():
    assert W.resolve_current("/kb", _readlink=lambda p: f"builds/{GOOD}/X") is None
    assert W.resolve_current("/kb", _readlink=lambda p: f"builds/{GOOD}X") is None


def test_resolve_current_leading_slash_names_the_last_component_only():
    assert W.resolve_current("/kb", _readlink=lambda p: f"/{GOOD}") == GOOD
    assert W.resolve_current("/kb", _readlink=lambda p: "/") is None


# --------------------------------------------------------------------------- _seconds


@pytest.mark.parametrize("raw,expected", [
    (None, 7.5), ("", 7.5), ("abc", 7.5), ("0", 7.5), ("0.0", 7.5), ("-3", 7.5), ("-0.5", 7.5), ("nan", 7.5),
    ("2.5", 2.5), ("0.5", 0.5), ("1", 1.0), ("0.001", 0.001), ("90", 90.0),
])
def test_seconds_takes_a_positive_number_from_the_environment(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("WI15K_T", raising=False)
    else:
        monkeypatch.setenv("WI15K_T", raw)
    assert W._seconds("WI15K_T", 7.5) == expected


# --------------------------------------------------------------------------- _now / envelopes


def test_now_is_a_utc_second_stamp_whatever_the_local_zone(monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    time.tzset()
    try:
        stamp = W._now()
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", stamp)
        parsed = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        assert abs((datetime.now(timezone.utc) - parsed).total_seconds()) < 10
    finally:
        monkeypatch.undo()
        time.tzset()


def test_tool_result_envelope():
    assert W._tool_result(7, "hello") == {"jsonrpc": "2.0", "id": 7,
                                          "result": {"content": [{"type": "text", "text": "hello"}], "isError": False}}
    assert W._tool_result("a", "bad", True) == {
        "jsonrpc": "2.0", "id": "a", "result": {"content": [{"type": "text", "text": "bad"}], "isError": True}}


def test_error_envelope():
    assert W._error(3, -32601, "Method not found: x") == {
        "jsonrpc": "2.0", "id": 3, "error": {"code": -32601, "message": "Method not found: x"}}
    assert W._error(None, -32700, "m") == {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "m"}}


def test_error_codes_are_the_json_rpc_ones():
    assert (W.PARSE_ERROR, W.INVALID_REQUEST, W.METHOD_NOT_FOUND, W.INVALID_PARAMS, W.INTERNAL_ERROR) == (
        -32700, -32600, -32601, -32602, -32603)


# --------------------------------------------------------------------------- _initialize_result


def test_initialize_result_echoes_the_clients_protocol_version():
    assert W._initialize_result({"protocolVersion": "2024-11-05"}, "1.2.3") == {
        "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
        "serverInfo": {"name": "klode", "version": "1.2.3"}}


@pytest.mark.parametrize("params", [None, "text", ["protocolVersion"], {}, {"protocolVersion": ""},
                                    {"protocolVersion": 5}, {"protocolVersion": None}, {"PROTOCOLVERSION": "x"},
                                    {"protocolversion": "x"}])
def test_initialize_result_falls_back_to_the_wrappers_protocol_version(params):
    assert W._initialize_result(params, "1.2.3")["protocolVersion"] == W.PROTOCOL_VERSION == "2025-06-18"


def test_initialize_result_unknown_version_and_full_shape():
    assert W._initialize_result({}, None) == {
        "protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
        "serverInfo": {"name": "klode", "version": "unknown"}}
    assert W._initialize_result({}, "")["serverInfo"]["version"] == "unknown"


# --------------------------------------------------------------------------- _library_facts / _published_at


def test_library_facts_reads_id_and_description_of_library_toml(tmp_path):
    kb = make_kb(tmp_path)
    assert W._library_facts(kb / "builds" / GOOD, "fallback") == ("血液库", "labs of the family")


@pytest.mark.parametrize("toml,expected", [
    ('[library]\ndescription = "d"\n', ("fb", "d")),
    ('[library]\nid = "x"\n', ("x", "")),
    ('[library]\nid = ""\ndescription = ""\n', ("fb", "")),
    ('[other]\nid = "x"\n', ("fb", "")),
    ("", ("fb", "")),
    ("[library\n", ("fb", "")),
    ('[library]\nid = 5\ndescription = 7\n', ("5", "7")),
])
def test_library_facts_fall_back_per_field(tmp_path, toml, expected):
    kb = make_kb(tmp_path, toml=toml)
    assert W._library_facts(kb / "builds" / GOOD, "fb") == expected


def test_library_facts_without_a_file_is_the_fallback(tmp_path):
    kb = make_kb(tmp_path, toml=None)
    assert W._library_facts(kb / "builds" / GOOD, "fb") == ("fb", "")


def test_published_at_is_the_at_of_the_last_row(tmp_path):
    rows = [{"at": "2026-10-01T00:00:00Z"}, {"at": "2026-10-02T00:00:00Z"}, {"at": "2026-10-03T00:00:00Z"}]
    kb = make_kb(tmp_path, log="".join(json.dumps(r) + "\n" for r in rows))
    assert W._published_at(kb) == "2026-10-03T00:00:00Z"
    kb2 = make_kb(tmp_path / "one", log=json.dumps(rows[0]) + "\n")
    assert W._published_at(kb2) == "2026-10-01T00:00:00Z"


@pytest.mark.parametrize("log", [None, "", "not json\n", "[1, 2]\n", '"x"\n', '{"at": 5}\n', '{"at": null}\n',
                                 '{"x": 1}\n', '{"at": "a"}\nnot json\n'])
def test_published_at_is_none_unless_the_last_line_is_a_row_with_a_string_at(tmp_path, log):
    kb = make_kb(tmp_path, log=log)
    assert W._published_at(kb) is None


# --------------------------------------------------------------------------- InProcess


def test_in_process_level_state_and_open(tmp_path):
    opened = []
    config = SimpleNamespace(load=lambda p: ("cfg", p))
    pool = SimpleNamespace(single=lambda cfg: ("pool", cfg))
    server = SimpleNamespace(TOOLS=[{"name": "a"}], SERVER_VERSION=0.5, handle=lambda p, m: opened.append((p, m)))
    ip = W.InProcess(server, pool, config)
    assert ip.level == 0 and ip.pool is None
    build = Path("/some/build")
    ip.open(build, {"ignored": True})
    assert ip.pool == ("pool", ("cfg", build / "library.toml"))
    ip.close()
    assert ip.pool is None
    tools = ip.tools()
    assert tools == [{"name": "a"}] and tools is not server.TOOLS
    assert ip.version() == "0.5"
    ip.pool = "P"
    assert ip.meta({"id": 1}) is None and ip.call({"id": 2}) is None
    assert opened == [(None, {"id": 1}), ("P", {"id": 2})]


# --------------------------------------------------------------------------- _Lines


def pipe_with(data: bytes):
    r, w = os.pipe()
    os.write(w, data)
    return r, w


def test_lines_returns_a_ready_line_within_a_short_timeout():
    r, w = pipe_with(b"hello\n")
    try:
        assert W._Lines(r).readline(0.5) == b"hello"
    finally:
        os.close(r), os.close(w)


def test_lines_times_out_without_data_and_at_end_of_file():
    r, w = os.pipe()
    try:
        lines = W._Lines(r)
        started = time.monotonic()
        assert lines.readline(0.2) is None
        assert time.monotonic() - started < 3
        os.close(w)
        assert lines.readline(1.0) is None
    finally:
        os.close(r)


def test_lines_with_no_time_left_does_not_even_look(monkeypatch):
    r, w = pipe_with(b"ready\n")
    monkeypatch.setattr(W.time, "monotonic", lambda: 100.0)
    try:
        assert W._Lines(r).readline(0) is None
    finally:
        os.close(r), os.close(w)


def test_lines_reads_64_kib_at_a_time(monkeypatch):
    sizes, real = [], os.read
    monkeypatch.setattr(W.os, "read", lambda fd, n: (sizes.append(n), real(fd, n))[1])
    r, w = pipe_with(b"x\n")
    try:
        assert W._Lines(r).readline(1.0) == b"x"
    finally:
        os.close(r), os.close(w)
    assert sizes == [65536]


def cut_reads(monkeypatch, n):
    real = os.read
    monkeypatch.setattr(W.os, "read", lambda fd, size: real(fd, min(size, n)))


def test_lines_joins_a_line_that_arrives_in_pieces_and_keeps_the_rest(monkeypatch):
    cut_reads(monkeypatch, 3)
    r, w = pipe_with(b"abcdef\nXY\nlast\n")
    try:
        lines = W._Lines(r)
        assert lines.readline(1.0) == b"abcdef"
        assert lines.readline(1.0) == b"XY"
        assert lines.readline(1.0) == b"last"
        assert lines.buf == b""
    finally:
        os.close(r), os.close(w)


def test_lines_splits_at_the_first_newline_of_a_buffer_holding_several():
    r, w = pipe_with(b"one\ntwo\nthree\n")
    try:
        lines = W._Lines(r)
        assert lines.readline(1.0) == b"one"
        assert lines.buf == b"two\nthree\n"
        assert lines.readline(1.0) == b"two"
        assert lines.readline(1.0) == b"three"
    finally:
        os.close(r), os.close(w)


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(lines=st.lists(st.binary(max_size=40).filter(lambda b: b"\n" not in b), max_size=8), cut=st.integers(1, 17))
def test_lines_round_trip_for_any_cut(monkeypatch, lines, cut):
    """S4: the lines that go into the pipe are the lines that come out, whatever size the reads have."""
    real = os.read
    with monkeypatch.context() as m:
        m.setattr(W.os, "read", lambda fd, size: real(fd, min(size, cut)))
        r, w = pipe_with(b"".join(line + b"\n" for line in lines))
        os.close(w)
        try:
            reader = W._Lines(r)
            got = [reader.readline(2.0) for _ in lines]
            assert got == lines
            assert reader.readline(0.5) is None
        finally:
            os.close(r)


# --------------------------------------------------------------------------- Proxy (no process)


def test_proxy_initial_state():
    p = W.Proxy("/x/klode-mcp")
    assert (p.level, p.exe, p.proc, p.lines, p.klode_version) == (1, "/x/klode-mcp", None, None, None)


def test_proxy_open_of_a_missing_command_is_backend_unavailable(tmp_path):
    exe = str(tmp_path / "no-such-klode-mcp")
    p = W.Proxy(exe)
    with pytest.raises(W.BackendUnavailable) as caught:
        p.open(tmp_path, None)
    assert str(caught.value).startswith(f"{exe}: ") and "No such file" in str(caught.value)
    assert p.proc is None


def test_proxy_write_needs_a_running_child():
    with pytest.raises(AssertionError):
        W.Proxy("x")._write({"a": 1})


class FakeFile:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeProc:
    def __init__(self, hang):
        self.stdin, self.stdout, self.hang, self.waits, self.killed = FakeFile(), FakeFile(), hang, [], False

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if self.hang and timeout is not None:
            raise subprocess.TimeoutExpired("klode-mcp", timeout)
        return 0

    def kill(self):
        self.killed = True


def test_proxy_close_gives_a_working_child_two_seconds_then_kills_it():
    p = W.Proxy("x")
    p.proc, p.lines = FakeProc(hang=True), object()
    proc = p.proc
    p.close()
    assert proc.waits == [2, None] and proc.killed
    assert proc.stdin.closed and proc.stdout.closed
    assert p.proc is None and p.lines is None
    p.close()  # nothing to close: no error


def test_proxy_close_of_a_child_that_exits_by_itself_does_not_kill():
    p = W.Proxy("x")
    p.proc = FakeProc(hang=False)
    proc = p.proc
    p.close()
    assert proc.waits == [2] and not proc.killed and proc.stdin.closed and proc.stdout.closed


def test_proxy_close_asserts_both_pipes_exist():
    p = W.Proxy("x")
    p.proc = SimpleNamespace(stdin=None, stdout=FakeFile())
    with pytest.raises(AssertionError):
        p.close()
    p.proc = SimpleNamespace(stdin=FakeFile(), stdout=None)
    with pytest.raises(AssertionError):
        p.close()


def test_proxy_version_is_klode_mcps_own_or_the_clis(monkeypatch):
    monkeypatch.setattr(W.shutil, "which", lambda name, **kw: f"/fake/bin/{name}")
    monkeypatch.setattr(W, "klode_cli_version", lambda exe: f"cli:{exe}")
    p = W.Proxy("x")
    assert p.version() == "cli:/fake/bin/klode"
    p.klode_version = "3.2.1"
    assert p.version() == "3.2.1"


def test_proxy_meta_answers_initialize_and_ping_itself():
    p = W.Proxy("x")
    p.klode_version = "3.2.1"
    init = {"jsonrpc": "2.0", "id": "i-1", "method": "initialize", "params": {"protocolVersion": "2024-11-05"}}
    assert p.meta(init) == {"jsonrpc": "2.0", "id": "i-1", "result": {
        "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
        "serverInfo": {"name": "klode", "version": "3.2.1"}}}
    assert p.meta({"id": 9, "method": "initialize"})["result"]["protocolVersion"] == "2025-06-18"
    assert p.meta({"id": 4, "method": "ping"}) == {"jsonrpc": "2.0", "id": 4, "result": {}}


# --------------------------------------------------------------------------- Cli (no process)


def test_cli_initial_state_open_and_close(tmp_path):
    cli = W.Cli("/x/klode")
    assert (cli.level, cli.exe, cli.library, cli.kb_id, cli.description) == (2, "/x/klode", None, "", "")
    kb = make_kb(tmp_path)
    cli.open(kb / "builds" / GOOD, None)
    assert cli.library == kb / "builds" / GOOD / "library.toml"
    assert (cli.kb_id, cli.description) == ("血液库", "labs of the family")
    cli.close()
    assert cli.library is None


def test_cli_open_without_library_facts_uses_the_kb_directory_name(tmp_path):
    kb = make_kb(tmp_path, toml="")
    cli = W.Cli("/x/klode")
    cli.open(kb / "builds" / GOOD, None)
    assert (cli.kb_id, cli.description) == ("lib", "")


def test_cli_version_asks_the_interpreter_of_its_own_script(tmp_path):
    exe = tmp_path / "klode"
    exe.write_text(f"#!{sys.executable}\n", encoding="utf-8")
    W.klode_cli_version.cache_clear()
    import importlib.metadata as m

    assert W.Cli(str(exe)).version() == m.version("klode")
    W.klode_cli_version.cache_clear()
    assert W.Cli(None).version() is None


def test_cli_meta_answers_initialize_and_ping_itself():
    cli = W.Cli("x")
    cli.version = lambda: "0.5.0"
    init = {"jsonrpc": "2.0", "id": 11, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}}
    assert cli.meta(init) == {"jsonrpc": "2.0", "id": 11, "result": {
        "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
        "serverInfo": {"name": "klode", "version": "0.5.0"}}}
    assert cli.meta({"id": 12, "method": "initialize"})["result"]["protocolVersion"] == "2025-06-18"
    assert cli.meta({"id": "p", "method": "ping"}) == {"jsonrpc": "2.0", "id": "p", "result": {}}
    cli.version = lambda: None
    assert cli.meta({"id": 1, "method": "initialize"})["result"]["serverInfo"]["version"] == "unknown"


def opened_cli(tmp_path, exe="/x/klode"):
    kb = make_kb(tmp_path)
    cli = W.Cli(exe)
    cli.open(kb / "builds" / GOOD, None)
    return cli


def test_cli_call_list_kbs_is_answered_without_klode(tmp_path, monkeypatch):
    monkeypatch.setattr(W.subprocess, "run", lambda *a, **k: pytest.fail("klode must not run"))
    cli = opened_cli(tmp_path)
    assert cli.call(call_msg("list_kbs", {}, 5)) == W._tool_result(5, mcp_render.list_kbs_text("血液库", "labs of the family"))


def test_cli_call_local_answers(tmp_path, monkeypatch):
    monkeypatch.setattr(W.subprocess, "run", lambda *a, **k: pytest.fail("klode must not run"))
    cli = opened_cli(tmp_path)
    # a local answer that is not an error is tagged like klode's rendered ones (a grounding tool always)
    assert cli.call(call_msg("verify_quote", {"id": "x", "phrase": ""}, 6)) == W._tool_result(
        6, "[血液库]\nNo phrase given to verify.")
    assert cli.call(call_msg("search_sources", {"query": " "}, 7)) == W._tool_result(7, "No search terms given.")
    # a local error is the text as is, flagged
    engine = cli.call(call_msg("consult_dimension", {"dimension": "d", "audience": "engine"}, 8))
    assert engine == W._tool_result(8, "audience 'engine' is not available while the klode MCP server cannot be "
                                       "reached (command-line fallback); use 'writer' or 'full'.", True)
    assert cli.call(call_msg("no_such_tool", {}, 9)) == W._tool_result(9, "Unknown tool: 'no_such_tool'", True)


class Recorder:
    def __init__(self, stdout=b"", stderr=b"", error=None):
        self.stdout, self.stderr, self.error, self.calls = stdout, stderr, error, []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if self.error is not None:
            raise self.error
        return subprocess.CompletedProcess(argv, 0, self.stdout, self.stderr)


EMPTY_SEARCH = json.dumps({"value": {"hits": [], "total": 0}, "provenance": {"kb": "lib"}}).encode("utf-8")


def test_cli_call_runs_klode_json_with_the_documented_boundary(tmp_path, monkeypatch):
    rec = Recorder(EMPTY_SEARCH)
    monkeypatch.setattr(W.subprocess, "run", rec)
    monkeypatch.delenv("KLODE_KIT_MCP_CALL_TIMEOUT", raising=False)
    monkeypatch.setenv("PYTHONPATH", "/should/not/reach/klode")
    monkeypatch.setenv("WI15K_KEEP", "yes")
    cli = opened_cli(tmp_path, "/x/klode")
    answer = cli.call(call_msg("search_sources", {"query": "a b"}, 3))
    assert answer == W._tool_result(3, "No cards match 'a b'.")
    (argv, kwargs), = rec.calls
    assert argv == ["/x/klode", "--json", "-c", str(cli.library), "search", "a", "b"]
    assert kwargs["capture_output"] is True
    assert kwargs["timeout"] == 600.0 == W.CALL_TIMEOUT
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert "PYTHONPATH" not in kwargs["env"] and kwargs["env"]["WI15K_KEEP"] == "yes"
    assert set(kwargs) == {"capture_output", "timeout", "env", "stdin"}


def test_cli_call_timeout_comes_from_the_environment_or_the_constant(tmp_path, monkeypatch):
    rec = Recorder(EMPTY_SEARCH)
    monkeypatch.setattr(W.subprocess, "run", rec)
    cli = opened_cli(tmp_path)
    monkeypatch.setenv("KLODE_KIT_MCP_CALL_TIMEOUT", "12.5")
    cli.call(call_msg("search_sources", {"query": "a"}))
    monkeypatch.delenv("KLODE_KIT_MCP_CALL_TIMEOUT")
    monkeypatch.setattr(W, "CALL_TIMEOUT", 33.0)
    cli.call(call_msg("search_sources", {"query": "a"}))
    assert [k["timeout"] for _, k in rec.calls] == [12.5, 33.0]


def test_cli_call_that_cannot_run_klode_is_call_failed(tmp_path, monkeypatch):
    cli = opened_cli(tmp_path)
    monkeypatch.setattr(W.subprocess, "run", Recorder(error=OSError(errno.ENOENT, "No such file")))
    with pytest.raises(W.CallFailed) as caught:
        cli.call(call_msg("search_sources", {"query": "a"}))
    assert str(caught.value) == "klode did not run: [Errno 2] No such file"
    monkeypatch.setattr(W.subprocess, "run", Recorder(error=subprocess.TimeoutExpired("klode", 600)))
    with pytest.raises(W.CallFailed) as caught:
        cli.call(call_msg("search_sources", {"query": "a"}))
    assert str(caught.value) == "klode did not run: Command 'klode' timed out after 600 seconds"


def test_cli_call_with_unreadable_output_fails_the_call_and_passes_stderr_on(tmp_path, monkeypatch, capsys):
    cli = opened_cli(tmp_path)
    monkeypatch.setattr(W.subprocess, "run", Recorder(b"oops, not json", b"bad \xff byte\n"))
    answer = cli.call(call_msg("search_sources", {"query": "a"}, 41))
    assert answer == W._tool_result(41, "Tool 'search_sources' failed (see server logs).", True)
    assert capsys.readouterr().err == "bad � byte\n"
    # JSON that the renderer cannot use is the same failure
    monkeypatch.setattr(W.subprocess, "run", Recorder(b'{"value": {"hits": 1}}', b""))
    assert cli.call(call_msg("search_sources", {"query": "a"}, "s")) == W._tool_result(
        "s", "Tool 'search_sources' failed (see server logs).", True)


# --------------------------------------------------------------------------- detect / _next_level


def bindir(tmp_path, *names):
    d = tmp_path / "bin"
    d.mkdir(parents=True, exist_ok=True)
    for name in names:
        f = d / name
        f.write_text("#!/bin/sh\n", encoding="utf-8")
        f.chmod(0o755)
    return str(d)


def no_klode_module(monkeypatch):
    import klode.lib as kl

    monkeypatch.delattr(kl, "mcp_server", raising=False)
    monkeypatch.setitem(sys.modules, "klode.lib.mcp_server", None)


def test_detect_without_the_klode_module_takes_klode_mcp_from_the_given_path(tmp_path, monkeypatch):
    no_klode_module(monkeypatch)
    d = bindir(tmp_path, "klode-mcp", "klode")
    backend = W.detect(d)
    assert isinstance(backend, W.Proxy) and backend.exe == os.path.join(d, "klode-mcp")


def test_detect_without_klode_mcp_takes_klode_for_the_cli(tmp_path, monkeypatch):
    no_klode_module(monkeypatch)
    d = bindir(tmp_path, "klode")
    backend = W.detect(d)
    assert isinstance(backend, W.Cli) and backend.exe == os.path.join(d, "klode")


def test_detect_without_either_command_is_a_cli_that_fails_per_call(tmp_path, monkeypatch):
    no_klode_module(monkeypatch)
    backend = W.detect(bindir(tmp_path))
    assert isinstance(backend, W.Cli) and backend.exe == "klode"


def test_next_level_after_the_proxy_skips_klode_mcp(tmp_path):
    d = bindir(tmp_path, "klode-mcp", "klode")
    nxt = W._next_level(1, d)
    assert isinstance(nxt, W.Cli) and nxt.exe == os.path.join(d, "klode")
    assert W._next_level(0, d).exe == os.path.join(d, "klode-mcp")
    only_mcp = bindir(tmp_path / "other", "klode-mcp")
    assert W._next_level(1, only_mcp).exe == "klode"


def test_detect_with_the_klode_module_is_in_process():
    backend = W.detect()
    assert isinstance(backend, W.InProcess) and backend.level == 0
    assert backend.tools()[0]["name"] and backend.version()


# --------------------------------------------------------------------------- Wrapper: state and output


def test_wrapper_initial_state(tmp_path, monkeypatch):
    w, backend, out, kb = make(tmp_path, monkeypatch)
    assert w.kb == kb and w.out is out and w.backend is backend
    assert (w.cached, w.lease, w.client_init) == (None, None, None)
    assert w.facts == ("lib", "")


def test_send_writes_one_utf8_json_line_unescaped(tmp_path, monkeypatch):
    w, _, out, _ = make(tmp_path, monkeypatch)
    w.send({"text": "血红蛋白", "n": 1})
    assert out.getvalue() == '{"text": "血红蛋白", "n": 1}\n'


def test_send_without_an_out_stream_writes_stdout(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(W, "detect", lambda: FakeBackend())
    w = W.Wrapper(make_kb(tmp_path))
    assert w.out is None
    w.send({"a": "血"})
    assert capsys.readouterr().out == '{"a": "血"}\n'


# --------------------------------------------------------------------------- Wrapper: the lease


def test_lease_file_has_name_content_and_mode(tmp_path, monkeypatch):
    w, _, _, kb = make(tmp_path, monkeypatch)
    old = os.umask(0)
    try:
        lease = w._acquire(GOOD)
    finally:
        os.umask(old)
    assert lease.parent == kb / ".readers" / GOOD
    assert re.fullmatch(rf"{os.getpid()}-[0-9a-f]{{8}}", lease.name)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\n", lease.read_text(encoding="ascii"))
    assert (lease.stat().st_mode & 0o777) == 0o644


def test_two_leases_of_one_process_have_different_names(tmp_path, monkeypatch):
    w, _, _, _ = make(tmp_path, monkeypatch)
    assert w._acquire(GOOD).name != w._acquire(GOOD).name


def test_drop_removes_the_lease_and_its_empty_directory_only(tmp_path, monkeypatch):
    w, _, _, kb = make(tmp_path, monkeypatch)
    a, b = w._acquire(GOOD), w._acquire(GOOD)
    w._drop(a)
    assert not a.exists() and b.exists() and a.parent.is_dir()
    w._drop(b)
    assert not a.parent.exists()
    w._drop(b)  # already gone: no error
    w._drop(None)


# --------------------------------------------------------------------------- Wrapper: ensure / hand-over


def test_first_call_takes_the_lease_before_the_backend_opens_the_build(tmp_path, monkeypatch):
    seen = crashes(monkeypatch)
    w, backend, out, kb = make(tmp_path, monkeypatch)
    w.handle(call_msg("search_sources", {"query": "q"}, 12))
    (build, client_init), = backend.opened
    assert build == kb / "builds" / GOOD and client_init is None
    (names,) = backend.seen
    assert len(names) == 1 and names[0].startswith(f"{os.getpid()}-")  # the lease existed when the build was opened
    assert seen == ["mcp.lease_dir", "mcp.after_resolve"]
    assert sent(out) == [{"jsonrpc": "2.0", "id": 12, "result": {"served": True}}]
    assert w.cached == GOOD and w.lease is not None and w.lease.exists()
    assert w.facts == ("血液库", "labs of the family")
    w.handle(call_msg("search_sources", {"query": "q"}, 13))
    assert len(backend.opened) == 1  # same build: no reload


def test_backend_open_gets_the_clients_initialize_params(tmp_path, monkeypatch):
    w, backend, _, _ = make(tmp_path, monkeypatch)
    params = {"protocolVersion": "2024-11-05", "capabilities": {"x": 1}}
    w.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": params})
    w.handle(call_msg("t"))
    assert backend.opened[0][1] == params


def test_switch_loads_the_new_build_and_lets_go_of_the_old_lease(tmp_path, monkeypatch):
    w, backend, _, kb = make(tmp_path, monkeypatch, ids=(GOOD, GOOD2))
    w.handle(call_msg("t", {}, 1))
    first = w.lease
    point(kb, GOOD2)
    w.handle(call_msg("t", {}, 2))
    assert [b.name for b, _ in backend.opened] == [GOOD, GOOD2]
    assert w.cached == GOOD2 and not first.exists() and not first.parent.exists()
    assert w.lease.parent == reader_lease_dir(kb, GOOD2) and w.lease.exists()


def test_current_missing_answers_not_published_and_the_process_lives_on(tmp_path, monkeypatch):
    w, backend, out, kb = make(tmp_path, monkeypatch, current=None)
    w.handle(call_msg("t", {}, 21))
    assert sent(out) == [W._tool_result(21, f"kb not published: {kb}/current is missing or not a build link", True)]
    assert backend.calls == [] and backend.closed >= 1
    point(kb, GOOD)
    w.handle(call_msg("t", {}, 22))
    assert sent(out)[1] == {"jsonrpc": "2.0", "id": 22, "result": {"served": True}}


def test_a_build_pruned_between_readlink_and_lease_is_looked_for_again(tmp_path, monkeypatch):
    kb = make_kb(tmp_path, ids=(GOOD, GOOD2))
    (kb / "builds" / GOOD / "library.toml").unlink()
    backend = FakeBackend()
    monkeypatch.setattr(W, "detect", lambda: backend)
    hits = []

    def hook(name):
        hits.append(name)
        if name == "mcp.after_resolve" and hits.count(name) == 1:
            point(kb, GOOD2)

    monkeypatch.setattr(W, "crashpoint", hook)
    out = io.StringIO()
    w = W.Wrapper(kb, out)
    w.handle(call_msg("t", {}, 1))
    assert hits.count("mcp.after_resolve") == 2
    assert [b.name for b, _ in backend.opened] == [GOOD2]
    assert not reader_lease_dir(kb, GOOD).exists()  # the lease of the vanished build was dropped
    assert sent(out) == [{"jsonrpc": "2.0", "id": 1, "result": {"served": True}}]


def test_a_build_that_keeps_moving_away_fails_the_call_after_five_looks(tmp_path, monkeypatch):
    kb = make_kb(tmp_path)
    (kb / "builds" / GOOD / "library.toml").unlink()
    seen = crashes(monkeypatch)
    backend = FakeBackend()
    monkeypatch.setattr(W, "detect", lambda: backend)
    out = io.StringIO()
    w = W.Wrapper(kb, out)
    w.handle(call_msg("t", {}, 31))
    assert W.MAX_LOOKS == 5 and seen.count("mcp.after_resolve") == 5
    assert sent(out) == [W._tool_result(31, "Tool 't' failed: the build moved away 5 times while it was being "
                                            "loaded", True)]
    assert backend.opened == [] and not reader_lease_dir(kb, GOOD).exists()
    assert w.cached is None and w.lease is None


def test_a_build_that_cannot_be_opened_drops_its_lease_and_fails_the_call(tmp_path, monkeypatch, capsys):
    backend = FakeBackend(open_error=RuntimeError("cannot open"))
    w, _, out, kb = make(tmp_path, monkeypatch, backend=backend)
    w.handle(call_msg("t", {}, 32))
    assert sent(out) == [W._tool_result(32, "Tool 't' failed (see server logs).", True)]
    assert capsys.readouterr().err == "klode-kit mcp: 't': RuntimeError: cannot open\n"
    assert not reader_lease_dir(kb, GOOD).exists() and w.cached is None and w.lease is None


def test_an_unusable_level_hands_over_to_the_next_one_and_replays_initialize(tmp_path, monkeypatch, capsys):
    first = FakeBackend(level=1, open_error=W.BackendUnavailable("no answer"))
    second = FakeBackend(level=2)
    asked = []
    monkeypatch.setattr(W, "_next_level", lambda after: (asked.append(after), second)[1])
    w, _, out, _ = make(tmp_path, monkeypatch, backend=first)
    params = {"protocolVersion": "2024-11-05"}
    w.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": params})
    w.handle(call_msg("t", {}, 2))
    assert capsys.readouterr().err == "klode-kit mcp: level 1 unusable (no answer); using the next\n"
    assert asked == [1] and first.closed >= 1 and w.backend is second
    assert second.opened[0][1] == params
    assert sent(out)[-1] == {"jsonrpc": "2.0", "id": 2, "result": {"served": True}}


def test_settle_lets_go_of_a_build_that_current_has_left(tmp_path, monkeypatch):
    kb_holder = {}

    def responder(msg):
        point(kb_holder["kb"], GOOD2)
        return {"jsonrpc": "2.0", "id": msg["id"], "result": {"served": "old"}}

    backend = FakeBackend(responder=responder)
    w, _, out, kb = make(tmp_path, monkeypatch, backend=backend, ids=(GOOD, GOOD2))
    kb_holder["kb"] = kb
    w.handle(call_msg("t", {}, 1))
    assert sent(out) == [{"jsonrpc": "2.0", "id": 1, "result": {"served": "old"}}]  # answered on the build it started on
    assert w.cached is None and w.lease is None and backend.closed >= 1
    assert not reader_lease_dir(kb, GOOD).exists()
    backend.responder = None
    w.handle(call_msg("t", {}, 2))
    assert [b.name for b, _ in backend.opened] == [GOOD, GOOD2]


def test_settle_keeps_the_build_while_current_stays(tmp_path, monkeypatch):
    w, backend, _, kb = make(tmp_path, monkeypatch)
    w.handle(call_msg("t"))
    assert w.cached == GOOD and backend.closed == 0 and w.lease.exists()
    w.close()
    assert w.cached is None and w.lease is None and backend.closed == 1
    assert not reader_lease_dir(kb, GOOD).exists()


# --------------------------------------------------------------------------- Wrapper: kb_version


def test_kb_version_text_is_exactly_the_sorted_unescaped_json(tmp_path, monkeypatch):
    log = json.dumps({"at": "2026-10-26T00:00:00Z"}) + "\n" + json.dumps({"at": "2026-10-27T00:00:00Z"}) + "\n"
    w, backend, out, kb = make(tmp_path, monkeypatch, log=log, backend=FakeBackend(level=0))
    w.handle(call_msg("kb_version", {}, 51))
    expected = ('{"build_id": "%s", "kb_id": "血液库", "klode_version": "0.5.0", "library_toml": "%s/builds/%s/'
                'library.toml", "mode": "in-process", "published_at": "2026-10-27T00:00:00Z", '
                '"wrapper_version": "%s"}' % (GOOD, kb, GOOD, __version__))
    assert sent(out) == [W._tool_result(51, expected)]
    assert backend.calls == []  # the wrapper's own tool never reaches the backend


@pytest.mark.parametrize("level,mode", [(0, "in-process"), (1, "subprocess-proxy"), (2, "cli")])
def test_kb_version_reports_the_level(tmp_path, monkeypatch, level, mode):
    w, _, out, _ = make(tmp_path, monkeypatch, backend=FakeBackend(level=level))
    w.handle(call_msg("kb_version", None, 1))
    info = json.loads(sent(out)[0]["result"]["content"][0]["text"])
    assert info["mode"] == mode == W.LEVEL_NAMES[level]


def test_kb_version_unknown_klode_and_no_publish_log(tmp_path, monkeypatch):
    w, _, out, _ = make(tmp_path, monkeypatch, backend=FakeBackend(version=None))
    w.handle(call_msg("kb_version", {}, 1))
    info = json.loads(sent(out)[0]["result"]["content"][0]["text"])
    assert (info["klode_version"], info["published_at"]) == ("unknown", None)
    assert list(info) == sorted(info)  # sort_keys


def test_kb_version_tool_is_listed_after_the_backends_tools(tmp_path, monkeypatch):
    w, _, out, _ = make(tmp_path, monkeypatch)
    w.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
    assert sent(out) == [{"jsonrpc": "2.0", "id": 3, "result": {"tools": [{"name": "t1"}, W.KB_VERSION_TOOL]}}]
    assert W.KB_VERSION_TOOL["name"] == "kb_version" == W.KB_VERSION


# --------------------------------------------------------------------------- Wrapper: tools/call guards and failures


@pytest.mark.parametrize("params,message", [
    ("text", "params must be an object"),
    (None, "params must be an object"),
    ([1], "params must be an object"),
    ({}, "tool name must be a non-empty string"),
    ({"name": ""}, "tool name must be a non-empty string"),
    ({"name": 5}, "tool name must be a non-empty string"),
    ({"name": None}, "tool name must be a non-empty string"),
    ({"name": ["x"]}, "tool name must be a non-empty string"),
    ({"name": "t", "arguments": "x"}, "tool arguments must be an object"),
    ({"name": "t", "arguments": [1]}, "tool arguments must be an object"),
    ({"name": "t", "arguments": 5}, "tool arguments must be an object"),
])
def test_tools_call_guards_answer_invalid_params(tmp_path, monkeypatch, params, message):
    w, backend, out, _ = make(tmp_path, monkeypatch)
    w.handle({"jsonrpc": "2.0", "id": 61, "method": "tools/call", "params": params})
    assert sent(out) == [{"jsonrpc": "2.0", "id": 61, "error": {"code": -32602, "message": message}}]
    assert backend.calls == [] and backend.opened == []


@pytest.mark.parametrize("args", [None, {}, [], 0, ""])
def test_tools_call_empty_arguments_are_fine(tmp_path, monkeypatch, args):
    w, backend, out, _ = make(tmp_path, monkeypatch)
    msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "t", "arguments": args}}
    w.handle(msg)
    assert sent(out) == [{"jsonrpc": "2.0", "id": 1, "result": {"served": True}}] and backend.calls == [msg]


def test_a_backend_that_writes_its_own_answer_leaves_the_wrapper_silent(tmp_path, monkeypatch):
    w, backend, out, _ = make(tmp_path, monkeypatch, backend=FakeBackend(responder=lambda msg: None))
    w.handle(call_msg("t"))
    assert out.getvalue() == "" and len(backend.calls) == 1


def test_call_failed_answers_with_the_reason_and_lets_go_of_the_build(tmp_path, monkeypatch):
    backend = FakeBackend(responder=lambda msg: W.CallFailed("klode-mcp gave no answer"))
    w, _, out, kb = make(tmp_path, monkeypatch, backend=backend)
    w.handle(call_msg("search_sources", {}, 71))
    assert sent(out) == [W._tool_result(71, "Tool 'search_sources' failed: klode-mcp gave no answer", True)]
    assert w.cached is None and w.lease is None and backend.closed >= 1
    assert not reader_lease_dir(kb, GOOD).exists()


def test_any_other_failure_is_logged_and_reported_without_detail(tmp_path, monkeypatch, capsys):
    backend = FakeBackend(responder=lambda msg: ValueError("boom ✓"))
    w, _, out, _ = make(tmp_path, monkeypatch, backend=backend)
    w.handle(call_msg("zoom_card", {}, 72))
    assert sent(out) == [W._tool_result(72, "Tool 'zoom_card' failed (see server logs).", True)]
    assert capsys.readouterr().err == "klode-kit mcp: 'zoom_card': ValueError: boom ✓\n"
    assert w.cached is None and backend.closed >= 1
    assert capsys.readouterr().out == ""  # stdout carries protocol JSON only


# --------------------------------------------------------------------------- Wrapper: handle / serve


def test_notifications_get_no_answer(tmp_path, monkeypatch):
    w, backend, out, _ = make(tmp_path, monkeypatch)
    for method in ("notifications/initialized", "initialize", "tools/call", "tools/list", "bogus"):
        w.handle({"jsonrpc": "2.0", "method": method, "params": {}})
    assert out.getvalue() == "" and backend.metas == [] and backend.calls == []


def test_initialize_remembers_the_params_and_the_backend_answers(tmp_path, monkeypatch):
    w, backend, out, _ = make(tmp_path, monkeypatch)
    params = {"protocolVersion": "2024-11-05", "clientInfo": {"name": "c"}}
    w.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": params})
    assert w.client_init == params
    assert sent(out) == [{"jsonrpc": "2.0", "id": 1, "result": {"meta": "initialize"}}]
    w.handle({"jsonrpc": "2.0", "id": 2, "method": "ping", "params": {"other": 1}})
    assert w.client_init == params  # ping does not replace it
    assert sent(out)[1] == {"jsonrpc": "2.0", "id": 2, "result": {"meta": "ping"}}
    w.handle({"jsonrpc": "2.0", "id": 3, "method": "initialize"})
    assert w.client_init is None


def test_a_meta_answer_the_backend_wrote_itself_is_not_sent_twice(tmp_path, monkeypatch):
    w, backend, out, _ = make(tmp_path, monkeypatch, backend=FakeBackend(meta_result=None))
    w.handle({"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert out.getvalue() == "" and len(backend.metas) == 1


@pytest.mark.parametrize("method,shown", [("resources/list", "resources/list"), (None, "None"), (5, "5")])
def test_unknown_method_is_method_not_found(tmp_path, monkeypatch, method, shown):
    w, _, out, _ = make(tmp_path, monkeypatch)
    msg = {"jsonrpc": "2.0", "id": "q"} if method is None else {"jsonrpc": "2.0", "id": "q", "method": method}
    w.handle(msg)
    assert sent(out) == [{"jsonrpc": "2.0", "id": "q", "error": {"code": -32601,
                                                                 "message": f"Method not found: {shown}"}}]


def test_serve_line_ignores_blank_lines(tmp_path, monkeypatch):
    w, _, out, _ = make(tmp_path, monkeypatch)
    w._serve_line("")
    assert out.getvalue() == ""


def test_serve_line_parse_error_names_the_json_problem(tmp_path, monkeypatch):
    w, _, out, _ = make(tmp_path, monkeypatch)
    bad = '{"jsonrpc": "2.0", "id": 1,'
    try:
        json.loads(bad)
    except json.JSONDecodeError as exc:
        reason = str(exc)
    w._serve_line(bad)
    assert sent(out) == [{"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"Parse error: {reason}"}}]
    assert reason in sent(out)[0]["error"]["message"] and reason.startswith("Expecting")


@pytest.mark.parametrize("line", ["[1, 2]", "5", '"text"', "null", "true"])
def test_serve_line_a_request_must_be_an_object(tmp_path, monkeypatch, line):
    w, backend, out, _ = make(tmp_path, monkeypatch)
    w._serve_line(line)
    assert sent(out) == [{"jsonrpc": "2.0", "id": None,
                          "error": {"code": -32600, "message": "Request must be a JSON object"}}]
    assert backend.metas == [] and backend.calls == []


def test_serve_line_an_internal_failure_is_logged_and_answered_with_the_requests_id(tmp_path, monkeypatch, capsys):
    w, _, out, _ = make(tmp_path, monkeypatch)

    def boom(msg):
        raise KeyError("secret detail")

    w.handle = boom
    w._serve_line(json.dumps({"jsonrpc": "2.0", "id": 81, "method": "ping"}))
    assert sent(out) == [{"jsonrpc": "2.0", "id": 81,
                          "error": {"code": -32603, "message": "Internal server error"}}]
    assert capsys.readouterr().err == "klode-kit mcp: KeyError: 'secret detail'\n"


def test_serve_line_an_internal_failure_of_a_notification_is_only_logged(tmp_path, monkeypatch, capsys):
    w, _, out, _ = make(tmp_path, monkeypatch)
    w.handle = lambda msg: (_ for _ in ()).throw(RuntimeError("x"))
    w._serve_line(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}))
    assert out.getvalue() == "" and capsys.readouterr().err == "klode-kit mcp: RuntimeError: x\n"


def test_serve_line_a_closed_pipe_is_not_swallowed(tmp_path, monkeypatch):
    w, _, _, _ = make(tmp_path, monkeypatch)
    w.handle = lambda msg: (_ for _ in ()).throw(BrokenPipeError())
    with pytest.raises(BrokenPipeError):
        w._serve_line(json.dumps({"id": 1, "method": "ping"}))


def test_serve_answers_a_session_line_by_line(tmp_path, monkeypatch):
    w, _, out, _ = make(tmp_path, monkeypatch)
    session = "\n".join([
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}}),
        "", "garbage{",
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        json.dumps(call_msg("kb_version", {}, 3)),
    ]) + "\n"
    assert w.serve(io.StringIO(session)) == 0
    answers = sent(out)
    assert [a["id"] for a in answers] == [1, None, 2, 3]
    assert answers[1]["error"]["code"] == -32700


# --------------------------------------------------------------------------- _terminate / main


def test_terminate_exits_with_128_plus_the_signal():
    with pytest.raises(SystemExit) as caught:
        W._terminate(15, None)
    assert caught.value.code == 143
    with pytest.raises(SystemExit) as caught:
        W._terminate(2, None)
    assert caught.value.code == 130


def test_main_help_exits_zero_and_names_the_tool(monkeypatch, capsys):
    monkeypatch.setenv("COLUMNS", "200")
    assert W.main(["--help"]) == 0
    text = capsys.readouterr().out
    assert text.startswith("usage: klode-kit mcp [-h] --kb KB\n")
    assert "\n\nMCP server of one library, following kb/<id>/current\n" in text
    assert re.search(r"\n  --kb KB +library root kb/<id> \(absolute path\)\n", text)


def test_main_without_kb_is_a_usage_error(monkeypatch, capsys):
    monkeypatch.setenv("COLUMNS", "200")
    assert W.main([]) == W.USAGE_EXIT == 2
    err = capsys.readouterr().err
    assert err.startswith("usage: klode-kit mcp [-h] --kb KB\n")
    assert "klode-kit mcp: error: the following arguments are required: --kb" in err


def test_main_with_an_unknown_option_is_a_usage_error(capsys):
    assert W.main(["--kb", "x", "--bogus"]) == 2
    assert "unrecognized arguments: --bogus" in capsys.readouterr().err


def test_main_kb_that_is_not_a_directory_is_a_usage_error(tmp_path, capsys):
    missing = tmp_path / "no-such-kb"
    assert W.main(["--kb", str(missing)]) == 2
    captured = capsys.readouterr()
    assert captured.err == f"klode-kit mcp: --kb is not a directory: {missing}\n" and captured.out == ""
    a_file = tmp_path / "file"
    a_file.write_text("x", encoding="utf-8")
    assert W.main(["--kb", str(a_file)]) == 2
    assert capsys.readouterr().err == f"klode-kit mcp: --kb is not a directory: {a_file}\n"
