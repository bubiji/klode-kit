"""WI-00s: devtools/coverage_gate (plan section 0.3). Added cases: the matrix has no 00s row for it.

Input: a coverage.py JSON report + the tdd-guardian config (coverageThresholds, criticalPaths).
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

from klode_kit.devtools import coverage_gate
import pytest

pytestmark = pytest.mark.unit


def file_entry(stmts: int, covered: int, branches: int, covered_branches: int) -> dict:
    return {
        "summary": {
            "num_statements": stmts,
            "covered_lines": covered,
            "num_branches": branches,
            "covered_branches": covered_branches,
        }
    }


def report(files: dict[str, dict]) -> dict:
    tot = {k: sum(f["summary"][k] for f in files.values()) for k in ("num_statements", "covered_lines", "num_branches", "covered_branches")}
    return {"meta": {"format": 3}, "files": files, "totals": tot}


def run(tmp_path: Path, rep: dict, thresholds: dict, critical: list | None = None) -> tuple[int, str]:
    rp = tmp_path / "coverage.json"
    cp = tmp_path / "config.json"
    rp.write_text(json.dumps(rep))
    cp.write_text(json.dumps({"coverageThresholds": thresholds, "criticalPaths": critical or []}))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = coverage_gate.main(["--config", str(cp), str(rp)])
    return code, buf.getvalue()


T = {"lines": 90, "functions": 90, "branches": 85, "statements": 90}


def test_passes_at_thresholds(tmp_path):
    code, out = run(tmp_path, report({"src/klode_kit/a.py": file_entry(100, 90, 20, 17)}), T)
    assert code == 0, out
    assert "PASS" in out


def test_branch_just_below_threshold_fails(tmp_path):
    code, out = run(tmp_path, report({"src/klode_kit/a.py": file_entry(100, 100, 100, 84)}), T)
    assert code != 0
    assert "branches" in out


def test_lines_just_below_threshold_fails(tmp_path):
    code, out = run(tmp_path, report({"src/klode_kit/a.py": file_entry(1000, 899, 10, 10)}), T)
    assert code != 0
    assert "lines" in out


def test_zero_lines_measured_fails(tmp_path):
    code, out = run(tmp_path, report({}), T)
    assert code != 0
    assert "0 lines" in out


def test_functions_unmeasured_warns_not_fails(tmp_path):
    code, out = run(tmp_path, report({"src/klode_kit/a.py": file_entry(10, 10, 2, 2)}), T)
    assert code == 0
    assert "WARN" in out and "functions" in out


def test_critical_glob_matching_nothing_fails(tmp_path):
    rep = report({"src/klode_kit/a.py": file_entry(10, 10, 2, 2)})
    code, out = run(tmp_path, rep, T, [{"glob": "src/klode_kit/publish.py", "thresholds": {"branches": 95}}])
    assert code != 0
    assert "src/klode_kit/publish.py" in out
    assert "matched no file" in out


def test_critical_path_below_its_own_threshold_fails_even_when_global_passes(tmp_path):
    rep = report(
        {
            "src/klode_kit/big.py": file_entry(1000, 1000, 200, 200),
            "src/klode_kit/publish.py": file_entry(10, 10, 20, 18),
        }
    )
    code, out = run(tmp_path, rep, T, [{"glob": "src/klode_kit/publish.py", "thresholds": {"branches": 95}}])
    assert code != 0
    assert "publish.py" in out


def test_critical_glob_star_matches_nested(tmp_path):
    rep = report({"src/klode_kit/audit/writeback.py": file_entry(10, 10, 4, 4)})
    code, out = run(tmp_path, rep, T, [{"glob": "src/klode_kit/audit/*.py", "thresholds": {"branches": 95}}])
    assert code == 0, out


def test_threshold_keys_absent_are_not_judged(tmp_path):
    code, out = run(tmp_path, report({"src/klode_kit/a.py": file_entry(10, 5, 2, 0)}), {"lines": 40})
    assert code == 0, out
    assert "branches" not in out


def test_cli_reads_default_config_path(tmp_path, monkeypatch):
    (tmp_path / ".claude" / "tdd-guardian").mkdir(parents=True)
    (tmp_path / ".claude" / "tdd-guardian" / "config.json").write_text(json.dumps({"coverageThresholds": {"lines": 99}}))
    (tmp_path / "c.json").write_text(json.dumps(report({"src/klode_kit/a.py": file_entry(100, 98, 0, 0)})))
    monkeypatch.chdir(tmp_path)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = coverage_gate.main(["c.json"])
    assert code != 0
    assert "lines 98.00% < 99%" in buf.getvalue()
