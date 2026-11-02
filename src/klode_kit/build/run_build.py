"""WI-09b: the 13-step build into the immutable ``kb/<id>/builds/<build_id>/`` (design 02 r3.1 audit_bindings, r3.2
"不可变版本目录", r4.1 stale_candidates; K8; impl-deviations WI-01c / 02b / 02d / 03a / 03b / 00b3b / 08d).

    klode-kit build [--project DIR] [--kb DIR] [--source F --shelf S [--manifest M]] [--desk F [--holdout F]]
                    [--syntheses DIR] [--launcher PATH] [--adopt OLD_KB] [--cards-from DIR] [--desk-routing F]
    exit 0 PASS; 20 content gate (also ``no_verified_records``); 30 contract; 40 environment / configuration /
    the build lock; 50, 60, 61 as the children report them (61: the real-call gate is closed); 2 usage

Three paths, one 13-step receipt (``build_receipt.steps[]``, names from WI-00b1)::

    export · init_kb · ingest · split_manifest · klode_build · dispatch_writers · gate1 · write_card · check_strict ·
    desk_apply · desk_verify · gen_agent · self_check

- **records** (default; the medical shelves ``items`` / ``records``): the snapshot of ``records/_by_id`` is taken once
  at the start (``audit_bindings`` = the **effective** verified revision of each record, r3.1); ``records-to-txt``
  exports exactly that snapshot (de-identified in ``deident.mode``); ``ingest`` and ``split_manifest`` are not
  applicable (one file is one source; ``gen_manifest`` is never called, WI-03a) and PASS with that detail; the cards are
  deterministic (``records-to-l1``, zero model calls); the desk comes from ``gen-desk`` (after ``write-card``, before
  ``desk-build --apply``, WI-03b) and its syntheses are mechanical indexes of the dimension's own anchors.
- **book** (``--source F``): the text is staged in ``src/source/``, ``klode ingest`` -> ``gen-manifest --apply``
  (``--manifest`` for an explicit chapter list) -> ``klode build --stamp`` -> the card writers through the execution
  bridge (``klode-kit bridge run``; replay under the fake ``claude``, a real call stays behind gate G2: exit 61) ->
  the same gates. The desk workers' syntheses are an input (``--syntheses DIR``, ``<dimension>.md``): the workers are
  dispatched by the skill layer; missing ones fail ``desk_apply`` naming the dimensions.
- **adopt** (``--adopt OLD_KB``, WI-34): an existing pre-klode-kit klode library becomes a build. ``klode_kit.build.adopt``
  reads the old tree (never writes it); ``init_kb`` writes the new skeleton from the old library.toml (name,
  description, shelves, guard dirs, bibliography switch; directory names are klode-kit's) and copies every
  ``library/<shelf>/*.txt`` byte for byte; ``klode build --stamp``; the cards are the old ``cards_content/<id>.md``, else
  the part below the scaffold marker of ``library/cards/<id>.md``, else ``--cards-from``; **no writer is called**
  (a card nobody has fails ``dispatch_writers``, exit 20, naming it). ``export`` / ``ingest`` / ``split_manifest`` are
  "not applicable: adopted". The old ``src/desk.json`` + holdout + ``_syntheses`` are carried when its frameworks are
  enabled (``desk-build --verify`` as usual); otherwise the desk is generated from the cards (one piece per card) and
  the first build ends exit 20 ``desk syntheses missing for: ...`` (``desk-tasks``, workers, then a second build with
  ``--syntheses`` and ``--repair``, as on the book path).
- **--desk-routing FILE** (WI-35, D-46; book and adopt paths): ``desk_verify`` runs ``desk-build --verify --routing FILE``:
  the holdout gate is the hit rate of a model's routing (``desk-route-tasks`` writes the prompt for one worker), keyword
  ``diagnose`` is only reported. The file's sha goes into ``build_inputs.json`` (``desk_routing``). Without it the keyword
  gate stands, and a failing ``desk_verify`` names ``desk-route-tasks`` as the next command.
- **--cards-from DIR** (book and adopt paths): ``DIR/<id>.md`` is copied byte for byte instead of dispatching a writer
  (recorded with its sha in ``build_inputs.json``), so in-session workers can write cards outside the bridge.

**Immutable.** Everything is written below ``builds/<build_id>/`` (the only writes outside it: the build lock is an
``flock`` on the ``builds/`` directory itself - no file - and the project's de-identification key at its first use,
``_private/deident_key.json``, a one-time secret and not a build product). The build id is
``b-<UTC>-<sha256(inputs)[:8]>``; a second concurrent build of the project exits 40 and creates nothing. Every step
leaves ``receipts/<step>.json`` (contract ``receipt``); a failure at step k stops the run (later steps are
``NOT_RUN`` and leave no receipt) and ``current`` is never touched (publishing is WI-09d). Finished or failed, the
tree gets ``MANIFEST.sha256`` (``<sha256>  <path>``, sorted, without itself and ``.readers/``) and is made
read-only recursively (``chmod a-w``).

**Private side** (WI-01c): ``src/export_map.jsonl`` and ``src/audit_bindings.json`` hold audited original values; the
build marks ``src/`` with ``src/PRIVATE.md`` and ignore lines in its ``.gitignore``. The receipt records
``deident_mode``. Only ``library/`` and the cards are the de-identified view.

**Never ``--force``** (WI-02b): no child argv carries it (``pipeline.child_argv``), and a ``write_receipt`` with
``forced: true`` fails ``write_card`` even when the command succeeded.

**Self-check** (step 13): the provenance chain of the products (``Layout.for_project``, WI-00b3b) must have no
violation, ``check-published --all`` passes, and ``smoke-mcp`` runs against the real ``klode-mcp``.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, TextIO

from klode_kit import __version__, receipts
from klode_kit import config as config_mod
from klode_kit.build import adopt as adopt_mod
from klode_kit.build import gen_synthesis, records_to_txt
from klode_kit.build.check_synthesis import ANCHOR_RE
from klode_kit.build.write_card import RECEIPT_NAME as WRITE_RECEIPT
from klode_kit.build.write_card import SCHEMA_ID as _WRITE_SCHEMA  # noqa: F401  (the receipt contract of step 8)
from klode_kit.check import doctor
from klode_kit.contract import provenance
from klode_kit.pipeline import EXIT_CODE, Reason, child_argv, classify_child_exit, default_launcher, write_receipt_verdict
from klode_kit.seams import crashpoint

STEPS = ("export", "init_kb", "ingest", "split_manifest", "klode_build", "dispatch_writers", "gate1", "write_card",
         "check_strict", "desk_apply", "desk_verify", "gen_agent", "self_check")
SCHEMA = "klode-kit.build/1"
RECEIPT_SCHEMA = "klode-kit.receipt/1"
BUILD_RECEIPT_SCHEMA = "klode-kit.build_receipt/1"
REPAIR_SCHEMA = "klode-kit.repair/1"  # the input of a repair round (klode_kit.loops, WI-09c)
REPAIR_MARKER = "KLODE-KIT REPAIR ROUND"  # in the writer prompt of a card that failed in the previous round
RECORD_SHELVES = ("items", "records")
MANIFEST_NAME = "MANIFEST.sha256"
CHECK_RECEIPT_NAME = "check_receipt.json"  # the verdict of kb-check about the tree, not part of it (WI-10a)
SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
SHELF_RE = re.compile(r"[A-Za-z0-9._-]+")
USAGE_EXIT = 2
PRIVATE_IGNORES = ("src/export_map.jsonl", "src/audit_bindings.json")
COMMAND_TIMEOUT_S = 1800


# --------------------------------------------------------------------------- build id, manifest (pure helpers)


def _canon(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def inputs_hash(inputs: Mapping[str, Any]) -> str:
    """sha256 of the canonical JSON of the build inputs (key order does not matter)."""
    return hashlib.sha256(_canon(inputs)).hexdigest()


def make_build_id(inputs: Mapping[str, Any], now: datetime) -> str:
    """``b-<UTC yyyymmddTHHMMSSZ>-<sha256(inputs)[:8]>`` (design r3.2)."""
    return f"b-{now.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{inputs_hash(inputs)[:8]}"


def _sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _tracked(root: Path) -> dict[str, Path]:
    """``{relative path: file}`` of every regular file below ``root``, minus the manifest, the root ``check_receipt.json``
    (the verdict of ``kb-check`` about this tree, written after it) and ``.readers/``."""
    out: dict[str, Path] = {}
    for dirpath, dirs, files in os.walk(root):
        if Path(dirpath) == root and ".readers" in dirs:
            dirs.remove(".readers")
        for name in files:
            p = Path(dirpath, name)
            rel = p.relative_to(root).as_posix()
            if rel not in (MANIFEST_NAME, CHECK_RECEIPT_NAME) and p.is_file():
                out[rel] = p
    return dict(sorted(out.items()))


def write_manifest(root: Path) -> None:
    lines = "".join(f"{_sha_file(p)}  {rel}\n" for rel, p in _tracked(root).items())
    (root / MANIFEST_NAME).write_text(lines, encoding="utf-8")


def verify_manifest(build_dir: str | os.PathLike) -> list[str]:
    """Problems of ``MANIFEST.sha256`` against the tree (``[]`` = every file there, unchanged, nothing else)."""
    root = Path(build_dir)
    try:
        rows = [ln.split("  ", 1) for ln in (root / MANIFEST_NAME).read_text(encoding="utf-8").splitlines() if ln]
    except OSError as exc:
        return [f"{MANIFEST_NAME}: cannot read: {exc}"]
    listed = {rel: sha for sha, rel in rows}
    on_disk = _tracked(root)
    problems = []
    for rel, sha in sorted(listed.items()):
        if rel not in on_disk:
            problems.append(f"missing: {rel}")
        elif _sha_file(on_disk[rel]) != sha:
            problems.append(f"changed: {rel}")
    problems += [f"unlisted: {rel}" for rel in on_disk if rel not in listed]
    return problems


def make_readonly(root: Path) -> None:
    """``chmod a-w`` on every file and directory below ``root`` (and ``root``); links are skipped; ``.readers/``
    (reader leases written by the MCP wrapper) stays as it is."""
    for dirpath, dirs, files in os.walk(root, topdown=False):
        if ".readers" in Path(dirpath).relative_to(root).parts:
            continue
        for name in files:
            p = Path(dirpath, name)
            if not p.is_symlink():
                os.chmod(p, p.stat().st_mode & ~0o222)
        os.chmod(dirpath, os.stat(dirpath).st_mode & ~0o222)


# --------------------------------------------------------------------------- small helpers


def _now_iso(clock: Callable[[], datetime]) -> str:
    return clock().astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _utc() -> datetime:
    return datetime.now(timezone.utc)


def slug(text: str) -> str:
    return "-".join(re.findall(r"[a-z0-9]+", text.lower())) or "kb"


def _last_json(text: str) -> dict:
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                data = json.loads(line)
            except ValueError:
                continue
            if isinstance(data, dict):
                return data
    return {}


def rc_reason(rc: int, one: Reason) -> Reason:
    """A child's exit code as a reason: 0 ok, 1 = ``one`` (the step's own failure kind), the closed set as itself,
    anything else (a crash, a signal, 127) environment."""
    if rc == 0:
        return Reason.ok
    if rc == 1:
        return one
    return classify_child_exit(rc)


@dataclass
class Result:
    reason: Reason
    detail: str = ""
    cost_usd: float | None = None
    report: str | None = None  # the failing command's output, kept as reports/<step>.txt for the repair loops (WI-09c)


class LockBusy(Exception):
    pass


@dataclass
class Options:
    source: Path | None = None
    shelf: str = "book"
    manifest: Path | None = None
    desk: Path | None = None
    holdout: Path | None = None
    syntheses: Path | None = None
    repair: Path | None = None  # repair spec of a loop round (klode_kit.loops, WI-09c)
    repair_doc: dict | None = None
    budget_usd: float | None = None  # writer budget of this run; every writer call is reserved before it starts
    adopt: "adopt_mod.OldKb | None" = None  # WI-34: the old klode library this build adopts
    cards_from: Path | None = None  # WI-34: directory of <id>.md cards carried instead of dispatching writers
    desk_routing: Path | None = None  # WI-35: the model routing file that gates the desk holdout (desk_route.py)


# --------------------------------------------------------------------------- the build


class Build:
    """One build: its directory, the 13 step methods and the receipt written after every step."""

    routing_file: Path | None = None  # WI-35: set from Options.desk_routing

    def __init__(self, project: Path, kb_dir: Path, kb_id: str, cfg: Any, opts: Options, launcher: Path,
                 klode: str, env: Mapping[str, str], out: TextIO, clock: Callable[[], datetime]):
        self.project, self.kb_dir, self.kb_id, self.cfg, self.opts = project, kb_dir, kb_id, cfg, opts
        self.launcher, self.klode, self.env, self.out, self.clock = launcher, klode, dict(env), out, clock
        self.routing_file = opts.desk_routing
        self.mode = "adopt" if opts.adopt is not None else "book" if opts.source is not None else "records"
        self.shelves: tuple[str, ...] = (RECORD_SHELVES if self.mode == "records"
                                         else opts.adopt.shelves if opts.adopt is not None else (opts.shelf,))
        self.deident = cfg["deident.mode"] if self.mode == "records" else None
        self.bound: dict[str, dict] = {}
        self.excluded: list[dict] = []
        self.stale: list[dict] = []
        self.audit_bindings: dict[str, dict] = {}
        self.inputs: dict[str, Any] = {}
        self.inputs_digest = ""
        self.build_id = ""
        self.dir: Path = kb_dir
        self.steps: list[dict] = []
        self.started = ""
        self.ended: str | None = None

    # ---- paths
    @property
    def toml(self) -> Path:
        return self.dir / "library.toml"

    @property
    def shelf(self) -> str:
        return self.shelves[0]

    # ---- processes
    def sh(self, argv: Sequence[str]) -> tuple[int, str, str]:
        try:
            proc = subprocess.run(list(argv), env=self.env, cwd=str(self.project), capture_output=True,
                                  timeout=COMMAND_TIMEOUT_S)
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"build: {Path(argv[0]).name}: {type(exc).__name__}: {exc}", file=self.out)
            return 127, "", str(exc)
        text, err = proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace")
        for chunk in (text, err):
            if chunk:
                self.out.write(chunk if chunk.endswith("\n") else chunk + "\n")
        self.out.flush()
        return proc.returncode, text, err

    def kit(self, *args: str) -> tuple[int, str, str]:
        return self.sh(child_argv(self.launcher, *args))

    def klode_run(self, *args: str) -> tuple[int, str, str]:
        return self.sh(child_argv(self.klode, "-c", str(self.toml), *args))

    # ---- receipts
    def _build_receipt(self, status: str) -> dict:
        rec: dict[str, Any] = {
            "schema": BUILD_RECEIPT_SCHEMA, "build_id": self.build_id, "kb_id": self.kb_id, "status": status,
            "started": self.started, "inputs_hash": self.inputs_digest,
            "steps": self.steps + [{"step": n, "status": "NOT_RUN"} for n in STEPS[len(self.steps):]],
            "audit_bindings": self.audit_bindings,
            "excluded": sorted(self.excluded, key=lambda e: e["rec_id"]),
            "stale_candidates": sorted(self.stale, key=lambda e: e["rec_id"]),
        }
        if self.deident:
            rec["deident_mode"] = self.deident
        if self.ended:
            rec["ended"] = self.ended
        return rec

    def save(self, status: str) -> None:
        receipts.write(self.dir / "build_receipt.json", self._build_receipt(status), "build_receipt")

    def _signature(self) -> dict[str, str]:
        sig = {}
        for rel, p in _tracked(self.dir).items():
            if rel != "build_receipt.json" and not rel.startswith("receipts/"):
                sig[rel] = _sha_file(p)
        return sig

    def run_step(self, name: str, fn: Callable[[], Result], prior: list[dict]) -> Result:
        started = _now_iso(self.clock)
        before = self._signature()
        try:
            res = fn()
        except Exception as exc:  # noqa: BLE001 - a crashing step is recorded, not lost
            res = Result(Reason.environment, f"{type(exc).__name__}: {exc}")
        if res.reason is not Reason.ok and res.report:
            (self.dir / "reports").mkdir(exist_ok=True)
            (self.dir / "reports" / f"{name}.txt").write_text(res.report[:200_000], encoding="utf-8")
        after = self._signature()
        outputs = [{"path": rel, "sha256": sha} for rel, sha in after.items() if before.get(rel) != sha]
        ended = _now_iso(self.clock)
        ok = res.reason is Reason.ok
        rec: dict[str, Any] = {
            "schema": RECEIPT_SCHEMA, "step": name, "status": "done" if ok else "failed", "started": started,
            "ended": ended, "inputs_hash": inputs_hash({"build": self.inputs_digest, "step": name,
                                                        "upstream": prior}),
            "config_hash": self.cfg.sha256(), "config_version": self.cfg.config_version, "outputs": outputs,
            "exit_code": EXIT_CODE[res.reason], "pid": os.getpid(),
        }
        if res.cost_usd is not None:
            rec["cost_usd"] = res.cost_usd
        receipts.write(self.dir / "receipts" / f"{name}.json", rec, "receipt")
        entry = {"step": name, "status": "PASS" if ok else "FAIL", "receipt": f"receipts/{name}.json",
                 "started": started, "ended": ended}
        if res.detail:
            entry["detail"] = res.detail[:500]
        self.steps.append(entry)
        self.save("FAIL")
        prior.append({"step": name, "outputs": outputs})
        return res

    # ---- the steps
    def step_export(self) -> Result:
        if self.mode == "adopt":
            old = self.opts.adopt
            assert old is not None
            if not old.sources:
                return Result(Reason.content_gate, f"no sources: {old.root} has no <library>/<shelf>/*.txt")
            return Result(Reason.ok, f"not applicable: adopted from {old.root} ({len(old.sources)} sources, "
                                     f"{len(old.shelves)} shelves)")
        if self.mode == "book":
            src = self.opts.source
            assert src is not None
            dest = self.dir / "src" / "source" / src.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
            return Result(Reason.ok, f"staged {src.name}")
        if not self.bound:
            return Result(Reason.content_gate, "no_verified_records")
        (self.dir / "src").mkdir(parents=True, exist_ok=True)
        snap = self.dir / "src" / "audit_bindings.json"
        snap.write_text(json.dumps(self.bound, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        rc, text, _ = self.kit("records-to-txt", "--project", str(self.project), "--out", str(self.dir),
                               "--kb-id", self.kb_id, "--bindings", str(snap), "--deident", str(self.deident))
        reason = rc_reason(rc, Reason.contract_violation)
        if reason is not Reason.ok:
            return Result(reason, f"records-to-txt exit {rc}")
        summary = _last_json(text)
        self.audit_bindings = dict(summary.get("audit_bindings", {}))
        extra = [{"rec_id": e["rec_id"], "reason": _reason_text(e["reason"], e.get("status"))}
                 for e in summary.get("excluded", [])]
        self.excluded += extra
        if not self.audit_bindings:
            return Result(Reason.content_gate, "no_verified_records")
        return Result(Reason.ok, f"{len(self.audit_bindings)} records, {len(self.excluded)} excluded")

    def step_init_kb(self) -> Result:
        shelf_args = [a for s in self.shelves for a in ("--shelf", s)]
        rc, _, _ = self.sh(child_argv(self.klode, "init", *shelf_args, str(self.dir)))
        reason = rc_reason(rc, Reason.environment)
        if reason is not Reason.ok:
            return Result(reason, f"klode init exit {rc}")
        name, description = self.kb_id, f"Library {self.kb_id} built by klode-kit"
        if self.mode == "adopt":
            old = self.opts.adopt
            assert old is not None
            name = _one_line(old.name) or name
            description = _one_line(old.description) or description
        rc, _, _ = self.kit("init-kb", str(self.dir), "--id", self.kb_id, *shelf_args, "--name", name,
                            "--description", description)
        reason = rc_reason(rc, Reason.contract_violation)
        if reason is not Reason.ok:
            return Result(reason, f"init-kb exit {rc}")
        if self.mode == "records":
            self._mark_private()
        if self.mode == "adopt":
            return self._adopt_library()
        return Result(Reason.ok, ", ".join(self.shelves))

    def _adopt_library(self) -> Result:
        """Settings of the old library.toml and its sources (byte for byte, sha checked against the inputs)."""
        old = self.opts.adopt
        assert old is not None
        self.toml.write_text(adopt_mod.patch_toml(self.toml.read_text(encoding="utf-8"), old), encoding="utf-8")
        want = self.inputs["adopt"]["sources"]
        for rel, src in sorted(old.sources.items()):
            dest = self.dir / "library" / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
            if adopt_mod.sha_file(dest) != want.get(rel):
                return Result(Reason.contract_violation, f"source changed while it was adopted: {rel}")
        bib = adopt_mod.bibliography_text(old)
        if bib is not None:
            (self.dir / "library" / "BIBLIOGRAPHY.md").write_bytes(bib.encode("utf-8"))
        return Result(Reason.ok, f"{', '.join(self.shelves)}; {len(old.sources)} sources copied")

    def _mark_private(self) -> None:
        (self.dir / "src" / "PRIVATE.md").write_text(
            "# Private side of this build\n\n`export_map.jsonl` and `audit_bindings.json` hold the audited original\n"
            "values (the de-identification transforms record what they replaced). Only `library/` and the cards\n"
            "are the de-identified view: never share or publish this directory.\n", encoding="utf-8")
        gi = self.dir / ".gitignore"
        text = gi.read_text(encoding="utf-8") if gi.is_file() else ""
        have = set(text.splitlines())
        add = [ln for ln in PRIVATE_IGNORES if ln not in have]
        if add:
            gi.write_text(text + ("" if not text or text.endswith("\n") else "\n")
                          + "# klode-kit: audited originals (private side)\n" + "\n".join(add) + "\n",
                          encoding="utf-8")

    def step_ingest(self) -> Result:
        if self.mode == "adopt":
            return Result(Reason.ok, "not applicable: adopted (the sources were copied verbatim at init_kb)")
        if self.mode == "records":
            return Result(Reason.ok, "not applicable: items / records shelves hold one file per source")
        src = self.dir / "src" / "source" / self.opts.source.name  # type: ignore[union-attr]
        rc, _, _ = self.klode_run("ingest", str(src), "--shelf", self.shelf, "--id", f"_whole-{self.shelf}",
                                  "--format", "txt")
        return Result(rc_reason(rc, Reason.contract_violation), f"klode ingest exit {rc}" if rc else "ingested")

    def step_split(self) -> Result:
        if self.mode == "adopt":
            return Result(Reason.ok, "not applicable: adopted (one source file, one card, as in the old library)")
        if self.mode == "records":
            return Result(Reason.ok, "not applicable: items / records shelves are not chapter-split")
        args = ["gen-manifest", str(self.dir), "--shelf", self.shelf, "--apply"]
        if self.opts.manifest is not None:
            args += ["--manifest", str(self.opts.manifest)]
        rc, _, _ = self.kit(*args)
        return Result(rc_reason(rc, Reason.contract_violation), f"gen-manifest exit {rc}" if rc else "split")

    def step_klode_build(self) -> Result:
        rc, _, _ = self.klode_run("build", "--stamp")
        return Result(rc_reason(rc, Reason.environment), f"klode build exit {rc}" if rc else "stamped")

    def step_dispatch(self) -> Result:
        if self.mode == "records":
            rc, _, _ = self.kit("records-to-l1", str(self.dir))
            reason = rc_reason(rc, Reason.content_gate)
            if reason is Reason.ok:
                self._overlay_fixed()
            return Result(reason, f"records-to-l1 exit {rc}" if rc else "deterministic cards")
        cards = sorted(p.stem for p in (self.dir / "library" / "cards").glob("*.md") if p.stem != "INDEX")
        if not cards:
            return Result(Reason.contract_violation, "klode build left no card scaffold")
        repair = self.opts.repair_doc or {}
        redo: dict[str, list[str]] = {c: list(v) for c, v in (repair.get("redispatch") or {}).items()}
        carried = self._carry_cards(cards, redo)
        placed = self._place_cards_from(cards, carried, redo)
        adopted = self._place_adopted([c for c in cards if c not in carried and c not in placed and c not in redo])
        need = [c for c in cards if c not in carried and c not in placed and c not in adopted]
        if self.mode == "adopt" and need:  # no writer in adopt mode: the card has to come from somewhere
            return Result(Reason.content_gate, f"cards missing in {self.opts.adopt.root}: {', '.join(need)} "  # type: ignore[union-attr]
                                               "(write them outside the build and pass --cards-from DIR)")
        if not need:
            self._overlay_fixed()
            return Result(Reason.ok, self._placed_detail(adopted, carried, placed, repair))
        guide = (resources.files("klode_kit") / "prompts" / "write_card.md").read_text(encoding="utf-8")
        tasks = []
        for cid in need:
            instructions = (f"{guide}\n\nCard id: {cid}\nSource text: {_source_of(self.dir, cid)}\n"
                            f"Write cards_content/{cid}.md (aliases line, ## Thin, ## Full).")
            if cid in redo:
                self._place_previous_card(cid)
                lines = "\n".join(f"- {t}" for t in redo[cid]) or "- (no detail was reported)"
                instructions += (f"\n\n{REPAIR_MARKER} {repair.get('round')}: cards_content/{cid}.md holds the previous "
                                 f"version of this card. It failed these checks - rewrite the card so that none of them "
                                 f"is left, and keep everything that was fine:\n{lines}")
            tasks.append({"task_id": cid, "build_dir": str(self.dir), "card": f"cards_content/{cid}.md",
                          "instructions": instructions})
        rdir = self.dir / "receipts"
        rdir.mkdir(parents=True, exist_ok=True)
        tasks_file, run_file = rdir / "writer_tasks.json", rdir / "writer_run.json"
        tasks_file.write_text(json.dumps({"tasks": tasks}, ensure_ascii=False, indent=1), encoding="utf-8")
        args = ["bridge", "run", "--tasks", str(tasks_file), "--receipt", str(run_file), "--project", str(self.project)]
        if self.opts.budget_usd is not None:
            args += ["--budget-usd", repr(float(self.opts.budget_usd))]
        rc, _, _ = self.kit(*args)
        cost = None
        if run_file.is_file():
            cost = float(json.loads(run_file.read_text(encoding="utf-8")).get("cost_usd", 0.0))
        reason = rc_reason(rc, Reason.content_gate)
        if reason is not Reason.ok and _auth_failure(run_file):
            return Result(Reason.auth, "claude is not authenticated (writers did not start work)", cost)
        if reason is Reason.ok:
            missing = [c for c in cards if not (self.dir / "cards_content" / f"{c}.md").is_file()]
            if missing:
                return Result(Reason.content_gate, f"writers left no card: {', '.join(missing)}", cost)
            self._overlay_fixed()
        done = f"{len(need)} cards written, {len(carried)} carried over"
        if placed:
            done += f", {len(placed)} carried from {self.opts.cards_from}"
        return Result(reason, f"bridge exit {rc}" if rc else done, cost)

    def _placed_detail(self, adopted: Sequence[str], carried: Sequence[str], placed: Sequence[str],
                       repair: Mapping[str, Any]) -> str:
        parts = []
        if adopted:
            parts.append(f"{len(adopted)} cards adopted from {self.opts.adopt.root}")  # type: ignore[union-attr]
        if carried:
            parts.append(f"{len(carried)} cards carried over from {repair.get('from_build')}")
        if placed:
            parts.append(f"{len(placed)} cards carried from {self.opts.cards_from}")
        return "; ".join(parts)

    def _place_cards_from(self, cards: Sequence[str], carried: Sequence[str], redo: Mapping[str, Any]) -> list[str]:
        """``--cards-from``: ``DIR/<id>.md`` byte for byte for every card the repair carry-over did not place. A card to
        be redone is taken only when the file differs from the version that failed (else it still needs a writer)."""
        src_dir = self.opts.cards_from
        placed: list[str] = []
        if src_dir is None:
            return placed
        prev_dir = self._from_build()
        for cid in cards:
            src = src_dir / f"{cid}.md"
            if cid in carried or not src.is_file():
                continue
            prev = prev_dir / "cards_content" / f"{cid}.md" if prev_dir else None
            if cid in redo and prev is not None and prev.is_file() and prev.read_bytes() == src.read_bytes():
                continue
            self._put_card(cid, src.read_bytes())
            placed.append(cid)
        return placed

    def _place_adopted(self, cards: Sequence[str]) -> list[str]:
        """Adopt mode: the cards the old library has (``cards_content`` first, else below the scaffold marker)."""
        old = self.opts.adopt
        adopted: list[str] = []
        if old is None:
            return adopted
        for cid in cards:
            got = adopt_mod.card_source(old, cid)
            if got is not None:
                self._put_card(cid, got[1])
                adopted.append(cid)
        return adopted

    def _put_card(self, cid: str, data: bytes) -> None:
        dest = self.dir / "cards_content" / f"{cid}.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)

    # ---- repair rounds (klode_kit.loops, WI-09c)
    def _from_build(self) -> Path | None:
        doc = self.opts.repair_doc
        return self.project / doc["from_build"] if doc else None

    def _carry_cards(self, cards: Sequence[str], redo: Mapping[str, Any]) -> list[str]:
        """Cards of the build the repair round starts from that need no writer: copied over byte for byte."""
        src_dir = self._from_build()
        carried: list[str] = []
        for cid in cards:
            src = src_dir / "cards_content" / f"{cid}.md" if src_dir else None
            if src is not None and cid not in redo and src.is_file():
                dest = self.dir / "cards_content" / f"{cid}.md"
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dest)
                carried.append(cid)
        return carried

    def _place_previous_card(self, cid: str) -> None:
        src_dir = self._from_build()
        src = src_dir / "cards_content" / f"{cid}.md" if src_dir else None
        if src is not None and src.is_file():
            dest = self.dir / "cards_content" / f"{cid}.md"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)

    def _overlay_fixed(self) -> None:
        """Cards the L3 modifier (``fix-anchors``) rewrote replace the generated / carried ones."""
        for cid, rel in sorted(((self.opts.repair_doc or {}).get("fixed") or {}).items()):
            src = self.project / rel
            if src.is_file():
                dest = self.dir / "cards_content" / f"{cid}.md"
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dest)

    def step_gate1(self) -> Result:
        rc, text, _ = self.kit("check-cards", "--all", "--kb", str(self.dir), "--project", str(self.project), "--json")
        reason = rc_reason(rc, Reason.content_gate)
        return Result(reason, f"check-cards exit {rc}" if rc else "gate 1 pass", report=text if rc else None)

    def step_write_card(self) -> Result:
        rc, _, _ = self.kit("write-card", str(self.dir), "--project", str(self.project))
        reason = rc_reason(rc, Reason.content_gate)
        if reason is not Reason.ok:
            return Result(reason, f"write-card exit {rc}")
        try:
            doc = json.loads((self.dir / WRITE_RECEIPT).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return Result(Reason.contract_violation, "write-card left no readable write receipt")
        if write_receipt_verdict(doc) != "PASS":
            return Result(Reason.content_gate, f"write receipt: status {doc.get('status')}, forced {doc.get('forced')}")
        return Result(Reason.ok, f"{len(doc.get('written', []))} cards published")

    def step_strict(self) -> Result:
        rc, text, err = self.klode_run("check", "--strict")
        last = (text.strip().splitlines() or [""])[-1]
        if rc == 0 and "0 errors, 0 warning(s)" in last:
            return Result(Reason.ok, last)
        return Result(Reason.content_gate, f"klode check --strict exit {rc}: {last}"[:300], report=text + err)

    def step_desk_apply(self) -> Result:
        (self.dir / "src").mkdir(parents=True, exist_ok=True)
        gen = ["gen-desk", str(self.dir), "--project", str(self.project)]
        if self.mode == "adopt" and self.opts.desk is None:
            self._write_adopt_manifests()  # the generic desk is planned from chapters-*.json: one piece per card
        if self.opts.desk is not None:
            shutil.copyfile(self.opts.desk, self.dir / "src" / "desk.json")
            hold = self.opts.holdout or self.opts.desk.with_name("holdout.jsonl")
            if hold.is_file():
                shutil.copyfile(hold, self.dir / "src" / "holdout.jsonl")
            gen += ["--desk", "src/desk.json"]
        rc, _, _ = self.kit(*gen)
        reason = rc_reason(rc, Reason.contract_violation)
        if reason is not Reason.ok:
            return Result(reason, f"gen-desk exit {rc}")
        rc, _, _ = self.kit("desk-build", str(self.dir), "--apply", "--project", str(self.project))
        reason = rc_reason(rc, Reason.contract_violation)
        if reason is not Reason.ok:
            return Result(reason, f"desk-build --apply exit {rc}")
        missing = self._place_syntheses()
        if missing:
            return Result(Reason.content_gate, "desk syntheses missing for: " + ", ".join(missing))
        rc, _, _ = self.kit("check-synthesis", str(self.dir))
        return Result(rc_reason(rc, Reason.content_gate), f"check-synthesis exit {rc}" if rc else "syntheses pass")

    def _write_adopt_manifests(self) -> None:
        """``src/chapters-<shelf>.json`` for a library that came without a desk: every card is a piece (gen-desk reads
        only ``pieces[].id`` / ``title``), in shelf order, cards by id."""
        by_shelf: dict[str, list[dict]] = {s: [] for s in self.shelves}
        for path in sorted((self.dir / "library" / "cards").glob("*.md")):
            if path.stem == "INDEX":
                continue
            text = path.read_text(encoding="utf-8")
            parts = _source_of(self.dir, path.stem).split("/")
            shelf = parts[1] if len(parts) > 2 and parts[1] in by_shelf else None
            title = re.search(r"^# (.+)$", text, re.M)
            if shelf is not None:
                by_shelf[shelf].append({"id": path.stem, "title": title.group(1).strip() if title else path.stem})
        (self.dir / "src").mkdir(parents=True, exist_ok=True)
        for shelf, pieces in by_shelf.items():
            if pieces:
                doc = {"shelf": shelf, "whole": "", "citation": self.opts.adopt.name,  # type: ignore[union-attr]
                       "pieces": pieces, "generated_by": {"tool": "klode-kit build --adopt"}}
                (self.dir / "src" / f"chapters-{shelf}.json").write_text(
                    json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _place_syntheses(self) -> list[str]:
        """Provided syntheses are copied in; records builds fill the rest mechanically; returns missing dimensions."""
        spec = json.loads((self.dir / "src" / "desk.json").read_text(encoding="utf-8"))
        dims = [d["dimension"] for d in spec["dimensions"]]
        syn = self.dir / "library" / "frameworks" / "_syntheses"
        syn.mkdir(parents=True, exist_ok=True)
        have = set()
        if self.opts.syntheses is not None:
            for dim in dims:
                src = self.opts.syntheses / f"{dim}.md"
                if src.is_file():
                    shutil.copyfile(src, syn / f"{dim}.md")
                    have.add(dim)
        if self.mode == "records":
            gen_synthesis.write(self.dir, [d for d in spec["dimensions"] if d["dimension"] not in have])
            return []
        return [d for d in dims if d not in have]

    def step_desk_verify(self) -> Result:
        argv = ["desk-build", str(self.dir), "--verify", "--project", str(self.project)]
        if self.routing_file is not None:
            argv += ["--routing", str(self.routing_file)]
        rc, _, _ = self.kit(*argv)
        if rc:
            detail = f"desk-build --verify exit {rc}"
            if rc == 1 and self.routing_file is None:  # WI-35: keyword routing cannot accept an honest holdout
                try:
                    shown = self.dir.relative_to(self.project).as_posix()
                except ValueError:
                    shown = str(self.dir)
                detail += (f"; if the holdout is made of new phrasings, accept the desk by model routing: "
                           f"klode-kit desk-route-tasks --build {shown} --out DIR, dispatch one worker with the "
                           f"prompt, then build again with --desk-routing DIR/routing.json")
            return Result(rc_reason(rc, Reason.content_gate), detail)
        # check_strict (step 9) runs before the desk syntheses exist: run it again so what they introduce is seen (P16)
        src, text, err = self.klode_run("check", "--strict")
        last = (text.strip().splitlines() or [""])[-1]
        if src == 0 and "0 errors, 0 warning(s)" in last:
            return Result(Reason.ok, f"desk verified; strict after desk: {last}")
        return Result(Reason.content_gate, f"strict after desk: klode check --strict exit {src}: {last}"[:300],
                      report=text + err)

    def step_gen_agent(self) -> Result:
        out = self.dir / "agent" / f"{self.kb_id}-klode-expert.md"
        root = self.kb_dir.relative_to(self.project).as_posix()
        rc, _, _ = self.kit("gen-agent", str(self.dir), "--out", str(out), "--build-id", self.build_id,
                            "--kb-root", f"{root}/current")
        return Result(rc_reason(rc, Reason.content_gate), f"gen-agent exit {rc}" if rc else out.name)

    def step_self_check(self) -> Result:
        if self.mode == "records":
            violations = provenance.validate_build(self.project, self.dir)  # scoped to the bound batches (WI-37)
            if violations:
                codes = sorted({v.code for v in violations})
                return Result(Reason.contract_violation, f"provenance: {len(violations)} violation(s): {', '.join(codes)}; "
                                                         f"first at {violations[0].location}"[:300])
        rc, _, _ = self.kit("check-published", "--all", "--kb", str(self.dir), "--project", str(self.project))
        reason = rc_reason(rc, Reason.content_gate)
        if reason is not Reason.ok:
            return Result(reason, f"check-published exit {rc}")
        card, phrase = self._smoke_target()
        if card is None:
            return Result(Reason.contract_violation, "no card with an anchor to smoke-test")
        rc, _, _ = self.kit("smoke-mcp", str(self.dir), "--card", card, "--real", phrase)
        return Result(rc_reason(rc, Reason.content_gate), f"smoke-mcp exit {rc}" if rc else f"smoke {card}")

    def _smoke_target(self) -> tuple[str | None, str]:
        for path in sorted((self.dir / "library" / "cards").glob("*.md")):
            if path.stem == "INDEX":
                continue
            found = ANCHOR_RE.findall(path.read_text(encoding="utf-8"))
            if found:
                return path.stem, found[0]
        return None, ""

    def steps_table(self) -> list[tuple[str, Callable[[], Result]]]:
        return [("export", self.step_export), ("init_kb", self.step_init_kb), ("ingest", self.step_ingest),
                ("split_manifest", self.step_split), ("klode_build", self.step_klode_build),
                ("dispatch_writers", self.step_dispatch), ("gate1", self.step_gate1),
                ("write_card", self.step_write_card), ("check_strict", self.step_strict),
                ("desk_apply", self.step_desk_apply), ("desk_verify", self.step_desk_verify),
                ("gen_agent", self.step_gen_agent), ("self_check", self.step_self_check)]


AUTH_RE = re.compile(r"not logged in|please run /login|invalid api key|authentication|unauthori[sz]ed|\b401\b", re.I)


def _auth_failure(run_file: Path) -> bool:
    """The bridge receipt shows an authentication failure (an environment-class result: no loop can repair it)."""
    try:
        doc = json.loads(run_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    for task in doc.get("tasks", []):
        texts = [str(task.get("error") or "")] + [f"{a.get('error') or ''} {a.get('stderr_tail') or ''}"
                                                  for a in task.get("attempts_log", [])]
        if any(AUTH_RE.search(t) for t in texts):
            return True
    return False


def _one_line(text: str) -> str:
    return re.sub(r"[\x00-\x1f\x7f]+", " ", text).strip()


def _reason_text(reason: str, status: Any) -> str:
    return f"{reason}:{status}" if reason == "not_verified" and status else reason


def _source_of(build: Path, cid: str) -> str:
    text = (build / "library" / "cards" / f"{cid}.md").read_text(encoding="utf-8")
    m = re.search(r"^file: (.+)$", text, re.M)
    return m.group(1).strip() if m else f"library/*/{cid}.txt"


# --------------------------------------------------------------------------- snapshot of the records


def _page_reason(project: Path, rec: str, status: Any) -> str:
    """Why a record is not in the build: the page-level uncertain ``<engine>:<cause>`` of its newest candidate
    (r2.4), else ``not_verified:<status>``."""
    try:
        front = records_to_txt.read_front(project / "records" / "_by_id" / f"{rec}.md")
    except records_to_txt.ExportError:
        return "not_verified"
    cands = front.get("candidates") or []
    for u in (cands[-1].get("uncertain") if cands else None) or []:
        if isinstance(u, dict) and u.get("scope") == "page" and u.get("reason"):
            return f"{u['engine']}:{u['reason']}" if u.get("engine") else str(u["reason"])
    return _reason_text("not_verified", status)


def take_snapshot(project: Path) -> tuple[dict[str, dict], list[dict], list[dict]]:
    """``(bound, excluded, stale_candidates)`` of ``records/_by_id`` at this moment (design r3.1, r4.1)."""
    bound, not_verified = records_to_txt.snapshot(project)
    excluded = [{"rec_id": e["rec_id"], "reason": _page_reason(project, e["rec_id"], e.get("status"))}
                for e in not_verified]
    stale = []
    for rec, b in sorted(bound.items()):
        front = records_to_txt.read_front(project / "records" / "_by_id" / f"{rec}.md")
        newer = [c for c in front.get("candidates", []) if isinstance(c.get("rev"), int) and c["rev"] > b["rev"]]
        if newer:
            top = max(newer, key=lambda c: c["rev"])
            stale.append({"rec_id": rec, "candidate_rev": top["rev"], "reason": f"candidate_{top.get('status')}"})
    return bound, excluded, stale


def _items_table_sha() -> str:
    return hashlib.sha256((resources.files("klode_kit.config") / "items.yaml").read_bytes()).hexdigest()


def _file_sha(path: Path | None) -> str | None:
    return _sha_file(path) if path is not None and path.is_file() else None


def _lock(builds: Path) -> int:
    fd = os.open(builds, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise LockBusy(f"another build holds the lock of {builds}") from None
    return fd


# --------------------------------------------------------------------------- run


def _finish(stdout: TextIO, code: int, reason: str, **extra: Any) -> int:
    print(json.dumps({"schema": SCHEMA, "exit_code": code, "reason": reason, **extra}, ensure_ascii=False,
                     sort_keys=True), file=stdout)
    return code


def _usage(out: TextIO, stdout: TextIO, message: str) -> int:
    print(f"build: {message}", file=out)
    return _finish(stdout, USAGE_EXIT, "usage", detail=message)


def default_kb(project: Path) -> Path:
    existing = sorted(p for p in (project / "kb").iterdir() if p.is_dir()) if (project / "kb").is_dir() else []
    return existing[0] if len(existing) == 1 else project / "kb" / slug(project.resolve().name)


def run(project: str | os.PathLike, *, kb: str | os.PathLike | None = None, source: str | os.PathLike | None = None,
        shelf: str = "book", manifest: str | os.PathLike | None = None, desk: str | os.PathLike | None = None,
        holdout: str | os.PathLike | None = None, syntheses: str | os.PathLike | None = None,
        launcher: str | os.PathLike | None = None, env: Mapping[str, str] | None = None, out: TextIO | None = None,
        stdout: TextIO | None = None, clock: Callable[[], datetime] | None = None,
        repair: str | os.PathLike | None = None, budget_usd: float | None = None,
        adopt: str | os.PathLike | None = None, adopt_shelves: Sequence[str] | None = None,
        cards_from: str | os.PathLike | None = None, desk_routing: str | os.PathLike | None = None) -> int:
    out = sys.stderr if out is None else out
    stdout = sys.stdout if stdout is None else stdout
    clock = clock or _utc
    env = dict(os.environ if env is None else env)
    project = Path(project)
    if not project.is_dir():
        return _usage(out, stdout, f"no such project directory: {project}")
    project = project.resolve()
    files = {"source": source, "manifest": manifest, "desk": desk, "holdout": holdout, "desk-routing": desk_routing}
    paths: dict[str, Path | None] = {}
    for label, value in files.items():
        p = Path(value) if value is not None else None
        if p is not None and not p.is_file():
            return _usage(out, stdout, f"--{label}: not a file: {p}")
        paths[label] = p.resolve() if p is not None else None
    syn = Path(syntheses).resolve() if syntheses is not None else None
    if syn is not None and not syn.is_dir():
        return _usage(out, stdout, f"--syntheses: not a directory: {syn}")
    if paths["manifest"] is not None and paths["source"] is None:
        return _usage(out, stdout, "--manifest needs --source")
    old: adopt_mod.OldKb | None = None
    if adopt is not None:
        if paths["source"] is not None:
            return _usage(out, stdout, "--adopt cannot be combined with --source (the sources are the old library's)")
        try:
            old = adopt_mod.select(adopt_mod.read_old(adopt), adopt_shelves)
        except adopt_mod.AdoptError as exc:
            return _usage(out, stdout, str(exc) if str(exc).startswith("--adopt") else f"--adopt: {exc}")
    elif adopt_shelves is not None:
        return _usage(out, stdout, "--adopt-shelves needs --adopt")
    cards_dir: Path | None = None
    if cards_from is not None:
        cards_dir = Path(cards_from).resolve()
        if not cards_dir.is_dir():
            return _usage(out, stdout, f"--cards-from: not a directory: {cards_dir}")
        if paths["source"] is None and old is None:
            return _usage(out, stdout, "--cards-from needs --source or --adopt (the records path writes no cards by hand)")
    repair_path = Path(repair).resolve() if repair is not None else None
    repair_doc: dict | None = None
    if repair_path is not None:
        try:
            repair_doc = json.loads(repair_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return _usage(out, stdout, f"--repair: cannot read {repair_path}: {exc}")
        if (not isinstance(repair_doc, dict) or repair_doc.get("schema") != REPAIR_SCHEMA
                or not isinstance(repair_doc.get("from_build"), str)):
            return _usage(out, stdout, f"--repair: not a {REPAIR_SCHEMA} document: {repair_path}")
    if budget_usd is not None and (isinstance(budget_usd, bool) or not isinstance(budget_usd, (int, float))
                                   or not 0 < budget_usd < float("inf")):
        return _usage(out, stdout, f"--budget-usd must be a number > 0: {budget_usd!r}")
    if not SHELF_RE.fullmatch(shelf):
        return _usage(out, stdout, f"--shelf must be a plain name: {shelf!r}")
    kb_dir = (Path(kb) if Path(kb).is_absolute() else project / kb) if kb is not None else default_kb(project)
    kb_dir = kb_dir.parent.resolve() / kb_dir.name
    try:
        kb_dir.relative_to(project)
    except ValueError:
        return _usage(out, stdout, f"--kb must be inside the project: {kb_dir}")
    if not SLUG_RE.fullmatch(kb_dir.name):
        return _usage(out, stdout, f"the library directory name must be a klode slug (a-z, 0-9, -): {kb_dir.name!r}")
    if old is not None and (old.root.resolve() == kb_dir or kb_dir in old.root.resolve().parents):
        return _usage(out, stdout, f"--adopt: the old library is the target library itself: {old.root}")
    if old is not None:  # the old library's desk, holdout and syntheses (only when its frameworks are on) unless given
        if old.frameworks_enabled and old.desk is not None and paths["desk"] is None:
            paths["desk"] = old.desk.resolve()
            if paths["holdout"] is None and old.holdout is not None:
                paths["holdout"] = old.holdout.resolve()
            if syn is None and old.syntheses_dir is not None:
                syn = old.syntheses_dir.resolve()
    launcher_path = Path(launcher) if launcher is not None else default_launcher()
    if not launcher_path.is_file() or not os.access(launcher_path, os.X_OK):
        print(f"build: the launcher is missing or not executable: {launcher_path}", file=out)
        return _finish(stdout, EXIT_CODE[Reason.environment], Reason.environment.value, detail="launcher missing")
    klode = shutil.which("klode", path=env.get("PATH"))
    if klode is None:
        print("build: klode 0.5.x is required on PATH", file=out)
        return _finish(stdout, EXIT_CODE[Reason.environment], Reason.environment.value, detail="klode missing")
    try:
        cfg = config_mod.load(project)
    except config_mod.ConfigError as exc:
        print(f"build: configuration error: {exc}", file=out)
        return _finish(stdout, EXIT_CODE[Reason.config], Reason.config.value, detail=str(exc))

    builds = kb_dir / "builds"
    os.makedirs(builds, exist_ok=True)
    try:
        lock = _lock(builds)
    except LockBusy as exc:
        print(f"build: {exc}", file=out)
        return _finish(stdout, EXIT_CODE[Reason.lock_busy], Reason.lock_busy.value, detail=str(exc))
    try:
        opts = Options(paths["source"], shelf, paths["manifest"], paths["desk"], paths["holdout"], syn, repair_path,
                       repair_doc, budget_usd, old, cards_dir, paths["desk-routing"])
        b = Build(project, kb_dir, kb_dir.name, cfg, opts, launcher_path, klode, env, out, clock)
        return _run_locked(b, builds, stdout)
    finally:
        os.close(lock)


def _run_locked(b: Build, builds: Path, stdout: TextIO) -> int:
    project, opts = b.project, b.opts
    if b.mode == "records":
        b.bound, b.excluded, b.stale = take_snapshot(project)
    syn_files = (sorted(p for p in opts.syntheses.glob("*.md") if not p.name.startswith("_"))
                 if opts.syntheses is not None else [])
    b.inputs = {
        "mode": b.mode,
        "records": sorted(f"{r}:{v['rev']}" for r, v in b.bound.items()),
        "items_table": _items_table_sha(),
        "desk": _file_sha(opts.desk), "holdout": _file_sha(opts.holdout), "source": _file_sha(opts.source),
        "manifest": _file_sha(opts.manifest), "syntheses": [_sha_file(p) for p in syn_files],
        "config_sha256": b.cfg.sha256(), "config_version": b.cfg.config_version,
        "plugin_version": __version__, "klode_version": doctor.klode_version(b.klode, b.env),
        "deident_mode": b.deident,
    }
    if opts.adopt is not None:
        b.inputs["adopt"] = adopt_mod.inputs(opts.adopt)
    if opts.cards_from is not None:
        b.inputs["cards_from"] = {p.stem: _sha_file(p) for p in sorted(opts.cards_from.glob("*.md")) if p.is_file()}
    if opts.desk_routing is not None:
        b.inputs["desk_routing"] = _sha_file(opts.desk_routing)
    if opts.repair is not None:
        b.inputs["repair"] = _sha_file(opts.repair)
    b.inputs_digest = inputs_hash(b.inputs)
    now = b.clock()
    for _ in range(40):
        b.build_id = make_build_id(b.inputs, now)
        if not (builds / b.build_id).exists():
            break
        time.sleep(0.25)  # the same inputs within the same second: the next second is another id
        now = b.clock()
    else:
        return _finish(stdout, EXIT_CODE[Reason.environment], Reason.environment.value, detail="build id taken")
    b.dir = builds / b.build_id
    os.mkdir(b.dir)
    b.started = _now_iso(b.clock)
    (b.dir / "build_inputs.json").write_text(json.dumps(b.inputs, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                                             encoding="utf-8")
    b.save("FAIL")
    prior: list[dict] = []
    failed: Result | None = None
    for name, fn in b.steps_table():
        res = b.run_step(name, fn, prior)
        print(f"build: {name}: {'PASS' if res.reason is Reason.ok else 'FAIL'}"
              f"{' - ' + res.detail if res.detail else ''}", file=b.out)
        if res.reason is not Reason.ok:
            failed = res
            break
    b.ended = _now_iso(b.clock)
    b.save("FAIL" if failed else "PASS")
    write_manifest(b.dir)
    crashpoint("build.manifest")  # the manifest is written, the tree is still writable
    make_readonly(b.dir)
    crashpoint("build.sealed")  # sealed, not yet checked: nothing points to it (K5)
    rel = b.dir.relative_to(project).as_posix()
    if failed:
        code = EXIT_CODE[failed.reason]
        reason = "no_verified_records" if failed.detail == "no_verified_records" else failed.reason.value
        return _finish(stdout, code, reason, build_id=b.build_id, build_dir=rel, status="FAIL", detail=failed.detail)
    return _finish(stdout, 0, "ok", build_id=b.build_id, build_dir=rel, status="PASS")


# --------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="klode-kit build", description=__doc__.splitlines()[0])
    parser.add_argument("--project", type=Path, default=None, help="data project directory (default: cwd)")
    parser.add_argument("--kb", type=Path, default=None, help="library directory (default: the project's only kb/*, "
                                                              "else kb/<project name>)")
    parser.add_argument("--source", type=Path, default=None, help="book path: the text to build a library from")
    parser.add_argument("--shelf", default="book", help="book path: shelf name (default book)")
    parser.add_argument("--manifest", type=Path, default=None, help="book path: explicit chapter manifest")
    parser.add_argument("--desk", type=Path, default=None, help="explicit desk spec (src/desk.json)")
    parser.add_argument("--holdout", type=Path, default=None, help="holdout for --desk (default: holdout.jsonl beside it)")
    parser.add_argument("--syntheses", type=Path, default=None, help="desk workers' output: <dimension>.md files")
    parser.add_argument("--desk-routing", type=Path, default=None, metavar="FILE",
                        help="desk acceptance by model routing: the routing.json a worker wrote for the prompt of "
                             "`desk-route-tasks` (hit rate over the frozen holdout gates desk_verify instead of keyword "
                             "diagnose)")
    parser.add_argument("--launcher", type=Path, default=None, help="command launcher (default: this repo's bin/klode-kit)")
    parser.add_argument("--adopt", type=Path, default=None, metavar="OLD_KB",
                        help="adopt an existing (pre-klode-kit) klode library: its sources, cards and desk become this "
                             "build; no card writer is called")
    parser.add_argument("--adopt-shelves", action="append", default=None, metavar="SHELF[,SHELF]",
                        help="with --adopt: take only these shelves of the old library (repeatable or comma list)")
    parser.add_argument("--cards-from", type=Path, default=None, metavar="DIR",
                        help="book / adopt path: DIR/<id>.md cards are carried byte for byte instead of dispatching "
                             "writers")
    parser.add_argument("--loops", action="store_true", help="repair a failed content gate with the three loops "
                                                              "(L1 desk routing, L2 card content, L3 anchors), one new build per round")
    parser.add_argument("--repair", type=Path, default=None, help="repair spec of a loop round (written by --loops)")
    parser.add_argument("--budget-usd", type=float, default=None, help="writer budget of this run in USD "
                                                                       "(each writer call is reserved before it is dispatched)")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return USAGE_EXIT if exc.code else 0
    project = Path.cwd() if args.project is None else args.project
    shelves = ([s.strip() for chunk in args.adopt_shelves for s in chunk.split(",") if s.strip()]
               if args.adopt_shelves is not None else None)
    if args.loops and args.desk_routing is not None:
        return _usage(sys.stderr, sys.stdout, "--loops cannot be combined with --desk-routing")
    if args.loops and (args.adopt is not None or args.cards_from is not None):
        return _usage(sys.stderr, sys.stdout, "--loops cannot be combined with --adopt or --cards-from")
    if args.loops:
        from klode_kit import loops

        return loops.run(project, kb=args.kb, source=args.source, shelf=args.shelf, manifest=args.manifest,
                         desk=args.desk, holdout=args.holdout, syntheses=args.syntheses, launcher=args.launcher,
                         budget_usd=args.budget_usd)
    try:
        return run(project, kb=args.kb, source=args.source, shelf=args.shelf, manifest=args.manifest, desk=args.desk,
                   holdout=args.holdout, syntheses=args.syntheses, launcher=args.launcher, repair=args.repair,
                   budget_usd=args.budget_usd, adopt=args.adopt, adopt_shelves=shelves, cards_from=args.cards_from,
                   desk_routing=args.desk_routing)
    except config_mod.ConfigError as exc:
        print(f"build: configuration error: {exc}", file=sys.stderr)
        return exc.exit_code


if __name__ == "__main__":  # pragma: no cover - entry is ``klode-kit build``
    sys.exit(main())
