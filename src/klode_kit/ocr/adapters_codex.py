"""Codex read-image adapter: one page image -> ``ocr_line`` rows (WI-06b; design 02 r2.2-A, r2.3, r2.6, R2-08,
R2-12; plan WI-06b). The codex twin of ``adapters_claude`` (WI-06a): same outcomes, same ledger, same receipt
schema; only the CLI output and the money differ.

``read_page(path, der_sha256, *, page_id, project, receipt_dir, isolation_receipt, ledger, ...)``:

0. effort lock: ``codex.effort`` must be ``EFFORT_LOCK`` (``max``; WI-00c3 decision "highest level, never
   downgraded"). Anything else raises ``EffortLocked`` (a ``ConfigError``, exit 40) before anything is reserved or
   started - a project file cannot lower it;
1. ``ledger.reserve()`` before every dispatch (``BudgetExceeded``, exit 50, when the batch budget is used up);
2. ``send.send(..., route="codex")`` - the only way an image leaves the machine. The argv is the WI-00c2c one
   (``cloud_argv.codex``: permission profile ``klode-kit-ocr``, ``--json``, ``--output-schema schema.json``,
   ``-c model_reasoning_effort=max``; no ``-o``: the send directory keeps exactly its three files);
3. the ``--json`` event stream is read (``parse_events``): the answer is the text of the LAST ``item.completed``
   whose item is an ``agent_message``, parsed as JSON and checked against ``schemas/ocr_cloud_output.json``; token
   usage is the sum of the integer fields of every ``turn.completed.usage``; an ``error`` or ``turn.failed`` event
   marks the call failed; ``model_actual`` is a ``model`` string carried by an event (codex 0.163.0 names no
   model in ``--json`` events - WI-00c3 recordings - so it is null there; the configured model stays in
   ``model``).

Money (self-defined, WI-06b): codex reports tokens, not dollars. Each call is charged
``estimate_cost_usd(tokens)`` - a deliberately high per-token estimate (``EST_USD_PER_MTOK``, USD per million
tokens: input and cache writes 10, output and reasoning output 60; cached input is part of ``input_tokens`` and is
not charged again) - or, when the stream has no priced usage (timeout, crash), the reservation. The estimate is
additive and monotone, so the ledger's law and the exit-50 rule are exactly the claude ones: reserve before every
dispatch, refuse once nothing is left, "more than the limit" is ``exceeded``. Codex has no per-call spending cap,
so a call may cost more than its reservation; that overrun is charged as estimated (never under-counted) and
widens the ledger's bound by it, as documented for claude in ``cloud_budget``.

Outcomes (design 02 r2.3 reason table), as in the claude adapter and with its exception classes, so ``kb-ocr``
treats both routes alike:

- timeout, non-zero exit, or an error event -> ``ReadFailed`` (route_failed);
- no answer, an answer that is not JSON or breaks the output schema -> one more call; still bad ->
  ``ParseFailed`` (parse_error);
- otherwise rows ``{"engine": "codex", "page_id", "der_sha256": <the image sent>, "line_idx": 0..n-1, "text":
  <verbatim>, "bbox_px": null, "tokens": null}`` (cloud routes carry no geometry, R2-08).

Page receipt ``<receipt_dir>/read-codex-<sha>.<n>.json`` (``klode-kit.cloud_read/1``)::

    {schema, route: "codex", der_sha256, page_id, status: done|parse_error|route_failed|budget_stop|refused,
     reason, model, effort, model_actual, cost_usd (sum of all attempts), cost_complete, cost_estimated: true,
     tokens (summed over attempts), lines, uncertain_lines,
     attempts: [{status, exit_code, timed_out, reserved_usd, cost_usd, model_actual, tokens, send_receipt}]}

No path, no file name from the data project and no pixels.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

from klode_kit import config as config_mod
from klode_kit.ocr import send as S
from klode_kit.ocr.adapters_claude import PageRead, ParseFailed, ReadFailed, _validator, _write
from klode_kit.ocr.cloud_budget import BudgetExceeded, Ledger, Reservation

__all__ = ["EFFORT_LOCK", "EST_USD_PER_MTOK", "EffortLocked", "PageRead", "ParseFailed", "Parsed", "ReadFailed",
           "estimate_cost_usd", "parse_events", "read_page", "to_rows"]

ROUTE = "codex"
RECEIPT_SCHEMA = "klode-kit.cloud_read/1"
PARSE_RETRIES = 1
EFFORT_LOCK = "max"
EST_USD_PER_MTOK: dict[str, Decimal] = {
    "input_tokens": Decimal("10"),
    "cache_write_input_tokens": Decimal("10"),
    "output_tokens": Decimal("60"),
    "reasoning_output_tokens": Decimal("60"),
}
_MTOK = Decimal(1_000_000)
_ERROR_EVENTS = frozenset({"error", "turn.failed"})


class EffortLocked(config_mod.ConfigError):
    """``codex.effort`` is not the locked level (exit 40); nothing was reserved or started."""

    def __init__(self, effort: Any):
        super().__init__(f"codex.effort is locked to {EFFORT_LOCK!r}, the project sets {effort!r}; remove the "
                         f"override (or set it to {EFFORT_LOCK!r})", key="codex.effort")


@dataclass(frozen=True)
class Parsed:
    """One codex event stream: ``lines`` (None without a valid answer), tokens, estimated cost, model, errors."""

    lines: list[dict] | None
    tokens: dict[str, int] = field(default_factory=dict)
    cost_usd: Decimal | None = None
    model_actual: str | None = None
    is_error: bool = False
    problem: str | None = None


def estimate_cost_usd(tokens: Mapping[str, Any]) -> Decimal | None:
    """USD estimate of a token usage; None when no priced kind is present (cost unknown, never zero)."""
    priced = [(kind, tokens[kind]) for kind in EST_USD_PER_MTOK
              if isinstance(tokens.get(kind), int) and not isinstance(tokens.get(kind), bool)]
    if not priced:
        return None
    return sum((Decimal(count) * EST_USD_PER_MTOK[kind] / _MTOK for kind, count in priced), Decimal(0))


def _events(stdout: str) -> list[dict]:
    found = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            found.append(event)
    return found


def parse_events(stdout: str) -> Parsed:
    """Read one ``codex exec --json`` event stream (see module docstring)."""
    events = _events(stdout)
    if not events:
        return Parsed(None, problem="stdout is not a codex --json event stream")
    tokens: dict[str, int] = {}
    model: str | None = None
    is_error = False
    answer: str | None = None
    for event in events:
        kind = event.get("type")
        if isinstance(event.get("model"), str) and event["model"]:
            model = event["model"]
        if kind in _ERROR_EVENTS:
            is_error = True
        elif kind == "turn.completed" and isinstance(event.get("usage"), dict):
            for key, value in event["usage"].items():
                if isinstance(value, int) and not isinstance(value, bool):
                    tokens[key] = tokens.get(key, 0) + value
        elif kind == "item.completed":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                answer = item["text"]
    cost = estimate_cost_usd(tokens)
    base = {"tokens": tokens, "cost_usd": cost, "model_actual": model, "is_error": is_error}
    if answer is None:
        return Parsed(None, problem="no agent_message in the event stream", **base)
    try:
        output = json.loads(answer)
    except ValueError:
        return Parsed(None, problem="the agent_message is not JSON", **base)
    errors = sorted(_validator().iter_errors(output), key=lambda e: list(e.path))
    if errors:
        where = "/".join(map(str, errors[0].path)) or "<root>"
        return Parsed(None, problem=f"output breaks ocr_cloud_output at {where}: {errors[0].message}", **base)
    return Parsed(list(output["lines"]), **base)


def to_rows(lines: list[dict], *, page_id: str, der_sha256: str) -> list[dict]:
    return [{"engine": ROUTE, "page_id": page_id, "der_sha256": der_sha256, "line_idx": idx, "text": line["text"],
             "bbox_px": None, "tokens": None} for idx, line in enumerate(lines)]


def _claim(receipt_dir: Path, sha: str) -> Path:
    receipt_dir.mkdir(parents=True, exist_ok=True)
    n = 1
    while True:
        path = receipt_dir / f"read-{ROUTE}-{sha}.{n}.json"
        try:
            os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644))
            return path
        except FileExistsError:
            n += 1


def _add_tokens(total: dict[str, int], more: Mapping[str, int]) -> None:
    for key, value in more.items():
        total[key] = total.get(key, 0) + value


class _Page:
    """Collects the attempts of one page and writes its receipt."""

    def __init__(self, sha: str, page_id: str, model: str, effort: str, receipt_dir: Path):
        self.receipt_dir = receipt_dir
        self.data: dict[str, Any] = {
            "schema": RECEIPT_SCHEMA, "route": ROUTE, "der_sha256": sha, "page_id": page_id, "status": None,
            "reason": None, "model": model, "effort": effort, "model_actual": None, "cost_usd": 0.0,
            "cost_complete": True, "cost_estimated": True, "tokens": {}, "lines": 0, "uncertain_lines": [],
            "attempts": []}

    def attempt(self, status: str, res: S.SendResult, reserved: Reservation, charged: Decimal,
                parsed: Parsed | None) -> None:
        model = parsed.model_actual if parsed else None
        tokens = dict(parsed.tokens) if parsed else {}
        self.data["attempts"].append({
            "status": status, "exit_code": res.returncode, "timed_out": res.timed_out,
            "reserved_usd": float(reserved.amount), "cost_usd": float(charged), "model_actual": model,
            "tokens": tokens, "send_receipt": res.receipt_path.name})
        self.data["cost_usd"] = float(Decimal(str(self.data["cost_usd"])) + charged)
        self.data["cost_complete"] = self.data["cost_complete"] and parsed is not None and parsed.cost_usd is not None
        _add_tokens(self.data["tokens"], tokens)
        if model:
            self.data["model_actual"] = model

    def finish(self, status: str, reason: str | None = None) -> tuple[dict[str, Any], Path]:
        self.data.update(status=status, reason=reason)
        path = _claim(self.receipt_dir, self.data["der_sha256"])
        _write(path, self.data)
        return self.data, path


def read_page(path: str | os.PathLike, der_sha256: str, *, page_id: str, project: str | os.PathLike,
              receipt_dir: str | os.PathLike, isolation_receipt: str | os.PathLike, ledger: Ledger,
              routes_state: str | os.PathLike | None = None, config: config_mod.Config | None = None,
              source_env: Mapping[str, str] | None = None, timeout_s: float | None = None) -> PageRead:
    """Read one page image on the codex route; see the module docstring."""
    receipt_dir = Path(receipt_dir)
    cfg = config if config is not None else config_mod.load(project)
    if cfg[f"{ROUTE}.effort"] != EFFORT_LOCK:
        raise EffortLocked(cfg[f"{ROUTE}.effort"])
    page = _Page(der_sha256, page_id, cfg[f"{ROUTE}.model"], cfg[f"{ROUTE}.effort"], receipt_dir)
    problem = "no call made"
    for _ in range(1 + PARSE_RETRIES):
        try:
            reservation = ledger.reserve()
        except BudgetExceeded:
            page.finish("budget_stop", "budget_exceeded")
            raise
        try:
            res = S.send(path, der_sha256, route=ROUTE, project=project, receipt_dir=receipt_dir,
                          isolation_receipt=isolation_receipt, routes_state=routes_state, config=cfg,
                          source_env=source_env, timeout_s=timeout_s)
        except S.SendRefused as exc:
            ledger.release(reservation)
            page.finish("refused", exc.reason)
            raise
        except BaseException:
            ledger.settle(reservation, None)  # it may have run: charge the reservation
            raise
        parsed = parse_events(res.stdout) if res.stdout.strip() else None
        charged = ledger.settle(reservation, parsed.cost_usd if parsed else None)
        if res.timed_out:
            page.attempt("timeout", res, reservation, charged, parsed)
            page.finish("route_failed", "timeout")
            raise ReadFailed("timeout", f"no answer within the timeout ({res.receipt.get('duration_s')} s)")
        if res.returncode != 0 or (parsed is not None and parsed.is_error):
            page.attempt("failed", res, reservation, charged, parsed)
            page.finish("route_failed", "cli_failed")
            raise ReadFailed("cli_failed", f"{ROUTE} exited {res.returncode}"
                             + (" (error event)" if parsed is not None and parsed.is_error else ""))
        if parsed is None or parsed.lines is None:
            problem = parsed.problem if parsed is not None and parsed.problem else "empty output"
            page.attempt("parse_error", res, reservation, charged, parsed)
            continue
        page.attempt("ok", res, reservation, charged, parsed)
        page.data["lines"] = len(parsed.lines)
        page.data["uncertain_lines"] = [i for i, line in enumerate(parsed.lines) if line.get("uncertain") is True]
        receipt, receipt_path = page.finish("done")
        return PageRead(to_rows(parsed.lines, page_id=page_id, der_sha256=der_sha256), receipt, receipt_path)
    page.finish("parse_error", problem)
    raise ParseFailed(problem)
