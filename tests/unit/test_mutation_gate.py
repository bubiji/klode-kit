"""WI-15c: ``mutation_gate`` - the manual, pre-release mutation gate over the config's criticalPaths.

Matrix (all unit lane; the engine cases mutate a tiny synthetic package for real, in subprocesses):
  15c-01 score               S4  score = killed / (killed + survived), in [0, 1], monotone in kills (hypothesis)
  15c-02 classify            S2  exit code -> killed | survived | timeout | no_tests | error
  15c-03 summarize           S2  timeout counts as killed; no_tests and error count as survived (never a free pass)
  15c-04 module_name         S1  src/klode_kit/a/b.py -> klode_kit.a.b
  15c-05 critical_files      S2/S3  criticalPaths globs from config.json; a glob matching nothing is an error
  15c-06 generate            S1  real mutants carry file line, old text, new text
  15c-07 tests_for           S2  per-function test selection, file-level union as the fallback
  15c-08 receipt             S5  write_receipt -> release_check reads the same score/sha/age (one criterion, two readers)
  15c-09 partial run         S3  a run that did not cover every criticalPath never writes work/mutation.json
  15c-10 engine, strong      S1  real mutation: a precise suite kills every mutant (score 1.0)
  15c-11 engine, weak        S1  real mutation: a weak suite leaves exactly the unkilled ones, listed file:line:change
  15c-12 mapping, isolation  S2  a function's mutants are tested only by the tests that execute that function
  15c-13 cli                 S2  --file runs one file only; usage errors exit 2
Law: score is a ratio, monotone in kills (15c-01); the receipt is a round trip with release_check (15c-08).
"""

from __future__ import annotations

import datetime as dt
import json
import textwrap

import pytest
from hypothesis import given, strategies as st

from klode_kit.devtools import mutation_gate as G
from klode_kit.devtools import release_check as RC

pytestmark = pytest.mark.unit

NOW = dt.datetime(2026, 10, 27, 12, 0, tzinfo=dt.timezone.utc)
SHA = "a" * 64


# --- 15c-01 ---------------------------------------------------------------------------------------------------

def test_score_examples():
    assert G.score(8, 2) == 0.8
    assert G.score(0, 5) == 0.0
    assert G.score(7, 0) == 1.0


def test_score_of_nothing_is_an_error_not_a_free_pass():
    with pytest.raises(ValueError):
        G.score(0, 0)


@given(k=st.integers(0, 10_000), s=st.integers(0, 10_000))
def test_score_is_a_bounded_ratio_monotone_in_kills(k, s):
    if k + s == 0:
        return
    sc = G.score(k, s)
    assert 0.0 <= sc <= 1.0
    assert sc == pytest.approx(k / (k + s))
    assert G.score(k + 1, s) >= sc          # one more kill never lowers it
    assert G.score(k, s + 1) <= sc          # one more survivor never raises it


# --- 15c-02 / 15c-03 -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("rc, timed_out, expected", [
    (0, False, "survived"),     # every test passed against the mutant
    (1, False, "killed"),       # a test failed
    (2, False, "killed"),       # collection error / interrupted: the mutant broke the code under test
    (5, False, "no_tests"),     # nothing selected: untested code
    (3, False, "error"),        # pytest internal error
    (4, False, "error"),        # usage error
    (None, False, "error"),
    (0, True, "timeout"),       # a timeout wins whatever the (killed) process left behind
    (1, True, "timeout"),
])
def test_classify(rc, timed_out, expected):
    assert G.classify(rc, timed_out) == expected


def test_summarize_counts_each_status_and_never_gives_free_passes():
    rows = [{"status": s} for s in ["killed"] * 6 + ["timeout"] * 2 + ["survived"] * 1 + ["no_tests"] * 1 + ["error"] * 1]
    out = G.summarize(rows)
    assert (out["killed"], out["timeout"], out["survived"], out["no_tests"], out["error"]) == (6, 2, 1, 1, 1)
    assert out["mutants"] == 11
    # killed + timeout = 8 caught; survived + no_tests + error = 3 not proven caught
    assert out["score"] == pytest.approx(8 / 11)


def test_summarize_empty_has_no_score():
    assert G.summarize([])["score"] is None


# --- 15c-04 / 15c-05 -------------------------------------------------------------------------------------------

def test_module_name():
    assert G.module_name("src/klode_kit/audit/align.py") == "klode_kit.audit.align"
    assert G.module_name("src/klode_kit/publish.py") == "klode_kit.publish"
    assert G.module_name("src/pkg/__init__.py") == "pkg"


def _config(root, globs):
    cfg = root / ".claude" / "tdd-guardian" / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({"criticalPaths": [{"glob": g} for g in globs]}))


def test_critical_files_follow_the_config(tmp_path):
    (tmp_path / "src" / "p").mkdir(parents=True)
    (tmp_path / "src" / "p" / "b.py").write_text("x = 1\n")
    (tmp_path / "src" / "p" / "a.py").write_text("x = 1\n")
    _config(tmp_path, ["src/p/b.py", "src/p/a.py", "src/p/a.py"])
    assert G.critical_files(tmp_path) == ["src/p/a.py", "src/p/b.py"]     # sorted, de-duplicated


def test_critical_files_glob_matching_nothing_is_an_error(tmp_path):
    (tmp_path / "src").mkdir()
    _config(tmp_path, ["src/missing.py"])
    with pytest.raises(ValueError, match="src/missing.py"):
        G.critical_files(tmp_path)


def test_critical_files_the_repo_config_lists_real_files():
    from _support.flows import REPO

    files = G.critical_files(REPO)
    assert len(files) >= 16 and all((REPO / f).is_file() for f in files)
    assert "src/klode_kit/contract/provenance.py" in files


# --- 15c-06 ----------------------------------------------------------------------------------------------------

SYNTH = textwrap.dedent('''\
    def add(a, b):
        return a + b


    def neg(a):
        return -a
''')


def test_generate_reports_line_old_and_new():
    mutated, mutants = G.generate("src/synth/calc.py", SYNTH)
    assert "mutmut" in mutated and len(mutants) == 2
    by_line = {}
    for m in mutants:
        by_line.setdefault(m.line, []).append(m)
    assert set(by_line) == {2, 6}                                          # the two return statements, original numbering
    assert any("a + b" in m.old and "a - b" in m.new for m in by_line[2])
    assert any("-a" in m.old and "+a" in m.new for m in by_line[6])
    names = [m.name for m in mutants]
    assert len(set(names)) == len(names)                                   # names identify a mutant uniquely
    assert {m.func for m in mutants} == {"x_add", "x_neg"}


def test_function_ranges_cover_methods_and_functions():
    code = "class K:\n    def m(self):\n        return 1\n\n\ndef f():\n    return 2\n"
    ranges = G.function_ranges(code)
    assert ranges["x_f"] == (6, 7)
    (key,) = [k for k in ranges if k != "x_f"]
    assert key.endswith("m") and "K" in key and ranges[key] == (2, 3)


# --- 15c-07 ----------------------------------------------------------------------------------------------------

def test_tests_for_prefers_the_function_and_falls_back_to_the_file_union():
    entry = {"funcs": {"x_add": ["t.py::a", "t.py::b"], "x_neg": ["t.py::c"]}, "all": ["t.py::a", "t.py::b", "t.py::c"]}
    assert G.tests_for(entry, "x_add") == ["t.py::a", "t.py::b"]
    assert G.tests_for(entry, "x_neg") == ["t.py::c"]
    assert G.tests_for(entry, "x_never_executed") == []                   # known-by-ast, no test runs it
    assert G.tests_for(entry, None) == ["t.py::a", "t.py::b", "t.py::c"]   # unknown function: union, never nothing


# --- 15c-08 / 15c-09 -------------------------------------------------------------------------------------------

def _file_result(killed, survived, rel="src/synth/calc.py"):
    muts = [{"name": f"k{i}", "status": "killed", "line": 1, "old": "a", "new": "b", "func": "x_f", "tests": 1} for i in range(killed)]
    muts += [{"name": f"s{i}", "status": "survived", "line": 2, "old": "a", "new": "c", "func": "x_f", "tests": 1} for i in range(survived)]
    return {"status": "scored", **G.summarize(muts), "mutants": muts, "seconds": 1.0, "wall_seconds": 1.0, "excluded_tests": []}


def _report(files, unrun=()):
    return {"files": files, "unrun": list(unrun), "partial": bool(unrun), "release_sha": SHA,
            "started_at": NOW.isoformat(), "finished_at": NOW.isoformat(), "markers": "unit", "threshold": G.THRESHOLD, "jobs": 1}


@pytest.mark.parametrize("killed, survived, ok", [(9, 1, True), (8, 2, True), (7, 3, False), (10, 0, True), (0, 4, False)])
def test_receipt_is_read_back_by_release_check_with_the_same_verdict(tmp_path, killed, survived, ok):
    report = _report({"src/synth/calc.py": _file_result(killed, survived)})
    path = G.write_receipt(tmp_path, report, now=NOW)
    assert path == tmp_path / "work" / "mutation.json"
    doc = json.loads(path.read_text())
    assert doc["schema"] == "klode-kit.mutation/1" and doc["code_tree_sha"] == SHA
    assert doc["score"] == pytest.approx(killed / (killed + survived))
    entry = RC._mutation(tmp_path, SHA, NOW, "required", RC.MUTATION_MIN, RC.DEFAULT_MAX_AGE)
    assert entry["ok"] is ok
    assert ("score_below_min" in entry["reasons"]) is (not ok)
    # the receipt is bound to the sha and to the clock the same way for both writer and reader
    assert "stale_sha" in RC._mutation(tmp_path, "b" * 64, NOW, "required", RC.MUTATION_MIN, RC.DEFAULT_MAX_AGE)["reasons"]
    later = NOW + dt.timedelta(days=8)
    assert "too_old" in RC._mutation(tmp_path, SHA, later, "required", RC.MUTATION_MIN, RC.DEFAULT_MAX_AGE)["reasons"]


def test_the_receipt_threshold_is_release_checks_threshold():
    assert G.THRESHOLD == RC.MUTATION_MIN == 0.80


def test_total_score_pools_mutants_not_averages_files(tmp_path):
    report = _report({"src/a.py": _file_result(1, 0), "src/b.py": _file_result(0, 99)})
    doc = json.loads(G.write_receipt(tmp_path, report, now=NOW).read_text())
    assert doc["score"] == pytest.approx(1 / 100)
    assert doc["files"]["src/a.py"]["score"] == 1.0 and doc["files"]["src/b.py"]["score"] == 0.0


def test_partial_run_never_writes_the_release_receipt(tmp_path):
    report = _report({"src/a.py": _file_result(10, 0)}, unrun=["src/b.py"])
    assert G.write_receipt(tmp_path, report, now=NOW) is None
    assert not (tmp_path / "work" / "mutation.json").exists()


def test_unscored_file_blocks_the_release_receipt(tmp_path):
    bad = {"status": "baseline_failed", "mutants": [], "excluded_tests": ["t.py::x"], "seconds": 0.0, "wall_seconds": 0.0}
    report = _report({"src/a.py": _file_result(10, 0), "src/b.py": bad})
    assert G.write_receipt(tmp_path, report, now=NOW) is None


def test_markdown_lists_survivors_as_file_line_change_and_the_per_file_table():
    report = _report({"src/synth/calc.py": _file_result(8, 2)})
    text = G.render_markdown(report)
    assert "| src/synth/calc.py |" in text and "0.80" in text
    assert "src/synth/calc.py:2:" in text and "`a`" in text and "`c`" in text
    assert G.survivors(report) == ["src/synth/calc.py:2: a -> c", "src/synth/calc.py:2: a -> c"]


# --- 15c-10 .. 15c-12: the real engine on a synthetic package -------------------------------------------------------

def _project(tmp_path, name, calc_test):
    root = tmp_path / name
    (root / "src" / "synth").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src" / "synth" / "__init__.py").write_text("")
    (root / "src" / "synth" / "calc.py").write_text(SYNTH)
    (root / "src" / "synth" / "other.py").write_text("def twice(x):\n    return x * 2\n")
    (root / "pyproject.toml").write_text('[tool.pytest.ini_options]\npythonpath = ["src"]\ntestpaths = ["tests"]\n')
    (root / "tests" / "test_calc.py").write_text(calc_test)
    (root / "tests" / "test_other.py").write_text("from synth.other import twice\n\n\ndef test_twice():\n    assert twice(2) == 4\n")
    _config(root, ["src/synth/calc.py"])
    return root


STRONG = "from synth.calc import add, neg\n\n\ndef test_add():\n    assert add(2, 3) == 5\n\n\ndef test_neg():\n    assert neg(4) == -4\n"
WEAK = "from synth.calc import add, neg\n\n\ndef test_add():\n    assert add(0, 0) == 0\n\n\ndef test_neg():\n    assert neg(0) == 0\n"


@pytest.fixture(scope="module")
def strong_run(tmp_path_factory):
    root = _project(tmp_path_factory.mktemp("mg"), "strong", STRONG)
    return root, G.run(root, jobs=2, markers="", sha=SHA, now=NOW)


@pytest.fixture(scope="module")
def weak_run(tmp_path_factory):
    root = _project(tmp_path_factory.mktemp("mg"), "weak", WEAK)
    return root, G.run(root, jobs=2, markers="", sha=SHA, now=NOW)


def test_strong_suite_kills_every_mutant(strong_run):
    root, report = strong_run
    res = report["files"]["src/synth/calc.py"]
    assert res["status"] == "scored" and res["survived"] == 0 and res["no_tests"] == 0 and res["error"] == 0
    assert res["killed"] + res["timeout"] == res["mutants"].__len__() == 2
    assert res["score"] == 1.0
    assert G.survivors(report) == []
    assert report["unrun"] == [] and report["partial"] is False


def test_strong_run_writes_a_receipt_release_check_accepts(strong_run):
    root, report = strong_run
    G.write_receipt(root, report, now=NOW)
    assert RC._mutation(root, SHA, NOW, "required", RC.MUTATION_MIN, RC.DEFAULT_MAX_AGE)["ok"] is True


def test_weak_suite_leaves_exactly_the_unkilled_mutants_and_lists_them(weak_run):
    root, report = weak_run
    res = report["files"]["src/synth/calc.py"]
    assert res["status"] == "scored"
    assert res["survived"] == 2 and res["killed"] == 0                     # add(0, 0) and neg(0) cannot tell either mutant from the original
    assert res["score"] == pytest.approx((res["killed"] + res["timeout"]) / res["mutants"].__len__())
    lines = G.survivors(report)
    assert any(s.startswith("src/synth/calc.py:2:") and "a - b" in s for s in lines)    # add(0, 0) cannot tell + from -
    assert any(s.startswith("src/synth/calc.py:6:") and "+a" in s for s in lines)       # neg(0) cannot tell -a from +a
    assert len(lines) == res["survived"]
    for m in res["mutants"]:
        assert m["status"] in {"killed", "survived", "timeout", "no_tests", "error"}


def test_weak_run_fails_the_release_threshold_through_release_check(weak_run):
    root, report = weak_run
    G.write_receipt(root, report, now=NOW)
    entry = RC._mutation(root, SHA, NOW, "required", RC.MUTATION_MIN, RC.DEFAULT_MAX_AGE)
    assert entry["ok"] is False and entry["reasons"] == ["score_below_min"]


def test_mutants_of_a_function_run_only_the_tests_that_execute_it(strong_run):
    _root, report = strong_run
    res = report["files"]["src/synth/calc.py"]
    per_func = {m["func"]: m["tests"] for m in res["mutants"]}
    assert per_func == {"x_add": 1, "x_neg": 1}                            # test_add for add, test_neg for neg; test_other never
    assert all("test_other" not in t for t in res["selected_tests"])


# --- 15c-13 ------------------------------------------------------------------------------------------------------

def test_cli_file_option_runs_one_file_and_never_writes_the_release_receipt(tmp_path, capsys):
    root = _project(tmp_path, "cli", STRONG)
    (root / "src" / "synth" / "other.py").write_text("def twice(x):\n    return x * 2\n")
    _config(root, ["src/synth/calc.py", "src/synth/other.py"])
    rc = G.main(["--root", str(root), "--file", "src/synth/other.py", "--jobs", "2", "--markers", ""])
    out = capsys.readouterr().out
    assert rc == 20                                                         # partial: not a release verdict
    report = json.loads(out)
    assert list(report["files"]) == ["src/synth/other.py"] and report["unrun"] == ["src/synth/calc.py"]
    assert not (root / "work" / "mutation.json").exists()
    assert list((root / ".claude" / "tdd-guardian").glob("mutation-*-partial.md"))


def test_cli_usage_errors_exit_2(tmp_path, capsys):
    root = _project(tmp_path, "usage", STRONG)
    assert G.main(["--root", str(root), "--file", "src/synth/nope.py"]) == 2
    assert G.main(["--root", str(root / "missing")]) == 2
    assert G.main(["--jobs", "0", "--root", str(root)]) == 2
    assert G.main(["--bogus"]) == 2


def test_mixed_suite_scores_the_fraction_it_kills(tmp_path):
    mixed = "from synth.calc import add, neg\n\n\ndef test_add():\n    assert add(2, 3) == 5\n\n\ndef test_neg():\n    assert neg(0) == 0\n"
    root = _project(tmp_path, "mixed", mixed)
    report = G.run(root, jobs=1, markers="", sha=SHA, now=NOW)
    res = report["files"]["src/synth/calc.py"]
    assert (res["killed"], res["survived"], res["score"]) == (1, 1, 0.5)
    assert G.survivors(report) == ["src/synth/calc.py:6: return -a -> return +a"]


def test_mutant_is_active_in_a_subprocess_started_with_a_scrubbed_environment(tmp_path):
    """Tests start the launcher / hooks with a minimal env: the mutant must reach them through the repository copy, not
    os.environ - and a test that reaches the module only through a subprocess (no in-process coverage) is still selected
    because it names the module (static discovery, lane-directory layout)."""
    sub = (
        "import subprocess, sys\nfrom pathlib import Path\n\n\n"
        "def test_add_in_a_clean_subprocess():\n"
        "    src = Path(__file__).resolve().parents[2] / 'src'\n"
        "    out = subprocess.run([sys.executable, '-c', 'from synth.calc import add; print(add(2, 3))'],\n"
        "                         env={'PYTHONPATH': str(src)}, capture_output=True, text=True, check=True)\n"
        "    assert out.stdout.strip() == '5'\n"
    )
    root = _project(tmp_path, "scrubbed", "")
    (root / "tests" / "unit").mkdir()
    (root / "tests" / "unit" / "test_sub.py").write_text(sub)
    report = G.run(root, jobs=1, markers="", sha=SHA, now=NOW)
    res = report["files"]["src/synth/calc.py"]
    rows = {m["func"]: m["status"] for m in res["mutants"]}
    assert rows == {"x_add": "killed", "x_neg": "survived"}       # neg is never called by that test, so its mutant lives
    assert "tests/unit/test_sub.py" in res["selected_tests"]


def test_mutant_is_active_when_the_module_runs_as_main_in_a_scrubbed_subprocess(tmp_path):
    """``python -m pkg.mod`` (hooks, CLIs) runs the module as ``__main__``: its mutants must still be selected there."""
    sub = (
        "import subprocess, sys\nfrom pathlib import Path\n\n\n"
        "def test_calc_as_main():\n"
        "    src = Path(__file__).resolve().parents[2] / 'src'\n"
        "    out = subprocess.run([sys.executable, '-m', 'synth.calc'], env={'PYTHONPATH': str(src)},\n"
        "                         capture_output=True, text=True, check=True)\n"
        "    assert out.stdout.strip() == '5'\n"
    )
    root = _project(tmp_path, "asmain", "")
    (root / "src" / "synth" / "calc.py").write_text(SYNTH + "\n\nif __name__ == '__main__':\n    print(add(2, 3))\n")
    (root / "tests" / "unit").mkdir()
    (root / "tests" / "unit" / "test_main.py").write_text(sub)
    report = G.run(root, jobs=1, markers="", sha=SHA, now=NOW)
    rows = {m["func"]: m["status"] for m in report["files"]["src/synth/calc.py"]["mutants"]}
    assert rows == {"x_add": "killed", "x_neg": "survived"}


def test_resume_keeps_finished_files_even_when_the_time_budget_is_spent(strong_run):
    root, first = strong_run
    again = G.run(root, jobs=1, markers="", sha=SHA, now=NOW, resume=True, max_seconds=0.0)
    assert again["unrun"] == [] and again["partial"] is False
    assert again["files"]["src/synth/calc.py"]["score"] == first["files"]["src/synth/calc.py"]["score"] == 1.0


def test_from_saved_reports_what_is_on_disk_and_is_never_a_release_receipt(tmp_path, capsys):
    root = _project(tmp_path, "saved", STRONG)
    G.run(root, jobs=1, markers="", sha=SHA, now=NOW)
    rc = G.main(["--root", str(root), "--from-saved"])
    report = json.loads(capsys.readouterr().out)
    assert rc == 20 and report["files"]["src/synth/calc.py"]["score"] == 1.0 and report["partial"] is True
    assert not (root / "work" / "mutation.json").exists()
    assert list((root / ".claude" / "tdd-guardian").glob("mutation-*-partial.md"))
