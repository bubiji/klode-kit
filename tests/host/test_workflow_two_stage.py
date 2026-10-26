"""WI-14 host acceptance (matrix 14-04, L3 S1; gates G2 and G5): ``/klode-kit:kb-pipeline`` run headless by
``claude -p --plugin-dir .`` on the synthetic ``fx-u3-mixed`` pages, in its two stages.

Written, not run by the implementer: the test carries ``gate_cloud``, so without ``KLODE_KIT_REAL=1`` and
``KLODE_KIT_GATE_ACK=G2-<yyyymmdd>`` the whole session is refused (exit 2, zero calls); with them it makes real
``claude`` calls (cap per call below, booked in ``work/host-spike/costs.jsonl``) and the pipeline's own OCR routes
run for real. The run is the user's decision (WI-31).

Headless is possible (WI-00d2, deviations item 11: ``claude -p`` waits for the background workflow and prints its
result), so 14-04 is a real host run and not a downgraded observation. What headless cannot show is the
``/workflows`` list with the running ``kb-pipeline`` (04 U3); that stays a user observation item for WI-31.

Evidence is the files the commands wrote (state, receipts) - not what a model says. Stage 1 must end with the
audit receipt at exit 10 and the batch ``awaiting_confirm``; stage 2 gets ``choices.json`` exported from the real
confirm page (default selections, by node) and must leave a done writeback receipt and not publish: args.publish is
absent, so ``kb/*/current`` and the registry stay untouched.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from _support.flows import REPO
from _support.host import Ledger, evidence_dir, lab_dir, run_claude_p
from klode_kit.audit import confirm_page as CP
from klode_kit.contract import schema as contract

CAP_USD = 2.0
WF_TOOLS = "Workflow(klode-kit:kb-pipeline),Bash(*/bin/klode-kit *),Bash(klode-kit *)"


@pytest.fixture(scope="module")
def ledger() -> Ledger:
    return Ledger()


def _workflow(project: Path, args: dict, label: str, ledger: Ledger, evidence: Path):
    prompt = (f"Run the /klode-kit:kb-pipeline workflow with args {json.dumps(args)} (pass args as that JSON "
              "object). Wait until the run has finished, then reply with its final result as JSON only.")
    return run_claude_p(["--plugin-dir", str(REPO), "--output-format", "json", "--model", "sonnet",
                         "--allowedTools", WF_TOOLS, "--no-session-persistence", prompt],
                        cwd=project, cap=CAP_USD, label=label, ledger=ledger, evidence=evidence)


def _batch_dir(project: Path) -> Path:
    batches = sorted(p for p in (project / "work").glob("*") if p.is_dir() and not p.is_symlink()
                     and (p / "pipeline" / "receipts").is_dir())
    assert batches, "the pipeline left no batch directory"
    return batches[-1]


def _receipt(batch: Path, stage: str) -> dict:
    return json.loads((batch / "pipeline" / "receipts" / f"{stage}.json").read_text(encoding="utf-8"))


@pytest.mark.host_acceptance
@pytest.mark.gate_cloud
def test_workflow_two_stage(ledger):
    """14-04: both segments pass in the host; stage 1 stops at audit exit 10, stage 2 writes back and does not publish."""
    project = lab_dir("kb-pipeline-two-stage")
    evidence = evidence_dir("14-04")
    inbox = project / "inbox"
    shutil.copytree(REPO / "tests" / "fixtures" / "fx-u3-mixed" / "images", inbox)

    first, _ = _workflow(project, {"project": str(project), "inbox": str(inbox)}, "stage1", ledger, evidence)
    batch = _batch_dir(project)
    for stage in ("preflight", "ocr", "audit"):
        assert contract.issues("receipt", _receipt(batch, stage)) == [], first.stderr.decode()[-2000:]
    assert _receipt(batch, "audit")["exit_code"] == 10
    audit_snapshot = (batch / "pipeline" / "receipts" / "audit.json").read_bytes()

    pages = sorted(batch.glob("audit/a*/confirm.html"))
    assert pages, "stage 1 produced no confirm page"
    exported = subprocess.run(["node", str(CP.EXPORT_JS), str(pages[-1])], capture_output=True, timeout=60)
    assert exported.returncode == 0, exported.stderr.decode()
    choices = project / "choices.json"
    choices.write_bytes(exported.stdout)

    second, _ = _workflow(project, {"project": str(project), "inbox": str(inbox), "resume": True,
                                    "choices": str(choices)}, "stage2", ledger, evidence)
    writeback = _receipt(batch, "writeback")
    assert contract.issues("receipt", writeback) == [], second.stderr.decode()[-2000:]
    assert writeback["exit_code"] in (0, 10)
    assert (batch / "pipeline" / "receipts" / "audit.json").read_bytes() == audit_snapshot  # stage 1 reused, not redone
    assert not (batch / "pipeline" / "publish_receipt.json").exists()  # publish is a manual gate
    assert not list(project.glob("kb/*/current"))
