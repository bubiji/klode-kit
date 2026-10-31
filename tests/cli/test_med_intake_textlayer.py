"""WI-33d (real-data pilot P8, decision D-35): ``med-intake`` / ``pipeline`` on a lab PDF *with a text layer*, with the
real poppler tools and the real kb-ocr / kb-audit / confirm-page children (cli_integration lane; the in-process cases
are in tests/unit/test_wi33d_textlayer.py).

Synthetic data only: ``_support.textpdf_fx`` builds a two-page lab report PDF (made-up names and numbers). No OCR
route is started: every page has a text layer, and the engine replay directory is empty on purpose - a route call
would fail the case.

Paired real path of the unit-lane mocks: none (the unit cases mock nothing). The launcher wrapper below is the
pipeline rig's (``doctor`` stub, everything else the real launcher); ``STUB_AUDIT`` additionally makes ``kb-audit
--batch B`` (the audit proper, not the writeback) exit 0 without writing anything, which is the pilot's symptom
reproduced at the child boundary: pages, and no record.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from _support import pipeline_fx as F
from _support.flows import base_env
from _support.textpdf_fx import write_lab_pdf
from klode_kit.contract import ids

pytestmark = pytest.mark.cli_integration

STUB_AUDIT = '''#!{python}
import json, os, sys
argv = sys.argv[1:]
if argv[:1] == ["kb-audit"] and "--writeback" not in argv:
    sys.exit(0)  # the pilot's symptom: the audit stage ran, said fine, produced nothing
real = {real!r}
os.execv(real, [real, *argv])
'''


class Rig:
    def __init__(self, tmp: Path, *, stub_audit: bool = False):
        self.project = tmp / "project"
        self.project.mkdir()
        self.inbox = tmp / "_inbox" / "2026-10-22"
        self.inbox.mkdir(parents=True)
        self.pdf = write_lab_pdf(self.inbox / "lab.pdf")
        self.wrapper = F.make_wrapper(tmp / "bin" / "launcher")
        if stub_audit:  # a launcher in front of the pipeline rig's: same doctor stub, kb-audit does nothing
            outer = tmp / "bin" / "outer"
            outer.write_text(STUB_AUDIT.format(python=sys.executable, real=str(self.wrapper)), encoding="utf-8")
            outer.chmod(0o755)
            self.wrapper = outer
        replay = tmp / "replay-empty"
        replay.mkdir()
        self.calls = tmp / "calls.jsonl"
        self.env = base_env({"KLODE_KIT_TEST_SEAMS": "1", "KLODE_KIT_ENGINE_REPLAY": str(replay),
                             "KLODE_KIT_FAKE_LOG": str(self.calls), "KLODE_KIT_PIPELINE_LOG": str(tmp / "pl.jsonl")})
        self.env.pop("KLODE_KIT_REAL", None)

    def run(self, command: str, *args: str) -> subprocess.CompletedProcess:
        flag = "--inbox" if command == "med-intake" else "--batch"
        argv = [sys.executable, "-m", "klode_kit", command, flag, str(self.inbox), "--project", str(self.project),
                "--launcher", str(self.wrapper), *args]
        return subprocess.run(argv, env=self.env, capture_output=True, timeout=300, cwd=str(self.project))

    @property
    def batch_dir(self) -> Path:
        return self.project / "work" / ids.batch_id([F.sha_file(self.pdf)])

    def candidates(self) -> list[dict]:
        path = self.batch_dir / "audit" / "a1" / "candidates.jsonl"
        return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]


def test_med_intake_audits_the_text_layer_and_does_not_call_a_failure_a_skip(tmp_path):
    rig = Rig(tmp_path)
    res = rig.run("med-intake")
    assert res.returncode == 0, res.stderr.decode()
    lines = res.stdout.decode().splitlines()
    assert lines[0] == "ocr: 2 页文字层，跳过 OCR"           # not "8 处失败"
    assert "失败" not in res.stdout.decode()
    assert lines[-1] == "status: nothing_to_confirm"          # every field agrees: nothing to click
    cands = rig.candidates()
    assert len(cands) == 2 and {c["status"] for c in cands} == {"verified"}
    assert {c["verified_by"] for c in cands} == {"auto"}
    fields = [f for c in cands for f in c["fields"]]
    assert fields and {e for f in fields for _, es in f["votes"] for e in es} == {"pdftext"}
    values = {v for f in fields for v, _ in f["votes"]}
    assert {"128", "6.2", "78.5"} <= values
    receipt = json.loads((rig.batch_dir / "med_intake.json").read_text(encoding="utf-8"))
    assert receipt["ocr_failures"] == [] and receipt["ocr_skipped"] == ["p001", "p002"]
    assert (receipt["record_type"], receipt["type_source"], receipt["status"]) == ("lab", "header", "nothing_to_confirm")
    assert not rig.calls.exists() or rig.calls.read_text(encoding="utf-8").strip() == ""  # no route was called


def test_second_segment_writes_the_records(tmp_path):
    """med-intake exit 0 -> `pipeline --stage writeback` ingests the auto-verified candidates (SKILL, segment 2)."""
    rig = Rig(tmp_path)
    assert rig.run("med-intake", "--type", "lab").returncode == 0
    res = rig.run("pipeline", "--type", "lab", "--stage", "writeback")
    assert res.returncode == 0, res.stderr.decode()
    records = sorted((rig.project / "records" / "_by_id").glob("*.md"))
    assert len(records) == 2
    by_id = "\n".join(p.read_text(encoding="utf-8") for p in records)
    assert by_id.count('"status": "verified"') >= 2 and '"verified_by": "auto"' in by_id
    views = sorted((rig.project / "verified").glob("2026-10-22-lab*.md"))
    assert len(views) == 2  # two reports of the same day and type: the second view is `-2`
    body = "\n".join(p.read_text(encoding="utf-8") for p in views)
    assert '"128"' in body and '"6.2"' in body and '"78.5"' in body


def test_rerun_after_the_old_silent_run_is_not_served_from_the_stale_stage_receipt(tmp_path):
    """The pilot's batch already has a done `audit` stage receipt from the run that audited nothing. Asking again
    with the type must run the audit (the type is part of the stage's inputs), not reuse the empty result."""
    rig = Rig(tmp_path)
    assert rig.run("pipeline", "--stage", "audit").returncode == 0      # the old behaviour: no type, nothing to audit
    assert not (rig.batch_dir / "audit").exists()
    res = rig.run("med-intake")
    assert res.returncode == 0, res.stderr.decode()
    assert len(rig.candidates()) == 2


def test_book_path_without_a_type_is_unchanged(tmp_path):
    """U4: `pipeline` without --type on a text-layer PDF audits nothing and is not an error."""
    rig = Rig(tmp_path)
    res = rig.run("pipeline", "--stage", "audit")
    assert res.returncode == 0, res.stderr.decode()
    assert not (rig.batch_dir / "audit").exists()


@pytest.mark.parametrize("command,args", [("med-intake", ()), ("pipeline", ("--type", "lab", "--stage", "audit"))])
def test_pages_but_no_records_is_exit_20_never_success(tmp_path, command, args):
    rig = Rig(tmp_path, stub_audit=True)
    res = rig.run(command, *args)
    err = res.stderr.decode()
    assert res.returncode == 20, err
    assert "0 records" in err and "2 page" in err, err
    assert "nothing_to_confirm" not in res.stdout.decode()
    assert not (rig.project / "records").exists()
    if command == "med-intake":
        assert not (rig.batch_dir / "med_intake.json").exists()  # no receipt that says the batch is done
