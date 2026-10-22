"""Keys and fingerprints (WI-00b2; design 02 r3.2, r4.4 R3-06, r2.3, r5).

=================  ====================================================================
key                definition
=================  ====================================================================
``doc_id``         ``d-`` + ``img_sha256[:12]`` (the original document's bytes)
``page_id``        ``<doc_id>/p<NNN>`` (1-based, at least three digits)
``rec_id``         ``r-`` + ``sha256("<doc_id>|p<NNN>|<record_idx>")[:12]`` - never the date
``batch_id``       ``sha256("\\n".join(sorted(set(img_sha256))))[:12]`` - a set: order and
                   repeated images do not change it (r5); ``duplicate_flags`` marks repeats
``cfg_fp``         ``sha256(canonical JSON of {effective config without config_version,
                   prompt_sha, schema_sha, model, effort, adapter_code_version,
                   sorted(set(der_sha256))})[:12]`` (r4.4 R3-06)
``attempt_id``     ``t<seq>``, monotonic per ``(batch, engine, cfg_fp)``, allocated atomically
``run_id``         ``<batch_id>/<engine>/<cfg_fp>/<attempt_id>``
``audit_rev``      ``<batch_id>/a<N>``
``field_id``       ``<rec_id>/<slot>`` (slot has no ``/`` and no whitespace)
``decision_id``    ``<audit_rev>/d<NNNN>``; ``commit_id`` is ``<audit_rev>/c<NNNN>``
idempotency key    ``<field_id>@<rev_base>#<sha256(canonical choice)[:16]>`` (r2.3)
``build_id``       ``b-<UTC yyyymmddTHHMMSSZ>-<sha256(inputs)[:8]>``, the clock is injected;
                   inputs = sorted ``rec_id:rev`` + items/desk/holdout sha + config_version +
                   plugin and klode versions (r3.2)
=================  ====================================================================

The formulas for ``doc_id``, ``rec_id`` and ``batch_id`` are the ones the WI-00a fixture
generator used, so fixture keys and computed keys agree.

Attempt allocation: the attempt directory ``<batch_dir>/ocr/<engine>/<cfg_fp>.t<seq>`` is claimed
with ``os.mkdir`` (atomic, fails if it exists). A process scans the highest existing ``seq`` and
tries ``seq + 1``, rescanning on collision. Directories are only ever added, so the claimed set
stays ``t1..tN`` without gaps and no attempt is reused - not after ``--redo``, not after a crash.
Crash points (matrix section 0.3): ``ids.mkdir`` after each missing parent directory is created,
``ids.attempt_mkdir`` after each attempt-directory mkdir (successful or not), so every segment
between two crash points makes at most one durable write.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Mapping

from klode_kit.seams import crashpoint

if TYPE_CHECKING:  # pragma: no cover
    from klode_kit.config import Config

CRASHPOINTS = ("ids.mkdir", "ids.attempt_mkdir")

_HEX64 = re.compile(r"[0-9a-f]{64}")
_HEX12 = re.compile(r"[0-9a-f]{12}")
_DOC = re.compile(r"d-[0-9a-f]{12}")
_REC = re.compile(r"r-[0-9a-f]{12}")
_SLOT = re.compile(r"[^/\s]+")
_ENGINE = re.compile(r"[a-z]+")
_ATTEMPT = re.compile(r"t([1-9][0-9]*)")
_AUDIT_REV = re.compile(r"[0-9a-f]{12}/a[1-9][0-9]*")
_FIELD = re.compile(r"r-[0-9a-f]{12}/[^/\s]+")


def _sha(text: str | bytes) -> str:
    return hashlib.sha256(text.encode("utf-8") if isinstance(text, str) else text).hexdigest()


def _canonical(data: Any) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _need(pattern: re.Pattern[str], value: Any, what: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"malformed {what}: {value!r}")
    return value


def _positive(value: Any, what: str, minimum: int = 1) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(f"{what} must be an integer >= {minimum}: {value!r}")
    return value


# ---------------------------------------------------------------- documents, pages, records


def doc_id(img_sha256: str) -> str:
    return "d-" + _need(_HEX64, img_sha256, "img_sha256")[:12]


def page_id(doc: str, page_no: int) -> str:
    _need(_DOC, doc, "doc_id")
    return f"{doc}/p{_positive(page_no, 'page_no'):03d}"


def rec_id(record: Mapping[str, Any]) -> str:
    """From ``doc_id``, ``page_id`` and ``record_idx`` only; date, type and the rest are ignored."""
    doc = _need(_DOC, record.get("doc_id"), "doc_id")
    page = record.get("page_id")
    m = re.fullmatch(re.escape(doc) + r"/(p[0-9]{3,})", page) if isinstance(page, str) else None
    if m is None:
        raise ValueError(f"page_id {page!r} does not belong to doc {doc}")
    idx = _positive(record.get("record_idx"), "record_idx", minimum=0)
    return "r-" + _sha(f"{doc}|{m.group(1)}|{idx}")[:12]


# ---------------------------------------------------------------- batches


def _shas(img_sha256s: Iterable[str]) -> list[str]:
    items = [_need(_HEX64, s, "img_sha256") for s in img_sha256s]
    if not items:
        raise ValueError("a batch needs at least one image")
    return items


def batch_id(img_sha256s: Iterable[str]) -> str:
    """Set semantics: input order and repeated images do not change the id (r5)."""
    return _sha("\n".join(sorted(set(_shas(img_sha256s)))))[:12]


def duplicate_flags(img_sha256s: Iterable[str]) -> list[bool]:
    """``True`` for every repeat of an image already seen earlier in the list (manifest ``duplicate``)."""
    seen: set[str] = set()
    flags = []
    for sha in _shas(img_sha256s):
        flags.append(sha in seen)
        seen.add(sha)
    return flags


# ---------------------------------------------------------------- runs


def cfg_fp(
    config: "Config",
    *,
    prompt_sha: str,
    schema_sha: str,
    model: str,
    effort: str,
    adapter_code_version: str,
    der_sha256s: Iterable[str],
) -> str:
    """Run fingerprint (r4.4 R3-06). ``config_version`` is not an input; der_sha256 is a set."""
    for name, value in (("prompt_sha", prompt_sha), ("schema_sha", schema_sha), ("model", model),
                        ("effort", effort), ("adapter_code_version", adapter_code_version)):
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name} must be a non-empty string: {value!r}")
    ders = sorted({_need(_HEX64, d, "der_sha256") for d in der_sha256s})
    payload = {
        "adapter_code_version": adapter_code_version,
        "config": json.loads(config.canonical()),
        "der_sha256": ders,
        "effort": effort,
        "model": model,
        "prompt_sha": prompt_sha,
        "schema_sha": schema_sha,
    }
    return _sha(_canonical(payload))[:12]


def attempt_id(seq: int) -> str:
    return f"t{_positive(seq, 'attempt seq')}"


def run_id(batch: str, engine: str, fp: str, attempt: str) -> str:
    _need(_HEX12, batch, "batch_id")
    _need(_ENGINE, engine, "engine")
    _need(_HEX12, fp, "cfg_fp")
    _need(_ATTEMPT, attempt, "attempt_id")
    return f"{batch}/{engine}/{fp}/{attempt}"


@dataclass(frozen=True)
class Attempt:
    seq: int
    attempt_id: str
    run_id: str
    path: Path


def _ensure_dir(directory: Path) -> None:
    missing = []
    cur = directory
    while not cur.is_dir():
        missing.append(cur)
        cur = cur.parent
    for d in reversed(missing):
        try:
            os.mkdir(d)
        except FileExistsError:
            if not d.is_dir():
                raise
        crashpoint("ids.mkdir")


def _fsync_dir(directory: Path) -> None:
    with contextlib.suppress(OSError):
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _highest(engine_dir: Path, fp: str) -> int:
    prefix = fp + "."
    best = 0
    for name in os.listdir(engine_dir):
        if name.startswith(prefix):
            m = _ATTEMPT.fullmatch(name[len(prefix):])
            if m:
                best = max(best, int(m.group(1)))
    return best


def allocate_attempt(batch_dir: str | os.PathLike, engine: str, fp: str) -> Attempt:
    """Atomically claim the next attempt directory ``<batch_dir>/ocr/<engine>/<fp>.t<seq>``.

    ``batch_dir``'s name is the batch id. Safe across processes: ``os.mkdir`` either creates the
    directory or fails, so two allocators never get the same attempt.
    """
    batch_dir = Path(batch_dir)
    batch = _need(_HEX12, batch_dir.name, "batch directory name (batch_id)")
    _need(_ENGINE, engine, "engine")
    _need(_HEX12, fp, "cfg_fp")
    engine_dir = batch_dir / "ocr" / engine
    _ensure_dir(engine_dir)
    while True:
        seq = _highest(engine_dir, fp) + 1
        path = engine_dir / f"{fp}.t{seq}"
        try:
            os.mkdir(path)
            claimed = True
        except FileExistsError:
            claimed = False
        crashpoint("ids.attempt_mkdir")
        if claimed:
            _fsync_dir(engine_dir)
            attempt = attempt_id(seq)
            return Attempt(seq, attempt, run_id(batch, engine, fp, attempt), path)


# ---------------------------------------------------------------- audit


def audit_rev(batch: str, n: int) -> str:
    return f"{_need(_HEX12, batch, 'batch_id')}/a{_positive(n, 'audit revision')}"


def field_id(rec: str, slot: str) -> str:
    _need(_REC, rec, "rec_id")
    return f"{rec}/{_need(_SLOT, slot, 'field slot')}"


def decision_id(rev: str, seq: int) -> str:
    return f"{_need(_AUDIT_REV, rev, 'audit_rev')}/d{_positive(seq, 'decision seq'):04d}"


def commit_id(rev: str, seq: int) -> str:
    return f"{_need(_AUDIT_REV, rev, 'audit_rev')}/c{_positive(seq, 'commit seq'):04d}"


def idempotency_key(field: str, rev_base: int, choice: Mapping[str, Any]) -> str:
    """``(field_id, rev_base, choice_hash)`` (r2.3): a resubmitted identical decision has the same key."""
    _need(_FIELD, field, "field_id")
    _positive(rev_base, "rev_base", minimum=0)
    return f"{field}@{rev_base}#{_sha(_canonical(dict(choice)))[:16]}"


# ---------------------------------------------------------------- builds


def build_id(
    *,
    rec_revs: Mapping[str, int] | Iterable[tuple[str, int]],
    items_sha: str,
    desk_sha: str,
    holdout_sha: str,
    config_version: int,
    plugin_version: str,
    klode_version: str,
    now: datetime,
) -> str:
    """``b-<UTC time of the injected now>-<sha256(inputs)[:8]>`` (r3.2)."""
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError(f"build_id needs a timezone-aware injected clock value, got {now!r}")
    pairs = rec_revs.items() if isinstance(rec_revs, Mapping) else rec_revs
    revs = sorted(f"{_need(_REC, r, 'rec_id')}:{_positive(n, 'rev', minimum=0)}" for r, n in pairs)
    payload = {
        "config_version": _positive(config_version, "config_version"),
        "desk_sha": desk_sha,
        "holdout_sha": holdout_sha,
        "items_sha": items_sha,
        "klode_version": klode_version,
        "plugin_version": plugin_version,
        "rec_revs": revs,
    }
    stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"b-{stamp}-{_sha(_canonical(payload))[:8]}"
