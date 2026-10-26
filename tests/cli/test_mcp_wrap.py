"""WI-11a: ``bin/klode-kit mcp`` - the MCP wrapper that follows ``kb/<id>/current`` (matrix 11a-01 ... 11a-13; design 02
r4.2 "自动版本交接的 MCP 包装" / "读者租约" / "降级").

Real processes throughout: the launcher, the real klode 0.5.0 (in-process through its site directory on ``PYTHONPATH``,
as ``klode-mcp`` child, or as ``klode`` CLI), three real builds judged by the real ``kb-check``, the real publish and
prune. Law (K7): in one process, whatever kind of request comes first after a switch and whichever tool it is, the answer
comes from the build ``current`` pointed to when the request started; ``kb_version.build_id`` is the readlink and the
build the last query used.
"""

from __future__ import annotations

import json
import os
import signal
import time
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from _support import mcp_fx as M
from _support import publish_fx as F
from _support.flows import audit_env
from klode_kit import __version__
from klode_kit import publish as P
from klode_kit.devtools import audit

pytestmark = pytest.mark.cli_integration


@pytest.fixture(scope="module")
def tools_table():
    return M.live_tools()


@pytest.fixture()
def world(tmp_path):
    return M.marked(tmp_path)


def served_text(answer):
    res = answer["result"]
    assert res["isError"] is False, res
    return res["content"][0]["text"]


# --------------------------------------------------------------------------- 11a-01 / 06 / 07: A -> B in one process


def flow_a_to_b(world, mode):
    """Publish A, query, publish B, query - all through one process."""
    M.publish(world, "A")
    server = M.start(world, mode)
    try:
        pid = server.pid
        a = server.version()
        assert (a["build_id"], a["mode"]) == (world.ids["A"], M.MODE_NAMES[mode])
        search_a = served_text(server.call("search_sources", {"query": "MARKERAZETA MARKERBZETA"}))
        zoom_a = served_text(server.call("zoom_card", {"id": "hemoglobin"}))
        verify_a = served_text(server.call("verify_quote", {"id": "hemoglobin", "phrase": "血红蛋白 135 g/L"}))
        assert M.marker_tags(search_a) == M.marker_tags(zoom_a) == {"A"}
        assert verify_a.startswith("[proj]\nVERIFIED")
        M.publish(world, "B")
        b = server.version()
        assert b["build_id"] == world.ids["B"]
        search_b = served_text(server.call("search_sources", {"query": "MARKERAZETA MARKERBZETA"}))
        zoom_b = served_text(server.call("zoom_card", {"id": "hemoglobin"}))
        verify_b = served_text(server.call("verify_quote", {"id": "hemoglobin", "phrase": "血红蛋白 135 g/L"}))
        assert M.marker_tags(search_b) == M.marker_tags(zoom_b) == {"B"}
        assert verify_b.startswith("[proj]\nVERIFIED")
        assert server.proc.poll() is None and server.pid == pid  # the same process the whole time
        assert (a["kb_id"], a["wrapper_version"]) == ("proj", __version__)
        return {"search": (search_a, search_b), "zoom": (zoom_a, zoom_b), "verify": (verify_a, verify_b)}
    finally:
        server.close()


def test_same_process_A_to_B(world):
    """11a-01 (L3, S5): the published build is the one served - A, then B - by one and the same process."""
    flow_a_to_b(world, "normal")


def test_degrade_subprocess_proxy(world):
    """11a-06 (L3, S3): ``klode.lib.mcp_server`` cannot be imported -> a ``klode-mcp`` child per build; the A -> B flow holds."""
    flow_a_to_b(world, "proxy")


def test_degrade_cli_mode(world, tmp_path):
    """11a-07 (L3, S3): the import fails and ``klode-mcp`` dies at once -> ``klode --json`` per call; the A -> B flow holds,
    and the texts are the ones of the normal mode (the other builds of this project have the same cards)."""
    out_cli = flow_a_to_b(world, "cli")
    (tmp_path / "again").mkdir()
    other = M.marked(tmp_path / "again")
    out_normal = flow_a_to_b(other, "normal")
    assert out_cli == out_normal


@pytest.mark.parametrize("mode", ["proxy", "cli"])
def test_degraded_texts_equal_the_normal_ones(world, mode):
    """11a-07 (L3, S2) widened: every tool, in the degraded modes, answers with the text of the normal mode."""
    M.publish(world, "A")
    plan = [(name, name, args) for name, args in M.TOOL_CALLS.items()] + [
        ("zoom_meta", "zoom_card", {"id": "hemoglobin", "level": "meta"}),
        ("zoom_content", "zoom_card", {"id": "hemoglobin", "level": "content"}),
        ("zoom_full", "zoom_card", {"id": "hemoglobin", "level": "full"}),
        ("zoom_bogus_level", "zoom_card", {"id": "hemoglobin", "level": "bogus"}),
        ("zoom_unknown", "zoom_card", {"id": "nosuch"}),
        ("verify_missing", "verify_quote", {"id": "hemoglobin", "phrase": "nonexistent zzz phrase"}),
        ("verify_unknown_card", "verify_quote", {"id": "nosuch", "phrase": "x"}),
        ("consult_full", "consult_dimension", {"dimension": "blood-routine", "audience": "full"}),
        ("consult_section", "consult_dimension", {"dimension": "blood-routine", "section": "Craft"}),
        ("consult_none", "consult_dimension", {"dimension": "nosuch"}),
        ("consult_framework_none", "consult_framework", {"name": "nosuch"}),
        ("consult_card_as_dimension", "consult_dimension", {"dimension": "hemoglobin"}),
        ("consult_card_as_framework", "consult_framework", {"name": "hemoglobin"}),
        ("search_empty", "search_sources", {"query": "  "}),
        ("search_full", "search_sources", {"query": "MARKERAZETA", "full": True, "limit": 1}),
        ("search_limited", "search_sources", {"query": "血红蛋白 白细胞", "limit": 1}),
        ("search_nothing", "search_sources", {"query": "qqqzzznothing"}),
        ("search_tagged", "search_sources", {"query": "MARKERAZETA", "kb": "proj"}),
        ("verify_empty", "verify_quote", {"id": "hemoglobin", "phrase": ""}),
        ("diagnose_empty", "diagnose", {"symptom": ""}),
    ]
    texts = {}
    for which in ("normal", mode):
        server = M.start(world, which)
        try:
            assert server.version()["mode"] == M.MODE_NAMES[which]
            texts[which] = {label: server.call(tool, args)["result"] for label, tool, args in plan}
            assert all(r["isError"] is False for r in texts[which].values()), texts[which]
        finally:
            server.close()
    assert texts[mode] == texts["normal"]


def test_audience_engine_is_refused_in_cli_mode(world):
    """CLI mode cannot render the ``engine`` projection of a dimension; it says so (``isError``) instead of guessing."""
    M.publish(world, "A")
    server = M.start(world, "cli")
    try:
        res = server.call("consult_dimension", {"dimension": "blood-routine", "audience": "engine"})["result"]
        assert res["isError"] is True and "engine" in res["content"][0]["text"]
        assert server.proc.poll() is None
    finally:
        server.close()


# --------------------------------------------------------------------------- 11a-02: the law, over generated sequences

STEPS = st.lists(st.one_of(st.tuples(st.just("switch"), st.sampled_from("ABC")),
                           st.tuples(st.just("req"), st.sampled_from(sorted(M.TOOL_CALLS)))),
                 min_size=1, max_size=7)


@pytest.fixture(scope="module")
def shared_world(tmp_path_factory):
    return M.marked(tmp_path_factory.mktemp("mcp-shared"))


@settings(max_examples=10, deadline=None)
@given(steps=STEPS)
def test_kb_version_equals_readlink(shared_world, steps):
    """11a-02 (L3, S4): after every switch the first request may be any tool; the markers of a query belong to the
    readlink target, the lease the query left is the readlink target's, and ``kb_version.build_id`` is the readlink as well as
    the build that last query used."""
    world = shared_world
    M.publish(world, "A")
    server = M.start(world)
    try:
        for kind, what in steps:
            if kind == "switch":
                M.publish(world, what)
                assert M.readlink_tag(world) == what
                continue
            target = M.readlink_tag(world)
            answer = server.call(what, M.TOOL_CALLS[what])
            assert answer["result"]["isError"] is False, answer
            tags = M.marker_tags(answer["result"]["content"][0]["text"])
            if what in ("search_sources", "zoom_card"):
                assert tags == {target}
            else:
                assert tags <= {target}
            assert M.lease_tags(world) == {target}  # the build the query actually used
            assert server.version()["build_id"] == world.ids[target]
            assert world.ids[target] == os.path.basename(os.readlink(world.kb / "current"))
    finally:
        server.close()


@pytest.mark.parametrize("tool", sorted(M.TOOL_CALLS))
@pytest.mark.parametrize("mode", M.MODES)
def test_query_first_after_switch_serves_new_build(world, mode, tool):
    """11a-11 (L3, S5): after the switch the very first request - any tool of klode's table, no ``kb_version`` and no
    ``tools/list`` before it - is answered from the new build, in every mode; ``kb_version`` then says B."""
    M.publish(world, "A")
    server = M.start(world, mode)
    try:
        assert M.marker_tags(served_text(server.call("zoom_card", {"id": "hemoglobin"}))) == {"A"}
        M.publish(world, "B")
        text = served_text(server.call(tool, M.TOOL_CALLS[tool]))
        tags = M.marker_tags(text)
        assert "A" not in tags and (tags == {"B"} if tool in ("search_sources", "zoom_card") else tags <= {"B"})
        assert M.lease_tags(world) == {"B"}
        info = server.version()
        assert info["build_id"] == world.ids["B"] and info["mode"] == M.MODE_NAMES[mode]
    finally:
        server.close()


def test_proxy_child_death_costs_one_call(world):
    """Proxy mode: when the ``klode-mcp`` child dies, that call answers ``isError`` and the next call starts a new child."""
    M.publish(world, "A")
    server = M.start(world, "proxy")
    try:
        assert server.version()["mode"] == "subprocess-proxy"
        served_text(server.call("zoom_card", {"id": "hemoglobin"}))
        kids = M.subprocess.run(["pgrep", "-P", str(server.pid)], capture_output=True, text=True).stdout.split()
        assert len(kids) == 1
        os.kill(int(kids[0]), signal.SIGKILL)
        deadline = time.monotonic() + 30
        while not M.is_zombie(int(kids[0])) and time.monotonic() < deadline:
            time.sleep(0.02)  # dead for certain: its pipes are closed, the next write fails at once
        res = server.call("zoom_card", {"id": "hemoglobin"})["result"]
        assert res["isError"] is True and server.proc.poll() is None
        assert M.marker_tags(served_text(server.call("zoom_card", {"id": "hemoglobin"}))) == {"A"}
    finally:
        server.close()


def test_no_klode_at_all_every_call_is_an_error(world, tmp_path):
    """Neither the module, ``klode-mcp`` nor ``klode``: the wrapper still answers - ``kb_version`` (its own tool) works, every
    klode tool is an ``isError`` answer, nothing crashes, no lease is left."""
    M.publish(world, "A")
    bare = tmp_path / "bare"
    bare.mkdir()
    env = M.mode_env(world, "proxy", {"PATH": f"{bare}{os.pathsep}/usr/bin{os.pathsep}/bin"})
    server = M.Server(world.kb, env)
    try:
        server.initialize()
        info = server.version()
        assert (info["mode"], info["klode_version"]) == ("cli", "unknown")
        for tool in ("search_sources", "list_lenses", "no_such_tool"):
            res = server.call(tool, M.TOOL_CALLS.get(tool, {}))["result"]
            assert res["isError"] is True
        assert server.proc.poll() is None
    finally:
        server.close()
    assert M.lease_files(world.kb) == []


# --------------------------------------------------------------------------- 11a-03 / 04 / 05 / 10 / 12


def test_missing_current_iserror(world):
    """11a-03 (L3, S3): without ``current`` every call answers ``isError: true`` and the process lives on; after the
    publish the calls work."""
    server = M.start(world)
    try:
        for name in ("search_sources", "kb_version", "zoom_card"):
            res = server.call(name, M.TOOL_CALLS.get(name, {}))["result"]
            assert res["isError"] is True and "not published" in res["content"][0]["text"]
        assert server.proc.poll() is None and M.lease_files(world.kb) == []
        M.publish(world, "A")
        assert M.marker_tags(served_text(server.call("zoom_card", {"id": "hemoglobin"}))) == {"A"}
        assert server.version()["build_id"] == world.ids["A"]
    finally:
        server.close()


@pytest.mark.parametrize("mode", M.MODES)
def test_tools_list_includes_kb_version(world, tools_table, mode):
    """11a-04 (L3, S1): ``tools/list`` is klode's TOOLS (whatever the mode, with nothing published yet) plus ``kb_version``."""
    server = M.start(world, mode)
    try:
        tools = server.request("tools/list")["result"]["tools"]
        assert tools[:-1] == tools_table
        assert [t["name"] for t in tools][-1] == "kb_version" and tools[-1]["inputSchema"]["type"] == "object"
        assert len(tools) == len(tools_table) + 1
    finally:
        server.close()


@pytest.mark.parametrize("mode", M.MODES)
def test_handle_none_one_response_per_request(world, mode):
    """11a-05 (L3, S2): klode's ``handle`` returns ``None`` after writing its own answer - one line per request, ids match,
    no stray ``null`` line."""
    M.publish(world, "A")
    server = M.start(world, mode)
    try:
        first = server.initialize_answer  # type: ignore[attr-defined]
        assert first["id"] == 1 and first["result"]["serverInfo"]["name"] == "klode"
        assert first["result"]["serverInfo"]["version"] == "0.5.0"
        for method, expect in (("tools/list", None), ("ping", {})):
            answer = server.request(method)
            assert answer["jsonrpc"] == "2.0" and "error" not in answer
            if expect is not None:
                assert answer["result"] == expect
        assert server.silent(0.5)  # nothing else was written
    finally:
        server.close()


def test_malformed_jsonrpc(world):
    """11a-10 (L3, S3): garbage, a non-object and bad params get error answers; the process serves the next request."""
    M.publish(world, "A")
    server = M.start(world)
    try:
        server.send_raw("")
        server.send_raw("   ")
        assert server.silent(0.3)  # blank lines are not messages
        server.send_raw("this is not json")
        bad = server.recv()
        assert bad["id"] is None and bad["error"]["code"] == -32700
        server.send_raw("[1, 2]")
        assert server.recv()["error"]["code"] == -32600
        assert server.request("tools/call", ["not", "an", "object"])["error"]["code"] == -32602
        assert server.request("tools/call", {"name": "", "arguments": {}})["error"]["code"] == -32602
        assert server.request("tools/call", {"name": "kb_version", "arguments": [1]})["error"]["code"] == -32602
        assert server.request("no/such/method")["error"]["code"] == -32601
        assert server.proc.poll() is None
        assert server.version()["build_id"] == world.ids["A"]
        unknown = server.call("no_such_tool")["result"]
        assert unknown["isError"] is True
    finally:
        server.close()


@pytest.mark.parametrize("mode", M.MODES)
def test_notification_no_response(world, mode):
    """11a-12 (L3, S2): a message without ``id`` gets no answer, whatever its method; the next request is answered."""
    M.publish(world, "A")
    server = M.start(world, mode)
    try:
        assert server.silent(0.4)  # `notifications/initialized` (sent by start) wrote nothing
        server.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        server.send({"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "kb_version", "arguments": {}}})
        server.send({"jsonrpc": "2.0", "method": "ping"})
        assert server.silent(0.6)
        assert server.request("ping")["result"] == {}
        assert M.lease_files(world.kb) == []  # a notification did not even load a build
    finally:
        server.close()


# --------------------------------------------------------------------------- 11a-08: leases


def test_lease_cleanup_on_switch_and_exit(world):
    """11a-08 (L2, S3): the lease is a file below ``kb/<id>/.readers/<build>/``, never inside the sealed build; the lease
    of the build left behind is gone after the switch, all leases are gone at EOF."""
    M.publish(world, "A")
    server = M.start(world)
    try:
        served_text(server.call("zoom_card", {"id": "hemoglobin"}))
        (lease,) = M.lease_files(world.kb)
        assert lease.parent == world.kb / ".readers" / world.ids["A"]
        assert lease.name.startswith(f"{server.pid}-")
        M.publish(world, "B")
        served_text(server.call("zoom_card", {"id": "hemoglobin"}))
        (lease,) = M.lease_files(world.kb)
        assert lease.parent.name == world.ids["B"]  # A's lease went with the switch
        for tag in "AB":
            assert not (world.kb / "builds" / world.ids[tag] / ".readers").exists()  # the seal is not touched
        assert F.sound(world.kb) == []
    finally:
        assert server.close() == 0
    assert M.lease_files(world.kb) == []


def test_lease_removed_on_sigterm_and_left_dead_by_sigkill(world):
    """A terminated wrapper removes its lease; a killed one leaves it - and prune sees a dead pid and clears it."""
    M.publish(world, "A")
    server = M.start(world)
    served_text(server.call("zoom_card", {"id": "hemoglobin"}))
    assert len(M.lease_files(world.kb)) == 1
    server.proc.send_signal(signal.SIGTERM)
    server.proc.wait(timeout=30)
    assert M.lease_files(world.kb) == []
    server = M.start(world)
    served_text(server.call("zoom_card", {"id": "hemoglobin"}))
    (lease,) = M.lease_files(world.kb)
    server.kill()
    assert lease.is_file()  # nobody removed it
    M.publish(world, "B")
    code, body = P.prune(world.kb, 0, env=world.env)
    assert code == 0 and body["cleaned_leases"] == 1 and not lease.exists()
    assert body["moved"] == [world.ids["C"]]  # C was never published; A is the rollback target of B


def test_prune_honours_the_lease_of_a_running_wrapper(world):
    """The whole path: a running wrapper's lease (in ``kb/.readers``) keeps its build out of ``builds/_trash``; once the
    wrapper has moved on, the build can be moved."""
    M.publish(world, "A")
    server = M.start(world)
    try:
        served_text(server.call("zoom_card", {"id": "hemoglobin"}))
        M.publish(world, "B")
        M.publish(world, "C")  # A is neither current nor the rollback target (B) any more
        code, body = P.prune(world.kb, 0, env=world.env)
        assert code == 0 and body["protected"][world.ids["A"]] == "reader" and body["moved"] == []
        assert M.marker_tags(served_text(server.call("zoom_card", {"id": "hemoglobin"}))) == {"C"}
        code, body = P.prune(world.kb, 0, env=world.env)
        assert body["moved"] == [world.ids["A"]] and F.trash(world.kb) == [world.ids["A"]]
    finally:
        server.close()


# --------------------------------------------------------------------------- 11a-09 / 11a-13: pause points


def paused(world, tmp_path, extra_env=None):
    pause = tmp_path / "pause"
    pause.mkdir()
    env_extra = {"KLODE_KIT_PAUSE_AT": "mcp.after_resolve", "KLODE_KIT_PAUSE_DIR": str(pause), **(extra_env or {})}
    return pause, env_extra


def wait_for(path: Path, timeout: float = 60.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.02)
    return False


def test_switch_mid_call_uses_old(world, tmp_path):
    """11a-09 (L3, S3): a switch while a call is under way - this call is answered from the build it started on, the next
    one from the new build."""
    M.publish(world, "A")
    pause, extra = paused(world, tmp_path)
    server = M.Server(world.kb, M.mode_env(world, "normal", extra))
    try:
        server.initialize()
        server.send({"jsonrpc": "2.0", "id": 100, "method": "tools/call",
                     "params": {"name": "zoom_card", "arguments": {"id": "hemoglobin"}}})
        assert wait_for(pause / "mcp.after_resolve.reached"), server.stderr
        M.publish(world, "B")  # the pointer moves while the call is in flight
        (pause / "mcp.after_resolve.go").write_text("", encoding="utf-8")
        answer = server.recv()
        assert answer["id"] == 100 and M.marker_tags(answer["result"]["content"][0]["text"]) == {"A"}
        assert M.marker_tags(served_text(server.call("zoom_card", {"id": "hemoglobin"}))) == {"B"}
        assert server.version()["build_id"] == world.ids["B"]
    finally:
        server.close()


def under(path: str | None, root: Path) -> bool:
    if not path:
        return False
    root_s = os.path.realpath(root)
    return path == root_s or path.startswith(root_s + os.sep)


def test_lease_before_load_under_prune(world, tmp_path):
    """11a-13 (L2, S3): a call has resolved B and is held at ``mcp.after_resolve``; meanwhile C is published, A made current
    again and ``--prune --keep 0`` runs - B is kept because of the wrapper's lease (and only that), is served, and its lease is
    gone after the request. The audit log of the wrapper process proves the order: the first file system change on B's lease
    path, then ``mcp.after_resolve``, then the first open of a file inside ``builds/B/`` (events by realpath at event time)."""
    M.publish(world, "A")
    M.publish(world, "B")
    pause, extra = paused(world, tmp_path)
    log = tmp_path / "audit.jsonl"
    env = audit_env(log, {**world.env, **extra, "PYTHONPATH": M.klode_site()})
    server = M.Server(world.kb, env)
    try:
        server.initialize()
        server.send({"jsonrpc": "2.0", "id": 100, "method": "tools/call",
                     "params": {"name": "zoom_card", "arguments": {"id": "hemoglobin"}}})
        assert wait_for(pause / "mcp.after_resolve.reached"), server.stderr  # the call resolved B and is held
        M.publish(world, "C")
        M.publish(world, "A")  # B is now neither current nor the rollback target
        code, body = P.prune(world.kb, 0, env=world.env)
        assert code == 0 and body["protected"][world.ids["B"]] == "reader"
        assert (world.kb / "builds" / world.ids["B"]).is_dir() and world.ids["B"] not in F.trash(world.kb)
        (pause / "mcp.after_resolve.go").write_text("", encoding="utf-8")
        answer = server.recv()
        assert answer["id"] == 100 and answer["result"]["isError"] is False
        assert M.marker_tags(answer["result"]["content"][0]["text"]) == {"B"}
        assert world.ids["B"] not in F.trash(world.kb)
        # a call that resolved a build and found the pointer moved on lets go of it right after answering
        assert M.wait_until(lambda: M.lease_files(world.kb) == [])
        assert M.marker_tags(served_text(server.call("zoom_card", {"id": "hemoglobin"}))) == {"A"}
    finally:
        server.close()
    events = [e for e in audit.read_log(log) if e.get("pid") == server.pid]
    kb_real = os.path.realpath(world.kb)
    lease_root = Path(kb_real) / ".readers" / world.ids["B"]
    build_root = Path(kb_real) / "builds" / world.ids["B"]
    lease_changes = [i for i, e in enumerate(events) if e.get("mut") is True and under(e.get("path"), lease_root)]
    resolved = [i for i, e in enumerate(events) if e.get("ev") == audit.CRASHPOINT_EVENT and e.get("name") == "mcp.after_resolve"]
    opens = [i for i, e in enumerate(events) if e.get("ev") == "open" and under(e.get("path"), build_root)]
    assert lease_changes, "no file system change on the lease path of B"
    assert resolved, "mcp.after_resolve was never reached"
    assert opens, "no file of builds/B was ever opened"
    assert lease_changes[0] < resolved[0] and lease_changes[0] < opens[0]


# --------------------------------------------------------------------------- more states


def test_unloadable_current_is_an_error_not_a_crash(world):
    """A ``current`` that points to a build whose ``library.toml`` is unusable: the call answers ``isError``, no lease is
    left behind, the process lives on and serves the next, good build."""
    M.publish(world, "A")
    ghost = "b-20991231T235959Z-deadbeef"
    os.symlink(f"builds/{ghost}", world.kb / "current.next")
    os.replace(world.kb / "current.next", world.kb / "current")  # dangling: the build does not exist
    broken = world.kb / "builds" / "b-20991231T235958Z-badbad00"
    broken.mkdir()
    (broken / "library.toml").write_text("this is [not toml", encoding="utf-8")
    server = M.start(world)
    try:
        res = server.call("zoom_card", {"id": "hemoglobin"})["result"]
        assert res["isError"] is True and server.proc.poll() is None and M.lease_files(world.kb) == []
        os.symlink(f"builds/{broken.name}", world.kb / "current.next")
        os.replace(world.kb / "current.next", world.kb / "current")
        res = server.call("zoom_card", {"id": "hemoglobin"})["result"]
        assert res["isError"] is True and server.proc.poll() is None and M.lease_files(world.kb) == []
        M.publish(world, "B")
        assert M.marker_tags(served_text(server.call("zoom_card", {"id": "hemoglobin"}))) == {"B"}
    finally:
        server.close()


def test_current_pointing_outside_our_ids_is_not_published(world):
    M.publish(world, "A")
    os.symlink("builds/not-a-build-id", world.kb / "current.next")
    os.replace(world.kb / "current.next", world.kb / "current")
    server = M.start(world)
    try:
        res = server.call("kb_version")["result"]
        assert res["isError"] is True and "not published" in res["content"][0]["text"]
    finally:
        server.close()


def test_kb_version_fields(world):
    M.publish(world, "B")
    server = M.start(world)
    try:
        info = server.version()
        last = json.loads((world.kb / "PUBLISH.log").read_text(encoding="utf-8").splitlines()[-1])
        assert info == {"kb_id": "proj", "build_id": world.ids["B"],
                        "library_toml": str(Path(os.path.realpath(world.kb)) / "builds" / world.ids["B"] / "library.toml"),
                        "published_at": last["at"], "wrapper_version": __version__, "klode_version": "0.5.0",
                        "mode": "in-process"}
    finally:
        server.close()


def test_usage_errors_exit_2(tmp_path):
    for args in ([], ["--kb", str(tmp_path / "missing")], ["--nonsense"]):
        proc = M.subprocess.run([str(M.LAUNCHER), "mcp", *args], capture_output=True, timeout=60, stdin=M.subprocess.DEVNULL)
        assert proc.returncode == 2, (args, proc.stderr)
        assert proc.stdout == b""


# --------------------------------------------------------------------------- misbehaving klode, more states of the lease


def test_a_klode_mcp_that_is_no_program_hands_over_to_the_cli(world, tmp_path):
    """Proxy level: ``klode-mcp`` cannot even be started (exec format error) -> the CLI level, announced by ``kb_version.mode``."""
    M.publish(world, "A")
    server = M.Server(world.kb, M.mode_env(world, "proxy", M.fake_klode_mcp(tmp_path, "noexec")))
    try:
        server.initialize()
        assert server.version()["mode"] == "cli"
        assert M.marker_tags(served_text(server.call("zoom_card", {"id": "hemoglobin"}))) == {"A"}
        assert "unusable" in server.stderr
    finally:
        server.close()


def test_a_klode_mcp_that_never_answers_initialize_is_replaced(world, tmp_path):
    M.publish(world, "A")
    extra = {**M.fake_klode_mcp(tmp_path, "silent"), "KLODE_KIT_MCP_HANDSHAKE_TIMEOUT": "1"}
    server = M.Server(world.kb, M.mode_env(world, "proxy", extra))
    try:
        server.initialize()
        assert server.version()["mode"] == "cli"
        assert M.marker_tags(served_text(server.call("zoom_card", {"id": "hemoglobin"}))) == {"A"}
        assert M.children(server.pid) == []  # the mute child was killed, not left behind
    finally:
        server.close()


def test_noise_on_the_klode_mcp_stdout_is_not_forwarded(world, tmp_path):
    """A non-JSON line and a response for another id in front of the real answer are skipped; the client sees one answer per request."""
    M.publish(world, "A")
    server = M.Server(world.kb, M.mode_env(world, "proxy", M.fake_klode_mcp(tmp_path, "noisy")))
    try:
        server.initialize()
        assert server.version()["mode"] == "subprocess-proxy"
        assert M.marker_tags(served_text(server.call("zoom_card", {"id": "hemoglobin"}))) == {"A"}
        assert server.silent(0.5)
    finally:
        server.close()


def test_a_klode_mcp_that_stops_answering_costs_one_call_and_is_killed(world, tmp_path):
    M.publish(world, "A")
    extra = {**M.fake_klode_mcp(tmp_path, "hang"), "KLODE_KIT_MCP_CALL_TIMEOUT": "1"}
    server = M.Server(world.kb, M.mode_env(world, "proxy", extra))
    try:
        server.initialize()
        started = time.monotonic()
        res = server.call("zoom_card", {"id": "hemoglobin"})["result"]
        assert res["isError"] is True and "no answer" in res["content"][0]["text"]
        assert time.monotonic() - started < 30 and server.proc.poll() is None
        assert M.children(server.pid) == []  # it ignored EOF: the wrapper killed it
        assert server.version()["build_id"] == world.ids["A"]  # kb_version needs no klode-mcp answer
    finally:
        server.close()


def test_klode_cli_that_prints_no_json_is_an_error_answer(world, tmp_path):
    M.publish(world, "A")
    server = M.Server(world.kb, M.mode_env(world, "cli", M.garbage_klode(tmp_path)))
    try:
        server.initialize()
        res = server.call("zoom_card", {"id": "hemoglobin"})["result"]
        assert res["isError"] is True and "see server logs" in res["content"][0]["text"]
        assert server.proc.poll() is None and server.version()["mode"] == "cli"
    finally:
        server.close()
    assert "klode: oops" in server.stderr  # what klode said is not lost


def test_two_readers_of_one_build_keep_their_own_leases(world):
    M.publish(world, "A")
    one, two = M.start(world), M.start(world)
    try:
        served_text(one.call("zoom_card", {"id": "hemoglobin"}))
        served_text(two.call("zoom_card", {"id": "hemoglobin"}))
        assert len(M.lease_files(world.kb)) == 2
        assert one.close() == 0
        (left,) = M.lease_files(world.kb)
        assert left.name.startswith(f"{two.pid}-")  # the other reader's lease, and its directory, are still there
        code, body = P.prune(world.kb, 0, env=world.env)
        assert body["protected"][world.ids["A"]] == "current"
    finally:
        two.close()
    assert M.lease_files(world.kb) == []


def test_a_lease_removed_by_hand_is_not_an_error(world):
    M.publish(world, "A")
    server = M.start(world)
    try:
        served_text(server.call("zoom_card", {"id": "hemoglobin"}))
        (lease,) = M.lease_files(world.kb)
        os.unlink(lease)
        M.publish(world, "B")
        assert M.marker_tags(served_text(server.call("zoom_card", {"id": "hemoglobin"}))) == {"B"}
        assert M.lease_tags(world) == {"B"}
    finally:
        server.close()


def test_lease_survives_its_directory_being_pruned_away_before_the_file_is_made(world, tmp_path):
    """Pause between ``mkdir`` of the (still empty) lease directory and the creation of the file; a prune removes the empty
    directory just then: the wrapper makes it again and writes the lease."""
    M.publish(world, "A")
    pause = tmp_path / "pause"
    pause.mkdir()
    extra = {"KLODE_KIT_PAUSE_AT": "mcp.lease_dir", "KLODE_KIT_PAUSE_DIR": str(pause)}
    server = M.Server(world.kb, M.mode_env(world, "normal", extra))
    try:
        server.initialize()
        server.send({"jsonrpc": "2.0", "id": 100, "method": "tools/call",
                     "params": {"name": "zoom_card", "arguments": {"id": "hemoglobin"}}})
        assert wait_for(pause / "mcp.lease_dir.reached"), server.stderr
        lease_dir = P.reader_lease_dir(world.kb, world.ids["A"])
        assert lease_dir.is_dir() and list(lease_dir.iterdir()) == []
        os.rmdir(lease_dir)
        (pause / "mcp.lease_dir.go").write_text("", encoding="utf-8")
        answer = server.recv()
        assert answer["id"] == 100 and M.marker_tags(answer["result"]["content"][0]["text"]) == {"A"}
        assert M.lease_tags(world) == {"A"}
    finally:
        server.close()


def test_client_gone_ends_the_wrapper_quietly(world):
    M.publish(world, "A")
    server = M.start(world)
    served_text(server.call("zoom_card", {"id": "hemoglobin"}))
    server.proc.stdout.close()  # the reader of our answers goes away
    server.send({"jsonrpc": "2.0", "id": 99, "method": "ping"})
    assert server.proc.wait(timeout=30) == 0
    assert M.lease_files(world.kb) == [] and "Traceback" not in server.stderr


def test_kb_version_without_a_usable_publish_log(world):
    """``published_at`` is ``None`` when there is no log, or its last line is not a row; the rest is still answered."""
    os.symlink(f"builds/{world.ids['A']}", world.kb / "current")  # pointed at by hand: no PUBLISH.log at all
    server = M.start(world)
    try:
        assert server.version()["published_at"] is None
        for last in ("this is not json", "[1, 2]", '{"no_at": 1}'):
            (world.kb / "PUBLISH.log").write_text(last + "\n", encoding="utf-8")
            info = server.version()
            assert (info["published_at"], info["build_id"]) == (None, world.ids["A"])
    finally:
        server.close()


def test_cli_mode_with_an_unreadable_library_toml_still_names_the_kb(world):
    """The CLI level cannot read ``library.toml``: the library is named after its directory; klode's own answer is an error."""
    broken = world.kb / "builds" / "b-20991231T235958Z-badbad00"
    broken.mkdir()
    (broken / "library.toml").write_text("this is [not toml", encoding="utf-8")
    os.symlink(f"builds/{broken.name}", world.kb / "current")
    server = M.start(world, "cli")
    try:
        assert server.version()["kb_id"] == "proj"
        assert server.call("list_kbs")["result"]["content"][0]["text"].endswith("- proj — (no description)")
        assert server.call("zoom_card", {"id": "hemoglobin"})["result"]["isError"] is True
    finally:
        server.close()


def test_tools_list_after_the_hand_over_to_the_cli(world, tools_table, tmp_path):
    """The table is the same before and after a proxy that cannot work gave way to the CLI level."""
    M.publish(world, "A")
    server = M.Server(world.kb, M.mode_env(world, "proxy", M.fake_klode_mcp(tmp_path, "noexec")))
    try:
        server.initialize()
        before = server.request("tools/list")["result"]["tools"]
        assert server.version()["mode"] == "cli"
        after = server.request("tools/list")["result"]["tools"]
        assert before == after and after[:-1] == tools_table
    finally:
        server.close()


def test_kb_version_answer_keeps_the_contract(world):
    """The answer validates against ``schemas/kb_version.json`` (the contract of the only ``u5_ready`` evidence)."""
    from klode_kit.contract.schema import issues

    M.publish(world, "A")
    for mode in M.MODES:
        server = M.start(world, mode)
        try:
            assert issues("kb_version", server.version()) == []
        finally:
            server.close()
