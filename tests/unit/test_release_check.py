"""WI-15b: ``release_check`` (matrix 15b-02..05, 15b-07, 15b-V6).

The release gate over what a release run leaves behind: five lane receipts (PASS, same ``release_sha``, not
older than 7 days, made in this checkout by this interpreter, not tagged ``not-release``), a clean git tree, the
current ``RELEASE.sha256``, the install fingerprint, ``privacy_scan`` 0 findings, ``doctor`` 0 missing, mutation
score >= 0.80. It reports *every* unmet item (it never stops at the first one) and never invents a pass.

Exit codes: 0 releasable, 20 at least one item unmet, 2 usage.
Fixtures: a tiny complete checkout in tmp with its own git repository (``_support/release_fx``); ``doctor`` and
``privacy_scan`` are injected (their own tests are elsewhere), the clock is injected.
No law: a conjunction of independent predicates; each predicate is pinned by an S2/S3 case below.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import sys

import jsonschema
import pytest

from _support import release_fx as F
from _support.flows import REPO, base_env
from klode_kit.contract import schema as contract
from klode_kit.devtools import release_check as R
from klode_kit.devtools import release_manifest as M

pytestmark = pytest.mark.unit

OK_DOCTOR = lambda root: 0  # noqa: E731
OK_PRIVACY = lambda root: 0  # noqa: E731


@pytest.fixture()
def root(tmp_path):
    r = F.make_root(tmp_path)
    F.stamp(r)
    return r


def run_check(root, **kw):
    kw.setdefault("now", F.NOW)
    kw.setdefault("doctor", OK_DOCTOR)
    kw.setdefault("privacy", OK_PRIVACY)
    return R.check(root, **kw)


def item(report, ident):
    found = [c for c in report["checks"] if c["id"] == ident]
    assert len(found) == 1, (ident, [c["id"] for c in report["checks"]])
    return found[0]


def failed(report):
    return sorted(c["id"] for c in report["checks"] if not c["ok"])


def edit_receipt(root, lane, fn):
    path = root / "work" / "lanes" / f"{lane}.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    fn(doc)
    path.write_text(json.dumps(doc), encoding="utf-8")


# --------------------------------------------------------------------------- 15b-07


def test_fixture_receipts_are_schema_valid(root):
    """15b-07 precondition (L1/S1): the synthetic receipts are real ``lane_receipt`` documents."""
    for lane in F.LANES:
        doc = json.loads((root / "work" / "lanes" / f"{lane}.json").read_text(encoding="utf-8"))
        assert contract.issues("lane_receipt", doc) == []


def test_all_green_passes(root):
    """15b-07 (L1/S1): five PASS receipts and everything else in order -> releasable, exit 0."""
    report = run_check(root)
    assert failed(report) == []
    assert report["ok"] is True and report["missing"] == 0
    assert report["release_sha"] == M.release_sha(root)
    assert R.main(["--root", str(root)], now=F.NOW, doctor=OK_DOCTOR, privacy=OK_PRIVACY) == 0


def test_report_is_json_serialisable_with_the_documented_shape(root):
    report = run_check(root)
    json.dumps(report)
    assert report["schema"] == "klode-kit.release_check/1"
    assert {c["id"] for c in report["checks"]} == {f"lane:{x}" for x in F.LANES} | {
        "release_manifest", "dirty", "privacy_scan", "doctor", "install_fingerprint", "mutation"}
    for c in report["checks"]:
        assert set(c) >= {"id", "ok", "detail", "reasons", "fix"}


# --------------------------------------------------------------------------- 15b-03


@pytest.mark.parametrize("lane", F.LANES)
def test_missing_lane_receipt_fails(root, lane, capsys):
    """15b-03 (L1/S3): a missing receipt fails the release and the report names exactly that lane."""
    (root / "work" / "lanes" / f"{lane}.json").unlink()
    report = run_check(root)
    assert failed(report) == [f"lane:{lane}"]
    assert item(report, f"lane:{lane}")["reasons"] == ["missing"]
    assert report["ok"] is False
    code = R.main(["--root", str(root)], now=F.NOW, doctor=OK_DOCTOR, privacy=OK_PRIVACY)
    assert code == 20
    assert lane in capsys.readouterr().err


def test_all_unmet_items_are_reported_not_just_the_first(root):
    """15b-03b (L1/S3): host and model lanes absent (the real local state before gate G2) -> both listed."""
    for lane in ("model_integration", "host_acceptance"):
        (root / "work" / "lanes" / f"{lane}.json").unlink()
    report = run_check(root, privacy=lambda r: 3)
    assert failed(report) == ["lane:host_acceptance", "lane:model_integration", "privacy_scan"]
    assert report["missing"] == 3


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda d: d.update(status="FAIL", pytest_exit_code=1, tests_failed=2), "status:FAIL"),
        (lambda d: d.update(status="BOOTSTRAP", bootstrap=True, tests_discovered=0), "status:BOOTSTRAP"),
        (lambda d: d.update(tags=["not-release"]), "not_release"),
        (lambda d: d["env_fingerprint"].pop("code_tree_sha"), "no_code_tree_sha"),
        (lambda d: d["env_fingerprint"].update(code_tree_sha="0" * 64), "stale_sha"),
        (lambda d: d.pop("tests_discovered"), "invalid_receipt"),
    ],
    ids=["fail", "bootstrap", "ci-tag", "no-sha", "wrong-sha", "schema"],
)
def test_bad_receipt_is_named_with_its_reason(root, mutate, reason):
    """15b-03c (L1/S3): each way a receipt can be unusable gets its own machine-readable reason."""
    edit_receipt(root, "contract_replay", mutate)
    report = run_check(root)
    assert failed(report) == ["lane:contract_replay"]
    assert reason in item(report, "lane:contract_replay")["reasons"]


def test_unparsable_receipt_fails(root):
    (root / "work" / "lanes" / "unit.json").write_text("{not json", encoding="utf-8")
    report = run_check(root)
    assert item(report, "lane:unit")["reasons"] == ["unreadable"]
    assert report["ok"] is False


# --------------------------------------------------------------------------- 15b-02


def test_template_only_change_invalidates(root):
    """15b-02 (L2/S3): after changing ONE template file, every old lane receipt is stale (sha), even after the
    change is committed and the manifest regenerated - only a new run revalidates."""
    template = root / "src/klode_kit/templates/agent.md"
    template.write_text("template two\n", encoding="utf-8")
    report = run_check(root)
    for lane in F.LANES:
        assert item(report, f"lane:{lane}")["reasons"] == ["stale_sha"], lane
    assert not item(report, "dirty")["ok"] and not item(report, "release_manifest")["ok"]

    M.write(root)
    F.git(root, "add", "-A")
    F.git(root, "commit", "-q", "-m", "template")
    report = run_check(root)
    assert set(failed(report)) >= {f"lane:{x}" for x in F.LANES}
    for lane in F.LANES:
        assert item(report, f"lane:{lane}")["reasons"] == ["stale_sha"], lane
    assert item(report, "dirty")["ok"] and item(report, "release_manifest")["ok"]

    for lane in F.LANES:  # a fresh run bound to the new sha makes the release pass again
        F.put_receipt(root, F.lane_receipt(root, lane))
    F.put_mutation(root)
    assert failed(run_check(root)) == []


def test_file_outside_the_release_list_keeps_receipts_valid(root):
    """15b-02b (L2/S2): docs and tests are not release content - editing them (and committing) leaves receipts valid."""
    (root / "README.md").write_text("changed\n", encoding="utf-8")
    F.git(root, "add", "-A")
    F.git(root, "commit", "-q", "-m", "docs")
    assert failed(run_check(root)) == []


# --------------------------------------------------------------------------- 15b-04


def test_dirty_tree_fails(root):
    """15b-04 (L2/S3): an uncommitted change to a tracked file fails the release and names the file."""
    (root / "README.md").write_text("edited\n", encoding="utf-8")
    report = run_check(root)
    assert failed(report) == ["dirty"]
    assert "README.md" in item(report, "dirty")["detail"]


def test_untracked_file_counts_unless_allowed(root):
    (root / "stray.txt").write_text("x", encoding="utf-8")
    assert failed(run_check(root)) == ["dirty"]
    assert failed(run_check(root, allow_untracked=True)) == []


def test_not_a_git_checkout_fails(tmp_path):
    root = F.make_root(tmp_path, "nogit", commit=False)
    F.stamp(root)
    report = run_check(root)
    assert item(report, "dirty")["reasons"] == ["not_a_git_checkout"]


# --------------------------------------------------------------------------- 15b-05


def test_receipt_age_boundary(root):
    """15b-05 (L1/S2): exactly 7 days passes; 7 days and one second fails."""
    week = dt.timedelta(days=7)
    F.put_receipt(root, F.lane_receipt(root, "unit", age=week))
    assert item(run_check(root), "lane:unit")["ok"] is True
    F.put_receipt(root, F.lane_receipt(root, "unit", age=week + dt.timedelta(seconds=1)))
    report = run_check(root)
    assert item(report, "lane:unit")["reasons"] == ["too_old"]
    assert failed(report) == ["lane:unit"]


def test_receipt_from_the_future_fails(root):
    F.put_receipt(root, F.lane_receipt(root, "unit", age=-dt.timedelta(hours=2)))
    assert item(run_check(root), "lane:unit")["reasons"] == ["from_the_future"]


# --------------------------------------------------------------------------- 15b-V6


def test_receipt_invalid_after_repo_move(root, tmp_path):
    """15b-V6 (L2/S3): the receipts say they were made in another directory -> invalid (a rename cannot reuse a PASS)."""
    moved = tmp_path / "renamed"
    shutil.copytree(root, moved, symlinks=True)
    report = run_check(moved)
    for lane in F.LANES:
        assert "repo_moved" in item(report, f"lane:{lane}")["reasons"], lane
    assert item(report, "install_fingerprint")["ok"] is False  # the venv fingerprint also names the old root


def test_receipt_from_another_interpreter_is_invalid(root):
    """15b-V6b (L1/S3): the python realpath in the fingerprint must be this checkout's interpreter."""
    F.put_receipt(root, F.lane_receipt(root, "unit", python="/somewhere/else/python3"))
    assert "python_changed" in item(run_check(root), "lane:unit")["reasons"]
    assert failed(run_check(root, python="/somewhere/else/python3")) == sorted(
        f"lane:{x}" for x in F.LANES if x != "unit")


# --------------------------------------------------------------------------- release manifest / install / privacy / doctor


def test_stale_or_missing_release_manifest_fails(root):
    (root / "RELEASE.sha256").write_text("release_sha " + "0" * 64 + "\n", encoding="utf-8")
    F.git(root, "commit", "-aqm", "bad manifest")
    report = run_check(root)
    assert failed(report) == ["release_manifest"]
    (root / "RELEASE.sha256").unlink()
    F.git(root, "commit", "-aqm", "no manifest")
    assert item(run_check(root), "release_manifest")["reasons"] == ["missing"]


def test_uncovered_glob_fails_the_release(root):
    shutil.rmtree(root / "skills")
    F.git(root, "add", "-A")
    F.git(root, "commit", "-qm", "no skills")
    report = run_check(root)
    assert "uncovered_glob" in item(report, "release_manifest")["reasons"]
    assert "skills/**" in item(report, "release_manifest")["detail"]


@pytest.mark.parametrize(
    ("over", "reason"),
    [({"repo_root": "/elsewhere/klode-kit"}, "repo_moved"), ({"lock_sha256": "0" * 64}, "lock_changed")],
)
def test_install_fingerprint_must_match_this_checkout(root, over, reason):
    F.put_install(root, **over)
    report = run_check(root)
    assert failed(report) == ["install_fingerprint"]
    assert reason in item(report, "install_fingerprint")["reasons"]


def test_install_fingerprint_missing_fails(root):
    (root / ".venv" / "klode-kit-install.json").unlink()
    assert item(run_check(root), "install_fingerprint")["reasons"] == ["missing"]


def test_privacy_findings_fail(root):
    report = run_check(root, privacy=lambda r: 2)
    assert failed(report) == ["privacy_scan"]
    assert "2" in item(report, "privacy_scan")["detail"]


def test_doctor_missing_items_fail(root):
    report = run_check(root, doctor=lambda r: 4)
    assert failed(report) == ["doctor"]
    assert "4" in item(report, "doctor")["detail"]


def test_a_crashing_probe_is_a_failure_not_a_pass(root):
    def boom(_root):
        raise RuntimeError("probe died")

    report = run_check(root, doctor=boom, privacy=boom)
    assert failed(report) == ["doctor", "privacy_scan"]
    assert "probe died" in item(report, "doctor")["detail"]


# --------------------------------------------------------------------------- mutation


def test_mutation_missing_fails_by_default(root):
    (root / "work" / "mutation.json").unlink()
    report = run_check(root)
    assert failed(report) == ["mutation"]
    assert item(report, "mutation")["reasons"] == ["missing"]


@pytest.mark.parametrize(("score", "ok"), [(0.79, False), (0.80, True), (1.0, True), (0.0, False)])
def test_mutation_score_threshold(root, score, ok):
    F.put_mutation(root, score=score)
    report = run_check(root)
    assert item(report, "mutation")["ok"] is ok
    if not ok:
        assert item(report, "mutation")["reasons"] == ["score_below_min"]


def test_mutation_summary_bound_to_sha_and_age(root):
    F.put_mutation(root, sha="0" * 64)
    assert item(run_check(root), "mutation")["reasons"] == ["stale_sha"]
    F.put_mutation(root, age=dt.timedelta(days=8))
    assert item(run_check(root), "mutation")["reasons"] == ["too_old"]


def test_mutation_waiver_is_explicit_and_visible(root):
    """The plan demands mutation; the repo config has requireMutation=false. The waiver is opt-in and labelled."""
    (root / "work" / "mutation.json").unlink()
    report = run_check(root, mutation="skip")
    entry = item(report, "mutation")
    assert entry["ok"] is True and entry["reasons"] == ["waived"] and "WAIVED" in entry["detail"]
    assert report["ok"] is True and report["waived"] == ["mutation"]


# --------------------------------------------------------------------------- CLI


def test_cli_prints_json_and_lists_unmet_items_on_stderr(root):
    (root / "work" / "lanes" / "host_acceptance.json").unlink()
    proc = subprocess.run([sys.executable, "-m", "klode_kit.devtools.release_check", "--root", str(root),
                           "--no-doctor", "--no-privacy", "--now", F.iso(F.NOW)],
                          capture_output=True, text=True, env=base_env())
    assert proc.returncode == 20
    out = json.loads(proc.stdout)
    assert out["ok"] is False
    assert [c["id"] for c in out["checks"] if not c["ok"]] == ["lane:host_acceptance"]
    assert "MISSING lane:host_acceptance" in proc.stderr


def test_cli_usage_errors_exit_2(tmp_path):
    proc = subprocess.run([sys.executable, "-m", "klode_kit.devtools.release_check", "--mutation", "maybe"],
                          capture_output=True, text=True, env=base_env())
    assert proc.returncode == 2
    proc = subprocess.run([sys.executable, "-m", "klode_kit.devtools.release_check", "--root", str(tmp_path / "nope")],
                          capture_output=True, text=True, env=base_env())
    assert proc.returncode == 2


def test_default_python_is_the_running_interpreter(root):
    assert R.DEFAULT_MAX_AGE == dt.timedelta(days=7) and R.MUTATION_MIN == 0.80
    assert os.path.realpath(sys.executable) == R.current_python()


def test_cli_clock_override_exists_only_in_test_mode(root):
    """The injected clock is a test seam: without KLODE_KIT_TEST_SEAMS the option is refused (exit 2)."""
    proc = subprocess.run([sys.executable, "-m", "klode_kit.devtools.release_check", "--root", str(root), "--now", F.iso(F.NOW)],
                          capture_output=True, text=True, env=base_env(drop=("KLODE_KIT_TEST_SEAMS",)))
    assert proc.returncode == 2
    assert "--now" in proc.stderr
