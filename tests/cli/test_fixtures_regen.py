"""WI-00a: fixture regeneration on macOS (matrix 00a-01, 00a-02; F10 moved them out of unit).

Real processes only: ``<venv python> tests/fixtures/gen_all.py``. Regeneration needs the
pinned font (Hiragino Sans GB, macOS system font) and the locked Pillow/reportlab, so these
run in the ``macos`` subset of cli_integration; Linux CI never regenerates, it uses the
committed products checked by the unit lane (00a-03).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from _support.flows import REPO

pytestmark = [pytest.mark.cli_integration, pytest.mark.macos]

FIXTURES = REPO / "tests" / "fixtures"
GEN_ALL = FIXTURES / "gen_all.py"


def gen(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GEN_ALL), *args], capture_output=True, text=True, timeout=900, cwd=REPO
    )


def tree_shas(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts and p.name != ".DS_Store"
    }


def test_generation_deterministic(tmp_path):
    """00a-01: generate twice into two tmp dirs; every file has the same sha (S5)."""
    a, b = tmp_path / "a", tmp_path / "b"
    ra, rb = gen("--out", str(a)), gen("--out", str(b))
    assert ra.returncode == 0, ra.stderr
    assert rb.returncode == 0, rb.stderr
    sa, sb = tree_shas(a), tree_shas(b)
    assert len(sa) >= 100
    assert sa == sb
    manifest = json.loads((a / "MANIFEST.json").read_text(encoding="utf-8"))
    listed = {e["path"]: e["sha256"] for e in manifest["files"]}
    assert listed == {k: v for k, v in sa.items() if k != "MANIFEST.json"}


def test_gen_all_check_matches_manifest(tmp_path):
    """00a-02: ``gen_all --check`` exits 0; a fresh generation equals the committed MANIFEST byte for byte."""
    res = gen("--check")
    assert res.returncode == 0, res.stdout + res.stderr
    out = tmp_path / "fresh"
    assert gen("--out", str(out)).returncode == 0
    assert (out / "MANIFEST.json").read_bytes() == (FIXTURES / "MANIFEST.json").read_bytes()
    assert tree_shas(out) == {k: v for k, v in tree_shas(FIXTURES).items()}


def test_gen_all_check_detects_drift(tmp_path):
    """Added (S3): ``--check`` against a tree with one changed byte exits non-zero and names the file."""
    out = tmp_path / "tree"
    assert gen("--out", str(out)).returncode == 0
    victim = "fx-labs-1x/truth.json"
    data = bytearray((out / victim).read_bytes())
    data[-2] ^= 0x01
    (out / victim).write_bytes(bytes(data))
    res = gen("--check", "--against", str(out))
    assert res.returncode != 0
    assert victim in res.stdout + res.stderr
