"""WI-00s: PAUSE_AT without PAUSE_DIR is refused immediately (matrix 00s-15, F9).

The synthetic flow (``klode_kit.devtools.synthflow seams``) stands in for ``send``: it would
spawn the fake ``claude`` after the pause point, so a zero-entry call log is meaningful.
The real ``send`` parameter is a strict xfail until WI-05b provides it.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import pytest

from _support.flows import REAL_ENTRIES, base_env, run_synthflow
from _support.replay import log_entries, write_scenario

pytestmark = pytest.mark.unit

POINT = "send.after_source_check"
WAIT_SUFFIXES = (".reached", ".go", ".wait")


def _global_dirs(cwd: Path) -> list[Path]:
    dirs = {Path("/tmp").resolve(), Path(tempfile.gettempdir()).resolve(), cwd.resolve()}
    return sorted(d for d in dirs if d.is_dir())


def _waitish(dirs: list[Path]) -> set[str]:
    found = set()
    for d in dirs:
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for name in names:
            if name.endswith(WAIT_SUFFIXES) or POINT in name:
                found.add(str(d / name))
    return found


def _seam_env(tmp_path: Path) -> dict[str, str]:
    replay = tmp_path / "replay"
    write_scenario(replay, "claude", ["-p", "synth"], "ok\n")
    env = base_env({"KLODE_KIT_TEST_SEAMS": "1", "KLODE_KIT_PAUSE_AT": POINT, "KLODE_KIT_REPLAY_DIR": str(replay)})
    env.pop("KLODE_KIT_REAL", None)
    return env


def test_pause_at_requires_pause_dir(tmp_path, fake_log):
    """00s-15 (synthetic send stand-in): exit at once, name the missing variable, no global wait files, 0 fake calls."""
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    out = tmp_path / "artifact.txt"
    env = _seam_env(tmp_path)
    before = _waitish(_global_dirs(cwd))

    start = time.monotonic()
    res = run_synthflow("seams", tmp_path / "root", env, "--out", str(out), "--spawn-claude", cwd=cwd, timeout=30)
    elapsed = time.monotonic() - start

    assert elapsed < 15
    assert res.returncode == 2, res.stderr.decode()
    assert b"KLODE_KIT_PAUSE_DIR" in res.stderr
    assert _waitish(_global_dirs(cwd)) - before == set()
    assert list(cwd.iterdir()) == []
    assert log_entries(fake_log) == []
    assert not out.exists()


def test_pause_dir_must_exist(tmp_path, fake_log):
    env = _seam_env(tmp_path)
    env["KLODE_KIT_PAUSE_DIR"] = str(tmp_path / "does-not-exist")
    res = run_synthflow("seams", tmp_path / "root", env, "--out", str(tmp_path / "a.txt"), "--spawn-claude", timeout=30)
    assert res.returncode == 2
    assert b"KLODE_KIT_PAUSE_DIR" in res.stderr
    assert not (tmp_path / "does-not-exist").exists()
    assert log_entries(fake_log) == []


def test_pause_at_requires_pause_dir_real_send(tmp_path, fake_log):
    """00s-15 on the real ``send`` entry point."""
    entry = REAL_ENTRIES["send"]
    assert entry.runner is not None, f"{entry.name} runner not provided yet ({entry.depends_on})"
    env = _seam_env(tmp_path)
    before = _waitish(_global_dirs(tmp_path))
    start = time.monotonic()
    res, _ = entry.runner(tmp_path, env, 30)
    assert time.monotonic() - start < 15
    assert res.returncode == 2
    assert b"KLODE_KIT_PAUSE_DIR" in res.stderr
    assert _waitish(_global_dirs(tmp_path)) - before == set()
    assert log_entries(fake_log) == []
