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


def _stamp_events(path):
    import json

    return [json.loads(x) for x in path.read_text().splitlines()]


def test_wait_starts_barrier_holds_every_call_until_k_have_started(tmp_path, monkeypatch):
    """WI-15d: with ``wait_starts: 3`` no call ends before the third has written its start stamp, however late
    that process came up (calls are started 0.5 s apart)."""
    import json
    import time

    stamps = tmp_path / "stamps.jsonl"
    scen = tmp_path / "scen.json"
    scen.write_text(json.dumps({"steps": [{"stamps": str(stamps), "wait_starts": 3, "stdout": "ok"}]}))
    monkeypatch.setenv("KLODE_KIT_FAKE_SCENARIO", str(scen))
    procs = []
    for _ in range(3):
        procs.append(subprocess.Popen(["claude", "-p", "x"], stdout=subprocess.PIPE))
        time.sleep(0.5)
    outs = [p.communicate(timeout=60)[0] for p in procs]

    assert outs == [b"ok"] * 3 and [p.returncode for p in procs] == [0, 0, 0]
    events = _stamp_events(stamps)
    last_start = max(e["t"] for e in events if e["event"] == "start")
    assert sorted(e["event"] for e in events) == ["end"] * 3 + ["start"] * 3
    assert all(e["t"] >= last_start for e in events if e["event"] == "end")


def test_wait_starts_gives_up_after_its_timeout(tmp_path, monkeypatch):
    """WI-15d: when the K-th start never comes, the call goes on after ``wait_starts_timeout_s`` (not at once, not
    never) and still ends normally."""
    import json
    import time

    stamps = tmp_path / "stamps.jsonl"
    scen = tmp_path / "scen.json"
    scen.write_text(json.dumps({"steps": [{"stamps": str(stamps), "wait_starts": 2, "wait_starts_timeout_s": 0.6,
                                           "stdout": "done"}]}))
    monkeypatch.setenv("KLODE_KIT_FAKE_SCENARIO", str(scen))
    t0 = time.monotonic()

    res = call("claude", "-p", "x")

    elapsed = time.monotonic() - t0
    assert (res.returncode, res.stdout) == (0, b"done")
    events = _stamp_events(stamps)
    assert [e["event"] for e in events] == ["start", "end"]
    assert events[1]["t"] - events[0]["t"] >= 0.55
    assert elapsed < 20
