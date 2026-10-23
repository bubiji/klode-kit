"""Stage stand-in driven by ``workflows/kb-pipeline.js`` in the WI-00d2 host spike (development tooling only).

The real ``pipeline.py`` arrives with later work items. The spike needs only the r2.5 / r2.6 contract the
workflow shell sees, so each stage here does nothing but hash its inputs and write a receipt and one output.

``klode-kit dev spike-pipeline --stage <s> --project DIR --batch ID`` prints
``{stage, exit_code, state, cached, receipt, receipt_sha256}`` and exits with ``exit_code``:

- stage 1 ``preflight -> ocr -> audit``: ``audit`` exits 10 and sets ``work/<batch>/state.json`` to
  ``awaiting_confirm``; stage 2 ``writeback -> build -> check`` (``check`` sets ``done``);
- receipts ``work/<batch>/receipts/<stage>.json`` (schema ``receipt``), outputs ``work/<batch>/<stage>.out``;
- ``inputs_hash`` covers the ``inbox/`` files (relpath + content) and the predecessor receipt's sha; a done
  receipt with the same inputs_hash and config_hash whose outputs still match is reused untouched
  (``cached: true``);
- predecessor without a done receipt, or stage 2 before ``awaiting_confirm``: exit 30, nothing written;
  unknown stage: exit 2.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from klode_kit import receipts
from klode_kit.receipts import dumps

STAGES = ("preflight", "ocr", "audit", "writeback", "build", "check")
STAGE2 = ("writeback", "build", "check")
EXIT_AWAITING, EXIT_STALE = 10, 30
CONFIG_HASH = hashlib.sha256(b"klode-kit spike-pipeline config v1").hexdigest()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _inbox_hash(project: Path) -> str:
    h = hashlib.sha256()
    inbox = project / "inbox"
    for path in sorted(p for p in inbox.rglob("*") if p.is_file()) if inbox.is_dir() else []:
        rel = path.relative_to(project).as_posix()
        h.update(_sha(rel.encode("utf-8") + b"\0" + path.read_bytes()).encode("ascii"))
    return h.hexdigest()


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _state(work: Path) -> str | None:
    data = _read_json(work / "state.json")
    return data.get("status") if data else None


def _set_state(work: Path, batch: str, status: str) -> None:
    tmp = work / ".state.json.tmp"
    tmp.write_bytes(dumps({"batch": batch, "status": status}))
    os.replace(tmp, work / "state.json")


def _outputs_match(project: Path, rec: dict) -> bool:
    for out in rec.get("outputs", []):
        path = project / out["path"]
        if not path.is_file() or _sha(path.read_bytes()) != out["sha256"]:
            return False
    return True


def run_stage(project: Path, batch: str, stage: str) -> dict:
    work = project / "work" / batch
    rdir = work / "receipts"
    idx = STAGES.index(stage)
    prev_sha = ""
    if idx:
        prev_path = rdir / f"{STAGES[idx - 1]}.json"
        prev = _read_json(prev_path)
        if not prev or prev.get("status") != "done":
            return {"stage": stage, "exit_code": EXIT_STALE, "state": _state(work), "cached": False,
                    "error": f"predecessor {STAGES[idx - 1]} has no done receipt"}
        prev_sha = _sha(prev_path.read_bytes())
    if stage in STAGE2 and _state(work) not in ("awaiting_confirm", "done"):
        return {"stage": stage, "exit_code": EXIT_STALE, "state": _state(work), "cached": False,
                "error": "stage 2 needs the batch at awaiting_confirm"}
    inputs_hash = _sha(f"{stage}\0{_inbox_hash(project)}\0{prev_sha}".encode("utf-8"))
    exit_code = EXIT_AWAITING if stage == "audit" else 0
    rpath = rdir / f"{stage}.json"
    old = _read_json(rpath)
    cached = bool(old and old.get("status") == "done" and old.get("inputs_hash") == inputs_hash
                  and old.get("config_hash") == CONFIG_HASH and _outputs_match(project, old))
    if not cached:
        started = _now()
        out = work / f"{stage}.out"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(f"{stage} {inputs_hash}\n", encoding="utf-8")
        receipts.write(rpath, {
            "schema": "klode-kit.receipt/1", "step": stage, "status": "done", "started": started, "ended": _now(),
            "inputs_hash": inputs_hash, "config_hash": CONFIG_HASH, "config_version": 1,
            "outputs": [{"path": out.relative_to(project).as_posix(), "sha256": _sha(out.read_bytes())}],
            "exit_code": exit_code,
        }, "receipt")
    if stage == "audit":
        _set_state(work, batch, "awaiting_confirm")
    elif stage == "check":
        _set_state(work, batch, "done")
    elif stage == "preflight" and _state(work) is None:
        _set_state(work, batch, "running")
    return {"stage": stage, "exit_code": exit_code, "state": _state(work), "cached": cached,
            "receipt": rpath.relative_to(project).as_posix(), "receipt_sha256": _sha(rpath.read_bytes())}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="klode-kit dev spike-pipeline")
    parser.add_argument("--stage", required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--batch", required=True)
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    if args.stage not in STAGES:
        print(f"spike-pipeline: unknown stage {args.stage!r}; one of {', '.join(STAGES)}", file=sys.stderr)
        return 2
    result = run_stage(args.project.resolve(), args.batch, args.stage)
    sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
    return result["exit_code"]
