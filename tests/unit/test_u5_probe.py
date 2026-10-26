"""WI-11b: the ``u5_ready`` probes (``klode_kit.u5_probe``) - the pieces that need no real wrapper and no real model.

- the direct probe against a stand-in MCP server (started with the argv, cwd and env of ``.mcp.json``);
- the host probe's reading of ``claude -p --output-format stream-json --verbose`` by ``tool_use`` id (R4-04), on
  replayed streams (the replay fake ``claude`` of tests/fakes/bin; the real ``claude`` is the model_integration case
  11b-09, ``tests/model/test_host_probe_real.py``, gate G2 - not run here);
- the decision: ``u5_ready`` only when every probe that ran read the target build and the host sees the server connected;
  a probe that reads another build -> ``u5_ready:false`` and exit 21 (11b-08's unit half; the real half is in
  ``tests/cli/test_u5_probe_cli.py``).

Law (S4): ``u5_ready`` implies that the direct probe and the host probe both read exactly the target ``build_id``.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from _support import publish_fx as F
from _support import u5_fx as U
from klode_kit import gate
from klode_kit import u5_probe as P

pytestmark = pytest.mark.unit

T = F.build_id(1)       # the target
OLD = F.build_id(0)
SERVER = "demo-klode"


def env(tmp: Path, **extra) -> dict[str, str]:
    base = dict(os.environ)
    base.pop("KLODE_KIT_REAL", None)
    base.pop("KLODE_KIT_GATE_ACK", None)
    base["KLODE_KIT_FAKE_LOG"] = str(tmp / "fake.log")
    base.update(extra)
    return base


def proj(tmp: Path) -> Path:
    p = tmp / "proj"
    p.mkdir()
    return p


# --------------------------------------------------------------------------- the direct probe


def test_direct_probe_starts_the_entry_with_its_own_argv_cwd_and_env(tmp_path):
    p = proj(tmp_path)
    record = U.stub_entry(p, tmp_path, SERVER, build=T)
    out = P.direct_probe(p, SERVER, env=env(tmp_path))
    assert out["ok"] is True and out["build_id"] == T and out["error"] is None
    seen = json.loads(record.read_text(encoding="utf-8"))
    assert seen == {"argv": ["--kb", "KB"], "cwd": os.path.realpath(p), "marker": T}
    assert out["argv"] == [sys.executable, str(tmp_path / "stub_mcp.py"), "--kb", "KB"] and out["cwd"] == str(p)


@pytest.mark.parametrize("mode,fragment", [
    ("iserror", "isError"),
    ("nojson", "not JSON"),
    ("nobuild", "build_id"),
    ("crash", "closed"),
])
def test_direct_probe_failures_read_no_build(tmp_path, mode, fragment):
    p = proj(tmp_path)
    U.stub_entry(p, tmp_path, SERVER, build=T, mode=mode)
    out = P.direct_probe(p, SERVER, env=env(tmp_path), timeout=20)
    assert out["ok"] is False and out["build_id"] is None and fragment in out["error"]


def test_direct_probe_times_out_and_does_not_leave_the_server_running(tmp_path):
    p = proj(tmp_path)
    U.stub_entry(p, tmp_path, SERVER, build=T, mode="hang")
    out = P.direct_probe(p, SERVER, env=env(tmp_path), timeout=1.5)
    assert out["ok"] is False and "timed out" in out["error"]


@pytest.mark.parametrize("content,fragment", [
    (None, ".mcp.json"),
    ("{ broken", ".mcp.json"),
    ('{"mcpServers": {}}', "no entry"),
    ('{"mcpServers": {"demo-klode": {"args": []}}}', "command"),
    ('{"mcpServers": {"demo-klode": {"command": "/nonexistent/server", "args": []}}}', "cannot be started"),
])
def test_direct_probe_without_a_usable_entry(tmp_path, content, fragment):
    p = proj(tmp_path)
    if content is not None:
        (p / ".mcp.json").write_text(content, encoding="utf-8")
    out = P.direct_probe(p, SERVER, env=env(tmp_path))
    assert out["ok"] is False and out["build_id"] is None and fragment in out["error"]


def test_direct_probe_answer_with_a_non_build_id_is_not_ok(tmp_path):
    p = proj(tmp_path)
    U.stub_entry(p, tmp_path, SERVER, build="latest")
    out = P.direct_probe(p, SERVER, env=env(tmp_path))
    assert out["ok"] is False and out["build_id"] is None and "not a build id" in out["error"]


# --------------------------------------------------------------------------- reading the host's stream (R4-04)


def parse(lines, server=SERVER):
    return P.parse_host_stream("\n".join(lines) + "\n", server)


def test_stream_real_tool_result_by_tool_use_id():
    got = parse(U.stream(SERVER, T))
    assert (got["host_state"], got["build_id"], got["tool_use_id"], got["error"]) == ("connected", T, "toolu_real", None)


def test_stream_prose_and_final_text_do_not_count():
    """The model's own words (a build id in a text block or in the closing result line) are no evidence."""
    lines = [U.j(U.init(SERVER)), U.j(U.say(f"The library serves {T}.")), U.j(U.result_line(f"build {T}"))]
    got = parse(lines)
    assert got["build_id"] is None and "no kb_version call" in got["error"] and got["host_state"] == "connected"


def test_stream_tool_use_without_result():
    lines = [U.j(U.init(SERVER)), U.j(U.tool_use("toolu_x", f"mcp__{SERVER}__kb_version"))]
    got = parse(lines)
    assert got["build_id"] is None and "toolu_x" in got["error"]


def test_stream_error_result_reads_no_build():
    lines = [U.j(U.init(SERVER)), U.j(U.tool_use("toolu_x", f"mcp__{SERVER}__kb_version")),
             U.j(U.tool_result("toolu_x", "permission denied", is_error=True))]
    got = parse(lines)
    assert got["build_id"] is None and "is_error" in got["error"] and "permission denied" in got["error"]


@pytest.mark.parametrize("text", ["plain words", json.dumps({"kb_id": "x"}), json.dumps({"build_id": "latest"}),
                                  json.dumps([1])])
def test_stream_result_that_is_no_version_document(text):
    lines = [U.j(U.init(SERVER)), U.j(U.tool_use("t", f"mcp__{SERVER}__kb_version")), U.j(U.tool_result("t", text))]
    got = parse(lines)
    assert got["build_id"] is None and got["error"]


def test_stream_result_as_plain_string_content():
    lines = [U.j(U.init(SERVER)), U.j(U.tool_use("t", f"mcp__{SERVER}__kb_version")),
             U.j(U.tool_result("t", U.version_text(T), as_string=True))]
    assert parse(lines)["build_id"] == T


@pytest.mark.parametrize("status,state", [("connected", "connected"), ("pending", "pending"), ("failed", "failed"),
                                          ("needs-auth", "failed"), ("disabled", "failed")])
def test_stream_host_state_from_init(status, state):
    got = parse(U.stream(SERVER, T, status=status))
    assert got["host_state"] == state


def test_stream_server_missing_from_init_or_no_init_is_failed():
    other = U.init("some-other", "connected")
    assert parse([U.j(other)])["host_state"] == "failed"
    got = parse([U.j(U.say("hi"))])
    assert got["host_state"] == "failed" and "init" in got["error"]
    assert P.parse_host_stream("", SERVER)["host_state"] == "failed"


def test_stream_garbage_lines_are_skipped():
    lines = ["not json", "", "[1]", U.j({"type": "weird"}), *U.stream(SERVER, T)]
    assert parse(lines)["build_id"] == T


def test_stream_two_calls_the_last_answer_counts():
    lines = [U.j(U.init(SERVER)),
             U.j(U.tool_use("t1", f"mcp__{SERVER}__kb_version")), U.j(U.tool_result("t1", U.version_text(OLD))),
             U.j(U.tool_use("t2", f"mcp__{SERVER}__kb_version")), U.j(U.tool_result("t2", U.version_text(T)))]
    got = parse(lines)
    assert (got["build_id"], got["tool_use_id"]) == (T, "t2")


def test_stream_another_servers_kb_version_is_not_ours():
    lines = [U.j(U.init(SERVER)), U.j(U.tool_use("t", "mcp__other-klode__kb_version")),
             U.j(U.tool_result("t", U.version_text(OLD)))]
    got = parse(lines)
    assert got["build_id"] is None and "no kb_version call" in got["error"]


def decoys(draw_ids):
    return [e for i, bid in enumerate(draw_ids) for e in (
        U.tool_use(f"toolu_decoy{i}", "Read", {"file_path": "x"}), U.tool_result(f"toolu_decoy{i}", U.version_text(bid)),
        U.say(f"decoy {bid}"))]


@settings(max_examples=60, deadline=None)
@given(ids=st.lists(st.sampled_from([OLD, F.build_id(2), F.build_id(3)]), max_size=4),
       lead=st.integers(0, 12), gap=st.integers(0, 12), orphan=st.booleans())
def test_stream_pairing_is_by_id_whatever_surrounds_it(ids, lead, gap, orphan):
    """11b-09 pairing (L1, S4): the answer is the tool_result whose tool_use_id is the kb_version call's id - however many
    other tool calls, orphan results and chatter come before, between and after."""
    noise = decoys(ids)
    if orphan:
        noise.append(U.tool_result("toolu_never_used", U.version_text(OLD)))
    use, res = U.tool_use("toolu_real", f"mcp__{SERVER}__kb_version"), U.tool_result("toolu_real", U.version_text(T))
    i = min(lead, len(noise))
    j = min(i + gap, len(noise))
    events = [U.init(SERVER), *noise[:i], use, *noise[i:j], res, *noise[j:], U.result_line(f"build {OLD}")]
    got = parse([U.j(e) for e in events])
    assert (got["build_id"], got["tool_use_id"], got["error"]) == (T, "toolu_real", None)


# --------------------------------------------------------------------------- the host probe through the replay fake


def test_host_probe_runs_claude_print_stream_json_in_the_project(tmp_path):
    p = proj(tmp_path)
    e = env(tmp_path, **U.fake_claude_env(tmp_path, U.stream(SERVER, T)))
    out = P.host_probe(p, SERVER, env=e)
    assert (out["host_state"], out["build_id"], out["error"]) == ("connected", T, None)
    calls = [json.loads(x) for x in (tmp_path / "fake.log").read_text(encoding="utf-8").splitlines()]
    assert len(calls) == 1
    argv = calls[0]["argv"]
    assert calls[0]["cwd"] == os.path.realpath(p) and calls[0]["tool"] == "claude"
    assert "-p" in argv and argv[argv.index("--output-format") + 1] == "stream-json" and "--verbose" in argv
    assert argv[argv.index("--allowedTools") + 1] == f"mcp__{SERVER}__kb_version"
    assert "--max-budget-usd" in argv and out["argv"] == ["claude", *argv]


def test_host_probe_nonzero_exit_without_a_stream_is_failed(tmp_path):
    p = proj(tmp_path)
    e = env(tmp_path, **U.fake_claude_env(tmp_path, [], exit_code=1))
    out = P.host_probe(p, SERVER, env=e)
    assert out["host_state"] == "failed" and out["build_id"] is None and "exit 1" in out["error"]


def test_host_probe_the_gate_is_closed_for_a_real_claude(tmp_path):
    """G2 (manual gate): a ``claude`` that is not the replay fake is not started without KLODE_KIT_REAL=1 + ACK."""
    p = proj(tmp_path)
    flag = tmp_path / "started"
    U.executable(tmp_path / "bin" / "claude", f"#!/bin/sh\ntouch '{flag}'\n")
    e = env(tmp_path)
    e["PATH"] = str(tmp_path / "bin") + os.pathsep + e["PATH"]
    with pytest.raises(gate.GateClosed):
        P.host_probe(p, SERVER, env=e)
    assert not flag.exists()


def test_host_probe_without_claude_on_path(tmp_path):
    p = proj(tmp_path)
    e = env(tmp_path)
    e["PATH"] = str(tmp_path / "empty")
    e["KLODE_KIT_REAL"], e["KLODE_KIT_GATE_ACK"] = "1", "G2-20261026"
    with pytest.raises(P.ProbeEnvironmentError):
        P.host_probe(p, SERVER, env=e)


# --------------------------------------------------------------------------- the decision


@pytest.mark.parametrize("direct,ran,state,host_id,ready,code", [
    (T, False, "not_run", None, False, 0),
    (T, True, "connected", T, True, 0),
    (OLD, False, "not_run", None, False, 21),
    (None, False, "not_run", None, False, 21),
    (T, True, "connected", OLD, False, 21),
    (T, True, "connected", None, False, 21),
    (T, True, "pending", None, False, 21),
    (T, True, "failed", None, False, 21),
    (OLD, True, "connected", T, False, 21),
])
def test_decide_table(direct, ran, state, host_id, ready, code):
    assert P.decide(T, direct, ran, state, host_id) == (ready, code)


@settings(max_examples=200, deadline=None)
@given(direct=st.sampled_from([T, OLD, None]), ran=st.booleans(),
       state=st.sampled_from(["connected", "pending", "failed", "not_run"]), host_id=st.sampled_from([T, OLD, None]))
def test_u5_ready_implies_every_probe_read_the_target(direct, ran, state, host_id):
    """Law (S4): ``u5_ready`` => the direct probe and the host probe both read the target and the host is connected;
    a probe that ran and did not read the target => exit 21 and not ready."""
    ready, code = P.decide(T, direct, ran, state, host_id)
    if ready:
        assert direct == T and ran and state == "connected" and host_id == T and code == 0
    if direct != T or (ran and (state != "connected" or host_id != T)):
        assert not ready and code == 21
    if direct == T and not ran:
        assert not ready and code == 0


# --------------------------------------------------------------------------- run / main with the stand-in


def kb_of(tmp: Path, build_id: str = T) -> tuple[Path, Path]:
    p = proj(tmp)
    kb = p / "kb" / "demo"
    (kb / "builds" / build_id).mkdir(parents=True)
    return p, kb


def test_run_direct_only(tmp_path):
    p, kb = kb_of(tmp_path)
    U.stub_entry(p, tmp_path, SERVER, build=T)
    code, body = P.run(kb, T, project=p, env=env(tmp_path))
    assert code == 0 and body["u5_ready"] is False and body["host_state"] == "not_run"
    assert body["probe_build_id"] == T and body["build_id"] == T and body["host"] is None
    assert body["schema"] == "klode-kit.u5_probe/1" and body["server"] == SERVER and body["kb_id"] == "demo"


def test_run_direct_mismatch_exit_21(tmp_path):
    p, kb = kb_of(tmp_path)
    U.stub_entry(p, tmp_path, SERVER, build=OLD)
    code, body = P.run(kb, T, project=p, env=env(tmp_path))
    assert code == 21 and body["u5_ready"] is False and body["probe_build_id"] == OLD and body["exit_code"] == 21


def test_run_with_host_probe_ready(tmp_path):
    p, kb = kb_of(tmp_path)
    U.stub_entry(p, tmp_path, SERVER, build=T)
    e = env(tmp_path, **U.fake_claude_env(tmp_path, U.stream(SERVER, T)))
    code, body = P.run(kb, T, project=p, host=True, env=e)
    assert code == 0 and body["u5_ready"] is True and body["host_state"] == "connected"
    assert body["host"]["build_id"] == T and body["probe_build_id"] == T


def test_run_host_mismatch_exit_21(tmp_path):
    p, kb = kb_of(tmp_path)
    U.stub_entry(p, tmp_path, SERVER, build=T)
    e = env(tmp_path, **U.fake_claude_env(tmp_path, U.stream(SERVER, OLD)))
    code, body = P.run(kb, T, project=p, host=True, env=e)
    assert code == 21 and body["u5_ready"] is False and body["host"]["build_id"] == OLD


def test_run_gate_closed_is_exit_61_and_nothing_started(tmp_path):
    p, kb = kb_of(tmp_path)
    U.stub_entry(p, tmp_path, SERVER, build=T)
    flag = tmp_path / "started"
    U.executable(tmp_path / "bin" / "claude", f"#!/bin/sh\ntouch '{flag}'\n")
    e = env(tmp_path)
    e["PATH"] = str(tmp_path / "bin") + os.pathsep + e["PATH"]
    code, body = P.run(kb, T, project=p, host=True, env=e)
    assert code == 61 and body["u5_ready"] is False and body["reason"] == "gate_closed" and not flag.exists()


def test_run_defaults_build_to_the_current_link_and_project_to_above_kb(tmp_path):
    p, kb = kb_of(tmp_path)
    os.symlink(f"builds/{T}", kb / "current")
    U.stub_entry(p, tmp_path, SERVER, build=T)
    code, body = P.run(kb, None, env=env(tmp_path))
    assert code == 0 and body["build_id"] == T


def test_run_no_target_and_no_project_are_usage_errors(tmp_path):
    p, kb = kb_of(tmp_path)
    assert P.run(kb, None, project=p, env=env(tmp_path))[0] == 2          # no current, no --build
    assert P.run(kb, "latest", project=p, env=env(tmp_path))[0] == 2      # not a build id
    assert P.run(tmp_path / "elsewhere" / "demo", T, env=env(tmp_path))[0] == 2  # no project derivable
    assert P.run(kb, F.build_id(9), project=p, env=env(tmp_path))[0] == 2  # the build does not exist


def test_main_prints_one_json_line_and_writes_the_receipt(tmp_path, capsys, monkeypatch):
    p, kb = kb_of(tmp_path)
    U.stub_entry(p, tmp_path, SERVER, build=OLD)
    receipt = tmp_path / "out" / "u5.json"
    code = P.main(["--kb", str(kb), "--build", T, "--project", str(p), "--receipt", str(receipt)])
    out = capsys.readouterr().out
    assert code == 21 and out.count("\n") == 1
    assert json.loads(out) == json.loads(receipt.read_text(encoding="utf-8"))
    assert json.loads(out)["u5_ready"] is False
    assert P.main([]) == 2
    assert P.main(["--kb", str(kb), "--bogus"]) == 2
