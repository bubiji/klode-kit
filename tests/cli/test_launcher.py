"""WI-00s: launcher bin/klode-kit (matrix 00s-12; venv addendum V1/V2).

V2: the launcher locates the repo and .venv from its own real path; a missing or broken
venv python is a non-zero exit that prints the fix command, never a silent fallback to a
system python.
"""

from __future__ import annotations

import importlib.metadata
import os
import shutil
import subprocess
from pathlib import Path

from _support.flows import REPO
import pytest

pytestmark = pytest.mark.cli_integration

LAUNCHER = REPO / "bin" / "klode-kit"


def launch(path: Path, *args: str, env_extra: dict[str, str] | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("KLODE_KIT_PYTHON", None)
    env.pop("PYTHONPATH", None)
    env.update(env_extra or {})
    return subprocess.run([str(path), *args], capture_output=True, text=True, env=env, cwd=cwd, timeout=60)


def test_launcher_version(tmp_path):
    """00s-12: bin/klode-kit --version prints the installed package version."""
    res = launch(LAUNCHER, "--version", cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == importlib.metadata.version("klode-kit")


def test_launcher_via_symlink_resolves_real_path(tmp_path):
    link = tmp_path / "elsewhere" / "kk"
    link.parent.mkdir()
    link.symlink_to(LAUNCHER)
    res = launch(link, "--version", cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == importlib.metadata.version("klode-kit")


def _fake_repo(tmp_path: Path) -> Path:
    root = tmp_path / "moved-repo"
    (root / "bin").mkdir(parents=True)
    shutil.copy2(LAUNCHER, root / "bin" / "klode-kit")
    return root


def test_launcher_missing_venv_fails_with_fix_command(tmp_path):
    root = _fake_repo(tmp_path)
    res = launch(root / "bin" / "klode-kit", "--version", cwd=tmp_path)
    assert res.returncode == 40
    assert res.stdout == ""
    assert "scripts/install.sh" in res.stderr
    assert str(root.resolve() / ".venv" / "bin" / "python") in res.stderr


def test_launcher_broken_venv_python_fails_with_fix_command(tmp_path):
    root = _fake_repo(tmp_path)
    (root / ".venv" / "bin").mkdir(parents=True)
    (root / ".venv" / "bin" / "python").symlink_to(tmp_path / "gone" / "python3.12")
    res = launch(root / "bin" / "klode-kit", "--version", cwd=tmp_path)
    assert res.returncode == 40
    assert res.stdout == ""
    assert "scripts/install.sh" in res.stderr


def test_launcher_python_override_still_checked(tmp_path):
    res = launch(LAUNCHER, "--version", env_extra={"KLODE_KIT_PYTHON": str(tmp_path / "nope")}, cwd=tmp_path)
    assert res.returncode == 40
    assert "KLODE_KIT_PYTHON" in res.stderr


def test_launcher_has_no_absolute_paths():
    text = LAUNCHER.read_text()
    assert "/Users/" not in text and "/home/" not in text
