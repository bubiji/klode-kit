"""Helpers for the WI-05a header-masking tests (matrix 05a-*).

Vision boundary (plan section 0.4): ``ReplayVision`` stands in for ``OcrMacEngine``. It returns the real recorded
ocrmac observations of ``tests/recordings/vision/<der_sha256>.json`` (WI-00c3) through the real conversion
``engines.vision_lines``; pages without a recording take explicit observations (``extra``). Paired real-engine
test: tests/cli/test_mask_real.py (05a-07).

``make_batch`` builds a WI-04b batch the public way (``manifest.scan`` + ``manifest.plan``) and writes its
``work/<batch>/derived.jsonl`` and turned derived images, which is the input ``mask.mask_batch`` reads.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Mapping, Sequence

from _support.flows import REPO
from klode_kit.ocr import engines as E
from klode_kit.ocr import geometry as G
from klode_kit.ocr.manifest import dumps_jsonl, plan, scan

FIXTURES = REPO / "tests" / "fixtures"
RECORDINGS = REPO / "tests" / "recordings" / "vision"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ReplayVision:
    """``ocr_page(png, *, page_id, der_sha256)`` like ``OcrMacEngine``, from recorded raw observations."""

    name = "vision"

    def __init__(self, extra: Mapping[str, Sequence[dict]] | None = None):
        self.extra = dict(extra or {})
        self.calls: list[str] = []

    def ocr_page(self, png, *, page_id: str, der_sha256: str) -> list[dict]:
        data = Path(png).read_bytes()
        sha = sha256(data)
        self.calls.append(sha)
        if sha in self.extra:
            raw = list(self.extra[sha])
        else:
            rec = RECORDINGS / f"{sha}.json"
            if not rec.is_file():
                raise AssertionError(f"no Vision recording for {sha} ({png})")
            raw = json.loads(rec.read_text(encoding="utf-8"))["raw"]
        return E.vision_lines(raw, G.image_size(png), page_id=page_id, der_sha256=der_sha256)


def make_batch(project: Path, images: Sequence[Path]) -> str:
    """Copy ``images`` into ``project/_inbox`` and write the WI-04b batch inputs; returns the batch id."""
    inbox = project / "_inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    for i, img in enumerate(images):
        shutil.copyfile(img, inbox / f"{i:03d}_{img.name}")
    sources, _skipped = scan(inbox)
    batch = plan(sources, project, project / "work")
    bdir = project / "work" / batch.batch_id
    bdir.mkdir(parents=True, exist_ok=True)
    for page in batch.pages:
        if page.derived_bytes is not None:
            page.derived_path.parent.mkdir(parents=True, exist_ok=True)
            page.derived_path.write_bytes(page.derived_bytes)
    (bdir / "manifest.jsonl").write_bytes(dumps_jsonl(batch.manifest))
    (bdir / "derived.jsonl").write_bytes(dumps_jsonl(batch.derived))
    return batch.batch_id


def fixture_images(name: str) -> list[Path]:
    return sorted((FIXTURES / name / "images").glob("*.png"))


def files_under(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*") if p.is_file())


def worker(project: str, batch_id: str, barrier, env: Mapping[str, str] | None = None) -> None:
    """Child process body for the concurrency / crash tests (multiprocessing ``spawn``)."""
    if env:
        os.environ.update(env)
    from klode_kit.audit import mask

    if barrier is not None:
        barrier.wait(timeout=30)
    mask.mask_batch(Path(project), batch_id, engine=ReplayVision())
