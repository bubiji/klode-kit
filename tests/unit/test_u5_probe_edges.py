"""WI-11b: edges of the probes found while implementing (cases added after the red was recorded; the specification files
``test_u5_probe.py`` / ``test_register.py`` are unchanged by them): a server that is noisy or dies early, odd stream
events, a ``claude`` that times out or cannot be started.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from _support import publish_fx as F
from _support import u5_fx as U
from klode_kit import u5_probe as P

pytestmark = pytest.mark.unit

T = F.build_id(1)
SERVER = "demo-klode"

NOISY = r'''
import json, os, sys
for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg:
        continue
    print("this is not json", flush=True)
    print(json.dumps({"jsonrpc": "2.0", "id": 99, "result": {"stale": True}}), flush=True)
    print(json.dumps({"jsonrpc": "2.0", "method": "notifications/message", "params": {}}), flush=True)
    if msg["method"] == "initialize":
        out = {"protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "n", "version": "0"}}
    else:
        out = {"content": [{"type": "text", "text": json.dumps({"build_id": os.environ["STUB_BUILD"]})}], "isError": False}
    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": out}), flush=True)
'''

DIES_AFTER_INIT = r'''
import json, sys
msg = json.loads(sys.stdin.readline())
print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"protocolVersion": "2025-06-18", "capabilities": {}}}), flush=True)
sys.stderr.write("server: going away\n")
'''


def entry(tmp: Path, script: str) -> Path:
    project = tmp / "proj"
    project.mkdir()
    path = tmp / "srv.py"
    path.write_text(script, encoding="utf-8")
    U.mcp_json(project, SERVER, sys.executable, [str(path)], env={"STUB_BUILD": T})
    return project


def env(tmp: Path, **extra) -> dict[str, str]:
    base = dict(os.environ)
    for k in ("KLODE_KIT_REAL", "KLODE_KIT_GATE_ACK"):
        base.pop(k, None)
    base["KLODE_KIT_FAKE_LOG"] = str(tmp / "fake.log")
    base.update(extra)
    return base


def test_noise_lines_other_ids_and_notifications_are_skipped(tmp_path):
    out = P.direct_probe(entry(tmp_path, NOISY), SERVER, env=env(tmp_path))
    assert out["ok"] is True and out["build_id"] == T


def test_server_that_goes_away_after_initialize(tmp_path):
    out = P.direct_probe(entry(tmp_path, DIES_AFTER_INIT), SERVER, env=env(tmp_path))
    assert out["ok"] is False and "closed its output" in out["error"] and "going away" in out["error"]


def test_a_deadline_that_has_passed_is_a_timeout(tmp_path):
    out = P.direct_probe(entry(tmp_path, NOISY), SERVER, env=env(tmp_path), timeout=0.0)
    assert out["ok"] is False and "timed out" in out["error"]


@pytest.mark.parametrize("content", [None, 5, {"text": "x"}, [None, "x", {"type": "image"}]])
def test_odd_tool_result_content_reads_no_build(content):
    event = {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t", "content": content}]}}
    lines = [U.j(U.init(SERVER)), U.j(U.tool_use("t", f"mcp__{SERVER}__kb_version")), U.j(event)]
    got = P.parse_host_stream(lines, SERVER)
    assert got["build_id"] is None and got["error"]


def test_odd_blocks_and_messages_in_the_stream_are_ignored():
    lines = [U.j(U.init(SERVER)),
             U.j({"type": "assistant", "message": {"content": ["text", None, 3]}}),
             U.j({"type": "assistant", "message": "not a dict"}),
             U.j({"type": "assistant", "message": {"content": "not a list"}}),
             U.j({"type": "user", "message": {"content": [{"type": "tool_use", "name": f"mcp__{SERVER}__kb_version"}]}}),
             *U.stream(SERVER, T)[1:]]
    assert P.parse_host_stream(lines, SERVER)["build_id"] == T


def test_host_probe_timeout(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    scenario = tmp_path / "slow.json"
    scenario.write_text(json.dumps({"steps": [{"sleep_s": 3, "stdout": ""}]}), encoding="utf-8")
    out = P.host_probe(project, SERVER, env=env(tmp_path, KLODE_KIT_FAKE_SCENARIO=str(scenario)), timeout=0.5)
    assert out["host_state"] == "failed" and out["build_id"] is None and "timed out" in out["error"]


def real_claude_that_cannot_start(tmp: Path) -> dict[str, str]:
    U.executable(tmp / "bin" / "claude", "#!/nonexistent/interpreter\n")
    e = env(tmp, KLODE_KIT_REAL="1", KLODE_KIT_GATE_ACK="G2-20261026")
    e["PATH"] = str(tmp / "bin") + os.pathsep + e["PATH"]
    return e


def test_host_probe_claude_that_cannot_be_started(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    with pytest.raises(P.ProbeEnvironmentError, match="cannot be started"):
        P.host_probe(project, SERVER, env=real_claude_that_cannot_start(tmp_path))


def test_run_without_a_claude_is_exit_40_and_the_direct_probe_is_kept(tmp_path):
    project = entry(tmp_path, NOISY)
    kb = project / "kb" / "demo"
    (kb / "builds" / T).mkdir(parents=True)
    code, body = P.run(kb, T, project=project, host=True, env=real_claude_that_cannot_start(tmp_path))
    assert code == 40 and body["u5_ready"] is False and body["reason"] == "environment"
    assert body["direct"]["build_id"] == T and body["probe_build_id"] == T and body["host"] is None


def test_host_probe_model_option_is_passed(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    out = P.host_probe(project, SERVER, env=env(tmp_path, **U.fake_claude_env(tmp_path, U.stream(SERVER, T))),
                       model="synthetic-model")
    argv = out["argv"]
    assert argv[argv.index("--model") + 1] == "synthetic-model" and out["build_id"] == T
