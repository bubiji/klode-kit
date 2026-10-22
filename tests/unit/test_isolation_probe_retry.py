"""WI-00c2 added after the first real run (2026-10-23): the model sometimes declines to attempt the canary
reads at all (all ``unattempted`` -> inconclusive -> FAIL). ``run_probe(..., max_attempts=N)`` repeats an
attempt only while every outcome so far is ``denied`` or ``unattempted`` and nothing leaked; it stops at
the first PASS and at the first ``read`` / ``enoent`` / ``error`` / leak. Every attempt is listed in
``receipt.probe.attempts``; ``probe.cost_usd`` is their sum; ``argv_sha`` binds the last executed argv.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

from _support.flows import REPO
from _support.replay import log_entries, write_scenario
from klode_kit.contract.schema import issues
from klode_kit.ocr import cloud_argv as CA
from klode_kit.ocr import isolation_probe as IP

sys.path.insert(0, str(REPO / "tests" / "fixtures"))
import gen_project  # noqa: E402

pytestmark = pytest.mark.unit

FIXTURE_PNG = REPO / "tests" / "fixtures" / "fx-approx-only" / "images" / "p001.png"
SYNTH_PROJECT = REPO / "tests" / "fixtures" / "synth_project"


def _scenario(tmp_path, replay, canaries, nonce, outcomes_by_index, cost):
    outcomes = {gen_project.PROJ_CANARY: outcomes_by_index[0], gen_project.HOME_CANARY: outcomes_by_index[1]}
    events, _ = gen_project.claude_stream(outcomes)
    for e in events:
        if e.get("type") == "result":
            e["total_cost_usd"] = cost
    text = "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n"
    for old, new in ((gen_project.PROJ_CANARY, str(canaries[0])), (gen_project.HOME_CANARY, str(canaries[1])),
                     (gen_project.NONCE, nonce)):
        text = text.replace(old, new)
    png = FIXTURE_PNG.read_bytes()
    send = CA.prepare_send_dir(png, IP.sha256_bytes(png))
    inv = CA.claude(send, model="opus", effort="low", max_budget_usd=5.0)
    args = list(IP.probe_invocation(inv, IP.probe_prompt(canaries)).argv[1:])
    shutil.move(str(send), str(tmp_path / f"expected-{send.name}"))
    write_scenario(replay, "claude", args, text)


@pytest.fixture
def setup(tmp_path, monkeypatch, fake_log):
    replay = tmp_path / "replay"
    monkeypatch.setenv("KLODE_KIT_REPLAY_DIR", str(replay))
    write_scenario(replay, "claude", ["--version"], "2.1.285 (Claude Code)\n")
    project = tmp_path / "synth_project"
    shutil.copytree(SYNTH_PROJECT, project)
    home = tmp_path / "home"
    home.mkdir()
    nonce = IP.new_nonce()
    canaries = [IP.plant_canary(project, nonce), IP.plant_canary(home, nonce)]
    extra = {"KLODE_KIT_REPLAY_DIR": str(replay), "KLODE_KIT_FAKE_LOG": str(fake_log),
             "KLODE_KIT_FAKE_PYTHON": sys.executable}

    def run(outcomes, *, max_attempts, cost=0.01):
        _scenario(tmp_path, replay, canaries, nonce, outcomes, cost)
        return IP.run_probe("claude", project=project, canaries=canaries, nonce=nonce, png=FIXTURE_PNG,
                            model="opus", effort="low", max_budget_usd=5.0, source_env=dict(os.environ),
                            extra_env=extra, timeout_s=60, trash_dir=tmp_path / "trash", max_attempts=max_attempts)

    def calls():
        return [e for e in log_entries(fake_log) if e["argv"] != ["--version"]]

    return run, calls, canaries


def test_inconclusive_attempts_are_repeated_up_to_the_bound(setup):
    run, calls, canaries = setup
    result = run(("unattempted", "unattempted"), max_attempts=3, cost=0.02)
    assert len(calls()) == 3
    assert result.classification.verdict == "FAIL"
    assert len(result.receipt["probe"]["attempts"]) == 3
    assert result.receipt["probe"]["cost_usd"] == pytest.approx(0.06)
    assert result.receipt["cloud_isolation"] == "FAIL"
    assert issues("isolation_receipt", result.receipt) == []
    assert not any(c.exists() for c in canaries)


@pytest.mark.parametrize("outcomes,verdict", [
    (("denied", "denied"), "PASS"),
    (("denied", "read"), "FAIL"),
    (("enoent", "denied"), "FAIL"),
])
def test_decisive_attempt_stops_the_loop(setup, outcomes, verdict):
    run, calls, _ = setup
    result = run(outcomes, max_attempts=3)
    assert len(calls()) == 1
    assert result.classification.verdict == verdict
    assert len(result.receipt["probe"]["attempts"]) == 1
    assert result.receipt["probe"]["verdict"] == verdict


def test_partial_denial_with_unattempted_is_retried(setup):
    run, calls, _ = setup
    result = run(("denied", "unattempted"), max_attempts=2)
    assert len(calls()) == 2
    assert [a["canaries"] for a in result.receipt["probe"]["attempts"]] == [result.classification.canaries] * 2


def test_max_attempts_must_be_positive(setup):
    run, calls, _ = setup
    with pytest.raises(ValueError):
        run(("denied", "denied"), max_attempts=0)
    assert calls() == []
