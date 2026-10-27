"""WI-00c3: the replay fake also serves live recordings from ``tests/recordings/<tool>/*.json``.

When ``$KLODE_KIT_REPLAY_DIR/<tool>/<argv_sha>.json`` does not exist, the fake looks for a recording whose
``argv_sha`` equals ``isolation_check.argv_sha([tool, *args])`` (the full argv, program name included - the key
``spike4`` writes) in ``$KLODE_KIT_RECORDINGS_DIR/<tool>/`` (default ``tests/recordings``) and replays its
``stdout`` / ``stderr`` / ``exit_code`` byte for byte. Scenarios keep precedence; no match is still exit 97.
"""

from __future__ import annotations

import json
import subprocess
import tempfile

import pytest

from _support.flows import REPO
from _support.replay import log_entries, write_scenario
from klode_kit.ocr import cloud_argv as CA
from klode_kit.ocr import isolation_check as IC
from klode_kit.ocr import isolation_probe as IP

pytestmark = pytest.mark.contract_replay

PNG = REPO / "tests" / "fixtures" / "fx-rx-stamp" / "images" / "p001.png"


def _claude_args(tmp_path, effort: str) -> list[str]:
    old = tempfile.tempdir
    tempfile.tempdir = str(tmp_path)
    try:
        data = PNG.read_bytes()
        send = CA.prepare_send_dir(data, IP.sha256_bytes(data))
    finally:
        tempfile.tempdir = old
    inv = CA.claude(send, model="opus", effort=effort, max_budget_usd=5.0)
    IP.move_to_trash([send], tmp_path / "trash")
    return list(inv.argv[1:])


def _run(args, env):
    return subprocess.run(["claude", *args], capture_output=True, env=env, timeout=60)


@pytest.fixture
def env(tmp_path, monkeypatch):
    import os

    replay = tmp_path / "empty-replay"
    replay.mkdir()
    monkeypatch.setenv("KLODE_KIT_REPLAY_DIR", str(replay))
    return dict(os.environ)


def test_synthetic_recording_is_replayed(tmp_path, env, fake_log):
    args = ["-p", "hello", "--model", "x"]
    rec_dir = tmp_path / "recordings"
    (rec_dir / "claude").mkdir(parents=True)
    rec = {"recorded": True, "argv_sha": IC.argv_sha(["claude", *args]), "stdout": "{\"ok\": 1}\n",
           "stderr": "warn", "exit_code": 3}
    (rec_dir / "claude" / "any-name.json").write_text(json.dumps(rec), encoding="utf-8")
    env["KLODE_KIT_RECORDINGS_DIR"] = str(rec_dir)
    proc = _run(args, env)
    assert (proc.returncode, proc.stdout, proc.stderr) == (3, b"{\"ok\": 1}\n", b"warn")
    assert [e["argv"] for e in log_entries(fake_log)] == [args]
    # a different argv has no recording: still the no-scenario exit
    assert _run(["-p", "other"], env).returncode == 97


def test_scenario_wins_over_recording(tmp_path, env):
    args = ["-p", "hello"]
    rec_dir = tmp_path / "recordings"
    (rec_dir / "claude").mkdir(parents=True)
    (rec_dir / "claude" / "r.json").write_text(json.dumps(
        {"recorded": True, "argv_sha": IC.argv_sha(["claude", *args]), "stdout": "from-recording", "exit_code": 0}))
    env["KLODE_KIT_RECORDINGS_DIR"] = str(rec_dir)
    from pathlib import Path

    write_scenario(Path(env["KLODE_KIT_REPLAY_DIR"]), "claude", args, "from-scenario")
    assert _run(args, env).stdout == b"from-scenario"


def test_live_claude_recording_replays_byte_for_byte(tmp_path, env):
    """The committed fx-rx-stamp claude recording (opus / max / --max-budget-usd 5) answers the same argv."""
    sha = IP.sha256_bytes(PNG.read_bytes())
    rec = json.loads((REPO / "tests" / "recordings" / "claude" / f"{sha}.json").read_text(encoding="utf-8"))
    args = _claude_args(tmp_path, "max")
    # the recording keeps its argv with the recording machine's repository location as a placeholder (its argv_sha
    # was taken over the real path, so it only holds on that checkout): compare the argv, path mapped to this checkout
    recorded_root = "<home>/Documents/Claude/Projects/klode-kit"
    assert [a.replace(str(REPO), recorded_root, 1) if a.startswith(str(REPO) + "/") else a
            for a in ["claude", *args]] == rec["argv"]
    proc = _run(args, env)
    assert proc.returncode == rec["exit_code"] == 0
    assert proc.stdout.decode("utf-8") == rec["stdout"]
    assert json.loads(proc.stdout)["structured_output"]["lines"]
    assert _run(_claude_args(tmp_path, "high"), env).returncode == 97


def test_recording_with_home_placeholder_matches_on_another_checkout(tmp_path, env):
    """WI-15d: a recording stores the repository location as ``<home>/Documents/Claude/Projects/klode-kit`` while its
    argv_sha was taken over the recording machine's real path; on another checkout (CI) the argv still matches once
    the placeholder is mapped to this checkout, and a different argument or a different tail still gets exit 97."""
    settings = str(REPO / "src" / "klode_kit" / "config" / "cloud_settings.json")
    args = ["-p", "x", "--settings", settings]
    rec_dir = tmp_path / "recordings"
    (rec_dir / "claude").mkdir(parents=True)
    rec = {"recorded": True, "argv_sha": "0" * 64, "stdout": "from-placeholder-recording", "exit_code": 0,
           "argv": ["claude", "-p", "x", "--settings",
                    "<home>/Documents/Claude/Projects/klode-kit/src/klode_kit/config/cloud_settings.json"]}
    (rec_dir / "claude" / "r.json").write_text(json.dumps(rec), encoding="utf-8")
    env["KLODE_KIT_RECORDINGS_DIR"] = str(rec_dir)
    assert _run(args, env).stdout == b"from-placeholder-recording"
    assert _run(["-p", "y", "--settings", settings], env).returncode == 97
    assert _run(["-p", "x", "--settings", settings + "2"], env).returncode == 97
