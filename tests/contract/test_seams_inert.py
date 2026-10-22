"""WI-00s: test seams are inert unless in test mode (matrix 00s-14, F9).

Synthetic stand-in: ``klode_kit.devtools.synthflow seams`` writes an artifact, passes
``send.after_source_check`` (pause point), calls the fake ``claude``, passes
``writeback.synth_commit`` (crash point), then finishes the artifact. The real
send / writeback parameters are strict xfails until WI-05b / WI-08c provide them.

Positive controls prove the seams do fire in test mode, so "inert" cannot be satisfied
by a crashpoint() that does nothing.
"""

from __future__ import annotations

import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from _support.flows import REAL_ENTRIES, base_env, run_synthflow
from _support.replay import write_scenario

pytestmark = pytest.mark.contract_replay

PAUSE = "send.after_source_check"
CRASH = "writeback.synth_commit"
SEAM_NAMES = ("KLODE_KIT_CRASH_AT", "KLODE_KIT_PAUSE_AT", "KLODE_KIT_PAUSE_DIR")
TIMEOUT = 30


def _replay(tmp_path: Path) -> str:
    replay = tmp_path / "replay"
    write_scenario(replay, "claude", ["-p", "synth"], "claude-says-hi\n")
    return str(replay)


def _mode_env(tmp_path: Path, mode: str, seams: dict[str, str]) -> dict[str, str]:
    env = base_env({"KLODE_KIT_REPLAY_DIR": _replay(tmp_path), **seams})
    if mode == "seams_unset":
        env.pop("KLODE_KIT_TEST_SEAMS", None)
        env.pop("KLODE_KIT_REAL", None)
    elif mode == "real_on":
        env["KLODE_KIT_TEST_SEAMS"] = "1"
        env["KLODE_KIT_REAL"] = "1"
    elif mode == "active":
        env["KLODE_KIT_TEST_SEAMS"] = "1"
        env.pop("KLODE_KIT_REAL", None)
    else:  # pragma: no cover - test bug
        raise AssertionError(mode)
    return env


def _baseline(tmp_path: Path) -> bytes:
    out = tmp_path / "baseline.txt"
    env = _mode_env(tmp_path, "seams_unset", {})
    res = run_synthflow("seams", tmp_path / "root", env, "--out", str(out), "--spawn-claude", timeout=TIMEOUT)
    assert res.returncode == 0, res.stderr.decode()
    assert res.stderr == b""
    data = out.read_bytes()
    assert b"claude-says-hi" in data
    return data


@pytest.mark.parametrize("mode", ["seams_unset", "real_on"])
def test_seams_inert_unless_test_mode(tmp_path, mode):
    """00s-14 (synthetic): run completes, no .reached, stderr names the ignored vars, bytes identical."""
    baseline = _baseline(tmp_path)
    pause_dir = tmp_path / "pause"
    pause_dir.mkdir()
    env = _mode_env(
        tmp_path,
        mode,
        {"KLODE_KIT_PAUSE_AT": PAUSE, "KLODE_KIT_CRASH_AT": CRASH, "KLODE_KIT_PAUSE_DIR": str(pause_dir)},
    )
    out = tmp_path / "out.txt"

    res = run_synthflow("seams", tmp_path / "root", env, "--out", str(out), "--spawn-claude", timeout=TIMEOUT)

    assert res.returncode == 0, res.stderr.decode()
    assert res.returncode != -signal.SIGKILL
    assert list(pause_dir.iterdir()) == []
    err = res.stderr.decode("utf-8")
    assert "测试缝变量已忽略" in err
    for name in SEAM_NAMES:
        assert name in err
    assert out.read_bytes() == baseline


def test_seams_active_pause_blocks_until_go(tmp_path):
    """Positive control: in test mode PAUSE_AT writes <point>.reached, blocks, consumes <point>.go."""
    baseline = _baseline(tmp_path)
    pause_dir = tmp_path / "pause"
    pause_dir.mkdir()
    out = tmp_path / "out.txt"
    env = _mode_env(tmp_path, "active", {"KLODE_KIT_PAUSE_AT": PAUSE, "KLODE_KIT_PAUSE_DIR": str(pause_dir)})
    proc = subprocess.Popen(
        [sys.executable, "-m", "klode_kit.devtools.synthflow", "seams", "--root", str(tmp_path / "root"),
         "--out", str(out), "--spawn-claude"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        reached = pause_dir / f"{PAUSE}.reached"
        deadline = time.monotonic() + 15
        while not reached.exists():
            assert time.monotonic() < deadline, "reached file never appeared"
            assert proc.poll() is None, proc.communicate()
            time.sleep(0.02)
        time.sleep(0.3)
        assert proc.poll() is None, "process did not block at the pause point"
        assert b"claude-says-hi" not in out.read_bytes()
        (pause_dir / f"{PAUSE}.go").write_text("")
        _, err = proc.communicate(timeout=TIMEOUT)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode == 0, err.decode()
    assert not (pause_dir / f"{PAUSE}.go").exists()
    assert out.read_bytes() == baseline


def test_seams_active_crash_kills_at_point(tmp_path):
    """Positive control: in test mode CRASH_AT SIGKILLs the process at that point."""
    baseline = _baseline(tmp_path)
    out = tmp_path / "out.txt"
    env = _mode_env(tmp_path, "active", {"KLODE_KIT_CRASH_AT": CRASH})

    res = run_synthflow("seams", tmp_path / "root", env, "--out", str(out), "--spawn-claude", timeout=TIMEOUT)

    assert res.returncode == -signal.SIGKILL
    partial = out.read_bytes()
    assert b"claude-says-hi" in partial
    assert partial != baseline and baseline.startswith(partial)


def test_seams_active_unreached_crash_point_is_harmless(tmp_path):
    baseline = _baseline(tmp_path)
    out = tmp_path / "out.txt"
    env = _mode_env(tmp_path, "active", {"KLODE_KIT_CRASH_AT": "writeback.never_reached"})
    res = run_synthflow("seams", tmp_path / "root", env, "--out", str(out), "--spawn-claude", timeout=TIMEOUT)
    assert res.returncode == 0
    assert res.stderr == b""
    assert out.read_bytes() == baseline


@pytest.mark.xfail(strict=True, reason="依赖 WI-05b / WI-08c（真实 send、writeback 尚未实现）")
@pytest.mark.parametrize("mode", ["seams_unset", "real_on"])
@pytest.mark.parametrize("entry_name", ["send", "writeback"])
def test_seams_inert_unless_test_mode_real(tmp_path, mode, entry_name):
    """00s-14 on the real send (fake claude) and writeback (fx-u8) entry points."""
    entry = REAL_ENTRIES[entry_name]
    assert entry.runner is not None, f"{entry.name} runner not provided yet ({entry.depends_on})"
    crash = entry.crash_point or "writeback.unset"
    base_res, base_art = entry.runner(tmp_path / "base", _mode_env(tmp_path, "seams_unset", {}), TIMEOUT)
    assert base_res.returncode == 0
    pause_dir = tmp_path / "pause"
    pause_dir.mkdir()
    env = _mode_env(tmp_path, mode, {"KLODE_KIT_PAUSE_AT": PAUSE, "KLODE_KIT_CRASH_AT": crash, "KLODE_KIT_PAUSE_DIR": str(pause_dir)})
    res, art = entry.runner(tmp_path / "run", env, TIMEOUT)
    assert res.returncode == 0
    assert list(pause_dir.iterdir()) == []
    err = res.stderr.decode("utf-8")
    assert "测试缝变量已忽略" in err and all(n in err for n in SEAM_NAMES)
    assert art == base_art
