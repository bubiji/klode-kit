"""Helpers for the ``/med-intake`` tests (WI-13b; matrix 13b-*).

``IntakeRig`` is a ``pipeline_fx.Rig`` whose inbox is a dated directory ``_inbox/2026-10-22/`` (U1's layout) and whose
command line is ``klode-kit med-intake`` instead of ``klode-kit pipeline``. The launcher wrapper, the engine replay
seam (all four OCR routes, cloud routes included, are recorded rows - no real CLI is ever started) and the call log
are the pipeline rig's.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Mapping

from _support import pipeline_fx as F
from _support.flows import base_env

OCR_LINE = "ocr: 3 页 × 4 路 OK"
AUDIT_LINE = "audit: 5 条分歧 → confirm/20261022.html"


class IntakeRig(F.Rig):
    def __init__(self, tmp: Path, fixture: str = "fx-u1-3labs", day: str = "2026-10-22", *,
                 engine: Path | None = None, extra_env: Mapping[str, str] | None = None):
        self.fx = F.FIXTURES / fixture
        self.project = tmp / "project"
        self.project.mkdir(parents=True)
        self.inbox = tmp / "_inbox" / day
        self.inbox.mkdir(parents=True)
        for img in sorted((self.fx / "images").glob("*.png")):
            (self.inbox / img.name).write_bytes(img.read_bytes())
        self.log = tmp / "intake-calls.jsonl"
        self.wrapper = F.make_wrapper(tmp / "bin" / "launcher")
        self.pause_dir = tmp / "pause"
        self.pause_dir.mkdir(parents=True)
        self.env = base_env({
            "KLODE_KIT_TEST_SEAMS": "1",
            "KLODE_KIT_ENGINE_REPLAY": str(engine or self.fx / "engine"),
            "KLODE_KIT_PIPELINE_LOG": str(self.log),
            **(extra_env or {}),
        })
        self.env.pop("KLODE_KIT_REAL", None)

    def argv(self, *args: str, launcher: bool = True) -> list[str]:
        base = [sys.executable, "-m", "klode_kit", "med-intake", "--inbox", str(self.inbox),
                "--project", str(self.project)]
        if launcher:
            base += ["--launcher", str(self.wrapper)]
        return [*base, *args]

    # ---- reading
    @property
    def work(self) -> Path:
        return self.project / "work"

    def intake_receipt(self) -> dict:
        return json.loads((self.batch_dir / "med_intake.json").read_text(encoding="utf-8"))

    def link_targets(self) -> dict[str, str]:
        """Every symlink under ``work/`` and ``confirm/`` (project-relative name -> link text)."""
        out: dict[str, str] = {}
        for sub in ("work", "confirm"):
            d = self.project / sub
            if d.is_dir():
                for p in sorted(d.iterdir()):
                    if p.is_symlink():
                        out[f"{sub}/{p.name}"] = str(p.readlink())
        return out
