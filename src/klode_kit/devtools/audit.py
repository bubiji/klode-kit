"""Audit-log analysis for 00s-16 (test matrix section 0.3, F19).

The log is JSON lines written by ``audit_site/sitecustomize.py``. Each record has at least
``ev`` (audit event name) and ``pid``; filesystem records carry ``path`` (realpath of the
target's parent + final component, resolved at event time) and ``mut`` (whether the event
changes the filesystem); crash-point records carry ``name``.

Durable write: a ``mut`` record whose ``path`` is a durable root or lies under one,
excluding ``os.devnull`` and any path with a ``__pycache__`` component. A mutation whose
target the hook could not resolve (``path`` null) also counts, conservatively. Private temporary
directories (``tempfile.mkdtemp`` under TMPDIR) are excluded by construction, since they
are not under a durable root; a mkdtemp inside a durable root is durable state and counts.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

CRASHPOINT_EVENT = "klode_kit.crashpoint"


def read_log(path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


@dataclass
class Segment:
    start: str | None
    end: str | None = None
    writes: list[dict] = field(default_factory=list)


@dataclass
class Partition:
    segments: list[Segment]
    crashpoints: list[str]

    @property
    def violations(self) -> list[Segment]:
        return [s for s in self.segments if len(s.writes) > 1]

    @property
    def crashpoint_names(self) -> frozenset[str]:
        return frozenset(self.crashpoints)


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def is_durable(event: dict, roots: Iterable[str]) -> bool:
    if event.get("mut") is not True:
        return False
    path = event.get("path")
    if not isinstance(path, str) or not path:
        return True  # target unknown: count it, so an unclassifiable write can never read as green
    if path == os.devnull or "__pycache__" in path.split(os.sep):
        return False
    return any(_under(path, root) for root in roots)


def partition(events: Iterable[dict], roots) -> Partition:
    real_roots = [os.path.realpath(os.fspath(r)) for r in roots]
    segments = [Segment(start=None)]
    crashpoints: list[str] = []
    for event in events:
        if event.get("ev") == CRASHPOINT_EVENT:
            name = event.get("name")
            crashpoints.append(name)
            segments[-1].end = name
            segments.append(Segment(start=name))
        elif is_durable(event, real_roots):
            segments[-1].writes.append(event)
    return Partition(segments=segments, crashpoints=crashpoints)
