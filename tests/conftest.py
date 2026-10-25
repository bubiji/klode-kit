"""Repository-wide pytest configuration.

Lane guards (lane markers, socket blocking, KLODE_KIT_REAL=0, gate_cloud ACK) live in
``klode_kit.devtools.pytest_plugin`` (loaded via ``-p`` in pyproject addopts) so that
pytester sub-sessions can load exactly the same guards.

This file only wires the repository-specific parts:
- ``tests/fakes/bin`` goes first on PATH, so an unconfigured ``claude``/``codex`` call
  hits the replay fake and errors instead of reaching a real CLI;
- ``KLODE_KIT_TEST_SEAMS=1`` is set here and nowhere else (matrix section 0.3, F9);
- every test gets its own fake-CLI call log.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

pytest_plugins = ["pytester", "klode_kit.devtools.pytest_plugin"]

TESTS_DIR = Path(__file__).resolve().parent
FAKES_BIN = TESTS_DIR / "fakes" / "bin"

os.environ["PATH"] = str(FAKES_BIN) + os.pathsep + os.environ.get("PATH", "")
os.environ["KLODE_KIT_FAKE_PYTHON"] = sys.executable
os.environ["KLODE_KIT_TEST_SEAMS"] = "1"
# WI-09d: ``kb-publish`` registers a library in the klode registry; no test (and no process it starts) may ever reach the
# real ``~/.klode/registry.toml`` - every run gets a private one, whatever the tester's environment says.
os.environ["KLODE_REGISTRY"] = str(Path(tempfile.mkdtemp(prefix="klode-registry-")) / "registry.toml")


@pytest.fixture(autouse=True)
def fake_log(tmp_path_factory, monkeypatch) -> Path:
    """Per-test call log written by tests/fakes/bin/{claude,codex}."""
    path = tmp_path_factory.mktemp("fakelog") / "calls.jsonl"
    monkeypatch.setenv("KLODE_KIT_FAKE_LOG", str(path))
    return path
