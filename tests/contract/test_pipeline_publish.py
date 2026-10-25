"""WI-09d: the publish stage of the pipeline (design 02 r3.2: 13 steps + ``/kb-check`` on the build directory, then the
pointer; r4.2 replay). There is no stage left without an implementation.

The stage publishes the build the check stage judged - nothing else: its product is ``publish_receipt.json`` in the
batch's pipeline directory (stable across later log appends, so a later rollback is not undone by a resume), a failed
registration ends the run with exit 21 and the next ``--resume`` replays only that action. Real stages, real klode,
the real ``kb-publish`` as a child through the launcher wrapper.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from _support import build_fx as B
from _support import kb_check_fx as KX
from _support import pipeline_fx as PF
from _support import publish_fx as F
from klode_kit import pipeline as P

pytestmark = pytest.mark.contract_replay


def test_every_stage_has_an_implementation():
    reg = P.default_registry()
    assert list(reg) == list(P.STAGE_NAMES)
    assert all(st.run is not None and st.inputs is not None for st in reg.values())


def test_publish_stage_publishes_the_checked_build(tmp_path):
    rig = F.pipeline_rig(tmp_path, "check")
    kb = F.pipeline_kb(rig)
    assert not (kb / "current").exists()  # the check stage points to nothing (K5: switching is publish's)
    res = rig.run("--resume")
    assert res.returncode == 0, res.stderr.decode()[-1500:]
    checked = json.loads((rig.receipts_dir / "check.json").read_text(encoding="utf-8"))["outputs"][0]["path"]
    build_id = Path(checked).parent.name
    assert F.current(kb) == build_id and F.sound(kb) == []
    receipt = json.loads((rig.batch_dir / "pipeline" / "publish_receipt.json").read_text(encoding="utf-8"))
    assert (receipt["build_id"], receipt["exit_code"], receipt["reason"]) == (build_id, 0, "ok")
    stage = rig.receipt("publish")
    assert (stage["status"], stage["exit_code"]) == ("done", 0)
    assert [o["path"] for o in stage["outputs"]] == ["work/%s/pipeline/publish_receipt.json" % rig.batch_id]
    assert [r["action"] for r in F.rows(kb)] == ["switch", *F.ACTIONS]
    assert not [c for c in rig.calls() if c["argv"][0] == "kb-publish" and "--force" in c["argv"]]


def test_a_failed_registration_is_exit21_and_resume_replays_only_it(tmp_path):
    rig = F.pipeline_rig(tmp_path, "check")
    kb = F.pipeline_kb(rig)
    reg = F.registry_file(tmp_path).parent
    reg.mkdir(parents=True)
    reg.chmod(0o555)
    try:
        res = rig.run("--resume")
    finally:
        reg.chmod(0o755)
    assert res.returncode == 21, res.stderr.decode()[-1500:]
    assert rig.receipt("publish")["status"] == "failed" and rig.receipt("publish")["exit_code"] == 21
    assert F.current(kb) is not None and F.sound(kb) == []  # the pointer stays
    n = len(F.rows(kb))
    again = rig.run("--resume")
    assert again.returncode == 0, again.stderr.decode()[-1500:]
    assert [(r["action"], r["status"]) for r in F.rows(kb)[n:]] == [("registry", "done")]
    assert rig.receipt("publish")["status"] == "done"


def test_resume_after_a_rollback_does_not_switch_back(tmp_path):
    """The stage is done once published: a later deliberate rollback is not undone by ``--resume`` (its product is the
    stage's own receipt, not the log that the rollback appended to)."""
    rig = F.pipeline_rig(tmp_path, "check")
    assert rig.run("--resume").returncode == 0
    kb, project = F.pipeline_kb(rig), rig.project
    (first,) = [p.name for p in B.builds(project)]
    assert B.build(project, tmp_path).returncode == 0  # a second build of the same records
    second = [p.name for p in B.builds(project)][-1]
    assert second != first
    proc, receipt = KX.check(kb, "--build", second)
    assert proc.returncode == 0 and receipt["status"] == "PASS"
    assert F.cli(kb, project, tmp_path, "--build", second).returncode == 0
    assert F.cli(kb, project, tmp_path, "--rollback-to", first).returncode == 0
    assert F.current(kb) == first
    log = F.log_bytes(kb)
    res = rig.run("--resume")
    assert res.returncode == 0
    assert F.current(kb) == first and F.log_bytes(kb) == log


def test_publish_without_a_checked_build_is_a_contract_violation(tmp_path):
    """A check stage that left no product (a stub here) gives the publish stage nothing to publish: exit 30 and a failed
    receipt - it neither passes nor writes anywhere."""
    rig = PF.Rig(tmp_path)
    registry = P.default_registry()
    for name in ("build", "check"):
        registry[name] = P.Stage(name, inputs=lambda ctx: {"stub": 3},
                                 run=lambda ctx: P.Outcome(reason=P.Reason.ok, detail="stub", outputs=[]))
    from _support import writeback_fx as W
    from klode_kit.audit import confirm_page as CP

    assert rig.run().returncode == 10
    adir = rig.batch_dir / "audit" / "a1"
    choices = W.write_choices(tmp_path / "choices.json",
                              CP.export_choices((adir / "confirm.html").read_bytes(), W.picks(adir)))
    assert rig.run("--choices", str(choices), "--stage", "writeback").returncode == 0
    code = P.run(rig.project, rig.inbox, stage="publish", registry=registry, launcher=rig.wrapper, env=rig.env)
    assert code == 30
    assert rig.receipt("publish")["status"] == "failed" and rig.receipt("publish")["exit_code"] == 30
    assert not (rig.project / "kb").exists()
