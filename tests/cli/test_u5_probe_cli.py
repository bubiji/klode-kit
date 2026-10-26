"""WI-11b: ``bin/klode-kit u5-probe`` against the real wrapper - matrix 11b-07 (direct probe), 11b-08 (a probe that reads
another build -> ``u5_ready:false``, exit 21), and the hand-over to the real ``kb-check`` (``--host-state`` /
``--probe-build-id``, WI-10a).

The data project is a tmp synthetic one (``mcp_fx.marked``: three real builds of ``fx-labs-3x``); ``HOME`` and
``KLODE_REGISTRY`` are inside tmp; the host probe is the replay fake ``claude`` - the real ``claude`` is case 11b-09
(``tests/model/test_host_probe_real.py``, manual gate G2).
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from _support import mcp_fx as M
from _support import u5_fx as U
from klode_kit import publish as P

pytestmark = pytest.mark.cli_integration


@pytest.fixture()
def world(tmp_path):
    return M.marked(tmp_path)


def probe(world, *args, mode="normal", extra=None, timeout=180):
    env = M.mode_env(world, mode, extra)
    proc = subprocess.run([str(M.LAUNCHER), "u5-probe", "--kb", str(world.kb), "--project", str(world.project), *args],
                          env=env, capture_output=True, timeout=timeout, cwd=str(world.tmp))
    body = json.loads(proc.stdout.decode()) if proc.stdout.strip() else None
    return proc, body


def mcp_entry(world):
    return json.loads((world.project / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["proj-klode"]


def test_direct_probe_build_id(world):
    """11b-07 (L3, S1): the probe starts the wrapper with the very argv and cwd of ``.mcp.json`` and reads the published
    build's id from ``kb_version``."""
    M.publish(world, "B")
    entry = mcp_entry(world)
    proc, body = probe(world, "--build", world.ids["B"])
    assert proc.returncode == 0, proc.stderr.decode()
    assert body["probe_build_id"] == body["direct"]["build_id"] == world.ids["B"] and body["u5_ready"] is False
    assert body["direct"]["argv"] == [entry["command"], *entry["args"]]
    assert body["direct"]["cwd"] == str(world.project) and body["host_state"] == "not_run"
    assert M.wait_until(lambda: M.lease_files(world.kb) == [])  # the probe's wrapper has gone, its lease with it


def test_direct_probe_follows_the_pointer_not_the_registration(world):
    """11b-07 widened (L3, S2): the wrapper answers with ``current``; a registration naming A while ``current`` is B is
    still read as B."""
    M.publish(world, "A")
    M.publish(world, "B")
    assert mcp_entry(world)["env"]["KLODE_KIT_BUILD"] == world.ids["B"]
    doc = json.loads((world.project / ".mcp.json").read_text(encoding="utf-8"))
    doc["mcpServers"]["proj-klode"]["env"]["KLODE_KIT_BUILD"] = world.ids["A"]
    (world.project / ".mcp.json").write_text(json.dumps(doc), encoding="utf-8")
    proc, body = probe(world, "--build", world.ids["B"])
    assert proc.returncode == 0 and body["probe_build_id"] == world.ids["B"]


def test_probe_mismatch_exit21(world):
    """11b-08 (L2, S3): ``current`` points back to the older build -> the probe reads A, the target is B: ``u5_ready``
    false, exit 21."""
    M.publish(world, "A")
    M.publish(world, "B")
    P.switch_pointer(world.kb, world.ids["A"])
    proc, body = probe(world, "--build", world.ids["B"])
    assert proc.returncode == 21, proc.stderr.decode()
    assert body["u5_ready"] is False and body["probe_build_id"] == world.ids["A"] and body["exit_code"] == 21
    assert body["build_id"] == world.ids["B"]


def test_default_target_is_the_current_build(world):
    M.publish(world, "A")
    proc, body = probe(world)
    assert proc.returncode == 0 and body["build_id"] == world.ids["A"] == body["probe_build_id"]


def test_with_the_wrapper_in_degraded_mode_the_probe_reads_the_same(world):
    M.publish(world, "B")
    proc, body = probe(world, "--build", world.ids["B"], mode="proxy")
    assert proc.returncode == 0 and body["probe_build_id"] == world.ids["B"]


def check_receipt(world, tag):
    return json.loads((world.kb / "builds" / world.ids[tag] / "check_receipt.json").read_text(encoding="utf-8"))


def test_host_probe_and_check_make_u5_ready_in_the_check_receipt(world):
    """The probes hand their findings to the real ``kb-check`` (``--host-state connected --probe-build-id``): the check
    receipt of the build says ``u5_ready`` true with ``probe.build_id``; the build is still sealed and publishable."""
    M.publish(world, "B")
    lines = U.stream("proj-klode", world.ids["B"])
    proc, body = probe(world, "--build", world.ids["B"], "--host", "--check",
                       extra=U.fake_claude_env(world.tmp, lines))
    assert proc.returncode == 0, proc.stderr.decode()
    assert body["u5_ready"] is True and body["host_state"] == "connected" and body["check"]["u5_ready"] is True
    rec = check_receipt(world, "B")
    assert rec["u5_ready"] is True and rec["probe"] == {"build_id": world.ids["B"]}
    assert rec["mcp"]["mcp_host_visible"] == "connected" and rec["status"] in ("PASS", "WARN")
    assert P.verify_target(world.kb, world.ids["B"]) == []


def test_host_probe_mismatch_leaves_the_check_receipt_not_ready(world):
    M.publish(world, "B")
    lines = U.stream("proj-klode", world.ids["A"])
    proc, body = probe(world, "--build", world.ids["B"], "--host", "--check",
                       extra=U.fake_claude_env(world.tmp, lines))
    assert proc.returncode == 21 and body["u5_ready"] is False and body["host"]["build_id"] == world.ids["A"]
    rec = check_receipt(world, "B")
    assert rec["u5_ready"] is False and "probe" not in rec


def test_host_probe_gate_is_closed_for_the_real_claude(world):
    """The host probe is a manual gate (G2): with a ``claude`` on PATH that is not the replay fake, and no
    ``KLODE_KIT_REAL``/ACK, exit 61 and the program is never started."""
    M.publish(world, "B")
    flag = world.tmp / "started"
    U.executable(world.tmp / "realbin" / "claude", f"#!/bin/sh\ntouch '{flag}'\n")
    env_path = str(world.tmp / "realbin") + os.pathsep + os.environ["PATH"]
    proc, body = probe(world, "--build", world.ids["B"], "--host", extra={"PATH": env_path})
    assert proc.returncode == 61 and body["u5_ready"] is False and not flag.exists()
