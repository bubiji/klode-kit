"""WI-00s: ``python -m klode_kit`` subcommand registry and the V1 console-script guard."""

from __future__ import annotations

import importlib.metadata
import re
import subprocess
import sys

import pytest

from _support.flows import REPO, base_env

pytestmark = pytest.mark.unit


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "klode_kit", *args], capture_output=True, text=True, env=base_env(), timeout=60
    )


def test_version_flag_prints_package_version():
    res = run_cli("--version")
    assert res.returncode == 0
    assert res.stdout.strip() == importlib.metadata.version("klode-kit")


def test_unknown_subcommand_is_usage_error():
    res = run_cli("no-such-command")
    assert res.returncode == 2
    assert "no-such-command" in res.stderr


def test_no_arguments_is_usage_error():
    res = run_cli()
    assert res.returncode == 2
    assert "usage" in res.stderr.lower()


def test_dev_privacy_scan_dispatches(tmp_path):
    (tmp_path / "a.txt").write_text("x " + "138" + "12345678" + " y\n")
    res = run_cli("dev", "privacy-scan", "--root", str(tmp_path))
    assert res.returncode != 0
    assert "a.txt" in res.stdout


def test_dev_lane_dispatches(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_one.py").write_text("import pytest\n\n@pytest.mark.unit\ndef test_one():\n    assert True\n")
    res = run_cli("dev", "lane", "unit", "--root", str(tmp_path), "--no-cov")
    assert res.returncode == 0, res.stdout + res.stderr
    assert (tmp_path / "work" / "lanes" / "unit.json").is_file()


CONSOLE_SCRIPT = re.compile(r"\.venv/bin/(?!python(?:3(?:\.\d+)?)?\b)[A-Za-z0-9_.-]+")


def test_no_console_scripts_in_runners():
    """V1: lane runner, CI, config and launcher never call <venv>/bin/<console script>."""
    targets = [
        REPO / ".claude" / "tdd-guardian" / "config.json",
        REPO / ".github" / "workflows" / "ci.yml",
        REPO / "bin" / "klode-kit",
        REPO / "src" / "klode_kit" / "devtools" / "run_lane.py",
    ]
    for path in targets:
        text = path.read_text()
        assert CONSOLE_SCRIPT.findall(text) == [], path
    assert ".venv/bin/python -m pytest" in (REPO / ".claude" / "tdd-guardian" / "config.json").read_text()
