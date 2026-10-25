"""WI-09d (small fix of WI-09c, deviations "小修待办"): ``klode-kit build --loops`` holds a lock for its whole run.

Without it two ``build --loops`` of the same library interleave their rounds (each round is a new immutable build,
and a round's repair spec names the previous round's build). The lock is an ``flock`` on ``kb/<id>/loops/`` (a
directory fd, no file) that is held between the rounds as well - the builds lock of ``run_build`` is released after
every round. A process that dies releases it with its fd.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from _support import build_fx as B

pytestmark = pytest.mark.contract_replay


def loops_cmd(project: Path, *args: str) -> list[str]:
    return [sys.executable, "-m", "klode_kit", "build", "--project", str(project), "--loops", *args]


def start(project: Path, tmp: Path, pause: Path) -> subprocess.Popen:
    env = B.env(tmp, {"KLODE_KIT_PAUSE_AT": "loops.lock", "KLODE_KIT_PAUSE_DIR": str(pause)})
    return subprocess.Popen(loops_cmd(project), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            cwd=str(project))


def wait_reached(pause: Path, proc: subprocess.Popen) -> None:
    marker = pause / "loops.lock.reached"
    for _ in range(600):
        if marker.exists():
            return
        if proc.poll() is not None:
            raise AssertionError(proc.communicate())
        time.sleep(0.05)
    raise AssertionError("loops.lock not reached")


def test_concurrent_loops_run_is_refused_with_exit40(tmp_path):
    """A second ``build --loops`` while one runs: exit 40 ``lock_busy``, no build and no loops receipt of its own; the
    first run finishes as if alone."""
    project = B.project(tmp_path)
    pause = tmp_path / "pause"
    pause.mkdir()
    first = start(project, tmp_path, pause)
    try:
        wait_reached(pause, first)
        second = subprocess.run(loops_cmd(project), env=B.env(tmp_path), capture_output=True, timeout=120,
                                cwd=str(project))
        assert second.returncode == 40, second.stderr.decode()
        body = B.summary(second)
        assert (body["exit_code"], body["reason"]) == (40, "lock_busy")
        assert B.builds(project) == []  # the refused run created nothing; the first has not built yet
        (pause / "loops.lock.go").write_text("", encoding="utf-8")
        out, err = first.communicate(timeout=300)
        assert first.returncode == 0, err.decode()
    finally:
        if first.poll() is None:
            first.kill()
    assert len(B.builds(project)) == 1
    loops_dir = B.kb_dir(project) / "loops"
    assert len(list(p for p in loops_dir.iterdir() if p.is_dir())) == 1  # one run, one receipt directory


def test_the_lock_is_released_after_a_run_and_after_a_kill(tmp_path):
    project = B.project(tmp_path)
    done = subprocess.run(loops_cmd(project), env=B.env(tmp_path), capture_output=True, timeout=300, cwd=str(project))
    assert done.returncode == 0, done.stderr.decode()
    pause = tmp_path / "pause"
    pause.mkdir()
    victim = start(project, tmp_path, pause)
    try:
        wait_reached(pause, victim)
    finally:
        victim.kill()
        victim.communicate()
    again = subprocess.run(loops_cmd(project), env=B.env(tmp_path), capture_output=True, timeout=300, cwd=str(project))
    assert again.returncode == 0, again.stderr.decode()
    assert len(B.builds(project)) == 2
