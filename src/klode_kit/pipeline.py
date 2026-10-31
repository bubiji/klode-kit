"""``klode-kit pipeline``: the pipeline kernel and the stages up to the writeback (WI-09a; design 02 r2.4 "步骤原子性",
r2.6 exit codes, r3.2 "done 复用", venv addendum V1; deviations WI-02b, 08b, 08d, 02d, 03a, 03b, 01c, 10b).

Usage::

    klode-kit pipeline --batch <inbox dir or PDF> [--project DIR] [--stage S] [--choices FILE] [--routes a,b]
                       [--type lab|rx] [--launcher PATH] [--resume]

Stages, in this order (``--stage S`` runs up to and including ``S``; default: all of them)::

    preflight   ``doctor --project P --receipt <preflight_receipt.json>``
    ocr         ``kb-ocr --batch INBOX --project P [--routes R] [--type T]``
    audit       ``kb-audit --batch B --project P``; when records await the human (exit 10): the ingest
                ``kb-audit --batch B --writeback`` (WI-32c: a page without disagreement becomes ``verified``
                (auto), one with disagreements a ``pending_confirm`` candidate with no effective revision), then
                ``confirm-page --batch B`` (WI-08b: the kernel, not kb-audit, writes the page; made after the
                ingest so its ``rev_base`` is the ingested rev), then ``views``. Awaiting = exit 10, the first
                segment of the two-segment flow (U1/U3): the run ends here, with ``records/`` and ``verified/``
                already showing what awaits, until the human's ``choices.json`` is given with ``--choices``.
                Records and views are not products of this stage (the second segment changes them; the stage's
                inputs/outputs hashes, hence its reuse, are those of the audit artifacts as before).
    writeback   ``kb-audit --batch B --writeback <choices>`` (the human's choices, when given), then
                ``kb-audit --batch B --writeback`` (ingest), then ``views`` (WI-08d: views are rebuilt after
                every writeback).
    build       ``build --project P --loops`` (WI-09b / 09c): the 13 steps into ``kb/<id>/builds/<build_id>/``,
                a failed content gate repaired by the three loops
    check       ``kb-check`` on the build the build stage made (WI-10a), judged before anything points to it
    publish     ``kb-publish --build B`` (WI-09d): ``current`` moves to the checked build, ``PUBLISH.log``, the
                registrations; the stage's product is ``pipeline/publish_receipt.json`` (stable, so a later
                rollback is not undone by a resume). A registration that fails ends the run with exit 21 and the
                pointer stays; the next ``--resume`` repeats only that action.

WI-33d (real-data pilot P8): the audit stage ends with exit 20 (content gate) when pages were read and the audit made
no record for them - for a batch with a record type (``--type`` / ``audit_type``) every page counts, text-layer pages
included; without a type only pages that are not text-layer pages count (a text-layer book or guide has none, U4).
Never "ok". ``audit_type`` is the type ``med-intake`` decided from the headers; it goes to ``kb-audit --type`` and into
the audit stage's inputs, never to ``kb-ocr``.

A stage without a ``run`` (a registry entry replaced by a test) is reported and the run ends with exit 2 - it never
writes a receipt and never passes.

Everything runs as a child process ``<launcher> <command> ...`` where the launcher is this repository's
``bin/klode-kit`` (V1: never a venv console script; ``--launcher`` replaces it for tests). The kernel never
passes ``--force`` to any child (``child_argv`` refuses it; WI-02b).

Receipts: ``work/<batch>/pipeline/receipts/<stage>.json`` (contract ``receipt``)::

    running  {pid, started, inputs_hash, config_hash, outputs: []}        written before the stage starts
    done     status done, exit_code 0 (or 10 = awaiting the human), outputs [{path, sha256}] (project-relative)
    failed   status failed, exit_code in the table below

- A done receipt is reused only when ``inputs_hash``, ``config_hash`` and every ``outputs[].sha256`` match what
  is on disk now (r3.2); a missing product or a different sha reruns the step. ``inputs_hash`` of a stage covers
  the recorded products of the stage before it, so a changed upstream product reruns what follows. The human's
  choices file is kept as ``pipeline/choices.json`` (copied when ``--choices`` is given) and is part of the
  writeback's inputs, so resuming without ``--choices`` reuses the same decision.
- A ``running`` receipt whose pid is dead is a crash: the step runs again. One whose pid is alive (and is not
  this process) is another run in progress: exit 40, not stolen.
- ``work/.pipeline.lock`` (``fcntl`` exclusive, holder pid + batch): a second process exits 40 and writes
  nothing. It is a different file from ``work/.lock`` - kb-ocr, kb-audit and the writeback take that one
  themselves, so the kernel cannot hold it while it runs them. A killed holder releases the lock with its
  process.
- Environment-class results (exit 40, 60, 61, 50 ...) end the run; only a content-gate failure (exit 20) can
  enter a repair loop (``enters_loop``; the loops are ``klode_kit.loops``, WI-09c).

Exit codes (closed set): 0 ok, 2 usage, 10 awaiting_confirm, 20 content_gate, 21 post_publish_failed,
22 stale_replay, 30 contract / stale input, 40 environment / configuration / authentication / lock,
50 budget or no progress, 60 privacy, 61 isolation or the real-call gate. A child's exit code outside the set is
recorded as 40.

stdout: one JSON line ``{schema: "klode-kit.pipeline/1", batch_id, stages: [...], exit_code, reason}``; progress
and the children's output go to stderr.

Crash points: ``pipeline.mkdir`` (``work/`` and the stage receipts directory made; a loop, one name),
``pipeline.lock`` (lock taken, holder stamped), ``pipeline.stage_running`` (running receipt written),
``pipeline.stage_done`` (final receipt written).
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, TextIO

from klode_kit import __version__, receipts
from klode_kit import config as config_mod
from klode_kit.contract import ids
from klode_kit.contract.schema import issues
from klode_kit.ocr import manifest as manifest_mod
from klode_kit.seams import crashpoint

SCHEMA = "klode-kit.pipeline/1"
RECEIPT_SCHEMA = "klode-kit.receipt/1"
RECORD_TYPES = ("lab", "rx")
STAGE_NAMES = ("preflight", "ocr", "audit", "writeback", "build", "check", "publish")
LOCK_NAME = ".pipeline.lock"
CRASHPOINTS = ("pipeline.mkdir", "pipeline.lock", "pipeline.stage_running", "pipeline.stage_done")
USAGE_EXIT = 2
_BATCH_DIR = "pipeline"


class Reason(str, Enum):
    ok = "ok"
    usage = "usage"
    awaiting_confirm = "awaiting_confirm"
    content_gate = "content_gate"
    post_publish_failed = "post_publish_failed"
    stale_replay = "stale_replay"
    contract_violation = "contract_violation"
    stale_input = "stale_input"
    environment = "environment"
    config = "config"
    auth = "auth"
    lock_busy = "lock_busy"
    budget_exhausted = "budget_exhausted"
    no_progress = "no_progress"
    privacy = "privacy"
    isolation = "isolation"
    gate_closed = "gate_closed"


EXIT_CODE: dict[Reason, int] = {
    Reason.ok: 0,
    Reason.usage: 2,
    Reason.awaiting_confirm: 10,
    Reason.content_gate: 20,
    Reason.post_publish_failed: 21,
    Reason.stale_replay: 22,
    Reason.contract_violation: 30,
    Reason.stale_input: 30,
    Reason.environment: 40,
    Reason.config: 40,
    Reason.auth: 40,
    Reason.lock_busy: 40,
    Reason.budget_exhausted: 50,
    Reason.no_progress: 50,
    Reason.privacy: 60,
    Reason.isolation: 61,
    Reason.gate_closed: 61,
}
LOOPABLE: frozenset[Reason] = frozenset({Reason.content_gate})

_CHILD_REASON: dict[int, Reason] = {
    0: Reason.ok, 2: Reason.usage, 10: Reason.awaiting_confirm, 20: Reason.content_gate,
    21: Reason.post_publish_failed, 22: Reason.stale_replay, 30: Reason.contract_violation,
    40: Reason.environment, 50: Reason.budget_exhausted, 60: Reason.privacy, 61: Reason.isolation,
}
_CONTINUE = (Reason.ok, Reason.awaiting_confirm)


def enters_loop(reason: Reason) -> bool:
    """Only a failed content gate is repaired by a loop; environment-class results are never retried."""
    return reason in LOOPABLE


def classify_child_exit(code: int) -> Reason:
    """A child's exit code as a reason; a code outside the closed set (crash, signal, traceback) is environment."""
    return _CHILD_REASON.get(code, Reason.environment)


def default_launcher() -> Path:
    return Path(__file__).resolve().parents[2] / "bin" / "klode-kit"


def child_argv(launcher: str | os.PathLike, *args: str) -> list[str]:
    """``[launcher, *args]`` - and never a ``--force`` (WI-02b: forcing is for humans only)."""
    for a in args:
        if a.startswith("--force"):
            raise ValueError(f"the pipeline never passes --force to a child: {a!r}")
    return [str(launcher), *args]


def write_receipt_verdict(receipt: Any) -> str:
    """Build-step verdict of a ``klode-kit.write_receipt/1``: PASS only for ``done`` and not forced (WI-02b)."""
    if isinstance(receipt, Mapping) and receipt.get("status") == "done" and receipt.get("forced") is False:
        return "PASS"
    return "FAIL"


def pid_alive(pid: Any) -> bool:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _digest(obj: Any) -> str:
    return _sha(json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def _file_sha(path: Path) -> str | None:
    try:
        return _sha(path.read_bytes())
    except OSError:
        return None


def is_reusable(receipt: Any, *, inputs_hash: str, config_hash: str, base: Path) -> bool:
    """r3.2: a ``done`` receipt whose inputs, configuration and every product still match."""
    if not isinstance(receipt, Mapping) or receipt.get("status") != "done":
        return False
    if receipt.get("inputs_hash") != inputs_hash or receipt.get("config_hash") != config_hash:
        return False
    outputs = receipt.get("outputs")
    if not isinstance(outputs, list):
        return False
    for item in outputs:
        if not isinstance(item, Mapping) or not isinstance(item.get("path"), str):
            return False
        if _file_sha(Path(base) / item["path"]) != item.get("sha256"):
            return False
    return True


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# --------------------------------------------------------------------------- stage model

@dataclass
class Outcome:
    """What a stage run reports: a reason, a short detail and its products (project-relative paths)."""

    reason: Reason
    detail: str = ""
    outputs: list[str] = field(default_factory=list)


@dataclass
class Stage:
    name: str
    inputs: Callable[["Ctx"], Any] | None = None
    run: Callable[["Ctx"], Outcome] | None = None


@dataclass
class Ctx:
    project: Path
    inbox: Path
    batch_id: str
    image_shas: list[str]
    launcher: Path
    routes: str | None
    env: Mapping[str, str]
    cfg: Any
    out: TextIO
    choices: Path | None = None
    done: dict[str, dict] = field(default_factory=dict)
    last_stage: str | None = None  # WI-33b: the last stage of this run; doctor skips project_trust for local-only runs
    record_type: str | None = None  # WI-13c: explicit document type, handed to kb-ocr (``--type``)
    audit_type: str | None = None  # WI-33d: the type med-intake decided (header), handed to kb-audit only (``--type``)

    @property
    def effective_type(self) -> str | None:
        return self.audit_type or self.record_type

    @property
    def batch_dir(self) -> Path:
        return self.project / "work" / self.batch_id

    @property
    def pdir(self) -> Path:
        return self.batch_dir / _BATCH_DIR

    @property
    def receipts_dir(self) -> Path:
        return self.pdir / "receipts"

    def rel(self, path: Path) -> str:
        return path.relative_to(self.project).as_posix()

    def child(self, *args: str) -> tuple[int, str]:
        """Run ``<launcher> *args`` in the project; the child's output is relayed to stderr; (exit code, stdout)."""
        argv = child_argv(self.launcher, *args)
        proc = subprocess.run(argv, env=dict(self.env), cwd=str(self.project), capture_output=True)
        text = proc.stdout.decode("utf-8", "replace")
        for stream in (text, proc.stderr.decode("utf-8", "replace")):
            if stream:
                self.out.write(stream if stream.endswith("\n") else stream + "\n")
        self.out.flush()
        return proc.returncode, text

    def upstream(self, stage: str) -> list[dict]:
        return list(self.done.get(stage, {}).get("outputs", []))


# --------------------------------------------------------------------------- stages

def _preflight_inputs(ctx: Ctx) -> Any:
    inputs = {"python": sys.version, "kit": __version__, "platform": sys.platform, "launcher": str(ctx.launcher),
              "path": ctx.env.get("PATH", ""), "project": str(ctx.project.resolve())}
    if ctx.routes and ctx.last_stage:  # only when scoped: hashes of unscoped runs stay as they were
        inputs["trust_scope"] = [ctx.routes, ctx.last_stage]
    return inputs


def _preflight_run(ctx: Ctx) -> Outcome:
    product = ctx.pdir / "preflight_receipt.json"
    scope: list[str] = []
    if ctx.routes and ctx.last_stage:  # WI-33b: tell doctor what the run needs, so a local-only run skips trust
        scope = ["--routes", ctx.routes, "--last-stage", ctx.last_stage]
    code, _ = ctx.child("doctor", "--project", str(ctx.project), "--receipt", str(product), *scope)
    reason = classify_child_exit(code)
    if reason is Reason.ok and not product.is_file():
        return Outcome(Reason.contract_violation, "doctor wrote no receipt")
    return Outcome(reason, f"doctor exit {code}", [ctx.rel(product)] if product.is_file() else [])


def _ocr_inputs(ctx: Ctx) -> Any:
    inputs = {"images": sorted(ctx.image_shas), "routes": ctx.routes}
    if ctx.record_type:  # only when given: the hashes of runs without a type stay as they were
        inputs["type"] = ctx.record_type
    return inputs


def _ocr_run(ctx: Ctx) -> Outcome:
    args = ["kb-ocr", "--batch", str(ctx.inbox), "--project", str(ctx.project)]
    if ctx.routes:
        args += ["--routes", ctx.routes]
    if ctx.record_type:
        args += ["--type", ctx.record_type]
    code, _ = ctx.child(*args)
    names = ("manifest.jsonl", "derived.jsonl", "receipt.json")
    outputs = [ctx.rel(ctx.batch_dir / n) for n in names if (ctx.batch_dir / n).is_file()]
    reason = classify_child_exit(code)
    if reason is Reason.ok and len(outputs) != len(names):
        return Outcome(Reason.contract_violation, "kb-ocr left no batch receipt", outputs)
    return Outcome(reason, f"kb-ocr exit {code}", outputs)


def _after(name: str) -> Callable[[Ctx], Any]:
    def inputs(ctx: Ctx) -> Any:
        return {"after": name, "products": ctx.upstream(name)}
    return inputs


def newest_audit_rev(batch_dir: Path) -> Path | None:
    root = batch_dir / "audit"
    if not root.is_dir():
        return None
    revs = [p for p in root.iterdir() if p.is_dir() and p.name[:1] == "a" and p.name[1:].isdigit()]
    return max(revs, key=lambda p: int(p.name[1:])) if revs else None


def _audit_outputs(ctx: Ctx) -> list[str]:
    adir = newest_audit_rev(ctx.batch_dir)
    if adir is None:
        return []
    return [ctx.rel(adir / n) for n in ("runs.json", "cells.jsonl", "candidates.jsonl", "confirm.html")
            if (adir / n).is_file()]


def _audit_inputs(ctx: Ctx) -> Any:
    inputs = _after("ocr")(ctx)
    if ctx.audit_type:  # only when given: a run that audited nothing without the type is not reused for it (WI-33d)
        inputs["type"] = ctx.audit_type
    return inputs


def _candidate_count(batch_dir: Path) -> int:
    adir = newest_audit_rev(batch_dir)
    path = None if adir is None else adir / "candidates.jsonl"
    if path is None or not path.is_file():
        return 0
    return sum(1 for x in path.read_text(encoding="utf-8").splitlines() if x.strip())


def zero_record_message(batch_dir: Path, record_type: str | None) -> str | None:
    """WI-33d (real-data pilot P8): the failure text when pages were read but the audit made no record for them, else
    ``None``. A page that must yield a record is every page of a batch with a record type (lab / rx) and every page
    that is not a text-layer page; a text-layer batch without a type is a book or a guide (U4) and legitimately has
    none. Success with nothing ingested is never reported."""
    try:
        pages = json.loads((batch_dir / "receipt.json").read_text(encoding="utf-8")).get("pages") or []
    except (OSError, ValueError):
        return None
    wanted = [p for p in pages if record_type or p.get("text_class") != "text"]
    if not wanted or _candidate_count(batch_dir) > 0:
        return None
    kind = f"a {record_type} batch" if record_type else "a batch"
    return (f"kb-audit produced 0 records for the {len(wanted)} page(s) of {kind}: nothing was ingested "
            f"(batch {batch_dir.name}); this is not 'nothing to confirm'")


def _audit_run(ctx: Ctx) -> Outcome:
    args = ["kb-audit", "--batch", ctx.batch_id, "--project", str(ctx.project)]
    if ctx.audit_type:
        args += ["--type", ctx.audit_type]
    code, _ = ctx.child(*args)
    reason = classify_child_exit(code)
    if reason in _CONTINUE:
        lost = zero_record_message(ctx.batch_dir, ctx.effective_type)
        if lost is not None:
            print(f"pipeline: {lost}", file=ctx.out)
            return Outcome(Reason.content_gate, "audit produced no record", _audit_outputs(ctx))
    if reason is Reason.awaiting_confirm:
        # WI-32c: the first segment ends with the records on disk. The ingest (the writeback without choices) comes
        # BEFORE the confirm page so the page's rev_base is the record's current rev (the ingested r1); the second
        # segment's choices then go on from it. Records and views are not products of this stage (the second segment
        # changes them, which must not make the audit receipt stale).
        code, _ = ctx.child("kb-audit", "--batch", ctx.batch_id, "--project", str(ctx.project), "--writeback")
        if classify_child_exit(code) not in _CONTINUE:
            return Outcome(classify_child_exit(code), f"kb-audit --writeback (ingest) exit {code}",
                           _audit_outputs(ctx))
        code, _ = ctx.child("confirm-page", "--batch", ctx.batch_id, "--project", str(ctx.project))
        if code != 0:
            return Outcome(classify_child_exit(code), f"confirm-page exit {code}", _audit_outputs(ctx))
        code, _ = ctx.child("views", "--project", str(ctx.project))
        if code != 0:
            return Outcome(classify_child_exit(code), f"views exit {code}", _audit_outputs(ctx))
        code = EXIT_CODE[Reason.awaiting_confirm]
    return Outcome(reason, f"kb-audit exit {code}", _audit_outputs(ctx))


def _choices_sha(ctx: Ctx) -> str | None:
    return _file_sha(ctx.pdir / "choices.json")


def _writeback_inputs(ctx: Ctx) -> Any:
    return {"after": "audit", "products": ctx.upstream("audit"), "choices": _choices_sha(ctx)}


def _writeback_outputs(ctx: Ctx) -> list[str]:
    adir = newest_audit_rev(ctx.batch_dir)
    if adir is None or not (adir / "candidates.jsonl").is_file():
        return []
    outputs = []
    for line in (adir / "candidates.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            path = ctx.project / "records" / "_by_id" / f"{json.loads(line)['rec_id']}.md"
            if path.is_file():
                outputs.append(ctx.rel(path))
    return sorted(set(outputs))


def _writeback_run(ctx: Ctx) -> Outcome:
    base = ["kb-audit", "--batch", ctx.batch_id, "--project", str(ctx.project), "--writeback"]
    reasons: list[Reason] = []
    stored = ctx.pdir / "choices.json"
    steps = ([base + [str(stored)]] if stored.is_file() else []) + [base]
    for args in steps:
        code, _ = ctx.child(*args)
        reason = classify_child_exit(code)
        if reason not in _CONTINUE:
            return Outcome(reason, f"kb-audit --writeback exit {code}", _writeback_outputs(ctx))
        reasons.append(reason)
    code, _ = ctx.child("views", "--project", str(ctx.project))
    if code != 0:
        return Outcome(classify_child_exit(code), f"views exit {code}", _writeback_outputs(ctx))
    final = Reason.awaiting_confirm if Reason.awaiting_confirm in reasons else Reason.ok
    return Outcome(final, "writeback + views", _writeback_outputs(ctx))


def _build_run(ctx: Ctx) -> Outcome:
    """``klode-kit build --project P --loops`` (WI-09b / 09c): the 13 steps into ``kb/<id>/builds/<build_id>/``, a
    failed content gate repaired by the three loops (one new build per round); the stage's product is the last
    build's ``build_receipt.json`` (the whole tree is pinned by its ``MANIFEST.sha256``)."""
    code, text = ctx.child("build", "--project", str(ctx.project), "--loops")
    summary: dict = {}
    for line in reversed(text.splitlines()):
        if line.strip().startswith("{"):
            try:
                summary = json.loads(line)
            except ValueError:
                continue
            break
    rel = summary.get("build_dir") if isinstance(summary, dict) else None
    product = f"{rel}/build_receipt.json" if isinstance(rel, str) else None
    outputs = [product] if product and (ctx.project / product).is_file() else []
    reason = classify_child_exit(code)
    if reason is Reason.ok and not outputs:
        return Outcome(Reason.contract_violation, "build wrote no build receipt")
    return Outcome(reason, f"build exit {code}", outputs)


def _check_run(ctx: Ctx) -> Outcome:
    """``klode-kit kb-check`` on the build the build stage made (WI-10a): the whole-library criteria, judged on the
    build directory before anything points to it; the stage's product is the build's ``check_receipt.json``."""
    receipt = next((o["path"] for o in ctx.upstream("build") if str(o.get("path", "")).endswith("build_receipt.json")),
                   None)
    if receipt is None:
        return Outcome(Reason.contract_violation, "the build stage left no build receipt to check")
    build = (ctx.project / receipt).parent
    code, _ = ctx.child("kb-check", "--kb", str(build.parent.parent), "--build", build.name, "--entry",
                        "pipeline:check", "--project", str(ctx.project))
    product = build / "check_receipt.json"
    outputs = [ctx.rel(product)] if product.is_file() else []
    reason = classify_child_exit(code)
    if reason is Reason.ok and not outputs:
        return Outcome(Reason.contract_violation, "kb-check wrote no check receipt")
    return Outcome(reason, f"kb-check exit {code}", outputs)


def _publish_run(ctx: Ctx) -> Outcome:
    """``klode-kit kb-publish --build B`` for the build the check stage judged (WI-09d): the pointer, the log and the
    registrations. The stage's product is ``pipeline/publish_receipt.json``, the summary ``kb-publish`` writes."""
    receipt = next((o["path"] for o in ctx.upstream("check") if str(o.get("path", "")).endswith("check_receipt.json")),
                   None)
    if receipt is None:
        return Outcome(Reason.contract_violation, "the check stage left no check receipt to publish")
    build = (ctx.project / receipt).parent
    product = ctx.pdir / "publish_receipt.json"
    code, _ = ctx.child("kb-publish", "--kb", str(build.parent.parent), "--build", build.name, "--project",
                        str(ctx.project), "--receipt", str(product))
    outputs = [ctx.rel(product)] if product.is_file() else []
    reason = classify_child_exit(code)
    if reason is Reason.ok and not outputs:
        return Outcome(Reason.contract_violation, "kb-publish wrote no publish receipt")
    return Outcome(reason, f"kb-publish exit {code}", outputs)


def default_registry() -> dict[str, Stage]:
    """The seven stages."""
    return {
        "preflight": Stage("preflight", _preflight_inputs, _preflight_run),
        "ocr": Stage("ocr", _ocr_inputs, _ocr_run),
        "audit": Stage("audit", _audit_inputs, _audit_run),
        "writeback": Stage("writeback", _writeback_inputs, _writeback_run),
        "build": Stage("build", _after("writeback"), _build_run),
        "check": Stage("check", _after("build"), _check_run),
        "publish": Stage("publish", _after("check"), _publish_run),
    }


# --------------------------------------------------------------------------- the kernel

class LockBusy(Exception):
    pass


def _take_lock(work: Path, batch: str) -> int:
    path = work / LOCK_NAME
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        holder = os.pread(fd, 128, 0).decode("utf-8", "replace").split()
        os.close(fd)
        who = f"pid {holder[0]}, batch {holder[1]}" if len(holder) >= 2 else "unknown holder"
        raise LockBusy(f"another pipeline holds the project lock {path} ({who})") from None
    os.pwrite(fd, f"{os.getpid()} {batch}".ljust(63)[:63].encode("utf-8") + b"\n", 0)
    crashpoint("pipeline.lock")
    return fd


def _read_receipt(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or issues("receipt", data):
        return None
    return data


def _store_choices(ctx: Ctx, source: Path) -> None:
    data = source.read_bytes()
    target = ctx.pdir / "choices.json"
    if _file_sha(target) == _sha(data):
        return
    tmp = ctx.pdir / f".choices.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, target)


def _stage_record(ctx: Ctx, name: str, status: str, started: str, inputs_hash: str, config_hash: str,
                  outputs: list[dict], exit_code: int | None) -> dict:
    rec: dict[str, Any] = {"schema": RECEIPT_SCHEMA, "step": name, "status": status, "started": started,
                           "inputs_hash": inputs_hash, "config_hash": config_hash,
                           "config_version": ctx.cfg.config_version, "outputs": outputs, "pid": os.getpid()}
    if status != "running":
        rec["ended"] = _now()
        rec["exit_code"] = exit_code
    return rec


def _hash_outputs(ctx: Ctx, paths: list[str]) -> tuple[list[dict], list[str]]:
    outputs, missing = [], []
    for rel in paths:
        sha = _file_sha(ctx.project / rel)
        if sha is None:
            missing.append(rel)
        else:
            outputs.append({"path": rel, "sha256": sha})
    return outputs, missing


def _finish(code_reason: Reason, ctx: Ctx | None, batch: str | None, stages: list[dict], stdout: TextIO,
            detail: str = "") -> int:
    code = EXIT_CODE[code_reason]
    print(json.dumps({"schema": SCHEMA, "batch_id": batch, "stages": stages, "exit_code": code,
                      "reason": code_reason.value, **({"detail": detail} if detail else {})},
                     ensure_ascii=False, sort_keys=True), file=stdout)
    return code


def run(project: str | os.PathLike, inbox: str | os.PathLike, *, stage: str | None = None,
        choices: str | os.PathLike | None = None, routes: str | None = None,
        launcher: str | os.PathLike | None = None, registry: Mapping[str, Stage] | None = None,
        env: Mapping[str, str] | None = None, out: TextIO | None = None, stdout: TextIO | None = None,
        record_type: str | None = None, audit_type: str | None = None) -> int:
    out = sys.stderr if out is None else out
    stdout = sys.stdout if stdout is None else stdout
    project, inbox = Path(project), Path(inbox)
    last = stage or STAGE_NAMES[-1]
    for given in (record_type, audit_type):
        if given is not None and given not in RECORD_TYPES:
            print(f"pipeline: unknown document type {given!r}; types: {', '.join(RECORD_TYPES)}", file=out)
            return _finish(Reason.usage, None, None, [], stdout, f"unknown type {given!r}")
    if last not in STAGE_NAMES:
        print(f"pipeline: unknown stage {last!r}; stages: {', '.join(STAGE_NAMES)}", file=out)
        return _finish(Reason.usage, None, None, [], stdout, f"unknown stage {last!r}")
    if not project.is_dir():
        print(f"pipeline: no such project directory: {project}", file=out)
        return _finish(Reason.usage, None, None, [], stdout, "no project")
    choices_path = Path(choices) if choices is not None else None
    if choices_path is not None and not choices_path.is_file():
        print(f"pipeline: choices file not found: {choices_path}", file=out)
        return _finish(Reason.usage, None, None, [], stdout, "choices file missing")
    try:
        images, skipped = manifest_mod.scan(inbox)
    except manifest_mod.InboxError as exc:
        print(f"pipeline: {exc}", file=out)
        return _finish(Reason.usage, None, None, [], stdout, str(exc))
    for s in skipped:
        print(f"pipeline: skipped {s['path']} ({s['reason']})", file=out)
    if not images:
        print(f"pipeline: no images in {inbox}", file=out)
        return _finish(Reason.usage, None, None, [], stdout, "no images")
    launcher_path = Path(launcher) if launcher is not None else default_launcher()
    if not launcher_path.is_file() or not os.access(launcher_path, os.X_OK):
        print(f"pipeline: the launcher is missing or not executable: {launcher_path}", file=out)
        return _finish(Reason.environment, None, None, [], stdout, "launcher missing")
    try:
        cfg = config_mod.load(project)
    except config_mod.ConfigError as exc:
        print(f"pipeline: configuration error: {exc}", file=out)
        return _finish(Reason.config, None, None, [], stdout, str(exc))
    shas = [s.sha256 for s in images]
    batch_id = ids.batch_id(shas)
    ctx = Ctx(project, inbox, batch_id, shas, launcher_path, routes, dict(os.environ if env is None else env),
              cfg, out, choices_path, record_type=record_type, last_stage=last, audit_type=audit_type)
    reg = dict(default_registry() if registry is None else registry)
    work = project / "work"
    os.makedirs(work, exist_ok=True)
    crashpoint("pipeline.mkdir")
    try:
        lock = _take_lock(work, batch_id)
    except LockBusy as exc:
        print(f"pipeline: {exc}", file=out)
        return _finish(Reason.lock_busy, ctx, batch_id, [], stdout, str(exc))
    try:
        os.makedirs(ctx.receipts_dir, exist_ok=True)
        crashpoint("pipeline.mkdir")
        if choices_path is not None:
            _store_choices(ctx, choices_path)
        return _run_stages(ctx, reg, last, stdout)
    finally:
        os.close(lock)


def _run_stages(ctx: Ctx, reg: Mapping[str, Stage], last: str, stdout: TextIO) -> int:
    log: list[dict] = []
    config_hash = ctx.cfg.sha256()
    for name in STAGE_NAMES[: STAGE_NAMES.index(last) + 1]:
        st = reg[name]
        if st.run is None or st.inputs is None:
            print(f"pipeline: stage {name} is not implemented in this version; nothing was run for it", file=ctx.out)
            return _finish(Reason.usage, ctx, ctx.batch_id, log, stdout, f"stage {name} not implemented")
        inputs_hash = _digest(st.inputs(ctx))
        path = ctx.receipts_dir / f"{name}.json"
        rec = _read_receipt(path)
        if rec is not None and rec["status"] == "running":
            pid = rec.get("pid")
            if pid != os.getpid() and pid_alive(pid):
                print(f"pipeline: stage {name} is running in pid {pid}; not taking it over", file=ctx.out)
                return _finish(Reason.lock_busy, ctx, ctx.batch_id, log, stdout, f"{name} running in pid {pid}")
            print(f"pipeline: stage {name}: the run of pid {pid} died; running it again", file=ctx.out)
        if is_reusable(rec, inputs_hash=inputs_hash, config_hash=config_hash, base=ctx.project):
            assert rec is not None
            ctx.done[name] = rec
            reason = classify_child_exit(rec["exit_code"])
            how = "reused"
        else:
            how = "ran"
            started = _now()
            receipts.write(path, _stage_record(ctx, name, "running", started, inputs_hash, config_hash, [], None),
                           "receipt")
            crashpoint("pipeline.stage_running")
            try:
                outcome = st.run(ctx)
            except Exception as exc:  # noqa: BLE001 - a crashing stage is recorded, not lost
                outcome = Outcome(Reason.environment, f"{type(exc).__name__}: {exc}")
            outputs, missing = _hash_outputs(ctx, outcome.outputs)
            reason = outcome.reason
            if reason in _CONTINUE and missing:
                reason = Reason.contract_violation
                print(f"pipeline: stage {name}: product missing: {', '.join(missing)}", file=ctx.out)
            status = "done" if reason in _CONTINUE else "failed"
            rec = _stage_record(ctx, name, status, started, inputs_hash, config_hash, outputs, EXIT_CODE[reason])
            receipts.write(path, rec, "receipt")
            crashpoint("pipeline.stage_done")
            ctx.done[name] = rec
        code = EXIT_CODE[reason]
        log.append({"stage": name, "how": how, "status": rec["status"], "exit_code": code})
        print(f"pipeline: {name}: {rec['status']} ({how}, exit {code})", file=ctx.out)
        if reason is Reason.awaiting_confirm and not (name == "audit" and _choices_sha(ctx) is not None):
            if name == "audit":
                adir = newest_audit_rev(ctx.batch_dir)
                if adir is not None:
                    print(f"pipeline: awaiting the human: open {adir / 'confirm.html'}, export choices.json and "
                          f"resume with --choices", file=ctx.out)
            return _finish(Reason.awaiting_confirm, ctx, ctx.batch_id, log, stdout)
        if reason not in _CONTINUE:
            return _finish(reason, ctx, ctx.batch_id, log, stdout)
    return _finish(Reason.ok, ctx, ctx.batch_id, log, stdout)


# --------------------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="klode-kit pipeline", description="Run the pipeline stages up to writeback.")
    parser.add_argument("--batch", required=True, type=Path, help="inbox directory with the images/PDFs, or one PDF")
    parser.add_argument("--project", type=Path, default=None, help="data project directory (default: cwd)")
    parser.add_argument("--stage", choices=STAGE_NAMES, default=None, help="last stage to run (default: all)")
    parser.add_argument("--choices", type=Path, default=None, help="the human's choices.json (second segment)")
    parser.add_argument("--routes", default=None, help="comma-separated subset of the OCR routes")
    parser.add_argument("--type", dest="record_type", choices=RECORD_TYPES, default=None,
                        help="document type, handed to kb-ocr (decides records/<date>-<type>.md)")
    parser.add_argument("--launcher", type=Path, default=None, help="command launcher (default: this repo's bin/klode-kit)")
    parser.add_argument("--resume", action="store_true", help="explicit resume (this is always the behaviour: done "
                                                               "steps whose three hashes match are reused)")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return USAGE_EXIT if exc.code else 0
    project = Path.cwd() if args.project is None else args.project
    try:
        return run(project, args.batch, stage=args.stage, choices=args.choices, routes=args.routes,
                   launcher=args.launcher, record_type=args.record_type)
    except config_mod.ConfigError as exc:
        print(f"pipeline: configuration error: {exc}", file=sys.stderr)
        return exc.exit_code


if __name__ == "__main__":  # pragma: no cover - entry is ``klode-kit pipeline``
    sys.exit(main())
