"""WI-08e: the reopened field through the real stages and the provenance chain - contract_replay lane (matrix 08e-11,
08e-15, 08e-17, 08e-19, 08e-21, 08e-23; spec ``.claude/tdd-guardian/wi-08e-spec.md``).

Replay boundary: the OCR routes are the recorded rows of ``fx-u1-3labs`` (``KLODE_KIT_ENGINE_REPLAY`` for the pipeline
case, the fixture's engine files in process otherwise); ``doctor`` is the stub of the pipeline test wrapper. kb-audit,
the writeback, views, ``records_to_txt`` and the provenance validator are all real. Paired real path: the klode run of
``tests/cli/test_reopen_u5_klode.py`` (08e-20).

Laws: a reopen commit changes the reopened field and nothing else (08e-23, S5); a typed value round-trips into the
effective record and the export (08e-21, S4); the chain stays at 0 violations (08e-11, S5).
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from _support import pipeline_fx as F
from _support import reopen_fx as RF
from _support import writeback_fx as W
from _support.flows import REPO, base_env
from _support.reopen_fx import AUTO_REC, HB, NEW_HB, PLT, base, template  # noqa: F401
from klode_kit.build import records_to_txt as R
from klode_kit.contract import provenance as P

pytestmark = pytest.mark.contract_replay

VALUE = st.from_regex(r"[1-9][0-9]{0,2}(\.[0-9]{1,2})?", fullmatch=True)


def export_build(project: Path, out: Path) -> Path:
    res = R.export(project, out, kb_id="k")
    receipt = {"audit_bindings": res["audit_bindings"], "deident_mode": None, "kb_id": "k"}
    (out / "build_receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    return out


def violations(project: Path, build: Path) -> list[P.Violation]:
    return P.validate(project, P.Layout.for_project(project, build))


# --------------------------------------------------------------------------- 08e-11


def test_chain_has_no_violations_before_and_after_the_correction(base, tmp_path):
    """08e-11 (S5): 0 violations with the reopen pending, 0 with r2 effective, and the build made before the correction
    (bound to r1) is still 0 against the project that now has r2 (version binding)."""
    p = base.project
    old_build = export_build(p, tmp_path / "build_r1")
    assert violations(p, old_build) == []
    code, rc, err = RF.reopen(p, HB)
    assert code == 10, err
    assert violations(p, export_build(p, tmp_path / "build_pending")) == []

    assert RF.decide(p, RF.choose(p, rc, "manual", value=NEW_HB), tmp_path / "w")[0] == 0
    new_build = export_build(p, tmp_path / "build_r2")
    assert json.loads((new_build / "build_receipt.json").read_text())["audit_bindings"][AUTO_REC]["rev"] == 2
    assert violations(p, new_build) == []
    assert violations(p, old_build) == []          # bound to r1, which is still there, still decided


def test_validator_notices_a_decision_that_disagrees_with_the_revision(base, tmp_path):
    """08e-11 (S5): the zero above is not vacuous - changing the human decision's value is reported."""
    p, adir = base.project, base.adir
    _, rc, _ = RF.reopen(p, HB)
    assert RF.decide(p, RF.choose(p, rc, "manual", value=NEW_HB), tmp_path / "w")[0] == 0
    build = export_build(p, tmp_path / "build")
    path = adir / "fixes.jsonl"
    rows = W.jsonl(path)
    for r in rows:
        if r.get("reopen_id"):
            r["value"] = "12.9"
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    codes = {v.code for v in violations(p, build)}
    assert "record_value_mismatch" in codes


# --------------------------------------------------------------------------- 08e-15


def test_concurrent_reopens_of_one_field_make_one_event(base):
    """08e-15 (S5): several processes reopen the same field at once; the project lock serialises them - exactly one
    event line and one page, every process either reopened (10) or found the lock busy (40), at least one reopened."""
    procs = [subprocess.Popen(RF.argv(base.project, HB), env=base_env(), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE) for _ in range(4)]
    codes = []
    for proc in procs:
        proc.communicate(timeout=120)
        codes.append(proc.returncode)
    assert set(codes) <= {10, 40} and 10 in codes, codes
    assert len(RF.log(base.adir)) == 1 and len(RF.pages(base.adir)) == 1
    again = RF.cli(base.project, HB)
    assert again.returncode == 10 and RF.receipt_of(again)["result"] == "unchanged"


# --------------------------------------------------------------------------- 08e-17


SUCCESS_KEYS = {"schema", "result", "exit_code", "field_id", "rec_id", "batch_id", "audit_rev", "reopen_id",
                "rev_base", "effective_rev", "page", "confirm_sha256"}


def test_cli_prints_one_json_receipt(base):
    """08e-17 (S2): ``python -m klode_kit kb-audit --reopen``: exit 10 and a single-line JSON receipt."""
    res = RF.cli(base.project, HB)
    assert res.returncode == 10, res.stderr.decode()
    lines = [x for x in res.stdout.decode().splitlines() if x.strip()]
    assert len(lines) == 1
    rc = json.loads(lines[0])
    assert set(rc) == SUCCESS_KEYS
    assert (rc["schema"], rc["result"], rc["exit_code"]) == ("klode-kit.reopen_receipt/1", "opened", 10)
    assert isinstance(rc["rev_base"], int) and (base.project / rc["page"]).is_file()


def test_cli_launcher_works(base):
    """08e-17 (S2): the same through ``bin/klode-kit``."""
    res = subprocess.run([str(REPO / "bin" / "klode-kit"), "kb-audit", "--project", str(base.project), "--reopen", HB],
                         env=base_env(), capture_output=True, timeout=120)
    assert res.returncode == 10, res.stderr.decode()
    assert RF.receipt_of(res)["reopen_id"].endswith("/o0001")


def test_cli_usage_and_batch_check(base):
    """08e-17 (S3): ``--reopen`` with ``--writeback`` is usage (2); a ``--batch`` that is not the record's is refused (30);
    the right one is accepted; a malformed one is usage (2). Nothing is written by a refusal."""
    before = RF.project_files(base.project)
    both = RF.cli(base.project, HB, None, "--writeback")
    assert both.returncode == 2
    other = RF.cli(base.project, HB, None, "--batch", "0" * 12)
    assert other.returncode == 30 and RF.receipt_of(other)["result"] == "refused"
    junk = RF.cli(base.project, HB, None, "--batch", "nope")
    assert junk.returncode == 2
    assert RF.project_files(base.project) == before
    ok = RF.cli(base.project, HB, None, "--batch", base.batch)
    assert ok.returncode == 10


def test_cli_reason_lands_in_the_event(base):
    """08e-17 (S1): ``--reason`` is the human's note on the event line."""
    res = RF.cli(base.project, HB, None, "--reason", "四路同读 119，图上是 11.9")
    assert res.returncode == 10
    (row,) = RF.log(base.adir)
    assert row["reason"] == "四路同读 119，图上是 11.9"


# --------------------------------------------------------------------------- 08e-19


def test_pipeline_takes_the_reopen_choices(tmp_path):
    """08e-19 (S5): U5 first half through the pipeline. fx-manualfix: all four routes read 10.5 -> verified(auto)
    through the writeback stage; the field is reopened with the public command, the corrected value goes in with
    ``pipeline --choices``, the writeback stage runs again, views follow."""
    rig = F.Rig(tmp_path, "fx-manualfix")
    res = rig.run("--stage", "writeback")
    assert res.returncode == 0, res.stderr.decode()
    (rec,) = [p.stem for p in (rig.project / "records" / "_by_id").glob("*.md")]
    assert W.front(rig.project, rec)["effective"]["verified_by"] == "auto"
    fid = next(f for f, v in W.body(rig.project, rec, 1)["fields"].items() if v == "10.5")

    out = subprocess.run([str(REPO / "bin" / "klode-kit"), "kb-audit", "--project", str(rig.project), "--reopen", fid],
                         env=rig.env, capture_output=True, timeout=120)
    assert out.returncode == 10, out.stderr.decode()
    rc = RF.receipt_of(out)
    path = W.write_choices(tmp_path / "choices.json", RF.choose(rig.project, rc, "manual", value="1.05"))

    res = rig.run("--stage", "writeback", "--choices", str(path))
    assert res.returncode == 0, res.stderr.decode()
    assert rig.receipt("writeback")["status"] == "done"
    front = W.front(rig.project, rec)
    assert (front["effective"]["rev"], front["effective"]["verified_by"]) == (2, "human")
    assert W.body(rig.project, rec, 2)["fields"][fid] == "1.05"
    targets = {x.resolve().name for x in (rig.project / "verified").glob("*.md")}
    assert targets == {f"{rec}.r2.md"}


# --------------------------------------------------------------------------- 08e-21 / 08e-23


@settings(max_examples=10, deadline=None, suppress_health_check=[HealthCheck.too_slow,
                                                                HealthCheck.function_scoped_fixture])
@given(value=VALUE)
def test_typed_value_round_trips_into_record_and_export(template, value):
    """08e-21 (S4): whatever well-formed value is typed, it is the effective value and the exported text; the other
    fields and the old body are untouched."""
    with tempfile.TemporaryDirectory() as tmp:
        b = RF.copy(template, Path(tmp))
        r1_bytes = W.body_path(b.project, AUTO_REC, 1).read_bytes()
        r1 = RF.values(b.project, AUTO_REC, 1)
        _, (code, receipt, err) = RF.reopen_and_fix(b, HB, value, Path(tmp) / "w")
        assert code == 0, err
        assert RF.values(b.project, AUTO_REC, 2) == {**r1, HB: value}
        assert W.body_path(b.project, AUTO_REC, 1).read_bytes() == r1_bytes
        exported = R.export(b.project, Path(tmp) / "out", kb_id="k")
        rows = [r for r in exported["export_map"] if HB in r["field_ids"]]
        assert rows and all(r["value_audited"] == value and value in r["text"] for r in rows)


@settings(max_examples=6, deadline=None, suppress_health_check=[HealthCheck.too_slow,
                                                               HealthCheck.function_scoped_fixture])
@given(steps=st.lists(st.tuples(st.sampled_from([HB, PLT]), VALUE), min_size=1, max_size=4))
def test_sequence_of_corrections_changes_only_the_reopened_fields(template, steps):
    """08e-23 (S5): after any sequence of reopen -> type -> write back, rev n differs from rev n-1 in exactly the
    reopened field, revs are consecutive, no earlier body was rewritten, and the chain has 0 violations."""
    with tempfile.TemporaryDirectory() as tmp:
        b = RF.copy(template, Path(tmp))
        bodies = {1: W.body_path(b.project, AUTO_REC, 1).read_bytes()}
        for n, (fid, value) in enumerate(steps, start=2):
            before = RF.values(b.project, AUTO_REC, n - 1)
            _, (code, _, err) = RF.reopen_and_fix(b, fid, value, Path(tmp) / f"w{n}")
            assert code == 0, err
            assert RF.values(b.project, AUTO_REC, n) == {**before, fid: value}
            assert W.front(b.project, AUTO_REC)["effective"]["rev"] == n
            bodies[n] = W.body_path(b.project, AUTO_REC, n).read_bytes()
            for k, data in bodies.items():
                assert W.body_path(b.project, AUTO_REC, k).read_bytes() == data
        assert violations(b.project, export_build(b.project, Path(tmp) / "build")) == []
