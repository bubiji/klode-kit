"""Claude read-image adapter: one page image -> ``ocr_line`` rows (WI-06a; design 02 r2.2-A, r2.3, r2.6, R2-08,
R2-12; plan WI-06a).

``read_page(path, der_sha256, *, page_id, project, receipt_dir, isolation_receipt, ledger, ...)``:

1. ``ledger.reserve()`` before every dispatch (``BudgetExceeded``, exit 50, when the batch budget is used up -
   nothing is started); the reservation is the call's ``--max-budget-usd``;
2. ``send.send(...)`` - the only way an image leaves the machine (sha allowlist, isolation review, G2 gate;
   ``SendRefused`` 60/61 passes through and the reservation is released);
3. the CLI's ``--output-format json`` result is read: ``total_cost_usd`` settles the reservation (unknown cost:
   the reservation is charged), ``modelUsage`` gives ``model_actual`` (the model that answered, the one with the
   highest ``costUSD``), ``structured_output`` (or the ``result`` text as JSON) must match
   ``schemas/ocr_cloud_output.json``.

Outcomes (design 02 r2.3 reason table):

- timeout, non-zero exit, or ``is_error`` -> ``ReadFailed`` (route_failed; the batch may retry it, route_retry);
- output that is not JSON or breaks the output schema -> one more call; still bad -> ``ParseFailed``
  (parse_error); no further retries;
- otherwise rows: ``{"engine": "claude", "page_id", "der_sha256": <the image sent>, "line_idx": 0..n-1,
  "text": <the model's line, verbatim>, "bbox_px": null, "tokens": null}`` - so any span ``[s, e]`` of a field is
  taken in this very text (r2.2-D prov ``span``; cloud routes carry no geometry, R2-08).

Page receipt ``<receipt_dir>/read-claude-<sha>.<n>.json`` (``klode-kit.cloud_read/1``, defined here; ``n`` the
first free number, claimed with ``O_EXCL``)::

    {schema, route, der_sha256, page_id, status: done|parse_error|route_failed|budget_stop|refused, reason,
     model, effort (configured), model_actual (from the response), cost_usd (sum of all attempts),
     cost_complete, lines, uncertain_lines: [line_idx the model marked uncertain],
     attempts: [{status: ok|parse_error|failed|timeout, exit_code, timed_out, reserved_usd, cost_usd,
                 model_actual, send_receipt: <file name of the send receipt>}]}

No path, no file name from the data project and no pixels (like the send receipt, 05b-11).
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import jsonschema

from klode_kit import config as config_mod
from klode_kit.ocr import cloud_argv as CA
from klode_kit.ocr import send as S
from klode_kit.ocr.cloud_budget import BudgetExceeded, Ledger, Reservation

ROUTE = "claude"
RECEIPT_SCHEMA = "klode-kit.cloud_read/1"
PARSE_RETRIES = 1


class ReadFailed(Exception):
    """The CLI timed out or failed (``route_failed``)."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


class ParseFailed(Exception):
    """The output broke the schema on the first call and on the retry (``parse_error``)."""

    def __init__(self, detail: str):
        super().__init__(f"parse_error: {detail}")
        self.reason = "parse_error"


@dataclass(frozen=True)
class Parsed:
    """One CLI result: ``lines`` (None when the output breaks the schema), cost, model, error state."""

    lines: list[dict] | None
    cost_usd: float | None
    model_actual: str | None
    is_error: bool
    problem: str | None


@dataclass(frozen=True)
class PageRead:
    rows: list[dict]
    receipt: dict[str, Any]
    receipt_path: Path


_VALIDATOR: jsonschema.protocols.Validator | None = None


def _validator() -> jsonschema.protocols.Validator:
    global _VALIDATOR
    if _VALIDATOR is None:
        schema = json.loads(CA.output_schema_text())
        _VALIDATOR = jsonschema.Draft202012Validator(schema)
    return _VALIDATOR


def _model_actual(usage: Any) -> str | None:
    if not isinstance(usage, dict) or not usage:
        return None

    def cost(name: str) -> float:
        entry = usage[name]
        value = entry.get("costUSD") if isinstance(entry, dict) else None
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0

    return sorted(usage, key=lambda name: (-cost(name), name))[0]


def parse_result(stdout: str) -> Parsed:
    """Read one ``--output-format json`` result (see module docstring)."""
    try:
        data = json.loads(stdout)
    except ValueError:
        return Parsed(None, None, None, False, "stdout is not JSON")
    if not isinstance(data, dict):
        return Parsed(None, None, None, False, "stdout is not a JSON object")
    raw_cost = data.get("total_cost_usd")
    cost = float(raw_cost) if isinstance(raw_cost, (int, float)) and not isinstance(raw_cost, bool) else None
    model = _model_actual(data.get("modelUsage"))
    is_error = data.get("is_error") is True
    output = data.get("structured_output")
    if output is None and isinstance(data.get("result"), str):
        try:
            output = json.loads(data["result"])
        except ValueError:
            output = None
    if output is None:
        return Parsed(None, cost, model, is_error, "no structured output")
    errors = sorted(_validator().iter_errors(output), key=lambda e: list(e.path))
    if errors:
        where = "/".join(map(str, errors[0].path)) or "<root>"
        return Parsed(None, cost, model, is_error, f"output breaks ocr_cloud_output at {where}: {errors[0].message}")
    return Parsed(list(output["lines"]), cost, model, is_error, None)


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


def _write(path: Path, data: dict) -> None:
    tmp = path.parent / f".{path.name}.{secrets.token_hex(8)}.tmp"
    blob = (json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        os.write(fd, blob)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)


class _Page:
    """Collects the attempts of one page and writes its receipt."""

    def __init__(self, sha: str, page_id: str, model: str, effort: str, receipt_dir: Path):
        self.receipt_dir = receipt_dir
        self.data: dict[str, Any] = {
            "schema": RECEIPT_SCHEMA, "route": ROUTE, "der_sha256": sha, "page_id": page_id, "status": None,
            "reason": None, "model": model, "effort": effort, "model_actual": None, "cost_usd": 0.0,
            "cost_complete": True, "lines": 0, "uncertain_lines": [], "attempts": []}

    def attempt(self, status: str, res: S.SendResult, reserved: Reservation, charged: Any, parsed: Parsed | None,
                cost_known: bool) -> None:
        model = parsed.model_actual if parsed else None
        self.data["attempts"].append({
            "status": status, "exit_code": res.returncode, "timed_out": res.timed_out,
            "reserved_usd": float(reserved.amount), "cost_usd": float(charged), "model_actual": model,
            "send_receipt": res.receipt_path.name})
        self.data["cost_usd"] = round(self.data["cost_usd"] + float(charged), 9)
        self.data["cost_complete"] = self.data["cost_complete"] and cost_known
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
    """Read one page image on the claude route; see the module docstring."""
    receipt_dir = Path(receipt_dir)
    cfg = config if config is not None else config_mod.load(project)
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
                          source_env=source_env, timeout_s=timeout_s, max_budget_usd=float(reservation.amount))
        except S.SendRefused as exc:
            ledger.release(reservation)
            page.finish("refused", exc.reason)
            raise
        except BaseException:
            ledger.settle(reservation, None)  # it may have run: charge the reservation
            raise
        parsed = parse_result(res.stdout) if res.stdout.strip() else None
        cost = parsed.cost_usd if parsed else None
        charged = ledger.settle(reservation, cost)
        known = cost is not None
        if res.timed_out:
            page.attempt("timeout", res, reservation, charged, parsed, known)
            page.finish("route_failed", "timeout")
            raise ReadFailed("timeout", f"no answer within the timeout ({res.receipt.get('duration_s')} s)")
        if res.returncode != 0 or (parsed is not None and parsed.is_error):
            page.attempt("failed", res, reservation, charged, parsed, known)
            page.finish("route_failed", "cli_failed")
            raise ReadFailed("cli_failed", f"{ROUTE} exited {res.returncode}"
                             + (" (is_error)" if parsed is not None and parsed.is_error else ""))
        if parsed is None or parsed.lines is None:
            problem = parsed.problem if parsed is not None and parsed.problem else "empty output"
            page.attempt("parse_error", res, reservation, charged, parsed, known)
            continue
        page.attempt("ok", res, reservation, charged, parsed, known)
        page.data["lines"] = len(parsed.lines)
        page.data["uncertain_lines"] = [i for i, line in enumerate(parsed.lines) if line.get("uncertain") is True]
        receipt, receipt_path = page.finish("done")
        return PageRead(to_rows(parsed.lines, page_id=page_id, der_sha256=der_sha256), receipt, receipt_path)
    page.finish("parse_error", problem)
    raise ParseFailed(problem)
