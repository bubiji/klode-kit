"""Execution bridge: dispatch card-writer agents through the ``claude -p`` CLI (WI-00d1, design 02 r2.5 + R2-06).

No model is called unless ``claude`` resolves to the real CLI AND the G2 real-call gate is open (see below).

argv (``build_argv``), one process per attempt, cwd = ``builds/<build_id>/``::

    claude -p --agents <inline JSON generated from agents/*.md> --agent klode-card-writer
           --permission-mode dontAsk
           --allowedTools "Read,Grep,Glob,Edit(cards_content/**),Bash(*/bin/klode-kit *)"
           --disallowedTools "Edit(library/**),Edit(src/**),Edit(.claude/**)"
           --output-format json --json-schema <writer_receipt schema> --max-budget-usd <usd>
           --no-session-persistence "<card task prompt>"

- Path rules use ``Edit(...)``, never ``Write(...)`` (Claude Code consults file permissions against Edit/Read rules
  only); ``path`` rules are relative to the cwd, hence cwd = the build directory. The card checker is only ever
  reached through the launcher ``*/bin/klode-kit`` (R2-06, venv addendum V1). No ``--force`` on any path.
- The prompt is the last argument and follows the boolean ``--no-session-persistence``, so neither variadic tool
  list can swallow it.

Run (``run``):

- a thread pool runs at most ``concurrency`` writer processes at once;
- each process starts in its own session (process group). On timeout the group gets SIGTERM, then SIGKILL after
  ``kill_grace_s``; after every attempt the group is SIGKILLed so no grandchild survives;
- SIGINT (``KeyboardInterrupt`` in the main thread) cancels: every running group is terminated, unfinished tasks
  are ``cancelled``, the receipt is written and ``BridgeCancelled`` carries it;
- an attempt whose stdout is not the expected JSON (or whose ``structured_output`` breaks the WI-00b1
  ``writer_receipt`` contract) is ``parse_error``; a non-zero exit or a writer-reported failure is ``failed``. Both
  are retried, at most ``max_rounds`` attempts in total. ``timeout`` and ``cancelled`` are final;
- a card left behind by an attempt that did not finish ``done`` is moved to ``<build>/_quarantine/`` and any earlier
  content of the card restored, so no half-written card stays under ``cards_content/``;
- every attempt record carries the ``permission_denials`` the CLI reported (``[]`` when none or no JSON); the task
  record carries them all in attempt order (evidence for the R2-06 path rules, WI-00d2);
- ``cost_usd`` is always a number: the sum of the costs the CLI reported; ``cost_complete`` is false when an attempt
  reported none (a malformed output, a kill).

CLI: ``python -m klode_kit.bridge run --tasks <json> --receipt <path> [--project DIR] [--concurrency N]
[--timeout-s S] [--max-rounds N] [--max-budget-usd X]``. Exit 0 all done; 20 some task not done; 130 cancelled
(SIGINT/SIGTERM); 2 usage; 40 configuration; 61 real-call gate closed.

Real-call gate (G2, WI-00d2): unless ``claude`` resolves (real path) to the replay fake in this repository's
``tests/fakes/bin``, ``run`` starts nothing and raises ``BridgeGateClosed`` (CLI exit 61) when
``KLODE_KIT_REAL != "1"`` or ``KLODE_KIT_GATE_ACK`` is not ``G2-<yyyymmdd>``. The vetted absolute path is what
gets executed.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from klode_kit import gate as G
from klode_kit.contract import schema as contract
from klode_kit.receipts import dumps
from klode_kit.seams import startup

WRITER_AGENT = "klode-card-writer"
ALLOWED_TOOLS = "Read,Grep,Glob,Edit(cards_content/**),Bash(*/bin/klode-kit *)"
DISALLOWED_TOOLS = "Edit(library/**),Edit(src/**),Edit(.claude/**)"
# r2.6 has no writer budget key; r5 pre-authorises --max-budget-usd 5 per call (see impl-deviations, WI-00d1).
DEFAULT_MAX_BUDGET_USD = 5.0
PLUGIN_ROOT = Path(__file__).resolve().parents[2]
RECEIPT_SCHEMA = "klode-kit.bridge_run/1"
FAKES_BIN = G.FAKES_BIN
GATE_ACK_RE = G.GATE_ACK_RE

CARD_RE = re.compile(r"^cards_content/(?!.*\.\./).+\.md$")  # writer_receipt.json "card" pattern
TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
AGENT_FIELDS = {"description": "str", "tools": "list", "disallowedTools": "list", "model": "str"}

FINAL = ("done", "timeout", "cancelled")
EXIT_OK, EXIT_NOT_DONE, EXIT_CANCELLED, EXIT_USAGE, EXIT_CONFIG = 0, 20, 130, 2, 40
EXIT_GATE = G.GATE_EXIT


class BridgeUsageError(ValueError):
    """Invalid task, options or agent definitions (usage error, exit 2)."""


class BridgeGateClosed(G.GateClosed):
    """A non-fake claude would be started without KLODE_KIT_REAL=1 and a G2 ack (exit 61, zero calls)."""


class BridgeCancelled(Exception):
    """The run was cancelled by SIGINT; ``receipt`` holds the written run receipt."""

    def __init__(self, receipt: dict):
        super().__init__("bridge run cancelled")
        self.receipt = receipt


# --------------------------------------------------------------------------- tasks and options


@dataclass(frozen=True)
class Task:
    task_id: str
    build_dir: Path
    card: str
    instructions: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "build_dir", Path(self.build_dir))
        if not isinstance(self.task_id, str) or not TASK_ID_RE.match(self.task_id):
            raise BridgeUsageError(f"task_id must match {TASK_ID_RE.pattern}: {self.task_id!r}")
        if not isinstance(self.card, str) or not CARD_RE.match(self.card):
            raise BridgeUsageError(f"{self.task_id}: card must be a cards_content/**.md path: {self.card!r}")
        if self.build_dir.parent.name != "builds":
            raise BridgeUsageError(f"{self.task_id}: build_dir must be builds/<build_id>/: {self.build_dir}")
        if not isinstance(self.instructions, str):
            raise BridgeUsageError(f"{self.task_id}: instructions must be a string")


@dataclass(frozen=True)
class Options:
    concurrency: int
    timeout_s: float
    max_rounds: int
    max_budget_usd: float = DEFAULT_MAX_BUDGET_USD
    agents_dir: Path = field(default=PLUGIN_ROOT / "agents")
    launcher: Path = field(default=PLUGIN_ROOT / "bin" / "klode-kit")
    claude: str = "claude"
    kill_grace_s: float = 2.0

    def __post_init__(self) -> None:
        for name in ("concurrency", "max_rounds"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise BridgeUsageError(f"{name} must be an integer >= 1, got {value!r}")
        for name in ("timeout_s", "max_budget_usd", "kill_grace_s"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not value > 0:
                raise BridgeUsageError(f"{name} must be > 0, got {value!r}")
        object.__setattr__(self, "agents_dir", Path(self.agents_dir))
        object.__setattr__(self, "launcher", Path(self.launcher))

    @classmethod
    def from_config(cls, cfg: Any, **overrides: Any) -> "Options":
        """r2.6 keys ``cards.writer_concurrency / writer_timeout_s / writer_max_rounds``; overrides win."""
        values: dict[str, Any] = {
            "concurrency": cfg["cards.writer_concurrency"],
            "timeout_s": cfg["cards.writer_timeout_s"],
            "max_rounds": cfg["cards.writer_max_rounds"],
        }
        values.update(overrides)
        return cls(**values)


# --------------------------------------------------------------------------- argv


def _frontmatter(path: Path) -> tuple[dict[str, str], str]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise BridgeUsageError(f"{path}: agent file has no front matter")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise BridgeUsageError(f"{path}: unterminated front matter") from None
    meta: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if not sep:
            raise BridgeUsageError(f"{path}: front matter line is not 'key: value': {line!r}")
        meta[key.strip()] = value.strip()
    return meta, "\n".join(lines[end + 1:]).strip()


def agents_json(agents_dir: Path) -> dict[str, dict]:
    """``--agents`` object generated from ``agents/*.md`` (sub-agents doc: CLI-defined subagents, camelCase fields)."""
    files = sorted(Path(agents_dir).glob("*.md"))
    agents: dict[str, dict] = {}
    for path in files:
        meta, body = _frontmatter(path)
        name = meta.get("name", "")
        if not name or ":" in name or not meta.get("description"):
            raise BridgeUsageError(f"{path}: agent needs a name (no ':') and a description")
        if name in agents:
            raise BridgeUsageError(f"{path}: duplicate agent name {name}")
        spec: dict[str, Any] = {}
        for key, kind in AGENT_FIELDS.items():
            if key in meta:
                spec[key] = [t.strip() for t in meta[key].split(",") if t.strip()] if kind == "list" else meta[key]
        spec["prompt"] = body
        agents[name] = {"description": spec.pop("description"), "prompt": spec.pop("prompt"), **spec}
    if WRITER_AGENT not in agents:
        raise BridgeUsageError(f"{agents_dir}: no {WRITER_AGENT} agent definition")
    return agents


def task_prompt(task: Task, options: Options) -> str:
    return "\n".join([
        f"Card task {task.task_id}.",
        f"card: {task.card}",
        f"Write exactly this one card file, relative to the current directory: {task.card}",
        "",
        task.instructions.strip(),
        "",
        f'Check the card only with: "{options.launcher}" check-cards {task.card}',
        "Finish with the writer receipt as the structured result.",
    ])


CLI_SCHEMA_DROP = ("$schema", "$id")


def cli_schema(name: str) -> dict:
    """The contract schema as ``--json-schema`` accepts it: top-level ``$schema``/``$id`` removed.

    Claude Code 2.1.285 rejects a document declaring draft 2020-12 ("no schema with key or ref", exit 1 before any
    API call; WI-00d2 finding F-1). The repository schema file keeps its declaration for local validation.
    """
    return {k: v for k, v in contract.load(name).items() if k not in CLI_SCHEMA_DROP}


def build_argv(task: Task, options: Options) -> tuple[list[str], Path]:
    """The ``claude -p`` argv for one attempt of ``task`` and its cwd (``builds/<build_id>/``)."""
    agents = json.dumps(agents_json(options.agents_dir), ensure_ascii=False, separators=(",", ":"))
    schema = json.dumps(cli_schema("writer_receipt"), ensure_ascii=False, separators=(",", ":"))
    argv = [
        options.claude, "-p",
        "--agents", agents,
        "--agent", WRITER_AGENT,
        "--permission-mode", "dontAsk",
        "--allowedTools", ALLOWED_TOOLS,
        "--disallowedTools", DISALLOWED_TOOLS,
        "--output-format", "json",
        "--json-schema", schema,
        "--max-budget-usd", f"{options.max_budget_usd:.2f}",
        "--no-session-persistence",
        task_prompt(task, options),
    ]
    return argv, task.build_dir


# --------------------------------------------------------------------------- real-call gate (G2)


def gate(claude: str, env: Any = None) -> str:
    """The executable to start for ``claude``; raises ``BridgeGateClosed`` before any process is started.

    The repository replay fake (``tests/fakes/bin``, compared by real path, not by name) is always allowed.
    Anything else - a real CLI, a look-alike directory, a generated test fake, an unresolvable name - needs
    ``KLODE_KIT_REAL == "1"`` and ``KLODE_KIT_GATE_ACK == G2-<yyyymmdd>`` (gate G2 of the plan). The rule is
    ``klode_kit.gate.vet`` (shared with the cloud sender, WI-06a) without generated fakes.
    """
    resolved = G.vet(claude, env=env, error=BridgeGateClosed)
    if resolved is None:
        raise BridgeUsageError(f"claude executable not found: {claude!r}")
    return str(resolved)


# --------------------------------------------------------------------------- process groups


def _signal_group(pgid: int, sig: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pgid, sig)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _denials(stdout: bytes) -> list:
    """``permission_denials`` the CLI reported in its JSON result (``[]`` when absent, malformed or not JSON)."""
    try:
        obj = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return []
    value = obj.get("permission_denials") if isinstance(obj, dict) else None
    return value if isinstance(value, list) else []


def _classify(task: Task, returncode: int, stdout: bytes) -> tuple[str, float | None, dict | None, str | None]:
    """(status, cost, writer receipt, error) of a finished, not-killed attempt."""
    cost: float | None = None
    try:
        obj = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        obj, parse_err = None, f"stdout is not JSON: {exc}"
    else:
        parse_err = None if isinstance(obj, dict) else "stdout JSON is not an object"
    if isinstance(obj, dict):
        raw_cost = obj.get("total_cost_usd")
        if isinstance(raw_cost, (int, float)) and not isinstance(raw_cost, bool) and raw_cost >= 0:
            cost = float(raw_cost)
    if returncode != 0:
        return "failed", cost, None, f"exit {returncode}"
    if parse_err:
        return "parse_error", cost, None, parse_err
    assert isinstance(obj, dict)
    if obj.get("is_error") is True:
        return "failed", cost, None, f"result is_error ({obj.get('subtype')})"
    writer = obj.get("structured_output")
    problems = contract.issues("writer_receipt", writer)
    if problems:
        return "parse_error", cost, None, "structured_output: " + "; ".join(f"{i.path or '<root>'}: {i.message}" for i in problems)
    if writer["card"] != task.card:
        return "parse_error", cost, writer, f"writer receipt names {writer['card']}, task card is {task.card}"
    if writer["status"] != "written":
        return "failed", cost, writer, f"writer status {writer['status']}"
    if not (task.build_dir / task.card).is_file():
        return "failed", cost, writer, "card_missing"
    return "done", cost, writer, None


class _Run:
    def __init__(self, options: Options, executable: str | None = None):
        self.options = options
        self.executable = executable
        self.lock = threading.Lock()
        self.active: dict[str, subprocess.Popen] = {}
        self.cancel = threading.Event()

    # -- one attempt ------------------------------------------------------------------------------------------

    def _attempt(self, task: Task, n: int) -> dict:
        argv, cwd = build_argv(task, self.options)
        if self.executable:
            argv[0] = self.executable  # exactly the executable the gate vetted, no second PATH lookup
        card_path = task.build_dir / task.card
        before = card_path.read_bytes() if card_path.is_file() else None
        t0 = time.monotonic()
        with self.lock:
            if self.cancel.is_set():
                return {"attempt": n, "status": "cancelled", "exit_code": None, "cost_usd": None,
                        "duration_s": 0.0, "error": "cancelled before start", "writer": None, "started": False,
                        "permission_denials": []}
            proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, start_new_session=True)
            self.active[task.task_id] = proc
        timed_out = False
        try:
            try:
                out, err = proc.communicate(timeout=self.options.timeout_s)
            except subprocess.TimeoutExpired:
                timed_out = True
                _signal_group(proc.pid, signal.SIGTERM)
                try:
                    out, err = proc.communicate(timeout=self.options.kill_grace_s)
                except subprocess.TimeoutExpired:
                    _signal_group(proc.pid, signal.SIGKILL)
                    out, err = proc.communicate()
        finally:
            _signal_group(proc.pid, signal.SIGKILL)  # no grandchild outlives its attempt
            with self.lock:
                self.active.pop(task.task_id, None)
        rc = proc.returncode
        writer = None
        if timed_out:
            status, cost, error = "timeout", None, f"no result within {self.options.timeout_s}s"
        elif self.cancel.is_set() and rc != 0:
            status, cost, error = "cancelled", None, "cancelled (SIGINT)"
        else:
            status, cost, writer, error = _classify(task, rc, out)
        if status != "done":
            self._quarantine(task, n, before)
        return {"attempt": n, "status": status, "exit_code": rc if rc is not None and rc >= 0 else None,
                "cost_usd": cost, "duration_s": round(time.monotonic() - t0, 3), "error": error,
                "permission_denials": _denials(out or b""),
                "stderr_tail": err.decode("utf-8", "replace")[-500:], "writer": writer, "started": True}

    @staticmethod
    def _quarantine(task: Task, n: int, before: bytes | None) -> None:
        card_path = task.build_dir / task.card
        if not card_path.is_file():
            return
        if before is not None and card_path.read_bytes() == before:
            return
        target = task.build_dir / "_quarantine" / f"{task.task_id}.a{n}.{card_path.name}"
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(card_path, target)
        if before is not None:
            card_path.write_bytes(before)

    # -- one task ---------------------------------------------------------------------------------------------

    def task(self, task: Task) -> dict:
        started = _now()
        log: list[dict] = []
        for n in range(1, self.options.max_rounds + 1):
            attempt = self._attempt(task, n)
            if attempt.pop("started"):
                log.append(attempt)
            if attempt["status"] in FINAL:
                break
            if self.cancel.is_set():
                attempt = {**attempt, "status": "cancelled"}
                break
        last = attempt
        known = [a["cost_usd"] for a in log if a["cost_usd"] is not None]
        return {
            "task_id": task.task_id,
            "card": task.card,
            "build_dir": str(task.build_dir),
            "status": last["status"],
            "attempts": len(log),
            "exit_code": last["exit_code"] if log else None,
            "cost_usd": float(sum(known)),
            "cost_complete": len(known) == len(log),
            "writer": last["writer"] if last["status"] == "done" else None,
            "error": last["error"],
            "permission_denials": [d for a in log for d in a["permission_denials"]],
            "started": started,
            "ended": _now(),
            "attempts_log": [{k: v for k, v in a.items() if k != "writer"} for a in log],
        }

    # -- cancellation -----------------------------------------------------------------------------------------

    def cancel_all(self, futures: list[Future]) -> None:
        self.cancel.set()
        with self.lock:
            groups = [p.pid for p in self.active.values()]
        for pgid in groups:
            _signal_group(pgid, signal.SIGTERM)
        wait(futures, timeout=self.options.kill_grace_s)
        with self.lock:
            groups = [p.pid for p in self.active.values()]
        for pgid in groups:
            _signal_group(pgid, signal.SIGKILL)


def _write_receipt(path: Path, receipt: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.{secrets.token_hex(8)}.tmp"
    tmp.write_bytes(dumps(receipt))
    os.replace(tmp, path)


def run(tasks: list[Task], options: Options, *, receipt_path: Path | None = None,
        now: Callable[[], str] = _now) -> dict:
    """Run every task; return (and write, when ``receipt_path`` is given) the run receipt.

    Raises ``BridgeCancelled`` (receipt written first) when SIGINT arrives during the run.
    """
    tasks = list(tasks)
    ids = [t.task_id for t in tasks]
    if len(set(ids)) != len(ids):
        raise BridgeUsageError("duplicate task_id in one run")
    executable = gate(options.claude) if tasks else None  # BridgeGateClosed: nothing started, no receipt
    started = now()
    state = _Run(options, executable)
    results: dict[str, dict] = {}
    cancelled = False
    if tasks:
        pool = ThreadPoolExecutor(max_workers=options.concurrency, thread_name_prefix="klode-writer")
        futures: list[Future] = []
        try:
            futures = [pool.submit(state.task, t) for t in tasks]
            pending = set(futures)
            while pending:
                _, pending = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
        except KeyboardInterrupt:
            cancelled = True
            state.cancel_all(futures)
        finally:
            pool.shutdown(wait=True)
        for t, fut in zip(tasks, futures):
            results[t.task_id] = fut.result()
    records = [results[t.task_id] for t in tasks]
    statuses = {r["status"] for r in records}
    status = "cancelled" if cancelled or "cancelled" in statuses else ("done" if statuses <= {"done"} else "partial")
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "status": status,
        "task_count": len(records),
        "concurrency": options.concurrency,
        "timeout_s": options.timeout_s,
        "max_rounds": options.max_rounds,
        "max_budget_usd": options.max_budget_usd,
        "cost_usd": float(sum(r["cost_usd"] for r in records)),
        "cost_complete": all(r["cost_complete"] for r in records),
        "started": started,
        "ended": now(),
        "tasks": records,
    }
    if receipt_path is not None:
        _write_receipt(Path(receipt_path), receipt)
    if cancelled:
        raise BridgeCancelled(receipt)
    return receipt


# --------------------------------------------------------------------------- CLI


def _load_tasks(path: Path) -> list[Task]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [Task(task_id=t["task_id"], build_dir=Path(t["build_dir"]), card=t["card"],
                     instructions=t.get("instructions", "")) for t in data["tasks"]]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise BridgeUsageError(f"cannot read tasks file {path}: {exc}") from exc


def _sigterm_as_interrupt(signum, frame):  # pragma: no cover - exercised only by an external SIGTERM
    raise KeyboardInterrupt


def main(argv: list[str] | None = None) -> int:
    startup()
    from klode_kit.config import ConfigError, load

    parser = argparse.ArgumentParser(prog="python -m klode_kit.bridge", description="Dispatch card writers via claude -p.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--tasks", type=Path, required=True)
    r.add_argument("--receipt", type=Path, required=True)
    r.add_argument("--project", type=Path, default=None)
    r.add_argument("--concurrency", type=int)
    r.add_argument("--timeout-s", type=float)
    r.add_argument("--max-rounds", type=int)
    r.add_argument("--max-budget-usd", type=float)
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_USAGE if exc.code else 0
    overrides = {k: v for k, v in {"concurrency": args.concurrency, "timeout_s": args.timeout_s,
                                   "max_rounds": args.max_rounds, "max_budget_usd": args.max_budget_usd}.items()
                 if v is not None}
    try:
        options = Options.from_config(load(args.project), **overrides)
        tasks = _load_tasks(args.tasks)
    except ConfigError as exc:
        print(f"klode-kit bridge: configuration error: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except BridgeUsageError as exc:
        print(f"klode-kit bridge: usage error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    signal.signal(signal.SIGTERM, _sigterm_as_interrupt)
    try:
        receipt = run(tasks, options, receipt_path=args.receipt)
    except BridgeCancelled:
        print("klode-kit bridge: cancelled; running writers terminated", file=sys.stderr)
        return EXIT_CANCELLED
    except BridgeGateClosed as exc:
        print(f"klode-kit bridge: gate G2 closed: {exc}", file=sys.stderr)
        return EXIT_GATE
    except BridgeUsageError as exc:
        print(f"klode-kit bridge: usage error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    return EXIT_OK if receipt["status"] == "done" else EXIT_NOT_DONE


if __name__ == "__main__":
    sys.exit(main())
