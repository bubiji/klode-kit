"""Coverage gate: ``python -m klode_kit.devtools.coverage_gate --config <config.json> <coverage.json>``.

Reads a coverage.py JSON report (one merged report across the lanes being judged) and the
thresholds from ``.claude/tdd-guardian/config.json`` (``coverageThresholds`` and
``criticalPaths``). Rules (plan section 0.3):
- a report measuring 0 lines FAILs (0/0 would otherwise read as 100%);
- every measured metric must meet its threshold; an unmeasured metric (coverage.py has no
  function coverage) WARNs and is never treated as 0 or as a pass;
- every criticalPaths glob must match >= 1 file in the report, and the matched files
  together must meet the entry's thresholds (missing keys inherit the global ones).
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import sys
from pathlib import Path

METRICS = ("lines", "statements", "branches", "functions")


def _pct(covered: int, total: int) -> float | None:
    return None if total == 0 else 100.0 * covered / total


def metrics_for(files: list[dict]) -> dict[str, float | None]:
    stmts = sum(f["summary"]["num_statements"] for f in files)
    lines = sum(f["summary"]["covered_lines"] for f in files)
    branches = sum(f["summary"].get("num_branches", 0) for f in files)
    cbranches = sum(f["summary"].get("covered_branches", 0) for f in files)
    line_pct = _pct(lines, stmts)
    return {"lines": line_pct, "statements": line_pct, "branches": _pct(cbranches, branches), "functions": None}


def _judge(label: str, values: dict, thresholds: dict, out: list[str]) -> bool:
    ok = True
    for metric in METRICS:
        want = thresholds.get(metric)
        if want is None:
            continue
        got = values.get(metric)
        if got is None:
            out.append(f"WARN {label}: {metric} not measured (threshold {want})")
        elif got + 1e-9 < want:
            out.append(f"FAIL {label}: {metric} {got:.2f}% < {want}%")
            ok = False
        else:
            out.append(f"ok   {label}: {metric} {got:.2f}% >= {want}%")
    return ok


def gate(report: dict, config: dict) -> tuple[bool, list[str]]:
    out: list[str] = []
    files = report.get("files", {})
    totals = report.get("totals", {})
    if int(totals.get("num_statements", 0)) == 0:
        return False, ["FAIL global: 0 lines measured (an empty report would score 100%)"]
    thresholds = config.get("coverageThresholds", {})
    ok = _judge("global", metrics_for(list(files.values())), thresholds, out)
    for entry in config.get("criticalPaths", []) or []:
        glob = entry["glob"]
        matched = [f for name, f in files.items() if fnmatch.fnmatch(name, glob)]
        if not matched:
            out.append(f"FAIL critical {glob}: glob matched no file in the report")
            ok = False
            continue
        merged = {**thresholds, **(entry.get("thresholds") or {})}
        ok = _judge(f"critical {glob} ({len(matched)} file(s))", metrics_for(matched), merged, out) and ok
    return ok, out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="klode-kit dev coverage-gate")
    parser.add_argument("report", type=Path)
    parser.add_argument("--config", type=Path, default=Path(".claude/tdd-guardian/config.json"))
    args = parser.parse_args(argv)
    report = json.loads(args.report.read_text(encoding="utf-8"))
    config = json.loads(args.config.read_text(encoding="utf-8"))
    ok, lines = gate(report, config)
    for line in lines:
        print(line)
    print("coverage_gate: PASS" if ok else "coverage_gate: FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
