"""WI-00d2 (WI-00d1 leftover, impl-deviations "桥无 G2 闸门"): the execution bridge's real-call gate.

Rule: when the ``claude`` the bridge would start does NOT resolve to the replay fake in this repository's
``tests/fakes/bin``, the bridge refuses to start any process unless ``KLODE_KIT_REAL == "1"`` AND
``KLODE_KIT_GATE_ACK`` is ``G2-<yyyymmdd>`` (a real calendar date). Refusal = ``BridgeGateClosed`` in process,
exit 61 from ``python -m klode_kit.bridge run``; zero calls.

"Zero calls" is observed, not inferred: a pseudo-real ``claude`` (a shell script outside tests/fakes/bin that
appends one line per start to its own log) is put first on PATH. The positive control shows that the very same
harness does record a call once the gate is open, so an empty log means "never started".

Law (gate): for every environment that is not (REAL=1 and a valid G2 ack), a non-fake claude is started zero
times (S4, hypothesis over ack strings); the fake is never gated (tests stay replayable without an ack).
"""

from __future__ import annotations

import itertools
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from _support.flows import REPO, base_env
from _support.replay import log_entries
from klode_kit import bridge
from klode_kit.config import load

pytestmark = pytest.mark.unit

FAKES_BIN = REPO / "tests" / "fakes" / "bin"
VALID_ACK = "G2-20261023"


def _pseudo_real(dirpath: Path, log: Path) -> Path:
    """A 'real-looking' claude outside tests/fakes/bin that records every start."""
    dirpath.mkdir(parents=True, exist_ok=True)
    exe = dirpath / "claude"
    exe.write_text(f'#!/bin/sh\necho "start $$" >> "{log}"\necho "{{}}"\nexit 0\n', encoding="utf-8")
    exe.chmod(0o755)
    return exe


def _calls(log: Path) -> list[str]:
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def _tasks_file(root: Path) -> tuple[Path, Path]:
    build = root / "builds" / "b-20261023-0001"
    build.mkdir(parents=True, exist_ok=True)
    tasks = root / "tasks.json"
    tasks.write_text(json.dumps({"tasks": [{"task_id": "t-001", "build_dir": str(build),
                                            "card": "cards_content/c001.md", "instructions": "合成章节 1"}]}),
                     encoding="utf-8")
    return tasks, build


def _cli(root: Path, path_dirs: list[Path], env_extra: dict[str, str], drop: tuple[str, ...] = ()):
    tasks, build = _tasks_file(root)
    receipt = root / "receipt.json"
    env = base_env(env_extra, drop=("KLODE_KIT_REAL", "KLODE_KIT_GATE_ACK", *drop))
    env.update(env_extra)
    env["PATH"] = os.pathsep.join([*map(str, path_dirs), "/usr/bin", "/bin"])
    proc = subprocess.run([sys.executable, "-m", "klode_kit.bridge", "run", "--tasks", str(tasks),
                           "--receipt", str(receipt), "--max-rounds", "1", "--timeout-s", "30"],
                          env=env, capture_output=True, timeout=120)
    return proc, receipt, build


CLOSED_ENVS = [
    pytest.param({}, id="nothing-set"),
    pytest.param({"KLODE_KIT_REAL": "0", "KLODE_KIT_GATE_ACK": VALID_ACK}, id="real-0-valid-ack"),
    pytest.param({"KLODE_KIT_REAL": "true", "KLODE_KIT_GATE_ACK": VALID_ACK}, id="real-true-not-1"),
    pytest.param({"KLODE_KIT_REAL": "1"}, id="real-1-no-ack"),
    pytest.param({"KLODE_KIT_REAL": "1", "KLODE_KIT_GATE_ACK": ""}, id="real-1-empty-ack"),
    pytest.param({"KLODE_KIT_REAL": "1", "KLODE_KIT_GATE_ACK": "G2-2026102"}, id="ack-7-digits"),
    pytest.param({"KLODE_KIT_REAL": "1", "KLODE_KIT_GATE_ACK": "G2-202610231"}, id="ack-9-digits"),
    pytest.param({"KLODE_KIT_REAL": "1", "KLODE_KIT_GATE_ACK": "G3-20261023"}, id="ack-other-gate"),
    pytest.param({"KLODE_KIT_REAL": "1", "KLODE_KIT_GATE_ACK": "g2-20261023"}, id="ack-lowercase"),
    pytest.param({"KLODE_KIT_REAL": "1", "KLODE_KIT_GATE_ACK": " G2-20261023"}, id="ack-leading-space"),
    pytest.param({"KLODE_KIT_REAL": "1", "KLODE_KIT_GATE_ACK": "G2-20261023\n"}, id="ack-trailing-newline"),
    pytest.param({"KLODE_KIT_REAL": "1", "KLODE_KIT_GATE_ACK": "G2-20261332"}, id="ack-not-a-date"),
]


@pytest.mark.parametrize("env_extra", CLOSED_ENVS)
def test_cli_refuses_non_fake_claude_exit_61_zero_calls(tmp_path, env_extra):
    """S3: gate closed + pseudo-real claude first on PATH -> exit 61, the pseudo-real log stays empty."""
    log = tmp_path / "real-calls.log"
    _pseudo_real(tmp_path / "realbin", log)
    proc, receipt, build = _cli(tmp_path, [tmp_path / "realbin"], env_extra)
    assert proc.returncode == bridge.EXIT_GATE == 61, proc.stderr.decode()
    assert _calls(log) == []
    assert b"G2" in proc.stderr
    assert not (build / "cards_content" / "c001.md").exists()


def test_cli_gate_open_positive_control_calls_pseudo_real(tmp_path):
    """Control: with REAL=1 and a valid ack the same harness records exactly one start (log is a real detector)."""
    log = tmp_path / "real-calls.log"
    _pseudo_real(tmp_path / "realbin", log)
    proc, receipt, _ = _cli(tmp_path, [tmp_path / "realbin"], {"KLODE_KIT_REAL": "1", "KLODE_KIT_GATE_ACK": VALID_ACK})
    assert proc.returncode != bridge.EXIT_GATE, proc.stderr.decode()
    assert len(_calls(log)) == 1
    assert json.loads(receipt.read_text(encoding="utf-8"))["task_count"] == 1


def test_cli_fake_claude_is_never_gated(tmp_path, fake_log):
    """S2: the repository fake first on PATH runs with no REAL and no ack (replay lanes need no ack)."""
    proc, receipt, _ = _cli(tmp_path, [FAKES_BIN], {"KLODE_KIT_FAKE_LOG": str(fake_log)})
    assert proc.returncode != bridge.EXIT_GATE, proc.stderr.decode()
    assert len(log_entries(fake_log)) == 1


def test_run_refuses_absolute_non_fake_path(tmp_path, monkeypatch):
    """S3: Options.claude given as an absolute path outside tests/fakes/bin is gated too (no PATH lookup bypass)."""
    log = tmp_path / "real-calls.log"
    exe = _pseudo_real(tmp_path / "realbin", log)
    monkeypatch.setenv("KLODE_KIT_REAL", "0")
    monkeypatch.delenv("KLODE_KIT_GATE_ACK", raising=False)
    tasks, build = _tasks_file(tmp_path)
    task = bridge.Task(task_id="t-001", build_dir=build, card="cards_content/c001.md", instructions="x")
    receipt = tmp_path / "r.json"
    with pytest.raises(bridge.BridgeGateClosed):
        bridge.run([task], bridge.Options.from_config(load(), claude=str(exe), max_rounds=1), receipt_path=receipt)
    assert _calls(log) == []
    assert not receipt.exists()


def test_run_refuses_lookalike_fakes_dir(tmp_path, monkeypatch):
    """S3: a directory merely *named* tests/fakes/bin elsewhere is not the repository fake."""
    log = tmp_path / "real-calls.log"
    lookalike = tmp_path / "tests" / "fakes" / "bin"
    _pseudo_real(lookalike, log)
    monkeypatch.setenv("PATH", os.pathsep.join([str(lookalike), "/usr/bin", "/bin"]))
    monkeypatch.setenv("KLODE_KIT_REAL", "0")
    tasks, build = _tasks_file(tmp_path)
    task = bridge.Task(task_id="t-001", build_dir=build, card="cards_content/c001.md", instructions="x")
    with pytest.raises(bridge.BridgeGateClosed):
        bridge.run([task], bridge.Options.from_config(load(), max_rounds=1))
    assert _calls(log) == []


ACK_OK = re.compile(r"G2-[0-9]{8}")  # ASCII digits only
_CASES = itertools.count()


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(ack=st.one_of(st.text(st.characters(blacklist_characters="\x00", blacklist_categories=("Cs",)), max_size=14), st.from_regex(r"\AG2-[0-9]{6,10}\Z"), st.just("G2-20260230")),
       real=st.sampled_from(["1", "0", "", "yes"]))
def test_gate_property_zero_calls_unless_real_and_valid_ack(tmp_path, ack, real):
    """S4 law: the pseudo-real claude starts iff REAL == "1" and ack is G2-<valid yyyymmdd>; otherwise 0 starts."""
    import datetime as _dt

    root = tmp_path / f"case-{next(_CASES)}"
    log = root / "real-calls.log"
    exe = _pseudo_real(root / "realbin", log)
    _, build = _tasks_file(root)
    task = bridge.Task(task_id="t-001", build_dir=build, card="cards_content/c001.md", instructions="x")
    expected_open = real == "1" and bool(ACK_OK.fullmatch(ack))
    if expected_open:
        try:
            _dt.datetime.strptime(ack[3:], "%Y%m%d")
        except ValueError:
            expected_open = False
    with mock.patch.dict(os.environ, {"KLODE_KIT_REAL": real, "KLODE_KIT_GATE_ACK": ack}):
        options = bridge.Options.from_config(load(), claude=str(exe), max_rounds=1, timeout_s=30)
        if expected_open:
            bridge.run([task], options)
            assert len(_calls(log)) == 1
        else:
            with pytest.raises(bridge.BridgeGateClosed):
                bridge.run([task], options)
            assert _calls(log) == []
