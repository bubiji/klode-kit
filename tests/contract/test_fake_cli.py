"""WI-00s: replay fakes tests/fakes/bin/{claude,codex} (matrix 00s-05, 00s-06).

Called as real subprocesses resolved through PATH (conftest puts the fakes first).
"""

from __future__ import annotations

import subprocess

import pytest

from _support.replay import argv_sha, log_entries, write_scenario

pytestmark = pytest.mark.contract_replay


def call(tool: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([tool, *args], capture_output=True, timeout=60)


@pytest.mark.parametrize("tool", ["claude", "codex"])
def test_fake_claude_unconfigured_errors(tool, tmp_path, monkeypatch, fake_log):
    """00s-05: no scenario configured -> exit != 0, stderr names it, stdout empty."""
    monkeypatch.setenv("KLODE_KIT_REPLAY_DIR", str(tmp_path / "empty-replay"))

    res = call(tool, "-p", "x")

    assert res.returncode != 0
    assert b"no replay scenario" in res.stderr
    assert res.stdout == b""
    entries = log_entries(fake_log)
    assert [(e["tool"], e["argv"], e["argv_sha"]) for e in entries] == [(tool, ["-p", "x"], argv_sha(["-p", "x"]))]


@pytest.mark.parametrize("tool", ["claude", "codex"])
def test_fake_claude_replays_configured_scenario(tool, tmp_path, monkeypatch, fake_log):
    """00s-06: stdout equals the recording byte for byte; the call log gains one entry carrying the argv sha."""
    replay = tmp_path / "replay"
    args = ["-p", "读图：合成样本", "--output-format", "json"]
    recorded = '{"result": "测试患者甲", "cost_usd": 0.01}\n\ttrailing  '
    write_scenario(replay, tool, args, recorded, stderr="warn-line\n", exit_code=0)
    monkeypatch.setenv("KLODE_KIT_REPLAY_DIR", str(replay))
    before = len(log_entries(fake_log))

    res = call(tool, *args)

    assert res.returncode == 0, res.stderr
    assert res.stdout == recorded.encode("utf-8")
    assert res.stderr == b"warn-line\n"
    after = log_entries(fake_log)
    assert len(after) == before + 1
    assert after[-1]["argv_sha"] == argv_sha(args)
    assert after[-1]["argv"] == args
    assert after[-1]["tool"] == tool


def test_fake_replays_recorded_exit_code(tmp_path, monkeypatch):
    replay = tmp_path / "replay"
    write_scenario(replay, "claude", ["--bad"], "", stderr="boom\n", exit_code=3)
    monkeypatch.setenv("KLODE_KIT_REPLAY_DIR", str(replay))

    res = call("claude", "--bad")

    assert res.returncode == 3
    assert res.stderr == b"boom\n"


def test_fake_different_argv_is_a_different_scenario(tmp_path, monkeypatch):
    replay = tmp_path / "replay"
    write_scenario(replay, "claude", ["-p", "a"], "A")
    monkeypatch.setenv("KLODE_KIT_REPLAY_DIR", str(replay))

    hit = call("claude", "-p", "a")
    miss = call("claude", "-p", "a ")

    assert (hit.returncode, hit.stdout) == (0, b"A")
    assert miss.returncode != 0 and miss.stdout == b""
