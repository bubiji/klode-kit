"""WI-00s: lane runner (matrix 00s-01, 00s-02, 00s-03, 00s-13).

Observed only through the ``run_lane`` CLI (``python -m klode_kit.devtools.run_lane``),
its exit code / stdout / stderr, and the receipt it writes to ``work/lanes/<lane>.json``.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import jsonschema
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from _support.flows import REPO, base_env

pytestmark = pytest.mark.unit

SCHEMA_PATH = REPO / "src" / "klode_kit" / "schemas" / "lane_receipt.json"


def write_tests(root: Path, lane: str, n: int) -> None:
    tests = root / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    target = tests / f"test_gen_{lane}.py"
    if n == 0:
        target.unlink(missing_ok=True)
        return
    body = ["import pytest", ""]
    for i in range(n):
        body += [f"@pytest.mark.{lane}", f"def test_case_{i}():", "    assert True", ""]
    target.write_text("\n".join(body))


def run_lane(root: Path, lane: str, *extra: str, timeout: float = 180) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "klode_kit.devtools.run_lane", lane, "--root", str(root), "--no-cov", *extra],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=base_env(),
    )


def receipt(root: Path, lane: str) -> dict:
    path = root / "work" / "lanes" / f"{lane}.json"
    assert path.is_file(), f"run_lane wrote no receipt for lane {lane}"
    return json.loads(path.read_text())


def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


def test_run_lane_passes_with_tests(tmp_path):
    write_tests(tmp_path, "unit", 2)
    res = run_lane(tmp_path, "unit")
    assert res.returncode == 0, res.stdout + res.stderr
    rec = receipt(tmp_path, "unit")
    assert rec["status"] == "PASS"
    assert rec["tests_discovered"] == 2
    assert rec["bootstrap"] is False
    jsonschema.Draft202012Validator(schema()).validate(rec)


def test_run_lane_failing_test_fails(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_bad.py").write_text(
        "import pytest\n\n@pytest.mark.unit\ndef test_bad():\n    assert 1 == 2\n"
    )
    res = run_lane(tmp_path, "unit")
    assert res.returncode != 0
    assert receipt(tmp_path, "unit")["status"] == "FAIL"


def test_run_lane_zero_tests_fails(tmp_path):
    """00s-01: empty test dir + non-empty lane history -> FAIL, '0 tests discovered'."""
    write_tests(tmp_path, "unit", 1)
    first = run_lane(tmp_path, "unit")
    assert first.returncode == 0, first.stdout + first.stderr  # establishes non-empty history
    write_tests(tmp_path, "unit", 0)

    res = run_lane(tmp_path, "unit")

    assert res.returncode != 0
    assert "0 tests discovered" in res.stdout
    rec = receipt(tmp_path, "unit")
    assert rec["status"] == "FAIL"
    assert rec["tests_discovered"] == 0
    assert rec["bootstrap"] is False


def test_run_lane_bootstrap_warns_only_when_never_had_tests(tmp_path):
    """00s-02: lane history empty -> exit 0, loud bootstrap warning, receipt bootstrap:true, not PASS."""
    (tmp_path / "tests").mkdir()

    res = run_lane(tmp_path, "unit")

    assert res.returncode == 0, res.stdout + res.stderr
    assert "bootstrap" in res.stderr.lower()
    assert "0 tests discovered" in res.stdout
    rec = receipt(tmp_path, "unit")
    assert rec["bootstrap"] is True
    assert rec["status"] != "PASS"
    jsonschema.Draft202012Validator(schema()).validate(rec)


@settings(max_examples=6, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(counts=st.lists(st.sampled_from([0, 0, 1, 2]), min_size=1, max_size=4))
def test_bootstrap_exception_permanently_ends(counts):
    """00s-03 (S5): once a run discovers >=1 test, every later 0-test run FAILs; before that, only WARN."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "tests").mkdir()
        seen_tests = False
        for n in counts:
            write_tests(root, "unit", n)
            res = run_lane(root, "unit")
            rec = receipt(root, "unit")
            if n >= 1:
                assert res.returncode == 0 and rec["status"] == "PASS", (counts, res.stdout, res.stderr)
                seen_tests = True
            elif seen_tests:
                assert res.returncode != 0 and rec["status"] == "FAIL", (counts, res.stdout, res.stderr)
                assert rec["bootstrap"] is False
            else:
                assert res.returncode == 0 and rec["status"] != "PASS", (counts, res.stdout, res.stderr)
                assert rec["bootstrap"] is True
                assert "bootstrap" in res.stderr.lower()


def test_lane_receipt_schema_is_not_vacuous():
    """The schema used by 00s-13 must actually constrain receipts."""
    validator = jsonschema.Draft202012Validator(schema())
    jsonschema.Draft202012Validator.check_schema(schema())
    assert not validator.is_valid({})
    assert not validator.is_valid({"lane": "unit", "status": "PASS"})


@pytest.mark.parametrize("round_", range(3))
def test_lane_receipts_concurrent_lanes_no_clobber(tmp_path, round_):
    """00s-13: unit and contract_replay run concurrently; both receipts survive and are schema-valid."""
    write_tests(tmp_path, "unit", 1)
    write_tests(tmp_path, "contract_replay", 1)
    cmd = [sys.executable, "-m", "klode_kit.devtools.run_lane"]
    procs = {
        lane: subprocess.Popen(
            [*cmd, lane, "--root", str(tmp_path), "--no-cov"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=base_env(),
        )
        for lane in ("unit", "contract_replay")
    }
    for lane, proc in procs.items():
        out, err = proc.communicate(timeout=180)
        assert proc.returncode == 0, (lane, out, err)

    validator = jsonschema.Draft202012Validator(schema())
    for lane in ("unit", "contract_replay"):
        rec = receipt(tmp_path, lane)
        validator.validate(rec)
        assert rec["lane"] == lane
        assert rec["status"] == "PASS"
    leftovers = [p.name for p in (tmp_path / "work" / "lanes").iterdir() if not p.name.endswith(".json")]
    assert leftovers == []

    # Both lanes now have history: a 0-test run of either one must FAIL (no lost history update).
    write_tests(tmp_path, "unit", 0)
    write_tests(tmp_path, "contract_replay", 0)
    for lane in ("unit", "contract_replay"):
        res = run_lane(tmp_path, lane)
        assert res.returncode != 0, (lane, res.stdout, res.stderr)
        assert receipt(tmp_path, lane)["status"] == "FAIL"


def _sha(n: int) -> str:
    return f"{n:040x}"


@pytest.mark.parametrize(
    ("layout", "expected"),
    [
        ({"HEAD": "ref: refs/heads/main\n", "refs/heads/main": _sha(1) + "\n"}, _sha(1)),
        ({"HEAD": "ref: refs/heads/main\n", "packed-refs": f"# pack-refs\n{_sha(2)} refs/heads/main\n"}, _sha(2)),
        ({"HEAD": _sha(3) + "\n"}, _sha(3)),
        ({"HEAD": "ref: refs/heads/main\n"}, "unborn"),
        ({}, "unversioned"),
    ],
    ids=["ref-file", "packed-refs", "detached", "unborn", "no-git"],
)
def test_receipt_release_sha_from_git_metadata(tmp_path, layout, expected):
    """V6 groundwork: the receipt binds to the checked-out commit (read without spawning git)."""
    for rel, text in layout.items():
        target = tmp_path / ".git" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    write_tests(tmp_path, "unit", 1)
    assert run_lane(tmp_path, "unit").returncode == 0
    fp = receipt(tmp_path, "unit")["env_fingerprint"]
    assert fp["release_sha"] == expected
    assert fp["repo_root"] == str(tmp_path.resolve())


def test_run_lane_extra_pytest_args_and_hyphen_alias(tmp_path):
    write_tests(tmp_path, "contract_replay", 3)
    res = run_lane(tmp_path, "contract-replay", "--", "-k", "case_0 or case_2")
    assert res.returncode == 0, res.stdout + res.stderr
    rec = receipt(tmp_path, "contract_replay")
    assert (rec["lane"], rec["tests_discovered"], rec["status"]) == ("contract_replay", 2, "PASS")


def test_run_lane_without_tests_dir_is_bootstrap(tmp_path):
    res = run_lane(tmp_path, "cli_integration")
    assert res.returncode == 0
    rec = receipt(tmp_path, "cli_integration")
    assert (rec["status"], rec["bootstrap"], rec["tests_discovered"]) == ("BOOTSTRAP", True, 0)


def test_run_lane_unknown_lane_is_usage_error(tmp_path):
    res = run_lane(tmp_path, "smoke")
    assert res.returncode == 2
    assert not (tmp_path / "work" / "lanes").exists()
