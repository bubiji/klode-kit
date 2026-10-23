"""WI-00d2 (b): the stage stand-in the ``kb-pipeline`` workflow shell drives in the host spike.

The real ``pipeline.py`` arrives with later WIs; the spike only needs something with the r2.5 / r2.6 contract:

``klode-kit dev spike-pipeline --stage <s> --project DIR --batch ID`` prints one JSON object
``{stage, exit_code, state, cached, receipt, receipt_sha256}`` and exits with ``exit_code``.

- Stages in order: ``preflight -> ocr -> audit`` (stage 1; ``audit`` exits 10 and sets the batch state to
  ``awaiting_confirm``), then ``writeback -> build -> check`` (stage 2; ``check`` sets ``done``).
- Each stage writes ``work/<batch>/receipts/<stage>.json`` (schema ``receipt``, ``exit_code`` recorded) and one
  output file; the state is ``work/<batch>/state.json``.
- ``inputs_hash`` = sha over the ``inbox/`` files and the predecessor's receipt sha. A stage whose done receipt
  has the same inputs_hash and whose outputs still match is a cache hit: nothing is rewritten, ``cached: true``.
- A stage whose predecessor has no done receipt, or a stage-2 stage before ``awaiting_confirm``: exit 30,
  nothing written. Unknown stage: exit 2.

Law: re-running any finished stage with unchanged inputs is idempotent (receipt bytes unchanged, S5); changing an
input invalidates that stage and every later one (S5 monotone invalidation).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from _support.flows import base_env, run_module
from klode_kit.contract import schema as contract

pytestmark = pytest.mark.unit

STAGE1 = ["preflight", "ocr", "audit"]
STAGE2 = ["writeback", "build", "check"]


@pytest.fixture
def project(tmp_path):
    p = tmp_path / "proj"
    (p / "inbox").mkdir(parents=True)
    (p / "inbox" / "p001.txt").write_text("synthetic lab page 1\n", encoding="utf-8")
    (p / "inbox" / "p002.txt").write_text("synthetic lab page 2\n", encoding="utf-8")
    return p


def run_stage1(project: Path) -> None:
    """Precondition for later tests: stage 1 must have run to the confirm point."""
    assert [stage(project, s)[0] for s in STAGE1] == [0, 0, 10]


def stage(project: Path, name: str, batch: str = "b1"):
    proc = run_module("klode_kit", ["dev", "spike-pipeline", "--stage", name, "--project", str(project),
                                    "--batch", batch], base_env())
    out = json.loads(proc.stdout) if proc.stdout.strip() else None
    return proc.returncode, out, proc


def receipt(project: Path, name: str, batch: str = "b1") -> Path:
    return project / "work" / batch / "receipts" / f"{name}.json"


def state(project: Path, batch: str = "b1") -> str:
    return json.loads((project / "work" / batch / "state.json").read_text(encoding="utf-8"))["status"]


def test_stage1_ends_exit_10_awaiting_confirm(project):
    codes = [stage(project, s)[0] for s in STAGE1]
    assert codes == [0, 0, 10]
    assert state(project) == "awaiting_confirm"
    for s, code in zip(STAGE1, codes):
        data = json.loads(receipt(project, s).read_text(encoding="utf-8"))
        assert contract.issues("receipt", data) == []
        assert data["step"] == s and data["status"] == "done" and data["exit_code"] == code


def test_stage2_completes_and_leaves_stage1_receipts(project):
    run_stage1(project)
    before = {s: receipt(project, s).read_bytes() for s in STAGE1}
    results = [stage(project, s) for s in STAGE2]
    assert [r[0] for r in results] == [0, 0, 0]
    assert state(project) == "done"
    assert {s: receipt(project, s).read_bytes() for s in STAGE1} == before
    assert all(contract.issues("receipt", json.loads(receipt(project, s).read_text())) == [] for s in STAGE2)


def test_rerun_is_cache_hit_and_byte_identical(project):
    run_stage1(project)
    snap = {s: receipt(project, s).read_bytes() for s in STAGE1}
    reruns = [stage(project, s) for s in STAGE1]
    assert [r[1]["cached"] for r in reruns] == [True, True, True]
    assert [r[0] for r in reruns] == [0, 0, 10]
    assert {s: receipt(project, s).read_bytes() for s in STAGE1} == snap
    import hashlib
    assert reruns[0][1]["receipt_sha256"] == hashlib.sha256(snap["preflight"]).hexdigest()


def test_input_change_invalidates_stage_and_successors(project):
    run_stage1(project)
    old = {s: json.loads(receipt(project, s).read_text())["inputs_hash"] for s in STAGE1}
    (project / "inbox" / "p002.txt").write_text("synthetic lab page 2, rescanned\n", encoding="utf-8")
    fresh = [stage(project, s) for s in STAGE1]
    assert [r[1]["cached"] for r in fresh] == [False, False, False]
    new = {s: json.loads(receipt(project, s).read_text())["inputs_hash"] for s in STAGE1}
    assert all(new[s] != old[s] for s in STAGE1)


def test_tampered_output_is_not_a_cache_hit(project):
    assert stage(project, "preflight")[0] == 0
    out = json.loads(receipt(project, "preflight").read_text())["outputs"][0]["path"]
    (project / out).write_text("tampered", encoding="utf-8")
    code, data, _ = stage(project, "preflight")
    assert code == 0 and data["cached"] is False


@pytest.mark.parametrize("name", ["ocr", "writeback", "check"])
def test_out_of_order_stage_exit_30_writes_nothing(project, name):
    code, _, _ = stage(project, name)
    assert code == 30
    assert not receipt(project, name).exists()


def test_stage2_before_confirm_point_exit_30(project):
    assert stage(project, "preflight")[0] == 0
    assert stage(project, "ocr")[0] == 0
    code, _, _ = stage(project, "writeback")
    assert code == 30
    assert not receipt(project, "writeback").exists()


def test_unknown_stage_usage(project):
    assert stage(project, "publish")[0] == 2
