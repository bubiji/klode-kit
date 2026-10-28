"""WI-15k: ``mcp_wrap`` pinned exactly - the parts that start a process (mutation-gate survivors, 0.6875 -> >= 0.85).

Companion of ``tests/unit/test_mcp_wrap_wi15k.py`` (see its header for the whole picture and the equivalent mutants).
Here the real ``Proxy`` talks to a stand-in ``klode-mcp`` (a Python script with a shebang: it logs the bytes it is sent
and the argv / environment it was started with, and answers per ``FAKE_MODE``), the real ``Cli.call`` runs a stand-in
``klode``, and the locale-dependent reads run in a child whose locale is ``C`` (no UTF-8 by default: only an explicit
``encoding="utf-8"`` gets the text right).

No klode is needed and no socket is opened; the children are killed / closed by the tests.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from klode_kit import __version__
from klode_kit import mcp_wrap as W

pytestmark = pytest.mark.contract_replay

SRC = Path(W.__file__).resolve().parents[1]  # the src directory of the module under test (also in a mutant's copy)
BUILD = Path("/kb/builds/b-20261027T000000Z-0123abcd")

FAKE_MCP = r'''#!%(python)s
import json, os, sys, time
log = os.environ["FAKE_LOG"]
mode = os.environ.get("FAKE_MODE", "ok")
with open(log + ".argv", "w") as f:
    json.dump(sys.argv[1:], f)
with open(log + ".env", "w") as f:
    json.dump({k: os.environ.get(k) for k in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONSAFEPATH", "KEEP_ME")}, f)
out = sys.stdout
def say(doc):
    out.write(json.dumps(doc) + "\n")
    out.flush()
for raw in sys.stdin.buffer:
    with open(log, "ab") as f:
        f.write(raw)
    msg = json.loads(raw)
    if "id" not in msg:
        continue
    if mode == "silent":
        time.sleep(3)
        sys.exit(0)
    if mode == "garbage":
        out.write("not json at all\n[1, 2]\n" + json.dumps({"jsonrpc": "2.0", "id": "other", "result": {}}) + "\n")
        out.flush()
    if msg["method"] == "initialize":
        if mode == "badinfo":
            say({"jsonrpc": "2.0", "id": msg["id"], "result": {"serverInfo": "not a table"}})
        elif mode == "noresult":
            say({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -1, "message": "no"}})
        else:
            say({"jsonrpc": "2.0", "id": msg["id"],
                 "result": {"serverInfo": {"name": "fake", "version": os.environ.get("FAKE_VER", "1.2.3")}}})
    elif mode == "initonly":
        time.sleep(3)
        sys.exit(0)
    else:
        say({"jsonrpc": "2.0", "id": msg["id"], "result": {"echo": msg["method"], "params": msg.get("params")}})
if mode == "hang":
    time.sleep(30)
''' % {"python": sys.executable}

FAKE_KLODE = r'''#!%(python)s
import json, os, sys, time
mode = os.environ.get("FAKE_MODE", "json")
with open(os.environ["FAKE_LOG"], "w") as f:
    json.dump({"argv": sys.argv[1:], "stdin": sys.stdin.read(), "pythonpath": os.environ.get("PYTHONPATH")}, f)
if mode == "json":
    sys.stdout.write(os.environ["FAKE_OUT"])
elif mode == "bad":
    sys.stdout.write("oops, not json")
    sys.stderr.buffer.write(b"bad \xff byte\n")
elif mode == "sleep":
    time.sleep(4)
''' % {"python": sys.executable}


@pytest.fixture()
def mcp(tmp_path, monkeypatch):
    exe = tmp_path / "klode-mcp"
    exe.write_text(FAKE_MCP, encoding="utf-8")
    exe.chmod(0o755)
    log = tmp_path / "mcp.log"
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.delenv("FAKE_MODE", raising=False)
    monkeypatch.delenv("FAKE_VER", raising=False)
    monkeypatch.delenv("KLODE_KIT_MCP_HANDSHAKE_TIMEOUT", raising=False)
    monkeypatch.delenv("KLODE_KIT_MCP_CALL_TIMEOUT", raising=False)
    made = []

    def start(mode="ok"):
        monkeypatch.setenv("FAKE_MODE", mode)
        proxy = W.Proxy(str(exe))
        made.append(proxy)
        return proxy

    start.exe, start.log = str(exe), log
    yield start
    for proxy in made:
        try:
            proxy.close()
        except Exception:  # noqa: BLE001 - cleanup of a proxy the test already broke
            pass


def received(log: Path, count: int, wait: float = 10.0) -> list[bytes]:
    """The raw lines the stand-in has logged so far (waits until there are ``count``)."""
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        lines = log.read_bytes().splitlines(keepends=True) if log.exists() else []
        if len(lines) >= count:
            return lines
        time.sleep(0.02)
    raise AssertionError(f"only {len(lines)} of {count} lines arrived")


def dumps(doc) -> bytes:
    return (json.dumps(doc, ensure_ascii=False) + "\n").encode("utf-8")


# --------------------------------------------------------------------------- Proxy.open


def test_proxy_open_starts_klode_mcp_on_the_builds_library_and_replays_initialize(mcp):
    p = mcp()
    p.open(BUILD, None)
    lines = received(mcp.log, 2)
    assert json.loads(Path(str(mcp.log) + ".argv").read_text()) == ["-c", str(BUILD / "library.toml")]
    init = {"jsonrpc": "2.0", "id": "klode-kit-init", "method": "initialize", "params": {
        "protocolVersion": W.PROTOCOL_VERSION, "capabilities": {},
        "clientInfo": {"name": "klode-kit-mcp", "version": __version__}}}
    assert lines[0] == dumps(init)
    assert lines[1] == dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert p.klode_version == "1.2.3"
    assert (p.proc is not None) and (p.lines is not None)


def test_proxy_open_passes_the_clients_own_initialize_params_through_unescaped(mcp):
    params = {"protocolVersion": "2024-11-05", "capabilities": {"roots": {}},
              "clientInfo": {"name": "客户端", "version": "9"}, "extra": [1, 2]}
    keep = json.loads(json.dumps(params))
    p = mcp()
    p.open(BUILD, params)
    assert received(mcp.log, 2)[0] == dumps({"jsonrpc": "2.0", "id": "klode-kit-init", "method": "initialize",
                                             "params": keep})
    assert params == keep  # the client's dict is copied, not filled in


def test_proxy_open_fills_in_only_what_the_client_left_out(mcp):
    p = mcp()
    p.open(BUILD, {"protocolVersion": "2024-11-05"})
    sent = json.loads(received(mcp.log, 2)[0])["params"]
    assert sent == {"protocolVersion": "2024-11-05", "capabilities": {},
                    "clientInfo": {"name": "klode-kit-mcp", "version": __version__}}


@pytest.mark.parametrize("client_init", [None, "text", ["x"], 5])
def test_proxy_open_without_dict_params_sends_the_defaults(mcp, client_init):
    p = mcp()
    p.open(BUILD, client_init)
    assert json.loads(received(mcp.log, 2)[0])["params"] == {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "klode-kit-mcp", "version": __version__}}


def test_proxy_child_runs_with_the_python_environment_scrubbed(mcp, monkeypatch):
    for name in W._SCRUBBED_ENV:
        monkeypatch.setenv(name, "/should/not/reach/klode-mcp" if name != "PYTHONSAFEPATH" else "1")
    monkeypatch.setenv("KEEP_ME", "kept")
    p = mcp()
    p.open(BUILD, None)
    received(mcp.log, 1)
    env = json.loads(Path(str(mcp.log) + ".env").read_text())
    assert env == {"PYTHONPATH": None, "PYTHONHOME": None, "PYTHONSTARTUP": None, "PYTHONSAFEPATH": None,
                   "KEEP_ME": "kept"}


def test_proxy_open_reads_the_servers_version_from_its_answer(mcp, monkeypatch):
    monkeypatch.setenv("FAKE_VER", "7.8.9")
    p = mcp()
    p.open(BUILD, None)
    assert p.klode_version == "7.8.9" and p.version() == "7.8.9"


def test_proxy_open_survives_an_answer_without_a_result(mcp):
    p = mcp("noresult")
    p.open(BUILD, None)  # an error answer to initialize is not "unusable": there is just no version
    assert p.klode_version is None and p.proc is not None


def test_proxy_open_ignores_a_server_info_that_is_not_a_table(mcp):
    p = mcp("badinfo")
    p.open(BUILD, None)
    assert p.klode_version is None and p.proc is not None


def test_proxy_open_skips_lines_that_are_not_its_answer(mcp):
    p = mcp("garbage")
    p.open(BUILD, None)
    assert p.klode_version == "1.2.3"


def test_proxy_open_without_an_answer_is_backend_unavailable_within_the_handshake_timeout(mcp, monkeypatch):
    monkeypatch.setenv("KLODE_KIT_MCP_HANDSHAKE_TIMEOUT", "0.3")
    p = mcp("silent")
    started = time.monotonic()
    with pytest.raises(W.BackendUnavailable) as caught:
        p.open(BUILD, None)
    assert time.monotonic() - started < 2.5  # the child sleeps 3 s: the timeout, not its exit, ended the wait
    assert str(caught.value) == f"{mcp.exe} did not answer initialize: klode-mcp gave no answer (exited or timed out)"
    assert p.proc is None and p.lines is None


def test_proxy_handshake_timeout_defaults_to_the_constant(mcp, monkeypatch):
    monkeypatch.setattr(W, "HANDSHAKE_TIMEOUT", 0.3)
    p = mcp("silent")
    started = time.monotonic()
    with pytest.raises(W.BackendUnavailable):
        p.open(BUILD, None)
    assert time.monotonic() - started < 2.5


# --------------------------------------------------------------------------- Proxy._exchange / call / close


def test_proxy_exchange_returns_the_answer_with_the_requests_id(mcp):
    p = mcp("garbage")
    p.open(BUILD, None)
    req = {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "t", "arguments": {"q": "血"}}}
    assert p._exchange(req, 5.0) == {"jsonrpc": "2.0", "id": 5, "result": {"echo": "tools/call",
                                                                          "params": req["params"]}}
    assert received(mcp.log, 3)[2] == dumps(req)  # non-ASCII goes out as UTF-8, not as \u escapes


def test_proxy_call_forwards_and_returns_the_answer(mcp):
    p = mcp()
    p.open(BUILD, None)
    msg = {"jsonrpc": "2.0", "id": "c1", "method": "tools/call", "params": {"name": "search_sources"}}
    assert p.call(msg) == {"jsonrpc": "2.0", "id": "c1", "result": {"echo": "tools/call", "params": msg["params"]}}


def test_proxy_exchange_without_an_answer_is_call_failed(mcp):
    p = mcp("initonly")
    p.open(BUILD, None)
    started = time.monotonic()
    with pytest.raises(W.CallFailed) as caught:
        p._exchange({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {}}, 0.3)
    assert str(caught.value) == "klode-mcp gave no answer (exited or timed out)"
    assert time.monotonic() - started < 2.5


def test_proxy_exchange_with_a_dead_child_is_call_failed_gone(mcp):
    p = mcp()
    p.open(BUILD, None)
    p.proc.kill()
    p.proc.wait()
    with pytest.raises(W.CallFailed) as caught:
        p._exchange({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {}}, 1.0)
    assert str(caught.value).startswith("klode-mcp is gone: ") and "Broken pipe" in str(caught.value)


def test_proxy_call_timeout_comes_from_the_environment_and_a_failed_call_closes_the_child(mcp, monkeypatch):
    monkeypatch.setenv("KLODE_KIT_MCP_CALL_TIMEOUT", "0.3")
    p = mcp("initonly")
    p.open(BUILD, None)
    started = time.monotonic()
    with pytest.raises(W.CallFailed):
        p.call({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {}})
    assert time.monotonic() - started < 2.5
    assert p.proc is None and p.lines is None


def test_proxy_call_timeout_defaults_to_the_constant(mcp, monkeypatch):
    monkeypatch.setattr(W, "CALL_TIMEOUT", 0.3)
    p = mcp("initonly")
    p.open(BUILD, None)
    started = time.monotonic()
    with pytest.raises(W.CallFailed):
        p.call({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {}})
    assert time.monotonic() - started < 2.5


def test_proxy_close_kills_a_child_that_ignores_end_of_input(mcp):
    p = mcp("hang")
    p.open(BUILD, None)
    proc = p.proc
    p.close()
    assert proc.poll() is not None and p.proc is None and p.lines is None


def test_proxy_reopen_replaces_the_child(mcp):
    p = mcp()
    p.open(BUILD, None)
    first = p.proc
    p.open(BUILD, None)
    assert first.poll() is not None and p.proc is not first


# --------------------------------------------------------------------------- Cli.call with a stand-in klode


@pytest.fixture()
def klode(tmp_path, monkeypatch):
    exe = tmp_path / "klode"
    exe.write_text(FAKE_KLODE, encoding="utf-8")
    exe.chmod(0o755)
    log = tmp_path / "klode.log"
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.delenv("KLODE_KIT_MCP_CALL_TIMEOUT", raising=False)
    cli = W.Cli(str(exe))
    build = tmp_path / "kb" / "lib" / "builds" / "b-20261027T000000Z-0123abcd"
    build.mkdir(parents=True)
    (build / "library.toml").write_text('[library]\nid = "lib"\n', encoding="utf-8")
    cli.open(build, None)
    cli.log = log
    return cli


def req(name, args, rid=1):
    return {"jsonrpc": "2.0", "id": rid, "method": "tools/call", "params": {"name": name, "arguments": args}}


def test_cli_call_runs_the_real_command_and_renders_its_json(klode, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "json")
    monkeypatch.setenv("FAKE_OUT", json.dumps({"value": {"hits": [], "total": 0}, "provenance": {"kb": "lib"}}))
    monkeypatch.setenv("PYTHONPATH", "/should/not/reach/klode")
    answer = klode.call(req("search_sources", {"query": "血红蛋白 白细胞"}, 17))
    assert answer == W._tool_result(17, "No cards match '血红蛋白 白细胞'.")
    seen = json.loads(klode.log.read_text())
    assert seen == {"argv": ["--json", "-c", str(klode.library), "search", "血红蛋白", "白细胞"], "stdin": "",
                    "pythonpath": None}


def test_cli_call_gives_the_command_no_stdin(klode, monkeypatch):
    """The child reads its stdin to the end: it must see /dev/null (end at once), not the server's own input."""
    monkeypatch.setenv("FAKE_MODE", "json")
    monkeypatch.setenv("FAKE_OUT", json.dumps({"value": {"hits": [], "total": 0}}))
    code = ("import json, sys\nfrom klode_kit import mcp_wrap as W\ncli = W.Cli(sys.argv[1])\n"
            "cli.library = sys.argv[2]\n"
            "ans = cli.call({'id': 1, 'params': {'name': 'search_sources', 'arguments': {'query': 'x'}}})\n"
            "print(json.dumps(ans))\n")
    child = subprocess.Popen([sys.executable, "-c", code, klode.exe, str(klode.library)], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, env={**os.environ, "PYTHONPATH": str(SRC)})
    try:
        child.stdin.write(b"input meant for the parent only\n")
        child.stdin.flush()
        deadline = time.monotonic() + 30  # stdin stays open meanwhile: only /dev/null makes the command's read end
        while child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert child.poll() == 0, child.stderr.read()
        assert json.loads(klode.log.read_text())["stdin"] == ""
    finally:
        child.stdin.close()
        child.wait(timeout=10)
        child.stdout.close()
        child.stderr.close()


def test_cli_call_that_takes_longer_than_the_timeout_is_call_failed(klode, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "sleep")
    monkeypatch.setenv("KLODE_KIT_MCP_CALL_TIMEOUT", "0.3")
    started = time.monotonic()
    with pytest.raises(W.CallFailed) as caught:
        klode.call(req("search_sources", {"query": "a"}))
    assert time.monotonic() - started < 3.5  # the command sleeps 4 s
    assert str(caught.value).startswith("klode did not run: Command ") and "timed out after 0.3 seconds" in str(caught.value)


def test_cli_call_with_unreadable_output_reports_stderr_as_text(klode, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_MODE", "bad")
    answer = klode.call(req("search_sources", {"query": "a"}, "id-9"))
    assert answer == W._tool_result("id-9", "Tool 'search_sources' failed (see server logs).", True)
    assert capsys.readouterr().err == "bad � byte\n"


# --------------------------------------------------------------------------- the locale of the process


def child_c_locale(code: str, *args: str, stdin: bytes | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "LC_ALL": "C", "LANG": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0",
           "PYTHONPATH": str(SRC)}
    env.pop("PYTHONIOENCODING", None)
    return subprocess.run([sys.executable, "-c", code, *args], env=env, capture_output=True, input=stdin, timeout=120)


def test_library_toml_and_publish_log_are_read_as_utf8_whatever_the_locale(tmp_path):
    kb = tmp_path / "lib"
    build = kb / "builds" / "b-20261027T000000Z-0123abcd"
    build.mkdir(parents=True)
    (build / "library.toml").write_text('[library]\nid = "血液库"\ndescription = "家庭化验单"\n', encoding="utf-8")
    (kb / "PUBLISH.log").write_text(json.dumps({"at": "2026-10-27T00:00:00Z", "note": "发布"}, ensure_ascii=False)
                                    + "\n", encoding="utf-8")
    code = ("import json, sys\nfrom pathlib import Path\nfrom klode_kit import mcp_wrap as W\n"
            "print(json.dumps([list(W._library_facts(Path(sys.argv[1]), 'fb')), W._published_at(Path(sys.argv[2]))]))\n")
    ran = child_c_locale(code, str(build), str(kb))
    assert ran.returncode == 0, ran.stderr.decode(errors="replace")
    assert json.loads(ran.stdout) == [["血液库", "家庭化验单"], "2026-10-27T00:00:00Z"]


def test_the_server_writes_utf8_whatever_the_locale(tmp_path):
    """The request is pure ASCII (the id is a JSON escape): every non-ASCII byte on stdout is the server's own."""
    kb = tmp_path / "lib"
    kb.mkdir()
    code = "import sys\nfrom klode_kit import mcp_wrap as W\nsys.exit(W.main(['--kb', sys.argv[1]]))\n"
    request = b'{"jsonrpc": "2.0", "id": "\\u8840\\u7ea2", "method": "x"}\n'
    ran = child_c_locale(code, str(kb), stdin=request)
    assert ran.returncode == 0, ran.stderr.decode(errors="replace")
    assert ran.stdout == ('{"jsonrpc": "2.0", "id": "血红", "error": {"code": -32601, "message": '
                          '"Method not found: x"}}\n').encode("utf-8")
