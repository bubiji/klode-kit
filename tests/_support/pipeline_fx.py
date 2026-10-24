"""Helpers for the pipeline kernel tests (WI-09a; matrix 09a-*).

The kernel runs every sub-step as a child process ``<launcher> <command> ...`` (venv addendum V1). The tests
point ``--launcher`` at a thin wrapper that

- appends ``{"argv": [...], "pid": n}`` to ``$KLODE_KIT_PIPELINE_LOG`` for every call (the call log the cases
  count and inspect), and
- stands in for ``doctor`` (the real doctor needs klode, poppler, tesseract, MLX weights ... of the machine;
  its real path is ``tests/cli/test_pipeline_cli.py``): it writes a stub receipt to ``--receipt`` and exits
  ``$FAKE_DOCTOR_EXIT`` (default 0),
- hands every other command to the real launcher ``bin/klode-kit`` (kb-ocr with the engine replay seam,
  kb-audit, confirm-page, views ... all real).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Mapping

from _support.flows import REPO, base_env

FIXTURES = REPO / "tests" / "fixtures"
REAL_LAUNCHER = REPO / "bin" / "klode-kit"
STAGES = ("preflight", "ocr", "audit", "writeback", "build", "check", "publish")

WRAPPER = '''#!{python}
import json, os, sys
argv = sys.argv[1:]
log = os.environ.get("KLODE_KIT_PIPELINE_LOG")
if log:
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({{"argv": argv, "pid": os.getpid()}}) + "\\n")
if argv[:1] == ["doctor"]:
    if "--receipt" in argv:
        path = argv[argv.index("--receipt") + 1]
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({{"schema": "klode-kit.doctor/1", "stub": True}}) + "\\n")
    sys.exit(int(os.environ.get("FAKE_DOCTOR_EXIT", "0")))
real = {real!r}
os.execv(real, [real, *argv])
'''


def make_wrapper(dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(WRAPPER.format(python=sys.executable, real=str(REAL_LAUNCHER)), encoding="utf-8")
    dest.chmod(0o755)
    return dest


def sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Rig:
    """One pipeline test bed: data project, inbox of a fixture's images, engine replay, wrapper, call log."""

    def __init__(self, tmp: Path, fixture: str = "fx-u3-mixed", *, extra_env: Mapping[str, str] | None = None):
        self.fx = FIXTURES / fixture
        self.project = tmp / "project"
        self.project.mkdir()
        self.inbox = tmp / "inbox"
        self.inbox.mkdir()
        for img in sorted((self.fx / "images").glob("*.png")):
            (self.inbox / img.name).write_bytes(img.read_bytes())
        self.log = tmp / "pipeline-calls.jsonl"
        self.wrapper = make_wrapper(tmp / "bin" / "launcher")
        self.pause_dir = tmp / "pause"
        self.pause_dir.mkdir()
        self.env = base_env({
            "KLODE_KIT_TEST_SEAMS": "1",
            "KLODE_KIT_ENGINE_REPLAY": str(self.fx / "engine"),
            "KLODE_KIT_PIPELINE_LOG": str(self.log),
            **(extra_env or {}),
        })
        self.env.pop("KLODE_KIT_REAL", None)

    # ---- running
    def argv(self, *args: str, launcher: bool = True) -> list[str]:
        base = [sys.executable, "-m", "klode_kit", "pipeline", "--batch", str(self.inbox),
                "--project", str(self.project)]
        if launcher:
            base += ["--launcher", str(self.wrapper)]
        return [*base, *args]

    def run(self, *args: str, env: Mapping[str, str] | None = None, timeout: float = 180,
            launcher: bool = True) -> subprocess.CompletedProcess:
        e = dict(self.env)
        e.update(env or {})
        return subprocess.run(self.argv(*args, launcher=launcher), env=e, capture_output=True, timeout=timeout,
                              cwd=str(self.project))

    def popen(self, *args: str, env: Mapping[str, str] | None = None) -> subprocess.Popen:
        e = dict(self.env)
        e.update(env or {})
        return subprocess.Popen(self.argv(*args), env=e, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                cwd=str(self.project))

    def pause_env(self, point: str) -> dict[str, str]:
        return {"KLODE_KIT_PAUSE_AT": point, "KLODE_KIT_PAUSE_DIR": str(self.pause_dir)}

    def wait_reached(self, point: str, proc: subprocess.Popen, timeout: float = 60) -> None:
        marker = self.pause_dir / f"{point}.reached"
        deadline = time.time() + timeout
        while time.time() < deadline:
            if marker.exists():
                return
            if proc.poll() is not None:
                raise AssertionError(f"process ended before {point}: {proc.communicate()}")
            time.sleep(0.05)
        raise AssertionError(f"{point} not reached")

    def release(self, point: str) -> None:
        (self.pause_dir / f"{point}.go").write_text("", encoding="utf-8")

    # ---- reading
    @property
    def batch_id(self) -> str:
        from klode_kit.contract import ids

        return ids.batch_id(sha_file(p) for p in sorted(self.inbox.iterdir()) if p.is_file())

    @property
    def batch_dir(self) -> Path:
        return self.project / "work" / self.batch_id

    @property
    def receipts_dir(self) -> Path:
        return self.batch_dir / "pipeline" / "receipts"

    def receipt(self, stage: str) -> dict:
        return json.loads((self.receipts_dir / f"{stage}.json").read_text(encoding="utf-8"))

    def receipt_names(self) -> list[str]:
        """Stage receipts in the order they were started."""
        if not self.receipts_dir.is_dir():
            return []
        recs = [json.loads(p.read_text(encoding="utf-8")) for p in self.receipts_dir.glob("*.json")]
        return [r["step"] for r in sorted(recs, key=lambda r: r["started"])]

    def calls(self, command: str | None = None) -> list[dict]:
        if not self.log.exists():
            return []
        rows = [json.loads(x) for x in self.log.read_text(encoding="utf-8").splitlines() if x.strip()]
        return [r for r in rows if command is None or (r["argv"] and r["argv"][0] == command)]

    def tree(self) -> dict[str, bytes]:
        out: dict[str, bytes] = {}
        for dirpath, _dirs, files in os.walk(self.project):
            for f in files:
                p = Path(dirpath, f)
                out[p.relative_to(self.project).as_posix()] = p.read_bytes()
        return out
