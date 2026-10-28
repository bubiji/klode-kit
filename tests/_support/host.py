"""Helpers for the host lanes (model_integration / host_acceptance) of WI-00d2.

- ``real_claude()``: the first ``claude`` on PATH that is NOT the replay fake (conftest puts tests/fakes/bin first).
- ``Ledger``: cost ledger ``work/host-spike/costs.jsonl`` (gitignored). Before every real call the test reserves
  the call's ``--max-budget-usd`` cap; the call is refused when ledger total + cap would exceed the WI cap
  (``WI_CAP_USD`` = 10, gate G2 authorisation for WI-00d2). After the call the actual ``total_cost_usd`` is
  booked (a call that reported none books its whole cap - the conservative direction).
- ``lab_dir(name)``: a fresh directory under ``tests/host/_lab/`` (gitignored; inside the trusted repository, G5).
- ``evidence_dir(name)``: ``work/host-spike/<name>/`` for raw host outputs (gitignored; may hold local paths).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from _support.flows import REPO

FAKES_BIN = (REPO / "tests" / "fakes" / "bin").resolve()
LAB = REPO / "tests" / "host" / "_lab"
EVIDENCE = REPO / "work" / "host-spike"
# Cumulative cap over work/host-spike/costs.jsonl. 10 for WI-00d2; 12.5 for WI-32d (user 2026-10-27: G2 total $20, this WI
# at most $4 on top of the $8.22 booked when it started; the U3 test reserves 2 x $2 before it calls).
# 13.5 for WI-32a: the ledger stood at $9.595 when it started, this WI may add at most $4 (G2 total stays $20).
WI_CAP_USD = 21.1  # user G2 authorisation 2026-10-27: $20 for this round on top of the $1.50 booked before it (minus $0.33 in costs-wi11b.jsonl)


def real_claude() -> str:
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if not d or Path(d).resolve() == FAKES_BIN:
            continue
        cand = Path(d) / "claude"
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    raise RuntimeError("no real claude on PATH")


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def lab_dir(name: str) -> Path:
    path = LAB / f"{name}-{stamp()}"
    path.mkdir(parents=True)
    return path


def evidence_dir(name: str) -> Path:
    path = EVIDENCE / f"{name}-{stamp()}"
    path.mkdir(parents=True)
    return path


class BudgetExceeded(RuntimeError):
    pass


class Ledger:
    def __init__(self, path: Path = EVIDENCE / "costs.jsonl"):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def total(self) -> float:
        if not self.path.exists():
            return 0.0
        return sum(json.loads(ln)["booked_usd"] for ln in self.path.read_text(encoding="utf-8").splitlines() if ln)

    def reserve(self, label: str, cap: float) -> None:
        if self.total() + cap > WI_CAP_USD + 1e-9:
            raise BudgetExceeded(f"{label}: ledger {self.total():.4f} + cap {cap} > WI cap {WI_CAP_USD}")

    def book(self, label: str, cap: float, cost: float | None) -> None:
        booked = cap if cost is None else float(cost)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"label": label, "cap_usd": cap, "reported_usd": cost, "booked_usd": booked,
                                 "at": stamp()}) + "\n")


def run_claude_p(args: list[str], *, cwd: Path, cap: float, label: str, ledger: Ledger, evidence: Path,
                 timeout: float = 900, env: dict[str, str] | None = None) -> tuple[subprocess.CompletedProcess, dict | None]:
    """``claude -p <args> --max-budget-usd <cap>``; raw stdout/stderr saved; cost booked. Returns (proc, json)."""
    ledger.reserve(label, cap)
    argv = [real_claude(), "-p", *args[:-1], "--max-budget-usd", f"{cap:.2f}", args[-1]]
    (evidence / f"{label}.argv.json").write_text(json.dumps(argv, ensure_ascii=False, indent=1), encoding="utf-8")
    t0 = time.monotonic()
    proc = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
    (evidence / f"{label}.stdout.json").write_bytes(proc.stdout)
    (evidence / f"{label}.stderr.txt").write_bytes(proc.stderr)
    try:
        data = json.loads(proc.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        data = None
    cost = data.get("total_cost_usd") if isinstance(data, dict) else None
    ledger.book(label, cap, cost if isinstance(cost, (int, float)) else None)
    (evidence / f"{label}.meta.json").write_text(json.dumps({"rc": proc.returncode, "cost": cost,
                                                             "seconds": round(time.monotonic() - t0, 1)}))
    return proc, data


def copy_tree(src: Path, dst: Path) -> Path:
    shutil.copytree(src, dst)
    return dst
