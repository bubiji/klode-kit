"""WI-00s: lane guards (matrix 00s-04, 00s-07) and the guards' own plugin behaviour.

Sub-sessions load ``klode_kit.devtools.pytest_plugin`` explicitly via ``-p`` so that the
guard, not an outer command-line flag, is what is being tested.
"""

from __future__ import annotations

import os
import shutil
import socket
from pathlib import Path

import pytest
import pytest_socket

pytestmark = pytest.mark.unit

PLUGIN = ("-p", "klode_kit.devtools.pytest_plugin")


def test_socket_blocked():
    """00s-04: any socket connection inside a unit-lane test raises SocketBlockedError."""
    with pytest.raises(pytest_socket.SocketBlockedError):
        conn = socket.create_connection(("127.0.0.1", 9), timeout=1)
        conn.close()


def test_real_forced_off_in_offline_lanes():
    assert os.environ.get("KLODE_KIT_REAL") == "0"


def test_fakes_shadow_real_clis():
    """An unconfigured claude/codex call must reach the replay fake, never a real CLI."""
    for tool in ("claude", "codex"):
        found = shutil.which(tool)
        assert found is not None
        assert Path(found).parent.name == "bin" and Path(found).parent.parent.name == "fakes", found


def _gate_session(pytester, marker_file: Path):
    pytester.makepyfile(
        test_gc=f"""
import pathlib
import pytest

@pytest.mark.model_integration
@pytest.mark.gate_cloud
def test_cloud():
    pathlib.Path({str(marker_file)!r}).write_text("ran")

@pytest.mark.unit
def test_other():
    assert True
"""
    )


def test_gate_cloud_without_ack_usage_error(pytester, monkeypatch):
    """00s-07: gate_cloud selected without KLODE_KIT_GATE_ACK -> session exit 2, 0 tests executed (not skip)."""
    monkeypatch.delenv("KLODE_KIT_GATE_ACK", raising=False)
    ran = pytester.path / "ran.txt"
    _gate_session(pytester, ran)

    res = pytester.runpytest_subprocess(*PLUGIN, "-m", "model_integration")

    assert res.ret == 2
    assert not ran.exists()
    out = res.stdout.str()
    assert "skipped" not in out
    assert "passed" not in out
    assert "KLODE_KIT_GATE_ACK" in out + res.stderr.str()


def test_gate_cloud_malformed_ack_usage_error(pytester, monkeypatch):
    monkeypatch.setenv("KLODE_KIT_GATE_ACK", "yes")
    ran = pytester.path / "ran.txt"
    _gate_session(pytester, ran)

    res = pytester.runpytest_subprocess(*PLUGIN, "-m", "model_integration")

    assert res.ret == 2
    assert not ran.exists()


def test_gate_cloud_with_ack_runs(pytester, monkeypatch):
    monkeypatch.setenv("KLODE_KIT_GATE_ACK", "G2-20261022")
    ran = pytester.path / "ran.txt"
    _gate_session(pytester, ran)

    res = pytester.runpytest_subprocess(*PLUGIN, "-m", "model_integration")

    assert res.ret == 0
    assert ran.read_text() == "ran"
    res.assert_outcomes(passed=1, deselected=1)


def test_gate_cloud_deselected_is_not_an_error(pytester, monkeypatch):
    monkeypatch.delenv("KLODE_KIT_GATE_ACK", raising=False)
    ran = pytester.path / "ran.txt"
    _gate_session(pytester, ran)

    res = pytester.runpytest_subprocess(*PLUGIN, "-m", "unit")

    assert res.ret == 0
    assert not ran.exists()
    res.assert_outcomes(passed=1, deselected=1)


def test_plugin_blocks_socket_without_cli_flag(pytester, monkeypatch):
    """The guard comes from the plugin itself, not from --disable-socket on the command line."""
    monkeypatch.setenv("KLODE_KIT_REAL", "1")
    pytester.makepyfile(
        test_net="""
import os, socket
import pytest
import pytest_socket

@pytest.mark.contract_replay
def test_blocked():
    with pytest.raises(pytest_socket.SocketBlockedError):
        socket.create_connection(("127.0.0.1", 9), timeout=1)

@pytest.mark.unit
def test_real_off():
    assert os.environ["KLODE_KIT_REAL"] == "0"
"""
    )

    res = pytester.runpytest_subprocess(*PLUGIN)

    res.assert_outcomes(passed=2)


def test_test_without_lane_marker_is_rejected(pytester):
    """A test that belongs to no lane would silently never run in CI; the session refuses it."""
    ran = pytester.path / "ran.txt"
    pytester.makepyfile(
        test_nolane=f"""
import pathlib

def test_orphan():
    pathlib.Path({str(ran)!r}).write_text("ran")
"""
    )

    res = pytester.runpytest_subprocess(*PLUGIN)

    assert res.ret != 0
    assert not ran.exists()
    assert "test_orphan" in res.stdout.str() + res.stderr.str()
    assert "lane marker" in res.stdout.str() + res.stderr.str()


def test_directory_assigns_lane(pytester):
    """tests/unit -> unit, tests/contract -> contract_replay, tests/cli -> cli_integration."""
    pytester.mkdir("tests")
    for sub in ("unit", "contract", "replay", "cli"):
        pytester.mkdir(f"tests/{sub}")
        (pytester.path / "tests" / sub / f"test_{sub}_dir.py").write_text("def test_x():\n    assert True\n")

    unit = pytester.runpytest_subprocess(*PLUGIN, "-m", "unit", "--rootdir", str(pytester.path))
    contract = pytester.runpytest_subprocess(*PLUGIN, "-m", "contract_replay", "--rootdir", str(pytester.path))
    cli = pytester.runpytest_subprocess(*PLUGIN, "-m", "cli_integration", "--rootdir", str(pytester.path))

    unit.assert_outcomes(passed=1, deselected=3)
    contract.assert_outcomes(passed=2, deselected=2)
    cli.assert_outcomes(passed=1, deselected=3)
