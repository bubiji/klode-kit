"""WI-09d: ``current`` is complete at every crash point (matrix 09d-02, 09d-03; K5; design 02 r3.2 "单一生效指针").

Law (S5): whatever instant the process dies at - at each enumerated crash point of the build -> check -> publish
section (SIGKILL by the test seam ``KLODE_KIT_CRASH_AT``), and at random instants (SIGKILL after a drawn delay) - the
pointer either does not exist yet or points to a build that is verified (``MANIFEST.sha256`` equals the tree,
``check_receipt`` PASS for exactly that build, read-only all the way down: ``F.sound``), and resuming converges on a
consistent state: ``current`` on the target, the log gap-free with one pointer row, every registration done.
The section is enumerated twice: ``kb-publish`` itself (``publish.*``), and the pipeline around it
(``pipeline --resume`` crashed in the build, check and publish stages; the points of the build tree seal and of the
check receipt). 00s-16 pairs the publish list with the hooked run of ``pipeline --resume``.
"""

from __future__ import annotations

import errno
import json
import os
import stat
import signal
import subprocess
import threading
import time
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from _support import publish_fx as F
from _support.flows import PUBLISH_ONLY_CRASHPOINTS, audit_env
from klode_kit import publish as P

pytestmark = pytest.mark.contract_replay

KILLED = -signal.SIGKILL


def crash_env(point: str) -> dict[str, str]:
    return {"KLODE_KIT_CRASH_AT": point}


def converged(ab, target: str) -> None:
    """The state a completed publication of ``target`` leaves, however the earlier attempts ended."""
    kb = ab.kb
    assert F.current(kb) == target and F.sound(kb) == []
    rows = F.rows(kb)
    assert [r["seq"] for r in rows] == list(range(1, len(rows) + 1))
    assert len([r for r in rows if r["action"] == "switch" and r["target_build_id"] == target]) == 1
    assert P.pending_builds(rows) == set() and P.pending_actions(rows, target) == []
    server = f"{kb.name}-klode"
    mcp = json.loads((ab.project / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"][server]
    assert mcp["env"] == {"KLODE_KIT_BUILD": target}
    assert f'build_id = "{target}"' in F.registry_file(ab.tmp).read_text(encoding="utf-8")
    agent = (ab.project / ".claude" / "agents" / f"{kb.name}-klode-expert.md").read_text(encoding="utf-8")
    assert f"<!-- klode-kit build {target} -->" in agent
    settings_ = json.loads((ab.project / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
    assert settings_["enabledMcpjsonServers"].count(server) == 1
    leftovers = list(ab.project.rglob("*.tmp")) + list(F.registry_file(ab.tmp).parent.glob("*.tmp"))
    assert leftovers == [] and not os.path.lexists(kb / "current.tmp")


@pytest.mark.parametrize("pre_a", [False, True], ids=["fresh", "after-A"])
@pytest.mark.parametrize("point", sorted(PUBLISH_ONLY_CRASHPOINTS))
def test_pointer_always_complete_in_kb_publish(tmp_path, point, pre_a):
    """09d-02 (L2, S5), the ``kb-publish`` part: a kill at each of its crash points leaves ``current`` absent or on A
    or B - a verified build in each case - and the next run completes the publication of B exactly once."""
    if pre_a and point == "publish.mkdir":
        pytest.skip("with A published the directories of the registrations exist: the point is not on this path")
    ab = F.ab(tmp_path)
    if pre_a:
        assert F.cli(ab.kb, ab.project, tmp_path, "--build", ab.a).returncode == 0
    receipt = tmp_path / "out" / "publish_receipt.json"
    proc = F.cli(ab.kb, ab.project, tmp_path, "--build", ab.b, "--receipt", str(receipt), extra_env=crash_env(point))
    assert proc.returncode == KILLED, (point, proc.stderr.decode())  # every enumerated point is on the path
    assert F.current(ab.kb) in ((ab.a, ab.b) if pre_a else (None, ab.b))
    if (ab.kb / "current").is_symlink():
        assert F.sound(ab.kb) == []
    for line in F.log_bytes(ab.kb).decode().splitlines():
        json.loads(line)  # no torn row
    resumed = F.cli(ab.kb, ab.project, tmp_path, "--build", ab.b, "--receipt", str(receipt))
    assert resumed.returncode == 0, resumed.stderr.decode()
    converged(ab, ab.b)


def test_the_enumerated_points_are_all_there_is(tmp_path):
    """A run of ``kb-publish`` passes exactly the points of the list (nothing unlisted, nothing missing), so the
    enumeration above is the whole path (the 00s-16 partition judges the same names in the hooked pipeline run)."""
    ab = F.ab(tmp_path)
    log = tmp_path / "points.jsonl"
    env = audit_env(log, F.env(tmp_path))
    proc = subprocess.run(F.argv(ab.kb, ab.project, "--build", ab.a, "--receipt", str(tmp_path / "r.json")), env=env,
                          capture_output=True, timeout=120)
    assert proc.returncode == 0, proc.stderr.decode()
    names = {json.loads(ln)["name"] for ln in log.read_text(encoding="utf-8").splitlines()
             if json.loads(ln)["ev"] == "klode_kit.crashpoint"}
    assert names == PUBLISH_ONLY_CRASHPOINTS


# --------------------------------------------------------------------------- random instants


@settings(max_examples=14, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(delay=st.floats(min_value=0.0, max_value=0.9), pre_a=st.booleans())
def test_pointer_complete_under_random_sigkill(tmp_path_factory, delay, pre_a):
    """09d-02 (L2, S5), random part: SIGKILL after a drawn delay (the later crash points of a loop, and the instants
    between the points) - ``current`` is absent or verified; the resume converges."""
    tmp = tmp_path_factory.mktemp("kill")
    ab = F.ab(tmp)
    if pre_a:
        assert F.cli(ab.kb, ab.project, tmp, "--build", ab.a).returncode == 0
    proc = F.popen(ab.kb, ab.project, tmp, "--build", ab.b)
    time.sleep(delay)
    proc.kill()
    proc.communicate()
    if (ab.kb / "current").is_symlink():
        assert F.sound(ab.kb) == []
        assert F.current(ab.kb) in (ab.a, ab.b)
    else:
        assert not pre_a
    for line in F.log_bytes(ab.kb).decode().splitlines():
        json.loads(line)
    resumed = F.cli(ab.kb, ab.project, tmp, "--build", ab.b)
    assert resumed.returncode == 0, resumed.stderr.decode()
    converged(ab, ab.b)


# --------------------------------------------------------------------------- 09d-03


def test_switch_no_window(tmp_path):
    """09d-03 (L2, S4): a thread reads the link and resolves it over and over while another switches between two
    builds 200 times - the reader never meets a missing or dangling ``current``."""
    kb, ids = F.fake_kb(tmp_path, 2, current_to=0)
    stop = threading.Event()
    seen: set[str] = set()
    errors: list[str] = []

    def describe(path) -> str:  # diagnostics only: what the path is at the moment after a failure
        try:
            mode = os.lstat(path).st_mode
        except OSError as exc:
            return f"lstat failed: {exc!r}"
        kind = "symlink" if stat.S_ISLNK(mode) else "dir" if stat.S_ISDIR(mode) else "file" if stat.S_ISREG(mode) else "other"
        return f"lstat type={kind}"

    def retry_einval(fn, *a):  # WI-15f: a transient EINVAL while the link is renamed over is retried; other errors are not
        for attempt in range(6):
            try:
                return fn(*a)
            except OSError as exc:
                if exc.errno != errno.EINVAL or attempt == 5:
                    raise
                time.sleep(0.002)

    def reader():
        while not stop.is_set():
            call = "readlink"
            try:
                target = retry_einval(os.readlink, kb / "current")
                seen.add(os.path.basename(target))
                call = "stat"
                retry_einval(os.stat, kb / "current")  # resolves through the link: dangling raises
            except OSError as exc:
                errors.append(f"{repr(exc)} in os.{call}(current), errno={exc.errno}, {describe(kb / 'current')}, "
                              f"fs={describe(kb)}")

    t = threading.Thread(target=reader)
    t.start()
    try:
        for i in range(200):
            P.switch_pointer(kb, ids[(i + 1) % 2])
    finally:
        stop.set()
        t.join()
    assert errors == [] and seen == set(ids), (errors[:5], len(errors), sorted(seen))
    assert not os.path.lexists(kb / "current.tmp")


# --------------------------------------------------------------------------- the pipeline around it

PIPELINE_POINTS = [
    ("writeback", "pipeline.stage_running"),   # the build stage is marked running
    ("writeback", "build.manifest"),           # build: MANIFEST written, tree not yet read-only
    ("writeback", "build.sealed"),             # build: tree read-only, nothing checked
    ("writeback", "pipeline.stage_done"),      # build stage done
    ("build", "pipeline.stage_running"),       # check stage marked running: the build exists, unchecked
    ("build", "check.unsealed"),               # check: the build's root writable for the receipt, none written
    ("build", "check.written"),                # check: receipt in place, root still writable
    ("build", "pipeline.stage_done"),
    ("check", "pipeline.stage_running"),       # publish stage marked running
    ("check", "publish.tmp_link"),
    ("check", "publish.switched"),
    ("check", "publish.action_written"),
    ("check", "publish.receipt_written"),
    ("check", "pipeline.stage_done"),
]


@pytest.mark.parametrize("until,point", PIPELINE_POINTS, ids=[f"{u}-{p}" for u, p in PIPELINE_POINTS])
def test_pointer_always_complete_through_the_pipeline(tmp_path, until, point):
    """09d-02 (L2, S5), the pipeline part: ``pipeline --resume`` killed (or its build / check / publish child killed)
    at a point of the build -> check -> publish section - the pointer is absent or verified at every stop, never at a
    half-sealed or unchecked build; ``--resume`` then publishes a checked build."""
    rig = F.pipeline_rig(tmp_path, until)
    kb_hint = rig.project / "kb"
    crashed = rig.run("--resume", env=crash_env(point), timeout=300)
    assert crashed.returncode != 0, (point, crashed.stderr.decode()[-800:])
    kbs = [p for p in kb_hint.iterdir()] if kb_hint.is_dir() else []
    for kb in kbs:
        if (kb / "current").is_symlink():
            assert F.sound(kb) == [], point
    resumed = rig.run("--resume", timeout=300)
    assert resumed.returncode == 0, resumed.stderr.decode()[-1500:]
    (kb,) = list((rig.project / "kb").iterdir())
    assert F.sound(kb) == []
    receipt = json.loads((rig.batch_dir / "pipeline" / "publish_receipt.json").read_text(encoding="utf-8"))
    assert F.current(kb) == receipt["build_id"]
    assert rig.receipt("publish")["status"] == "done"
    again = rig.run("--resume", timeout=300)  # idempotent: nothing runs, nothing is written to the log
    log = F.log_bytes(kb)
    assert again.returncode == 0 and F.log_bytes(kb) == log
