"""U3 in the host (gates G2 and G5): "run the pipeline" -> ``/klode-kit:kb-pipeline`` run by ``claude -p --plugin-dir .``
on the synthetic ``fx-u3-mixed`` pictures (registered in ``tests/fixtures/MANIFEST.json``), its first segment, all four
OCR routes real (the pictures are masked first, see below).

Lane host_acceptance, ``gate_cloud``: without ``KLODE_KIT_REAL=1`` and ``KLODE_KIT_GATE_ACK=G2-<yyyymmdd>`` the whole
session stops with a usage error before any call. One ``claude`` call (cap below), booked in
``work/host-spike/costs.jsonl``; the pipeline's own OCR calls are booked in the batch's cloud ledger and added to the
same ledger. The project is a fresh directory under the trusted lab (``tests/host/_lab/``, G5).

What 04 says the user sees is "``/workflows`` shows the running ``kb-pipeline``". That list is the interactive UI of
Claude Code (``/workflows`` is a slash command of the TUI); a headless ``claude -p`` run can not open it. This test
asserts the evidence that does exist headless - the host's own event stream (``--output-format stream-json
--verbose``) shows the ``Workflow`` tool started with ``klode-kit:kb-pipeline`` (the name the list shows) and the
workflow's result - plus the files the commands wrote (stage receipts: preflight, ocr and audit done, audit at exit
10, nothing written back), and lists the visual check as the user's own (08 note: open ``/workflows`` while a run
is going).

The second segment (matrix 14-04) is a test of its own: the choices the confirm page's own exporter gives (run by
node) -> ``resume`` -> writeback done, the first segment's audit receipt untouched, nothing published. WI-31
found it stopping when the workflow's agent tried ``${CLAUDE_PLUGIN_ROOT}/bin/klode-kit`` (not substituted in a workflow
script; Bash refuses "Contains expansion"); WI-32d makes the workflow name ``klode-kit`` and the case passes (user
approved removing its xfail, 2026-10-27). ``tests/host/test_workflow_two_stage.py`` (14-04 as written) fails earlier for a setup reason: its project
has no ``.gitignore`` entries and the session's ``KLODE_KIT_TEST_SEAMS`` reaches ``claude``, so the workflow's
``preflight`` answers exit 40 (observed 2026-10-27, $0.35).

No masking step is run beforehand: the workflow's ``ocr`` stage (``kb-ocr``) masks by itself (WI-32a).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from _support import accept_fx as A
from _support import kb_ocr as K
from _support.flows import REPO
from _support.host import Ledger, evidence_dir, lab_dir, real_claude
from klode_kit.audit import confirm_page as CP
from klode_kit.contract import schema as contract

pytestmark = [pytest.mark.host_acceptance, pytest.mark.gate_cloud]

CAP_USD = 2.0
WF_TOOLS = "Workflow(klode-kit:kb-pipeline),Bash(*/bin/klode-kit *),Bash(klode-kit *)"


def workflow(project, args: dict, label: str, ledger: Ledger, evidence, env) -> tuple[subprocess.CompletedProcess, list]:
    """One ``claude -p`` call that runs the workflow with ``args``; the stream is saved, the cost booked."""
    ledger.reserve(label, CAP_USD)
    prompt = (f"Run the /klode-kit:kb-pipeline workflow with args {json.dumps(args)} (pass args as that JSON object). "
              "Wait until the run has finished, then reply with its final result as JSON only.")
    argv = [real_claude(), "-p", "--plugin-dir", str(REPO), "--output-format", "stream-json", "--verbose",
            "--model", "sonnet", "--allowedTools", WF_TOOLS, "--no-session-persistence",
            "--max-budget-usd", f"{CAP_USD:.2f}", prompt]
    (evidence / f"{label}.argv.json").write_text(json.dumps(argv, ensure_ascii=False, indent=1), encoding="utf-8")
    proc = subprocess.run(argv, cwd=project, env=env, capture_output=True, timeout=1800, stdin=subprocess.DEVNULL)
    (evidence / f"{label}.stream.jsonl").write_bytes(proc.stdout)
    (evidence / f"{label}.stderr.txt").write_bytes(proc.stderr)
    events = [json.loads(ln) for ln in proc.stdout.decode("utf-8").splitlines() if ln.strip().startswith("{")]
    result = next((e for e in reversed(events) if e.get("type") == "result"), {})
    ledger.book(label, CAP_USD, result.get("total_cost_usd"))
    assert result.get("is_error") is False, (label, result, proc.stderr.decode()[-1500:])
    return proc, events


@pytest.fixture(scope="module")
def first_segment():
    """A fresh lab project, the pictures masked, the workflow's first segment run once (one ``claude -p`` call)."""
    assert os.environ.get("KLODE_KIT_REAL") == "1"
    ledger = Ledger()
    ledger.reserve("u3-workflows", 2 * CAP_USD)
    lab = lab_dir("u3-workflows")
    evidence = evidence_dir("u3-workflows")
    project = lab / "project"
    project.mkdir()
    (project / ".gitignore").write_text("_work/\n_raw/\n_masked/\n_mask_review/\n_private/\nwork/\nconfirm/\n"
                                        "records/\nverified/\n", encoding="utf-8")
    inbox = lab / "inbox"
    inbox.mkdir()
    for img in sorted((K.FIXTURES / "fx-u3-mixed" / "images").glob("*.png")):
        shutil.copyfile(img, inbox / img.name)
    A.stage_isolation_receipts(project)
    env = A.real_env()
    _proc, events = workflow(project, {"project": str(project), "inbox": str(inbox)}, "stage1", ledger, evidence, env)
    (batch_dir,) = [p for p in (project / "work").iterdir() if p.is_dir() and not p.is_symlink()
                    and (p / "derived.jsonl").is_file()]
    batch_id = batch_dir.name
    ocr_spent = 0.0
    cloud_ledger = project / "work" / batch_id / "receipts" / "cloud_ledger.json"
    if cloud_ledger.is_file():
        ocr_spent = float(json.loads(cloud_ledger.read_text(encoding="utf-8"))["spent_usd"])
    ledger.book("u3-workflows-ocr", 5.0, ocr_spent)
    (evidence / "meta.json").write_text(json.dumps({"ocr_cloud_spent": ocr_spent}, indent=1), encoding="utf-8")
    return {"lab": lab, "project": project, "inbox": inbox, "env": env, "events": events, "ledger": ledger,
            "evidence": evidence}


def test_u3_workflows_host(first_segment):
    """U3-03 (L3 S1): the workflow's first segment in the host - the run is registered as the ``kb-pipeline``
    background workflow (what ``/workflows`` lists) and the segment ends at the human."""
    events, project = first_segment["events"], first_segment["project"]
    # the host's own record that the workflow ran: a Workflow tool call naming klode-kit:kb-pipeline, the background
    # task of type local_workflow named kb-pipeline (the entry /workflows lists) and its completion
    calls = [b for e in events if e.get("type") == "assistant"
             for b in (e.get("message", {}).get("content") or []) if isinstance(b, dict)
             and b.get("type") == "tool_use" and b.get("name", "").startswith("Workflow")]
    assert calls, [e.get("type") for e in events][:40]
    assert any("kb-pipeline" in json.dumps(c.get("input", {})) for c in calls), calls
    started = [e for e in events if e.get("type") == "system" and e.get("subtype") == "task_started"
               and e.get("task_type") == "local_workflow"]
    assert [e.get("workflow_name") for e in started] == ["kb-pipeline"], started
    listed = [t for e in events if e.get("subtype") == "background_tasks_changed" for t in e.get("tasks", [])]
    assert any(t.get("task_type") == "local_workflow" and t.get("task_id") == started[0]["task_id"] for t in listed)
    done = [e for e in events if e.get("subtype") == "task_notification" and e.get("task_id") == started[0]["task_id"]]
    assert done and done[-1]["status"] == "completed", done

    # the files the workflow's commands wrote: the first segment ends at the human
    (batch,) = [p for p in (project / "work").iterdir() if (p / "pipeline" / "receipts").is_dir()]
    for stage in ("preflight", "ocr", "audit"):
        rec = json.loads((batch / "pipeline" / "receipts" / f"{stage}.json").read_text(encoding="utf-8"))
        assert contract.issues("receipt", rec) == [] and rec["status"] == "done", (stage, rec)
    audit = json.loads((batch / "pipeline" / "receipts" / "audit.json").read_text(encoding="utf-8"))
    assert audit["exit_code"] == 10
    assert sorted(p.name for p in (batch / "pipeline" / "receipts").glob("*.json")) == ["audit.json", "ocr.json",
                                                                                         "preflight.json"]
    assert (batch / "audit" / "a1" / "confirm.html").is_file()
    assert not (project / "records").exists() and not list(project.glob("kb/*/current"))


def test_u3_second_segment_writes_back(first_segment):
    """U3-03, second segment (matrix 14-04): the choices the page's own exporter gives, ``resume`` -> writeback is done,
    the first segment's audit receipt is reused untouched, nothing is published."""
    project, lab, ledger, evidence, env = (first_segment[k] for k in ("project", "lab", "ledger", "evidence", "env"))
    (batch,) = [p for p in (project / "work").iterdir() if (p / "pipeline" / "receipts").is_dir()]
    page = batch / "audit" / "a1" / "confirm.html"
    picks = [{"field_id": f["field_id"], "kind": "route",
              "route": next(r for r in A.ROUTES if f["seen"].get(r) == f.get("recommended") or (
                  f.get("recommended") is None and f["seen"].get(r)))}
             for rec in CP.page_data(page.read_bytes())["records"] for f in rec["fields"]]
    assert picks, "the page asks for at least one click (an export without clicks is refused by the writeback)"
    selections = lab / "selections.json"
    selections.write_text(json.dumps(picks), encoding="utf-8")
    exported = subprocess.run(["node", str(CP.EXPORT_JS), str(page), str(selections)], capture_output=True, timeout=60)
    assert exported.returncode == 0, exported.stderr.decode()
    choices = lab / "choices.json"
    choices.write_bytes(exported.stdout)
    audit_snapshot = (batch / "pipeline" / "receipts" / "audit.json").read_bytes()
    _proc, events2 = workflow(project, {"project": str(project), "inbox": str(first_segment["inbox"]), "resume": True,
                                        "choices": str(choices)}, "stage2", ledger, evidence, env)
    writeback = json.loads((batch / "pipeline" / "receipts" / "writeback.json").read_text(encoding="utf-8"))
    assert contract.issues("receipt", writeback) == [] and writeback["exit_code"] in (0, 10)
    assert (batch / "pipeline" / "receipts" / "audit.json").read_bytes() == audit_snapshot  # reused, not redone
    assert not (batch / "pipeline" / "publish_receipt.json").exists()  # publish is a manual gate
    assert not list(project.glob("kb/*/current"))
    assert any(b.get("name", "").startswith("Workflow") for e in events2 if e.get("type") == "assistant"
               for b in (e.get("message", {}).get("content") or []) if isinstance(b, dict))
