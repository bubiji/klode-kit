"""WI-09c: the three repair loops and their termination (design 02 r2.8, handover section 2).

    klode-kit build --loops [build options] [--budget-usd X]

A build that fails the content gate (exit 20) is repaired and built again, in a **new immutable build** every
round (``kb/<id>/builds/<build_id>/``; the old ones stay, read-only). Every loop runs the full chain
*error -> modifier -> republish -> downstream invalidated -> re-check*; the re-check is the next round's build,
which runs every step downstream of what changed:

====  ==============================  =======================================  ===================================
loop  error (classified from a build)  modifier                                 republish / downstream
====  ==============================  =======================================  ===================================
L1    ``desk_verify``: the holdout     ``cue-suggest --write`` on a copy of     next build: ``gen-desk`` / ``desk-
      gate fails, the tuning set       ``src/desk.json`` (tuning set only,      build --apply`` / ``--verify``; the
      misses questions                 cues from card aliases; the holdout      holdout file is handed on unread
                                       is never read; no suggestion = no
                                       progress)
L2    gate 1, writers, write-card,     book: the writer is dispatched again     next build: gate 1 -> write-card ->
      strict errors that are not       for exactly the named cards, the         strict -> desk; the syntheses of the
      citation-rot, synthesis, smoke   problem list pasted into its prompt      dimensions citing a redone card are
                                       (repair spec ``redispatch``); medical    invalidated (S5 is checked again);
                                       path: the deterministic cards are        the other cards are carried over
                                       regenerated, no model is involved        byte for byte
L3    strict ``[F citation-rot]``      ``fix-anchors`` on a copy of the cards   next build: ``write-card`` and strict
                                       (repair spec ``fixed``); anchors it      again; a card it cannot fix goes to
                                       cannot fix escalate to L2 in the         L2 in the same round
                                       same round
====  ==============================  =======================================  ===================================

L3 comes before L2 when both are triggered by the same build; L1 last (the desk needs final cards).

**Termination** (exit 50, the receipt keeps the money spent and the problems that remain): the problem list
(``problems_hash``: loop, step, code, card, dimension of every problem - not the message text) is the same in
``loops.no_progress_rounds`` rounds in a row; or ``loops.max_total_rounds`` builds have failed (round 7 never starts
with the default 6); or the budget is used up (``--budget-usd``, default ``cloud.max_cost_usd_per_batch``): the
bridge reserves every writer call before it is dispatched (WI-06a ledger) and the loop stops when the spend reaches
the budget. Environment-class results (exit 30, 40, 50, 60, 61 of a build) never enter a loop: the run ends with
that exit and the receipt records one round without actions.

**Receipt** ``kb/<id>/loops/<run_id>/loops_receipt.json`` (``klode-kit.loops/1``, written after every round) and,
for every round that goes on, ``r<NN>/repair.json`` (``klode-kit.repair/1``: the input of the next build) with the
modifiers' products (``desk.json``, ``fixed/<card>.md``) beside it. These files are the only writes outside the
build directories.

stdout: the build summary line of the last round (``klode-kit.build/1`` plus ``loops`` and ``rounds``).
"""

from __future__ import annotations

import fcntl
import hashlib
import io
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Collection, Mapping, Sequence, TextIO

from jsonschema import Draft202012Validator

from klode_kit import config as config_mod
from klode_kit.build.run_build import REPAIR_MARKER, REPAIR_SCHEMA, STEPS
from klode_kit.ocr.cloud_budget import BudgetExceeded, Ledger
from klode_kit.pipeline import EXIT_CODE, Reason, child_argv, default_launcher
from klode_kit.receipts import dumps
from klode_kit.seams import crashpoint

SCHEMA = "klode-kit.loops/1"
BUILD_SCHEMA = "klode-kit.build/1"
EXIT_STOPPED = EXIT_CODE[Reason.no_progress]  # 50
CONTENT_GATE_EXIT = EXIT_CODE[Reason.content_gate]  # 20
USAGE_EXIT = 2
LOOPABLE_STEPS = ("dispatch_writers", "gate1", "write_card", "check_strict", "desk_apply", "desk_verify", "gen_agent",
                  "self_check")
STOP_REASONS = ("max_rounds", "no_progress", "budget_exhausted")
PROBLEM_KEY = ("loop", "step", "code", "card", "dimension")
STRICT_LINE = re.compile(r"^\s*\[(?P<tag>\w+)\s+(?P<code>[^\]]+?)\]\s+(?P<path>\S+?):")
ROT_CODES = ("citation-rot", "citation-rot?")
FIX_LINE = re.compile(r"^(?P<card>\S+): 修好 (?P<fixed>\d+)，仍找不到 (?P<left>\d+)", re.M)
MISSING_SYNTHESES = re.compile(r"desk syntheses missing for: (?P<dims>.+)$")
NO_CARD = re.compile(r"writers left no card: (?P<cards>.+)$")
RECEIPT_NAME = "loops_receipt.json"


# --------------------------------------------------------------------------- limits and the pure scheduler


@dataclass(frozen=True)
class Limits:
    """``loops.max_total_rounds`` (builds in one run) and ``loops.no_progress_rounds`` (equal problem lists in a row)."""

    max_total_rounds: int = 6
    no_progress_rounds: int = 2

    def __post_init__(self) -> None:
        for name in ("max_total_rounds", "no_progress_rounds"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be an integer >= 1, got {value!r}")

    @classmethod
    def from_config(cls, cfg: Any) -> "Limits":
        return cls(cfg["loops.max_total_rounds"], cfg["loops.no_progress_rounds"])


@dataclass(frozen=True)
class Decision:
    proceed: bool
    reason: str | None = None


@dataclass(frozen=True)
class RoundsResult:
    rounds: int
    hashes: list[str]
    passed: bool
    reason: str | None
    exit_code: int


def problems_hash(problems: Sequence[Mapping[str, Any]]) -> str:
    """sha256 over the *set* of ``(loop, step, code, card, dimension)`` - order, duplicates and message text do not matter."""
    keys = sorted({json.dumps([p.get(k) for k in PROBLEM_KEY], ensure_ascii=False) for p in problems})
    return hashlib.sha256("\n".join(keys).encode("utf-8")).hexdigest()


def may_run_round(n: int, limits: Limits) -> bool:
    return 1 <= n <= limits.max_total_rounds


def decide(hashes: Sequence[str], limits: Limits, *, over_budget: bool = False) -> Decision:
    """After ``len(hashes)`` failed rounds: may another one start? Budget wins, then no progress, then the bound."""
    if over_budget:
        return Decision(False, "budget_exhausted")
    k = limits.no_progress_rounds
    if len(hashes) >= k and len(set(hashes[-k:])) == 1:
        return Decision(False, "no_progress")
    if not may_run_round(len(hashes) + 1, limits):
        return Decision(False, "max_rounds")
    return Decision(True)


def run_rounds(attempt: Callable[[int], str | None], limits: Limits, *,
               over_budget: Callable[[], bool] | None = None) -> RoundsResult:
    """Drive ``attempt(n)`` (``None`` = the round passed, else the problem-list hash) until it passes or ``decide`` stops it."""
    hashes: list[str] = []
    n = 0
    while True:
        n += 1
        h = attempt(n)
        if h is None:
            return RoundsResult(n, hashes, True, None, 0)
        hashes.append(h)
        d = decide(hashes, limits, over_budget=bool(over_budget and over_budget()))
        if not d.proceed:
            return RoundsResult(n, hashes, False, d.reason, EXIT_STOPPED)


# --------------------------------------------------------------------------- classifying a failed build


def _problem(loop: str | None, step: str, code: str, card: str | None = None, dimension: str | None = None,
             detail: str = "") -> dict:
    return {"loop": loop, "step": step, "code": code, "card": card, "dimension": dimension, "detail": detail[:500]}


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def failed_step(build_dir: Path) -> tuple[str | None, str, int]:
    """``(step, detail, exit code)`` of the build's first FAIL step (``(None, "", 0)`` when none)."""
    rec = _json(Path(build_dir) / "build_receipt.json")
    steps = rec.get("steps") if isinstance(rec, dict) else None
    for s in steps if isinstance(steps, list) else []:
        if isinstance(s, dict) and s.get("status") == "FAIL":
            step = str(s.get("step"))
            doc = _json(Path(build_dir) / "receipts" / f"{step}.json")
            code = doc.get("exit_code") if isinstance(doc, dict) else None
            return step, str(s.get("detail") or ""), code if isinstance(code, int) else CONTENT_GATE_EXIT
    return None, "", 0


def classify(build_dir: Any, *, loopable_only: bool = True) -> list[dict]:
    """The problem list of a failed build: ``{loop, step, code, card, dimension, detail}`` each.

    Only a content-gate failure (exit 20) at a step the loops can repair is classified; ``loopable_only=False`` lists
    what is left of any failed build (the receipt's remaining problems after a budget stop, for instance)."""
    bd = Path(build_dir)
    step, detail, code = failed_step(bd)
    if step is None:
        return []
    if loopable_only and (code != CONTENT_GATE_EXIT or step not in LOOPABLE_STEPS):
        return []
    handler = _HANDLERS.get(step)
    if handler is None:
        return [_problem(None, step, f"exit_{code}", detail=detail)]
    return handler(bd, detail)


def _generic(step: str, code: str, detail: str) -> list[dict]:
    return [_problem("L2", step, code, detail=detail)]


def _dispatch(bd: Path, detail: str) -> list[dict]:
    run = _json(bd / "receipts" / "writer_run.json")
    tasks = run.get("tasks") if isinstance(run, dict) else None
    out = [_problem("L2", "dispatch_writers", f"writer_{t.get('status')}", card=str(t.get("task_id")),
                    detail=str(t.get("error") or ""))
           for t in (tasks if isinstance(tasks, list) else []) if isinstance(t, dict) and t.get("status") != "done"]
    if out:
        return out
    m = NO_CARD.search(detail)
    if m:
        return [_problem("L2", "dispatch_writers", "writer_no_card", card=c.strip(), detail=detail)
                for c in m.group("cards").split(",") if c.strip()]
    return _generic("dispatch_writers", "dispatch_failed", detail)


def _gate1(bd: Path, detail: str) -> list[dict]:
    doc = _json(bd / "reports" / "gate1.txt")
    out = []
    for card in doc.get("cards", []) if isinstance(doc, dict) else []:
        if not isinstance(card, dict) or card.get("pass") is not False:
            continue
        for p in card.get("problems") or [{"code": "gate1", "message": ""}]:
            out.append(_problem("L2", "gate1", str(p.get("code")), card=str(card.get("id")),
                                detail=str(p.get("message") or "")))
    return out or _generic("gate1", "gate1_failed", detail)


def _write_card(bd: Path, detail: str) -> list[dict]:
    return _generic("write_card", "write_card_failed", detail)


def _strict(bd: Path, detail: str) -> list[dict]:
    out = []
    for line in _text(bd / "reports" / "check_strict.txt").splitlines():
        m = STRICT_LINE.match(line)
        if not m:
            continue
        path = m.group("path")
        card = Path(path).stem if path.endswith(".md") else None
        code = m.group("code").strip()
        out.append(_problem("L3" if code in ROT_CODES else "L2", "check_strict", code, card=card, detail=line.strip()))
    return out or _generic("check_strict", "strict_failed", detail)


def _desk_apply(bd: Path, detail: str) -> list[dict]:
    m = MISSING_SYNTHESES.search(detail)
    if m:
        return [_problem("L2", "desk_apply", "synthesis_missing", dimension=d.strip(), detail=detail)
                for d in m.group("dims").split(",") if d.strip()]
    return _generic("desk_apply", "desk_apply_failed", detail)


def desk_scores(build_dir: Path) -> dict | None:
    """``{"tune": {hit, total}, "holdout": {hit, total}}`` of the build's desk receipt, or ``None``."""
    rec = _json(Path(build_dir) / "desk_receipt.json")
    if not isinstance(rec, dict):
        return None
    try:
        return {"tune": {"hit": rec["tune"]["hit"], "total": rec["tune"]["total"]},
                "holdout": {"hit": rec["holdout"]["hit"], "total": rec["holdout"]["total"]}}
    except (KeyError, TypeError):
        return None


def _desk_verify(bd: Path, detail: str) -> list[dict]:
    rec = _json(bd / "desk_receipt.json")
    scores = desk_scores(bd)
    only_holdout = (isinstance(rec, dict) and scores is not None
                    and rec.get("synthesis", {}).get("pass") is True and rec.get("strict", {}).get("pass") is True
                    and rec.get("diagnostics", {}).get("current") is True
                    and rec.get("holdout", {}).get("pass") is False)
    if not only_holdout:
        return _generic("desk_verify", "desk_verify_failed", detail)
    assert scores is not None and isinstance(rec, dict)
    out = [_problem("L1", "desk_verify", "tuning_miss", dimension=str(r.get("expect")), detail=str(r.get("q")))
           for r in rec["tune"].get("results", []) if isinstance(r, dict) and r.get("ok") is False]
    h = scores["holdout"]
    out.append(_problem("L1", "desk_verify", f"holdout_below:{h['hit']}/{h['total']}"))
    return out


def _gen_agent(bd: Path, detail: str) -> list[dict]:
    return _generic("gen_agent", "gen_agent_failed", detail)


def _self_check(bd: Path, detail: str) -> list[dict]:
    return _generic("self_check", "self_check_failed", detail)


_HANDLERS: dict[str, Callable[[Path, str], list[dict]]] = {
    "dispatch_writers": _dispatch, "gate1": _gate1, "write_card": _write_card, "check_strict": _strict,
    "desk_apply": _desk_apply, "desk_verify": _desk_verify, "gen_agent": _gen_agent, "self_check": _self_check,
}


# --------------------------------------------------------------------------- planning a round


def plan(problems: Sequence[Mapping[str, Any]], l3_tried: Collection[str] = ()) -> list[dict]:
    """The loops to run for a problem list, in order: L3, L2, L1. A card whose citation was already fixed once, or
    that has no card name to fix, goes to L2."""
    l3 = sorted({p["card"] for p in problems if p["loop"] == "L3" and p["card"] and p["card"] not in l3_tried})
    escalated = sorted({p["card"] for p in problems if p["loop"] == "L3" and p["card"] and p["card"] in l3_tried})
    l2_cards = sorted({p["card"] for p in problems if p["loop"] == "L2" and p["card"]} | set(escalated))
    l2 = bool(escalated) or any(p["loop"] == "L2" or (p["loop"] == "L3" and not p["card"]) for p in problems)
    acts: list[dict] = []
    if l3:
        acts.append({"loop": "L3", "cards": l3})
    if l2:
        acts.append({"loop": "L2", "cards": l2_cards, "escalated_from_l3": escalated})
    if any(p["loop"] == "L1" for p in problems):
        acts.append({"loop": "L1"})
    return acts


# --------------------------------------------------------------------------- the run


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


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


_RECEIPT_SCHEMA = {
    "type": "object",
    "required": ["schema", "run_id", "status", "exit_code", "reason", "max_total_rounds", "no_progress_rounds",
                 "budget_usd", "spent_usd", "rounds", "remaining_problems", "final_build_id", "started"],
    "properties": {
        "schema": {"const": SCHEMA}, "status": {"enum": ["PASS", "FAIL", "RUNNING"]},
        "exit_code": {"type": "integer"}, "reason": {"type": "string"},
        "rounds": {"type": "array", "items": {
            "type": "object",
            "required": ["round", "build_id", "build_dir", "exit_code", "status", "cost_usd", "scores", "problems",
                         "problems_hash", "actions"],
            "properties": {"round": {"type": "integer", "minimum": 1}, "actions": {"type": "array"},
                           "problems": {"type": "array"}}}},
    },
}
_VALIDATOR = Draft202012Validator(_RECEIPT_SCHEMA)


def _write_json(path: Path, doc: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(dumps(doc))
    os.replace(tmp, path)


class _State:
    """What the rounds hand on to each other."""

    def __init__(self, desk: Path | None, holdout: Path | None):
        self.desk, self.holdout = desk, holdout
        self.fixed: dict[str, str] = {}
        self.l3_tried: set[str] = set()
        self.repair: Path | None = None


def _round_cost(build_dir: Path | None) -> Decimal:
    total = Decimal(0)
    if build_dir is None:
        return total
    for step in STEPS:
        doc = _json(build_dir / "receipts" / f"{step}.json")
        cost = doc.get("cost_usd") if isinstance(doc, dict) else None
        if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost >= 0:
            total += Decimal(str(cost))
    return total


def _loops_lock_dir(project: Path, kb: str | os.PathLike | None) -> Path | None:
    """``kb/<id>/loops`` of the library this run builds (the directory the loop receipts live in), made if needed;
    ``None`` when the arguments do not name a library of this project (the build then reports the usage error)."""
    from klode_kit.build import run_build

    if not project.is_dir():
        return None
    root = project.resolve()
    kb_dir = (Path(kb) if Path(kb).is_absolute() else root / kb) if kb is not None else run_build.default_kb(root)
    kb_dir = kb_dir.parent.resolve() / kb_dir.name
    try:
        kb_dir.relative_to(root)
    except ValueError:
        return None
    if not run_build.SLUG_RE.fullmatch(kb_dir.name):
        return None
    os.makedirs(kb_dir / "loops", exist_ok=True)
    return kb_dir / "loops"


def run(project: str | os.PathLike, *, kb: str | os.PathLike | None = None, source: str | os.PathLike | None = None,
        shelf: str = "book", manifest: str | os.PathLike | None = None, desk: str | os.PathLike | None = None,
        holdout: str | os.PathLike | None = None, syntheses: str | os.PathLike | None = None,
        launcher: str | os.PathLike | None = None, env: Mapping[str, str] | None = None, out: TextIO | None = None,
        stdout: TextIO | None = None, clock: Callable[[], datetime] | None = None,
        budget_usd: float | None = None) -> int:
    """Build with the repair loops; the exit code is the build's own (0, 2, 20 for an unloopable gate failure, 30,
    40, 50, 60, 61) or 50 when a loop gave up, or 40 when another ``--loops`` run of the library holds the lock.

    The run holds an ``flock`` on ``kb/<id>/loops/`` (the directory itself, no file) from the first round to the last
    - the builds lock of ``run_build`` is released after every round, so without this one two runs would interleave
    their rounds. A killed run releases it with its fd (WI-09d)."""
    out_ = sys.stderr if out is None else out
    stdout_ = sys.stdout if stdout is None else stdout
    held: list[int] = []

    def take_lock() -> int | None:
        """Called by the rounds once the guards have passed (a refused run creates nothing, not even ``loops/``)."""
        lock_dir = _loops_lock_dir(Path(project), kb)
        if lock_dir is None:
            return None
        fd = os.open(lock_dir, os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            detail = f"another build --loops holds the lock of {lock_dir}"
            print(f"build: {detail}", file=out_)
            print(json.dumps({"schema": BUILD_SCHEMA, "exit_code": EXIT_CODE[Reason.lock_busy],
                              "reason": Reason.lock_busy.value, "detail": detail}, ensure_ascii=False, sort_keys=True),
                  file=stdout_)
            return EXIT_CODE[Reason.lock_busy]
        held.append(fd)
        crashpoint("loops.lock")
        return None

    try:
        return _run_rounds(project, kb=kb, source=source, shelf=shelf, manifest=manifest, desk=desk, holdout=holdout,
                           syntheses=syntheses, launcher=launcher, env=env, out=out, stdout=stdout, clock=clock,
                           budget_usd=budget_usd, take_lock=take_lock)
    finally:
        for fd in held:
            os.close(fd)


def _run_rounds(project: str | os.PathLike, *, kb: str | os.PathLike | None = None,
                source: str | os.PathLike | None = None, shelf: str = "book", manifest: str | os.PathLike | None = None,
                desk: str | os.PathLike | None = None, holdout: str | os.PathLike | None = None,
                syntheses: str | os.PathLike | None = None, launcher: str | os.PathLike | None = None,
                env: Mapping[str, str] | None = None, out: TextIO | None = None, stdout: TextIO | None = None,
                clock: Callable[[], datetime] | None = None, budget_usd: float | None = None,
                take_lock: Callable[[], int | None] = lambda: None) -> int:
    from klode_kit.build import run_build

    out = sys.stderr if out is None else out
    stdout = sys.stdout if stdout is None else stdout
    env = dict(os.environ if env is None else env)
    project = Path(project)

    def build_once(state: _State, remaining: float | None) -> tuple[int, dict, str]:
        buf = io.StringIO()
        code = run_build.run(project, kb=kb, source=source, shelf=shelf, manifest=manifest, desk=state.desk,
                             holdout=state.holdout, syntheses=syntheses, launcher=launcher, env=env, out=out,
                             stdout=buf, clock=clock, repair=state.repair, budget_usd=remaining)
        return code, _last_json(buf.getvalue()), buf.getvalue()

    if not project.is_dir():
        return run_build.run(project, kb=kb, source=source, shelf=shelf, manifest=manifest, desk=desk,
                             holdout=holdout, syntheses=syntheses, launcher=launcher, env=env, out=out, stdout=stdout,
                             clock=clock)
    try:
        cfg = config_mod.load(project)
        limits = Limits.from_config(cfg)
    except (config_mod.ConfigError, ValueError) as exc:
        print(f"build: configuration error: {exc}", file=out)
        print(json.dumps({"schema": BUILD_SCHEMA, "exit_code": EXIT_CODE[Reason.config], "reason": Reason.config.value,
                          "detail": str(exc)}, ensure_ascii=False, sort_keys=True), file=stdout)
        return EXIT_CODE[Reason.config]
    budget = cfg["cloud.max_cost_usd_per_batch"] if budget_usd is None else budget_usd
    try:
        ledger = Ledger(budget)
    except ValueError as exc:
        print(f"build: --budget-usd: {exc}", file=out)
        print(json.dumps({"schema": BUILD_SCHEMA, "exit_code": USAGE_EXIT, "reason": "usage", "detail": str(exc)},
                         ensure_ascii=False, sort_keys=True), file=stdout)
        return USAGE_EXIT
    busy = take_lock()
    if busy is not None:
        return busy

    state = _State(Path(desk).resolve() if desk is not None else None,
                   Path(holdout).resolve() if holdout is not None else None)
    rounds: list[dict] = []
    hashes: list[str] = []
    started = _now()
    loop_dir: Path | None = None
    run_id = ""
    final: dict = {}
    remaining_problems: list[dict] = []

    def receipt(status: str, code: int, reason: str, build: dict) -> dict:
        return {"schema": SCHEMA, "run_id": run_id, "status": status, "exit_code": code, "reason": reason,
                "max_total_rounds": limits.max_total_rounds, "no_progress_rounds": limits.no_progress_rounds,
                "budget_usd": float(ledger.limit_usd), "spent_usd": float(ledger.spent_usd), "rounds": rounds,
                "remaining_problems": remaining_problems, "final_build_id": build.get("build_id"),
                "final_build_dir": build.get("build_dir"), "started": started, "ended": _now()}

    def save(status: str, code: int, reason: str, build: dict) -> None:
        if loop_dir is not None:
            doc = receipt(status, code, reason, build)
            errors = list(_VALIDATOR.iter_errors(doc))
            assert not errors, errors[0].message
            _write_json(loop_dir / RECEIPT_NAME, doc)

    def finish(code: int, reason: str, build: dict, text: str) -> int:
        status = "PASS" if code == 0 else "FAIL"
        save(status, code, reason, build)
        if loop_dir is None:  # no build was started (usage / environment before the first step): the build's own line
            stdout.write(text)
            return code
        rel = loop_dir.relative_to(project.resolve()).as_posix() if project.exists() else str(loop_dir)
        summary = {**build, "schema": BUILD_SCHEMA, "exit_code": code, "reason": reason,
                   "status": "PASS" if code == 0 else "FAIL", "loops": f"{rel}/{RECEIPT_NAME}", "rounds": len(rounds)}
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True), file=stdout)
        return code

    n = 0
    while True:
        n += 1
        try:
            reservation = ledger.reserve()
        except BudgetExceeded:  # pragma: no cover - decide() stops a run whose spend reached the budget first
            return finish(EXIT_STOPPED, "budget_exhausted", final, "")
        remaining = float(ledger.limit_usd - ledger.spent_usd)
        t0 = _now()
        code, summary, text = build_once(state, remaining)
        build_dir = project.resolve() / summary["build_dir"] if isinstance(summary.get("build_dir"), str) else None
        cost = _round_cost(build_dir)
        ledger.settle(reservation, float(cost))
        if build_dir is None:  # nothing was built: usage, configuration, launcher, lock ...
            if loop_dir is None:
                return finish(code, str(summary.get("reason", "")), summary, text)
            return finish(code, str(summary.get("reason", "")), final, text)
        final = {k: summary[k] for k in ("build_id", "build_dir") if k in summary}
        if loop_dir is None:
            kb_dir = build_dir.parent.parent
            run_id = f"l-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{hashlib.sha256(summary['build_id'].encode()).hexdigest()[:8]}"
            loop_dir = kb_dir / "loops" / run_id
            loop_dir.mkdir(parents=True, exist_ok=True)
        rnd: dict[str, Any] = {"round": n, "build_id": summary.get("build_id"), "build_dir": summary.get("build_dir"),
                               "exit_code": code, "status": "PASS" if code == 0 else "FAIL", "started": t0,
                               "ended": _now(), "cost_usd": float(cost), "scores": desk_scores(build_dir),
                               "problems": [], "problems_hash": "", "actions": []}
        rounds.append(rnd)
        if code == 0:
            return finish(0, "ok", final, text)
        problems = classify(build_dir) if code == CONTENT_GATE_EXIT else []
        rnd["problems"] = problems
        rnd["problems_hash"] = problems_hash(problems)
        print(f"loops: round {n}: build {summary.get('build_id')} FAIL (exit {code}), {len(problems)} problem(s)",
              file=out)
        if not problems:  # environment-class result, or a failure no loop repairs
            remaining_problems[:] = classify(build_dir, loopable_only=False)
            return finish(code, str(summary.get("reason", "")), final, text)
        hashes.append(rnd["problems_hash"])
        decision = decide(hashes, limits, over_budget=ledger.spent_usd >= ledger.limit_usd)
        if not decision.proceed:
            remaining_problems[:] = problems
            print(f"loops: stop after round {n}: {decision.reason}", file=out)
            return finish(EXIT_STOPPED, str(decision.reason), final, text)
        changed = _repair(project.resolve(), loop_dir, n, build_dir, summary, problems, state, rnd, launcher, env, out,
                          is_book=source is not None)
        if not changed:
            remaining_problems[:] = problems
            print(f"loops: stop after round {n}: no_progress (no modifier had anything to change)", file=out)
            return finish(EXIT_STOPPED, "no_progress", final, text)
        save("RUNNING", CONTENT_GATE_EXIT, "repairing", final)


# --------------------------------------------------------------------------- the modifiers


def _rel(project: Path, path: Path) -> str:
    return path.relative_to(project).as_posix()


def _child(launcher: Any, env: Mapping[str, str], project: Path, out: TextIO, *args: str) -> tuple[int, str]:
    lp = Path(launcher) if launcher is not None else default_launcher()
    proc = subprocess.run(child_argv(lp, *args), env=dict(env), cwd=str(project), capture_output=True)
    text = proc.stdout.decode("utf-8", "replace")
    for chunk in (text, proc.stderr.decode("utf-8", "replace")):
        if chunk:
            out.write(chunk if chunk.endswith("\n") else chunk + "\n")
    out.flush()
    return proc.returncode, text


def _repair(project: Path, loop_dir: Path, n: int, build_dir: Path, summary: Mapping[str, Any],
            problems: list[dict], state: _State, rnd: dict, launcher: Any, env: Mapping[str, str], out: TextIO,
            *, is_book: bool) -> bool:
    """Run the planned modifiers of round ``n`` (L3, L2, L1), write the repair spec of round ``n+1`` and hand the new
    desk on in ``state``. ``False`` when nothing changed (only L1 ran and it had no suggestion)."""
    rdir = loop_dir / f"r{n:02d}"
    rdir.mkdir(parents=True, exist_ok=True)
    acts = plan(problems, state.l3_tried)
    records: list[dict] = rnd["actions"]
    escalated: list[str] = []
    changed = False
    redispatch: dict[str, list[str]] = {}
    for act in acts:
        started = _now()
        if act["loop"] == "L3":
            rec = _run_l3(project, rdir, build_dir, act["cards"], state, launcher, env, out)
            escalated = list(rec["left"])
            changed = True
        elif act["loop"] == "L2":
            cards = sorted(set(act["cards"]) | set(escalated))
            esc = sorted(set(act["escalated_from_l3"]) | set(escalated))
            step = ("redispatch_writers" if cards else "rebuild") if is_book else "regenerate"
            if is_book:
                for c in cards:
                    redispatch[c] = [f"[{p['code']}] {p['detail']}".strip() for p in problems if p["card"] == c]
                    state.fixed.pop(c, None)
            rec = {"loop": "L2", "step": step, "cards": cards, "escalated_from_l3": esc,
                   "invalidated_dimensions": _invalidated(state, build_dir, cards)}
            changed = True
        else:
            rec = _run_l1(project, rdir, build_dir, state, launcher, env, out)
            changed = changed or rec["exit_code"] == 0
        rec.update({"started": started, "ended": _now()})
        records.append(rec)
    if escalated and not any(a["loop"] == "L2" for a in acts):  # L3 leftovers with no L2 problem of their own
        started = _now()
        for c in escalated:
            redispatch[c] = [f"[{p['code']}] {p['detail']}".strip() for p in problems if p["card"] == c]
            state.fixed.pop(c, None)
        records.append({"loop": "L2", "step": "redispatch_writers" if is_book else "regenerate", "cards": escalated,
                        "escalated_from_l3": escalated, "invalidated_dimensions": _invalidated(state, build_dir, escalated),
                        "started": started, "ended": _now()})
    spec = {"schema": REPAIR_SCHEMA, "round": n + 1, "from_build": _rel(project, build_dir), "redispatch": redispatch,
            "fixed": dict(sorted(state.fixed.items())),
            "invalidated_dimensions": sorted({d for r in records for d in r.get("invalidated_dimensions", [])})}
    _write_json(rdir / "repair.json", spec)
    state.repair = rdir / "repair.json"
    return changed


def _invalidated(state: _State, build_dir: Path, cards: Sequence[str]) -> list[str]:
    """Dimensions of the desk whose synthesis cites one of ``cards`` (their S5 check runs again in the next build)."""
    spec = _json(state.desk) if state.desk is not None else None
    if not isinstance(spec, dict):
        spec = _json(build_dir / "src" / "desk.json")
    dims = spec.get("dimensions") if isinstance(spec, dict) else None
    return sorted(str(d["dimension"]) for d in (dims if isinstance(dims, list) else [])
                  if isinstance(d, dict) and set(d.get("cards", [])) & set(cards))


def _run_l3(project: Path, rdir: Path, build_dir: Path, cards: Sequence[str], state: _State, launcher: Any,
            env: Mapping[str, str], out: TextIO) -> dict:
    scratch = rdir / "scratch"
    originals: dict[str, bytes] = {}
    for cid in cards:
        card = build_dir / "cards_content" / f"{cid}.md"
        if card.is_file():
            originals[cid] = card.read_bytes()
            (scratch / "cards_content").mkdir(parents=True, exist_ok=True)
            (scratch / "cards_content" / f"{cid}.md").write_bytes(originals[cid])
        for src in sorted((build_dir / "library").glob(f"*/{cid}.txt")):
            dest = scratch / "library" / src.parent.name / src.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(src.read_bytes())
    rc, text = _child(launcher, env, project, out, "fix-anchors", str(scratch), *cards)
    left = sorted({m.group("card") for m in FIX_LINE.finditer(text) if int(m.group("left")) > 0})
    if rc != 0 and not left:
        left = sorted(cards)
    fixed = []
    for cid, before in originals.items():
        after = (scratch / "cards_content" / f"{cid}.md").read_bytes()
        if after != before:
            target = rdir / "fixed" / f"{cid}.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(after)
            state.fixed[cid] = _rel(project, target)
            fixed.append(cid)
    state.l3_tried |= set(cards)
    return {"loop": "L3", "step": "fix_anchors", "cards": list(cards), "exit_code": rc, "fixed": sorted(fixed),
            "left": left}


def _run_l1(project: Path, rdir: Path, build_dir: Path, state: _State, launcher: Any, env: Mapping[str, str],
            out: TextIO) -> dict:
    spec = build_dir / "src" / "desk.json"
    copy = rdir / "desk.json"
    if not spec.is_file():
        return {"loop": "L1", "step": "cue_suggest", "exit_code": USAGE_EXIT, "suggestions": {}}
    rdir.mkdir(parents=True, exist_ok=True)
    copy.write_bytes(spec.read_bytes())
    rc, text = _child(launcher, env, project, out, "cue-suggest", str(build_dir), "--spec", str(copy), "--receipt",
                      str(build_dir / "desk_receipt.json"), "--write")
    doc = _last_json_block(text)
    suggestions = doc.get("suggestions", {}) if isinstance(doc, dict) else {}
    if rc == 0:
        state.desk = copy
        hold = build_dir / "src" / "holdout.jsonl"
        if hold.is_file():
            state.holdout = hold
    return {"loop": "L1", "step": "cue_suggest", "exit_code": rc, "suggestions": suggestions}


def _last_json_block(text: str) -> Any:
    """The JSON document ``cue-suggest`` prints (indented, several lines)."""
    start = text.find("{")
    if start < 0:
        return {}
    try:
        return json.JSONDecoder().raw_decode(text[start:])[0]
    except ValueError:
        return {}
