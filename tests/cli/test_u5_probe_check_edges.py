"""WI-11b: ``u5-probe --check`` when the real ``kb-check`` itself fails - the probes read the right build, the build's tree
no longer equals its MANIFEST: the check's exit code (30) is the result and ``u5_ready`` stays false (added while
implementing; ``test_u5_probe_cli.py`` is unchanged by it).
"""

from __future__ import annotations

import json
import subprocess

import pytest

from _support import build_fx as B
from _support import mcp_fx as M
from _support import u5_fx as U

pytestmark = pytest.mark.cli_integration


def test_failing_check_keeps_its_exit_code_and_u5_ready_false(tmp_path):
    world = M.marked(tmp_path)
    M.publish(world, "B")
    build = world.kb / "builds" / world.ids["B"]
    B.unlock(build)
    (build / "stray.txt").write_text("not in the MANIFEST", encoding="utf-8")
    env = M.mode_env(world, "normal", U.fake_claude_env(world.tmp, U.stream("proj-klode", world.ids["B"])))
    proc = subprocess.run([str(M.LAUNCHER), "u5-probe", "--kb", str(world.kb), "--project", str(world.project),
                           "--build", world.ids["B"], "--host", "--check"], env=env, capture_output=True, timeout=180)
    body = json.loads(proc.stdout.decode())
    assert proc.returncode == 30, proc.stderr.decode()
    assert body["u5_ready"] is False and body["check"]["exit_code"] == 30 and body["reason"] == "contract_violation"
    assert body["probe_build_id"] == world.ids["B"] and body["host"]["build_id"] == world.ids["B"]
