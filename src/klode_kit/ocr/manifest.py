"""Batch input: inbox scan, manifest and derived (upright) images (WI-04b; design 02 r2.2-A, r2.4, r5).

- ``scan(inbox)``: the files directly inside the inbox directory, sorted by name. ``.png``/``.jpg``/``.jpeg``
  files that decode are images; everything else is skipped with a reason - ``not_image``,
  ``unreadable_image`` or ``directory`` - and the caller lists it in the receipt's ``skipped[]`` (r5: never
  silently). Nothing is written.
- ``plan(images, project, batch_root)``: one ``manifest`` line per image (``doc_id`` from the original bytes;
  a repeated image is marked ``duplicate`` and is not a second page, r5), ``batch_id`` over the set of
  ``img_sha256``, and per distinct image one page: key ``p<NNN>`` (position in the batch), ``page_id``
  ``<doc_id>/p001`` (an image is always page 1), orientation (``orient.upright``) and one ``derived`` line.
  An upright page's derived image is the original file (same sha); a turned page's is
  ``<batch dir>/derived/<key>.png``.

Paths in the manifest and derived lines are relative to the data project when they lie inside it (posix),
absolute otherwise.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from klode_kit.contract import ids, schema
from klode_kit.ocr import orient

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg")


class InboxError(ValueError):
    """The inbox is not a readable directory (usage error)."""


@dataclass(frozen=True)
class Source:
    path: Path
    name: str
    data: bytes
    sha256: str


@dataclass(frozen=True)
class Page:
    key: str
    doc_id: str
    page_id: str
    img_sha256: str
    der_sha256: str
    rotate: int
    source: Path
    derived_path: Path
    derived_bytes: bytes | None  # None: the derived image is the source file itself


@dataclass(frozen=True)
class Batch:
    batch_id: str
    manifest: list[dict]
    derived: list[dict]
    pages: list[Page]


def _decodes(data: bytes) -> bool:
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
    except Exception:  # any decoder failure means "not a usable image"
        return False
    return True


def scan(inbox: str | os.PathLike) -> tuple[list[Source], list[dict]]:
    """Images and skipped entries of ``inbox`` (top level only, sorted by name)."""
    root = Path(inbox)
    if not root.is_dir():
        raise InboxError(f"inbox is not a directory: {root}")
    images: list[Source] = []
    skipped: list[dict] = []
    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        if entry.is_dir():
            skipped.append({"path": entry.name, "reason": "directory"})
            continue
        if entry.suffix.lower() not in IMAGE_SUFFIXES:
            skipped.append({"path": entry.name, "reason": "not_image"})
            continue
        data = entry.read_bytes()
        if not _decodes(data):
            skipped.append({"path": entry.name, "reason": "unreadable_image"})
            continue
        images.append(Source(entry, entry.name, data, hashlib.sha256(data).hexdigest()))
    return images, skipped


def display_path(path: Path, project: Path) -> str:
    path, project = Path(os.path.abspath(path)), Path(os.path.abspath(project))
    try:
        return path.relative_to(project).as_posix()
    except ValueError:
        return str(path)


def plan(images: Iterable[Source], project: str | os.PathLike, batch_root: str | os.PathLike) -> Batch:
    """Manifest, pages and derived lines for ``images``; ``batch_root`` is ``<project>/work``."""
    images = list(images)
    project = Path(project)
    shas = [s.sha256 for s in images]
    batch = ids.batch_id(shas)
    batch_dir = Path(batch_root) / batch
    manifest, derived, pages = [], [], []
    for source, dup in zip(images, ids.duplicate_flags(shas)):
        doc = ids.doc_id(source.sha256)
        line: dict[str, Any] = {"doc_id": doc, "img_sha256": source.sha256, "kind": "image",
                                "path": display_path(source.path, project)}
        if dup:
            line["duplicate"] = True
            manifest.append(line)
            continue
        manifest.append(line)
        key = f"p{len(pages) + 1:03d}"
        rotate, data = orient.upright(source.data)
        if rotate == 0:
            der, der_path, der_bytes = source.sha256, source.path, None
        else:
            der, der_path, der_bytes = hashlib.sha256(data).hexdigest(), batch_dir / "derived" / f"{key}.png", data
        page = Page(key, doc, ids.page_id(doc, 1), source.sha256, der, rotate, source.path, der_path, der_bytes)
        pages.append(page)
        derived.append({"der_sha256": der, "parent": source.sha256, "page_id": page.page_id,
                        "path": display_path(der_path, project),
                        "transform": {"rotate": rotate, "crop": None, "mask_boxes": []}})
    for row in manifest:
        schema.validate("manifest", row)
    for row in derived:
        schema.validate("derived", row)
    return Batch(batch, manifest, derived, pages)


def dumps_jsonl(rows: Iterable[dict]) -> bytes:
    return "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows).encode("utf-8")
