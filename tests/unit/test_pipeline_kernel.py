"""WI-09a: pipeline kernel, unit lane (matrix 09a-07, 09a-09 and the pure parts of 09a-02 / 09a-08;
WI-02b -> 09 note "the pipeline never passes --force").

Law: the exit-code table is a total function from reasons to the closed set {0, 2, 10, 20, 21, 22, 30, 40, 50,
60, 61} (S2 + S4 over arbitrary child exit codes); a step is reused iff all three hashes match (S5, the
powerset is enumerated); child argv never carries ``--force`` (S4).
"""

from __future__ import annotations

import hashlib
import itertools
import json
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from _support.flows import REPO
from klode_kit import pipeline as P

pytestmark = pytest.mark.unit

CLOSED_SET = {0, 2, 10, 20, 21, 22, 30, 40, 50, 60, 61}

# 09a-07: one reason of every kind -> its exit code (design r2.6 + r3.2 publish codes)
EXPECTED = {
    "ok": 0,
    "usage": 2,
    "awaiting_confirm": 10,
    "content_gate": 20,
    "post_publish_failed": 21,
    "stale_replay": 22,
    "contract_violation": 30,
    "stale_input": 30,
    "environment": 40,
    "config": 40,
    "auth": 40,
    "lock_busy": 40,
    "budget_exhausted": 50,
    "no_progress": 50,
    "privacy": 60,
    "isolation": 61,
    "gate_closed": 61,
}


def test_exit_code_table() -> None:
    assert {r.name: P.EXIT_CODE[r] for r in P.Reason} == EXPECTED
    assert set(P.EXIT_CODE.values()) == CLOSED_SET
    assert set(P.EXIT_CODE) == set(P.Reason)  # total: no reason without a code


def test_only_content_gate_enters_loops() -> None:
    """Environment, privacy, budget, lock and contract problems never go into a repair loop (09a-08, kernel part)."""
    for reason in P.Reason:
        assert P.enters_loop(reason) is (reason is P.Reason.content_gate), reason
    assert P.LOOPABLE == frozenset({P.Reason.content_gate})


@pytest.mark.parametrize("code,reason", [
    (0, "ok"), (2, "usage"), (10, "awaiting_confirm"), (20, "content_gate"), (30, "contract_violation"),
    (40, "environment"), (50, "budget_exhausted"), (60, "privacy"), (61, "isolation"),
    (1, "environment"), (255, "environment"), (-9, "environment"), (137, "environment"),
])
def test_child_exit_classification(code: int, reason: str) -> None:
    assert P.classify_child_exit(code).name == reason


@given(st.integers(min_value=-300, max_value=300))
def test_any_child_exit_maps_into_the_closed_set(code: int) -> None:
    assert P.EXIT_CODE[P.classify_child_exit(code)] in CLOSED_SET


# ---------------------------------------------------------------- child argv: launcher only, never --force

def test_default_launcher_is_the_repo_launcher_not_a_console_script() -> None:
    launcher = P.default_launcher()
    assert launcher == REPO / "bin" / "klode-kit"
    assert ".venv" not in launcher.parts  # V1


def test_child_argv_is_launcher_then_arguments() -> None:
    assert P.child_argv(Path("/x/launcher"), "kb-ocr", "--batch", "in") == ["/x/launcher", "kb-ocr", "--batch", "in"]


@given(st.lists(st.text(max_size=12).filter(lambda s: not s.startswith("--force")), max_size=6))
def test_child_argv_passes_clean_arguments_unchanged(args: list[str]) -> None:
    assert P.child_argv(Path("/l"), "views", *args) == ["/l", "views", *args]


@pytest.mark.parametrize("bad", ["--force", "--force=1", "--force-write"])
def test_child_argv_refuses_force(bad: str) -> None:
    with pytest.raises(ValueError, match="--force"):
        P.child_argv(Path("/l"), "write-card", "kb", bad)


@pytest.mark.parametrize("receipt,verdict", [
    ({"status": "done", "forced": False}, "PASS"),
    ({"status": "failed", "forced": False}, "FAIL"),
    ({"status": "done", "forced": True}, "FAIL"),  # a forced write is a failed step even when it wrote
    ({"status": "failed", "forced": True}, "FAIL"),
    ({"status": "done"}, "FAIL"),  # malformed: no claim of "not forced"
    ({}, "FAIL"),
    ({"status": "weird", "forced": False}, "FAIL"),
])
def test_write_receipt_verdict(receipt: dict, verdict: str) -> None:
    assert P.write_receipt_verdict(receipt) == verdict


# ---------------------------------------------------------------- reuse needs all three hashes (09a-02, pure part)

def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _receipt(base: Path, *, inputs: str, config: str, out_sha: str, status: str = "done") -> dict:
    return {"schema": "klode-kit.receipt/1", "step": "ocr", "status": status, "started": "2026-10-24T00:00:00Z",
            "ended": "2026-10-24T00:00:01Z", "inputs_hash": inputs, "config_hash": config, "config_version": 1,
            "outputs": [{"path": "out.txt", "sha256": out_sha}], "exit_code": 0}


@pytest.mark.parametrize("changed", [c for n in range(4) for c in itertools.combinations(("inputs", "config", "outputs"), n)])
def test_reuse_iff_all_three_hashes_match(tmp_path: Path, changed: tuple[str, ...]) -> None:
    (tmp_path / "out.txt").write_bytes(b"product")
    cur = {"inputs": _sha(b"i"), "config": _sha(b"c")}
    rec = _receipt(tmp_path, inputs=cur["inputs"], config=cur["config"], out_sha=_sha(b"product"))
    if "inputs" in changed:
        rec["inputs_hash"] = _sha(b"other inputs")
    if "config" in changed:
        rec["config_hash"] = _sha(b"other config")
    if "outputs" in changed:
        rec["outputs"][0]["sha256"] = _sha(b"other product")
    ok = P.is_reusable(rec, inputs_hash=cur["inputs"], config_hash=cur["config"], base=tmp_path)
    assert ok is (not changed)


def test_missing_product_is_not_reusable(tmp_path: Path) -> None:
    rec = _receipt(tmp_path, inputs=_sha(b"i"), config=_sha(b"c"), out_sha=_sha(b"product"))
    assert P.is_reusable(rec, inputs_hash=_sha(b"i"), config_hash=_sha(b"c"), base=tmp_path) is False


@pytest.mark.parametrize("status", ["running", "failed"])
def test_only_done_is_reusable(tmp_path: Path, status: str) -> None:
    (tmp_path / "out.txt").write_bytes(b"product")
    rec = _receipt(tmp_path, inputs=_sha(b"i"), config=_sha(b"c"), out_sha=_sha(b"product"), status=status)
    assert P.is_reusable(rec, inputs_hash=_sha(b"i"), config_hash=_sha(b"c"), base=tmp_path) is False


@pytest.mark.parametrize("rec", [None, {}, {"status": "done"}, "text", 5])
def test_garbage_receipt_is_not_reusable(tmp_path: Path, rec) -> None:
    assert P.is_reusable(rec, inputs_hash=_sha(b"i"), config_hash=_sha(b"c"), base=tmp_path) is False


def test_pid_alive(tmp_path: Path) -> None:
    import os
    import subprocess
    import sys

    assert P.pid_alive(os.getpid()) is True
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    assert P.pid_alive(proc.pid) is False
    assert P.pid_alive(0) is False and P.pid_alive(-3) is False


# ---------------------------------------------------------------- usage errors (09a-09)

def _images(inbox: Path) -> Path:
    inbox.mkdir()
    src = REPO / "tests" / "fixtures" / "fx-labs-1x" / "images"
    first = sorted(src.glob("*.png"))[0]
    (inbox / first.name).write_bytes(first.read_bytes())
    return inbox


def test_usage_error_exit2_unknown_stage(tmp_path: Path, capsys) -> None:
    project = tmp_path / "p"
    project.mkdir()
    code = P.main(["--batch", str(_images(tmp_path / "in")), "--project", str(project), "--stage", "nonsense"])
    assert code == 2
    assert "nonsense" in capsys.readouterr().err
    assert list(project.iterdir()) == []  # nothing created


def test_usage_error_exit2_missing_batch(tmp_path: Path) -> None:
    assert P.main(["--project", str(tmp_path)]) == 2


def test_usage_error_exit2_missing_inbox_and_no_directories(tmp_path: Path) -> None:
    project = tmp_path / "p"
    project.mkdir()
    assert P.main(["--batch", str(tmp_path / "absent"), "--project", str(project)]) == 2
    assert list(project.iterdir()) == []


def test_usage_error_exit2_empty_inbox_creates_nothing(tmp_path: Path, capsys) -> None:
    project, inbox = tmp_path / "p", tmp_path / "in"
    project.mkdir()
    inbox.mkdir()
    assert P.main(["--batch", str(inbox), "--project", str(project)]) == 2
    assert "no images" in capsys.readouterr().err
    assert list(project.iterdir()) == []


def test_usage_error_exit2_choices_file_missing(tmp_path: Path) -> None:
    project = tmp_path / "p"
    project.mkdir()
    code = P.main(["--batch", str(_images(tmp_path / "in")), "--project", str(project),
                   "--choices", str(tmp_path / "nope.json")])
    assert code == 2
    assert list(project.iterdir()) == []


def test_stage_names_are_the_seven_stages_in_order() -> None:
    assert P.STAGE_NAMES == ("preflight", "ocr", "audit", "writeback", "build", "check", "publish")


def test_receipts_are_valid_receipt_contract_json() -> None:
    """The kernel's receipts are written through ``receipts.write(..., 'receipt')``: schema, step pattern."""
    from klode_kit.contract.schema import issues

    for name in P.STAGE_NAMES:
        rec = {"schema": "klode-kit.receipt/1", "step": name, "status": "running", "started": "2026-10-24T00:00:00Z",
               "inputs_hash": "0" * 64, "config_hash": "1" * 64, "config_version": 1, "outputs": [], "pid": 7}
        assert issues("receipt", rec) == [], (name, json.dumps(rec))
