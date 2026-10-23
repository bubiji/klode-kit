"""WI-00c3 four-route spike: one gated cloud call for one registered synthetic image (matrix 00c3-09/10).

``python -m klode_kit.eval.spike4 --route claude|codex --png FILE --project DIR --receipt FILE
[--routes-state FILE] --model M --effort E [--max-budget-usd X] --out FILE --trash DIR [--timeout-s N]``

Gate, in this order; each step runs only after the previous one passed:

1. ``KLODE_KIT_REAL=1`` - otherwise exit 2 (usage) and nothing is prepared;
2. ``--receipt`` is a schema-valid isolation receipt for this route with ``cloud_isolation: PASS`` and every
   check ok (``no_valid_receipt``);
3. ``--routes-state`` enables the route (``route_disabled``; ``isolation_probe.route_exit_code``);
4. the send directory is prepared and ``isolation_check.check`` reviews the exact invocation
   (``isolation_check:<failed checks>``) - the first step that starts a process (``<cli> --version``);
5. the CLI version - and for claude the pinned ``cloud_settings`` sha - equal the receipt's
   (``receipt_binding``). ``argv_sha`` / ``effective_config_sha`` are not compared: they bind the image sha and
   the random send directory, so they differ for every image by construction; step 4 re-reviews them instead.

Failure of 2-5: exit 61, the reason on stderr, no model call. Success: the call runs once and the raw output
(stdout / stderr / exit code, with the send directory and ``$HOME`` masked) plus the transcribed lines and the
reported cost and the guard hook's log entries (``<send dir>.guard.jsonl``, then moved to ``--trash``) are
written to ``--out`` as a replay recording (``recorded: true``); exit 0 when the call
returned a structured answer, 1 when it ran but did not (the recording is still written). The send directory
is always moved into ``--trash`` (never deleted).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from klode_kit.contract.schema import issues
from klode_kit.ocr import cloud_argv as CA
from klode_kit.ocr import isolation_check as IC
from klode_kit.ocr import isolation_probe as IP

USAGE_EXIT = 2
FAIL_EXIT = IP.ISOLATION_FAIL_EXIT
CALL_FAILED_EXIT = 1  # the call ran but did not return a schema answer; the recording is still written
DEFAULT_STATE = CA.PACKAGE / "config" / "routes_enabled.json"
RECORDING_SCHEMA = "klode-kit.replay_recording/1"


class GateError(Exception):
    """The route may not be used for this call (exit 61)."""


def receipt_ok(receipt: Any, route: str) -> bool:
    if not isinstance(receipt, Mapping) or receipt.get("route") != route:
        return False
    if issues("isolation_receipt", dict(receipt)):
        return False
    return receipt.get("cloud_isolation") == "PASS" and all(c.get("ok") is True for c in receipt["checks"])


def load_receipt(path: str | os.PathLike, route: str) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GateError(f"no_valid_receipt: cannot read {path}: {exc}") from exc
    if not receipt_ok(data, route):
        raise GateError(f"no_valid_receipt: {path} is not a PASS isolation receipt for route {route}")
    return data


def binding_ok(review: IC.Result, receipt: Mapping[str, Any]) -> list[str]:
    bad = []
    if review.cli_version != receipt.get("cli_version"):
        bad.append(f"cli_version {review.cli_version} != receipt {receipt.get('cli_version')}")
    if review.route == "claude" and review.cloud_settings_sha != receipt.get("cloud_settings_sha"):
        bad.append("cloud_settings_sha differs from the receipt")
    return bad


def build_invocation(route: str, send: Path, *, model: str, effort: str, max_budget_usd: float | None,
                     source_env: Mapping[str, str]) -> CA.Invocation:
    if route == "claude":
        return CA.claude(send, model=model, effort=effort, max_budget_usd=float(max_budget_usd or 0),
                         source_env=source_env)
    return CA.codex(send, model=model, effort=effort, source_env=source_env)


def transcribed_lines(route: str, events: list[dict]) -> list[str] | None:
    """The ``lines[].text`` of the structured answer, or None when the output carries no valid answer."""
    answer: Any = None
    if route == "claude":
        results = [e for e in events if e.get("type") == "result"]
        answer = results[-1].get("structured_output") if results else None
    else:
        msgs = [e["item"].get("text") for e in events
                if e.get("type") == "item.completed" and (e.get("item") or {}).get("type") == "agent_message"]
        try:
            answer = json.loads(msgs[-1]) if msgs else None
        except (TypeError, ValueError):
            answer = None
    if not isinstance(answer, Mapping) or not isinstance(answer.get("lines"), list):
        return None
    return [str(x.get("text", "")) for x in answer["lines"] if isinstance(x, Mapping)]


def _masker(send: Path):
    forms = sorted({str(send), os.path.realpath(send)}, key=len, reverse=True)
    home = os.path.expanduser("~")

    def mask(text: str) -> str:
        for f in forms:
            text = text.replace(f, "<send_dir>")
        return text.replace(home, "<home>") if home and home != "/" else text

    return mask


def collect_guard_log(send: Path, trash: str | os.PathLike) -> list[dict]:
    """Entries of the guard hook's ``<send dir>.guard.jsonl`` (send dir masked); the file is moved to ``trash``."""
    path = Path(f"{send}.guard.jsonl")
    mask = _masker(send)
    entries = [json.loads(mask(json.dumps(e, ensure_ascii=False))) for e in IP.read_guard_log(path)]
    IP.move_to_trash([path], trash)
    return entries


def recording(route: str, inv: CA.Invocation, execution: IP.Execution, *, der_sha256: str, png: str,
              model: str, effort: str, cli_version: str | None, send: Path,
              guard_log: list[dict] | None = None) -> dict[str, Any]:
    mask = _masker(send)
    lines = transcribed_lines(route, execution.events)
    use = IP.usage(route, execution.events)
    return {
        "schema": RECORDING_SCHEMA,
        "recorded": True,
        "engine": route,
        "der_sha256": der_sha256,
        "image": png,
        "model": model,
        "effort": effort,
        "cli_version": cli_version,
        "argv": [mask(str(t)) for t in inv.argv],
        "argv_sha": IC.argv_sha(inv.argv),
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "exit_code": execution.returncode,
        "timed_out": execution.timed_out,
        "stdout": mask(execution.stdout),
        "stderr": mask(execution.stderr),
        "lines": lines,
        "cost_usd": use["cost_usd"],
        "model_actual": use["model_actual"],
        "tokens": use["tokens"],
        "guard_log": guard_log or [],
    }


def run(args: argparse.Namespace, source_env: Mapping[str, str]) -> dict[str, Any]:
    """Gate + one call. Raises ``GateError`` before any model call when the route may not be used."""
    receipt = load_receipt(args.receipt, args.route)
    if IP.route_exit_code(args.routes_state, args.route) != 0:
        raise GateError(f"route_disabled: {args.route} is not enabled in {args.routes_state}")
    png_path = Path(args.png)
    data = png_path.read_bytes()
    sha = IP.sha256_bytes(data)
    send = CA.prepare_send_dir(data, sha)
    try:
        inv = build_invocation(args.route, send, model=args.model, effort=args.effort,
                               max_budget_usd=args.max_budget_usd, source_env=source_env)
        review = IC.check(inv, project=args.project)
        if not review.ok:
            raise GateError(f"isolation_check: {', '.join(review.failed)}")
        drift = binding_ok(review, receipt)
        if drift:
            raise GateError(f"receipt_binding: {'; '.join(drift)}")
        execution = IP.execute(inv, timeout_s=args.timeout_s)
        guard = collect_guard_log(send, args.trash)
        return recording(args.route, inv, execution, der_sha256=sha, png=args.png_label or png_path.name,
                         model=args.model, effort=args.effort, cli_version=review.cli_version, send=send,
                         guard_log=guard)
    finally:
        IP.move_to_trash([send, Path(f"{send}.guard.jsonl")], args.trash)


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="klode_kit.eval.spike4", description=__doc__.splitlines()[0])
    ap.add_argument("--route", choices=IP.ROUTES, required=True)
    ap.add_argument("--png", required=True)
    ap.add_argument("--png-label", default=None, help="name recorded for the image (default: file name)")
    ap.add_argument("--project", required=True)
    ap.add_argument("--receipt", required=True)
    ap.add_argument("--routes-state", default=str(DEFAULT_STATE))
    ap.add_argument("--model", required=True)
    ap.add_argument("--effort", required=True)
    ap.add_argument("--max-budget-usd", type=float, default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--trash", required=True)
    ap.add_argument("--timeout-s", type=float, default=900.0)
    return ap


def main(argv: list[str] | None = None) -> int:
    try:
        args = parser().parse_args(argv)
    except SystemExit as exc:
        return USAGE_EXIT if exc.code else 0
    if os.environ.get("KLODE_KIT_REAL") != "1":
        print("spike4: real cloud calls need KLODE_KIT_REAL=1 (gate G2); nothing was prepared", file=sys.stderr)
        return USAGE_EXIT
    try:
        rec = run(args, dict(os.environ))
    except GateError as exc:
        print(f"spike4: cloud_isolation FAIL - {exc}", file=sys.stderr)
        return FAIL_EXIT
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rec, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"engine": rec["engine"], "exit_code": rec["exit_code"], "lines": len(rec["lines"] or []),
                      "cost_usd": rec["cost_usd"]}))
    return 0 if rec["exit_code"] == 0 and rec["lines"] is not None else CALL_FAILED_EXIT


if __name__ == "__main__":  # pragma: no cover - CLI entry
    sys.exit(main())
