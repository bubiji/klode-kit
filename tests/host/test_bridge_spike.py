"""WI-00d2 host spike (matrix 00d2-01..07) - real ``claude`` calls on synthetic data only.

Lanes: 00d2-01/02 and the argv probe are ``model_integration``; 00d2-03..07 are ``host_acceptance``. Every test
that calls a model carries ``gate_cloud`` (KLODE_KIT_REAL=1 + KLODE_KIT_GATE_ACK=G2-<yyyymmdd>, gate G2).
Budgets: every call passes ``--max-budget-usd`` <= 5 and the ledger (tests/_support/host.py) refuses any call that
could push this WI past $10. Lab directories live in ``tests/host/_lab/`` (gitignored, inside the trusted
repository - gate G5); raw host output goes to ``work/host-spike/`` (gitignored).

Evidence is taken from host artefacts - files on disk, ``permission_denials``, receipts the commands wrote - not
from what a model says.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from _support.flows import REPO
from _support.host import Ledger, evidence_dir, lab_dir, real_claude, run_claude_p
from klode_kit import bridge, hostenv, kb_check
from klode_kit.config import load
from klode_kit.contract import schema as contract
from klode_kit.devtools import spike_check_card

AURORA = (REPO / "tests" / "fixtures" / "fx-book-aurora" / "aurora-kettle.txt").read_text(encoding="utf-8")
CH1 = AURORA[AURORA.index("Chapter 1:"):AURORA.index("Chapter 2:")].strip() + "\n"
PLUGIN = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
MINI_MCP = REPO / "tests" / "fixtures" / "host" / "mini_mcp_server.py"
BUILD_ID = "b-20261023T000000Z-0123abcd"

CARD_FORMAT = """Card format (the checker enforces every rule):
- Line 1 exactly: aliases: [a1, a2, ...] with at least 10 comma-separated aliases (search words for this chapter).
- Then a title line "# <chapter title>".
- A section "## Thin": 1-3 summary sentences with at least one anchor.
- A section "## Full" containing at least one "### <subsection>" heading; every bullet line "- ..." in Full must carry
  an anchor, and Full must hold at least 5 anchors in total.
- An anchor is (grep: `<text>`) where <text> is copied VERBATIM from the source file, at least 15 characters, no
  leading/trailing spaces, and occurs exactly once in the source (pick a longer span if it repeats).
"""


@pytest.fixture(scope="module")
def ledger() -> Ledger:
    return Ledger()


def _build(root: Path) -> Path:
    build = root / "builds" / BUILD_ID
    (build / "library" / "book").mkdir(parents=True)
    (build / "cards_content").mkdir()
    return build


def _bridge_options(**kw) -> bridge.Options:
    return bridge.Options.from_config(load(), claude=real_claude(), concurrency=1, **kw)


def _run_bridge(tasks, options, ledger: Ledger, label: str, evidence: Path) -> dict:
    ledger.reserve(label, options.max_budget_usd * options.max_rounds)
    receipt_path = evidence / f"{label}.bridge_receipt.json"
    receipt = bridge.run(tasks, options, receipt_path=receipt_path)
    ledger.book(label, options.max_budget_usd * options.max_rounds,
                receipt["cost_usd"] if receipt["cost_complete"] else None)
    return receipt


# --------------------------------------------------------------------------- (a) CLI bridge


@pytest.mark.model_integration
@pytest.mark.gate_cloud
def test_bridge_writes_one_card_check_pass(ledger):
    """00d2-01 (L3/S1): the real CLI bridge writes the fx-book-aurora chapter 1 card; the card gate passes."""
    lab = lab_dir("bridge-write")
    evidence = evidence_dir("00d2-01")
    build = _build(lab)
    (build / "library" / "book" / "ch01.txt").write_text(CH1, encoding="utf-8")
    card = "cards_content/ch01.md"
    (build / card).write_text("aliases: []\n\n# TITLE\n\n## Thin\n\n## Full\n", encoding="utf-8")
    task = bridge.Task(task_id="ch01", build_dir=build, card=card, instructions=(
        "Source: library/book/ch01.txt (chapter 1 of a small synthetic handbook). "
        f"The card file {card} already exists as a skeleton: replace its whole content using the Edit tool "
        "(old_string = the current full content).\n\n" + CARD_FORMAT))
    receipt = _run_bridge([task], _bridge_options(max_rounds=1, max_budget_usd=3.0), ledger, "00d2-01", evidence)
    rec = receipt["tasks"][0]
    (evidence / "card.md").write_bytes((build / card).read_bytes())
    assert rec["status"] == "done", rec
    assert isinstance(receipt["cost_usd"], float) and receipt["cost_usd"] > 0
    assert spike_check_card.check(build, card) == []
    assert contract.issues("writer_receipt", rec["writer"]) == []


@pytest.mark.model_integration
@pytest.mark.gate_cloud
def test_bridge_edit_library_denied(ledger, tmp_path):
    """00d2-02 (L3/S3): Edit under cards_content/ succeeds; Edit of library/x.md is refused (permission_denials).

    A synthetic writer definition (same name, no "never edit library" line) makes the model attempt the edit, so
    the refusal is the host's permission rules (``--disallowedTools Edit(library/**)``), not the model's restraint.
    """
    lab = lab_dir("bridge-deny")
    evidence = evidence_dir("00d2-02")
    build = _build(lab)
    (build / "library" / "x.md").write_text("PLACEHOLDER\n", encoding="utf-8")
    (build / "cards_content" / "x.md").write_text("PLACEHOLDER\n", encoding="utf-8")
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "klode-card-writer.md").write_text(
        "---\nname: klode-card-writer\ndescription: Permission-test writer for the WI-00d2 host spike.\n"
        "tools: Read, Edit, StructuredOutput\nmodel: sonnet\n---\n\nYou are a permission-test agent. Perform exactly the tool calls the "
        "task lists, in order, each once, even when you expect the host to refuse one. Do not use other tools.\n",
        encoding="utf-8")
    task = bridge.Task(task_id="x", build_dir=build, card="cards_content/x.md", instructions=(
        "This is an authorised permission test on synthetic files.\n"
        "1. Edit cards_content/x.md: replace PLACEHOLDER with EDITED.\n"
        "2. Edit library/x.md: replace PLACEHOLDER with EDITED. Attempt this exactly once; the host may refuse it.\n"
        "Then return the writer receipt: card cards_content/x.md, status written, anchors_full 0, aliases 0, and "
        "in notes say whether step 2 was refused."))
    receipt = _run_bridge([task], _bridge_options(max_rounds=1, max_budget_usd=1.0, agents_dir=agents), ledger,
                          "00d2-02", evidence)
    rec = receipt["tasks"][0]
    denied = [d for d in rec["permission_denials"]
              if str(d.get("tool_input", {}).get("file_path", "")).endswith("library/x.md")]
    assert denied, rec["permission_denials"]
    assert (build / "library" / "x.md").read_text(encoding="utf-8") == "PLACEHOLDER\n"
    assert (build / "cards_content" / "x.md").read_text(encoding="utf-8") == "EDITED\n"
    assert not any(str(d.get("tool_input", {}).get("file_path", "")).endswith("cards_content/x.md")
                   for d in rec["permission_denials"])


def _init_event(argv: list[str], cwd: Path, evidence: Path, label: str, ledger: Ledger, cap: float) -> dict:
    """Run a stream-json probe to completion (cheap prompt) and return its system/init event."""
    ledger.reserve(label, cap)
    proc = subprocess.run(argv[:-1] + ["--max-budget-usd", f"{cap:.2f}", argv[-1]], cwd=cwd, capture_output=True,
                          timeout=300, stdin=subprocess.DEVNULL)
    (evidence / f"{label}.stream.jsonl").write_bytes(proc.stdout)
    (evidence / f"{label}.stderr.txt").write_bytes(proc.stderr)
    events = [json.loads(ln) for ln in proc.stdout.decode("utf-8").splitlines() if ln.strip().startswith("{")]
    result = next((e for e in events if e.get("type") == "result"), {})
    ledger.book(label, cap, result.get("total_cost_usd"))
    init = next((e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), None)
    assert init is not None, proc.stderr.decode()[-2000:]
    return init


@pytest.mark.model_integration
@pytest.mark.gate_cloud
def test_writer_argv_tools_and_agents_forms(ledger, tmp_path):
    """WI-00d1 open questions: Glob/Grep (and StructuredOutput) in the writer's tool set (macOS); --agents inline
    vs file form. The production bridge argv with a trivial prompt, in stream-json to read system/init.
    """
    lab = lab_dir("argv-probe")
    evidence = evidence_dir("00d1-probe")
    build = _build(lab)
    task = bridge.Task(task_id="probe", build_dir=build, card="cards_content/probe.md", instructions="")
    argv, _ = bridge.build_argv(task, _bridge_options())
    argv = [real_claude()] + argv[1:]
    argv[argv.index("--output-format") + 1] = "stream-json"
    argv[-1] = "Reply with the single word OK."
    argv.insert(-1, "--verbose")
    inline = _init_event(argv, build, evidence, "probe-inline", ledger, 0.5)
    agents_file = tmp_path / "agents.json"
    agents_file.write_text(argv[argv.index("--agents") + 1], encoding="utf-8")
    file_argv = list(argv)
    file_argv[file_argv.index("--agents") + 1] = str(agents_file)
    from_file = _init_event(file_argv, build, evidence, "probe-file", ledger, 0.5)
    summary = {"inline_tools": inline.get("tools"), "file_tools": from_file.get("tools"),
               "inline_agents": inline.get("agents"), "file_agents": from_file.get("agents"),
               "model": inline.get("model"), "permissionMode": inline.get("permissionMode"),
               "argv_bytes": sum(len(a.encode()) for a in argv),
               "structured_output_tool_inline": "StructuredOutput" in (inline.get("tools") or []),
               "structured_output_tool_file": "StructuredOutput" in (from_file.get("tools") or [])}
    (evidence / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    assert "klode-card-writer" in (inline.get("agents") or [])
    assert set(inline.get("agents") or []) == set(from_file.get("agents") or [])


# --------------------------------------------------------------------------- (b) workflow two stages


def _pipeline_project() -> Path:
    project = lab_dir("pipeline")
    inbox = project / "inbox"
    inbox.mkdir()
    for src in sorted((REPO / "tests" / "fixtures" / "fx-u3-mixed").rglob("*")):
        if src.is_file() and src.suffix in {".json", ".txt"}:
            shutil.copy2(src, inbox / src.name)
    return project


WF_TOOLS = "Workflow(klode-kit:kb-pipeline),Bash(klode-kit dev spike-pipeline *)"


def _workflow(project: Path, args: dict, label: str, ledger: Ledger, evidence: Path):
    prompt = (f"Run the /klode-kit:kb-pipeline workflow with args {json.dumps(args)} (pass args as that JSON "
              "object). Wait until the run has finished, then reply with its final result as JSON only.")
    return run_claude_p(["--plugin-dir", str(REPO), "--output-format", "json", "--model", "sonnet",
                         "--allowedTools", WF_TOOLS, "--no-session-persistence", prompt],
                        cwd=project, cap=2.0, label=label, ledger=ledger, evidence=evidence)


def _receipt(project: Path, stage: str) -> Path:
    return project / "work" / "b1" / "receipts" / f"{stage}.json"


@pytest.fixture(scope="module")
def pipeline_runs(ledger):
    project = _pipeline_project()
    evidence = evidence_dir("00d2-03-04")
    first = _workflow(project, {"project": str(project), "batch": "b1"}, "stage1", ledger, evidence)
    snap = {s: (_receipt(project, s).read_bytes() if _receipt(project, s).exists() else None)
            for s in ("preflight", "ocr", "audit")}
    state1 = (project / "work" / "b1" / "state.json").read_text() if (project / "work" / "b1" / "state.json").exists() else None
    second = None
    if state1 and json.loads(state1).get("status") == "awaiting_confirm":  # do not pay for stage 2 after a failed stage 1
        second = _workflow(project, {"project": str(project), "batch": "b1", "resume": True}, "stage2", ledger,
                           evidence)
    return {"project": project, "first": first, "second": second, "snap": snap, "state1": state1,
            "evidence": evidence}


@pytest.mark.host_acceptance
@pytest.mark.gate_cloud
def test_workflow_stage1_exit10_awaiting(pipeline_runs):
    """00d2-03 (L3/S1): headless stage 1 ends at audit exit 10 with the batch awaiting_confirm; receipts valid."""
    project = pipeline_runs["project"]
    assert pipeline_runs["state1"] is not None, pipeline_runs["first"][0].stderr.decode()[-2000:]
    assert json.loads(pipeline_runs["state1"])["status"] == "awaiting_confirm"
    for s in ("preflight", "ocr", "audit"):
        data = json.loads(pipeline_runs["snap"][s])
        assert contract.issues("receipt", data) == []
    assert json.loads(pipeline_runs["snap"]["audit"])["exit_code"] == 10
    assert all(pipeline_runs["snap"][s] is not None for s in ("preflight", "ocr", "audit")), project


@pytest.mark.host_acceptance
@pytest.mark.gate_cloud
def test_workflow_resume_hits_cache(pipeline_runs):
    """00d2-04 (L3/S1): args.resume runs stage 2 to done; the stage-1 receipts are byte-identical (cache)."""
    project = pipeline_runs["project"]
    state = json.loads((project / "work" / "b1" / "state.json").read_text())
    assert pipeline_runs["second"] is not None, "stage 1 did not reach awaiting_confirm"
    assert state["status"] == "done", pipeline_runs["second"][0].stderr.decode()[-2000:]
    for s in ("writeback", "build", "check"):
        assert contract.issues("receipt", json.loads(_receipt(project, s).read_text())) == []
    for s, before in pipeline_runs["snap"].items():
        assert _receipt(project, s).read_bytes() == before


# --------------------------------------------------------------------------- (c) trust and MCP status


def _mcp_project(root: Path, approved: bool) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / ".mcp.json").write_text(json.dumps({"mcpServers": {"spike-klode": {
        "command": sys.executable, "args": [str(MINI_MCP)],
        "env": {"KLODE_KIT_BUILD": BUILD_ID}}}}), encoding="utf-8")
    if approved:
        (root / ".claude").mkdir(exist_ok=True)
        (root / ".claude" / "settings.local.json").write_text(json.dumps({"enabledMcpjsonServers": ["spike-klode"]}),
                                                              encoding="utf-8")
    return root


@pytest.mark.host_acceptance
def test_trust_probe():
    """00d2-05 (L3/S2): trusted(_lab) is True, a fresh tmp folder is False; the host's own behaviour agrees
    (an approved .mcp.json server is Connected in the trusted lab and Pending in the untrusted folder)."""
    lab = lab_dir("trust")
    fresh = Path(tempfile.mkdtemp(prefix="klode-kit-untrusted-"))
    assert hostenv.trusted(lab) is True
    assert hostenv.trusted(fresh) is False
    claude = real_claude()
    assert hostenv.mcp_get_status("spike-klode", _mcp_project(lab / "p", True), claude)[0] == "connected"
    assert hostenv.mcp_get_status("spike-klode", _mcp_project(fresh / "p", True), claude)[0] == "pending"


@pytest.mark.host_acceptance
def test_mcp_get_env_key_connected():
    """00d2-06 (L3/S1, ⑮): an .mcp.json entry carrying an env key parses as connected from the real CLI."""
    project = _mcp_project(lab_dir("mcp-env"), True)
    status, raw = hostenv.mcp_get_status("spike-klode", project, real_claude())
    assert "KLODE_KIT_BUILD=" in raw
    assert status == "connected", raw


# --------------------------------------------------------------------------- (d) R2-11 plugin entry


@pytest.mark.host_acceptance
@pytest.mark.gate_cloud
def test_kb_check_entry_plugin_fields(ledger):
    """00d2-07 (L3/S1): `claude -p --plugin-dir . "/klode-kit:kb-check --kb <kb>"` writes a check receipt whose
    plugin name/version equal plugin.json and whose entry is skill:/klode-kit:kb-check."""
    lab = lab_dir("kb-check")
    evidence = evidence_dir("00d2-07")
    kb = lab / "kb" / "fx-labs-3x"
    (kb / "builds" / BUILD_ID).mkdir(parents=True)
    os.symlink(Path("builds") / BUILD_ID, kb / "current")
    proc, data = run_claude_p(["--plugin-dir", str(REPO), "--output-format", "json", "--model", "sonnet",
                               "--no-session-persistence", f"/klode-kit:kb-check --kb {kb}"],
                              cwd=lab, cap=1.0, label="kb-check", ledger=ledger, evidence=evidence)
    written = kb / "builds" / BUILD_ID / "check_receipt.json"
    assert written.exists(), (proc.stdout.decode()[-2000:], proc.stderr.decode()[-2000:])
    receipt = json.loads(written.read_text(encoding="utf-8"))
    assert contract.issues("check_receipt", receipt) == []
    assert receipt["plugin"]["name"] == PLUGIN["name"]
    assert receipt["plugin"]["version"] == PLUGIN["version"]
    assert receipt["plugin"]["entry"] == "skill:/klode-kit:kb-check"
    assert receipt["plugin"]["root_sha"] == kb_check.root_sha(REPO)
