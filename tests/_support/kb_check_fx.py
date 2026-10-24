"""Helpers for the ``/kb-check`` kernel tests (WI-10a; matrix 10a-*).

- ``good_kb(tmp)``: a fresh copy of ``kb/<id>/`` of a real, finished build of ``fx-labs-3x`` (built once per
  process by the real ``klode-kit build`` with the real klode); every file is writable again so tests can tamper
  with a card, ``reseal(build)`` then regenerates ``MANIFEST.sha256`` and makes the tree read-only as a finished
  build is (the checks are about the content of a sound build tree, not about a tampered one).
- ``check(kb, ...)``: the real command ``python -m klode_kit kb-check`` as a subprocess; returns the process and the
  receipt printed on stdout (``None`` when nothing was printed).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Mapping

from _support import build_fx as B
from _support.flows import REPO, base_env
from klode_kit.build import run_build as RB

ENTRY = "skill:/klode-kit:kb-check"
_GOOD: dict[str, Path] = {}


def _built() -> Path:
    if "kb" not in _GOOD:
        root = Path(tempfile.mkdtemp(prefix="kb-check-fx-"))
        project = B.project(root)
        res = B.build(project, root)
        assert res.returncode == 0, res.stderr.decode()
        _GOOD["kb"] = B.kb_dir(project)
    return _GOOD["kb"]


def good_kb(tmp: Path) -> tuple[Path, Path]:
    """``(kb root, build dir)`` of a writable copy of a finished, sound build."""
    dest = tmp / "kb" / _built().name
    shutil.copytree(_built(), dest, symlinks=True)
    B.unlock(dest)
    (build,) = [p for p in (dest / "builds").iterdir() if p.is_dir()]
    return dest, build


def reseal(build: Path) -> None:
    """MANIFEST over the (changed) tree, then read-only again."""
    (build / RB.MANIFEST_NAME).unlink(missing_ok=True)
    RB.write_manifest(build)
    RB.make_readonly(build)


def env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    e = base_env(extra)
    e.pop("KLODE_KIT_REAL", None)
    return e


def check(kb: Path, *args: str, extra_env: Mapping[str, str] | None = None, entry: str = ENTRY,
          timeout: float = 600) -> tuple[subprocess.CompletedProcess, dict | None]:
    proc = subprocess.run([sys.executable, "-m", "klode_kit", "kb-check", "--kb", str(kb), "--entry", entry, *args],
                          env=env(extra_env), capture_output=True, timeout=timeout, cwd=str(REPO))
    text = proc.stdout.decode("utf-8").strip()
    return proc, (json.loads(text) if text else None)


def by_name(receipt: Mapping) -> dict[str, dict]:
    return {c["name"]: c for c in receipt["checks"]}


def written(build: Path) -> dict:
    return json.loads((build / "check_receipt.json").read_text(encoding="utf-8"))
