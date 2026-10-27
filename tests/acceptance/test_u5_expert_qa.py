"""U5 with the model (gate G2): ask the expert agent of a published medical library "示例片最近三次的剂量" and then
something the library does not hold.

What the user sees (04): every line of the answer carries a verbatim quotation ``(grep: 示例片 10mg 2026-09-18)``,
``klode verify`` says it is there; for a question the library cannot answer the reply is "查不到", not an invented value.

Lane model_integration, ``gate_cloud`` (matrix U5-02, U5-03): without ``KLODE_KIT_REAL=1`` and
``KLODE_KIT_GATE_ACK=G2-<yyyymmdd>`` the whole session stops with a usage error before any call. The library is the
synthetic prescription page ``fx-rx-stamp`` (audited, written back, built with the real klode, checked, published by the
real ``kb-publish`` into a private registry) in a data project under the trusted lab (``tests/host/_lab/``, G5): the
expert file, ``.mcp.json`` entry and the approval in ``settings.local.json`` are exactly what publish writes. One
``claude -p --agent <kb>-klode-expert`` call per question (cap below, booked in ``work/host-spike/costs.jsonl``).

The library holds ``示例片 10mg / 5mg / 0.25g`` of one prescription dated 2026-09-19 (04's example date 2026-09-18 is
not in it); "the last three doses" are those three lines. What is asserted is checkable by files and by klode, not by
how the model words it: every answer line has an anchor, every anchor is found by the real ``klode verify`` in the
library, the three doses are all named; and for the absent drug the reply says it is not found and contains no dose.
"""

from __future__ import annotations

import json
import os
import re
import subprocess

import pytest

from _support import accept_fx as A
from _support import build_fx as B
from _support.host import Ledger, evidence_dir, lab_dir, real_claude

pytestmark = [pytest.mark.model_integration, pytest.mark.gate_cloud]

CAP_USD = 1.5
PRESENT = ["示例片 10mg 2026-09-19", "示例片 5mg 2026-09-19", "示例片 0.25g 2026-09-19"]
TOOLS = ("mcp__proj-klode__search_sources mcp__proj-klode__zoom_card mcp__proj-klode__verify_quote "
         "mcp__proj-klode__list_kbs mcp__proj-klode__list_lenses mcp__proj-klode__diagnose "
         "mcp__proj-klode__consult_dimension Read Grep Glob")
ANCHOR = re.compile(r"\(grep:\s*`?([^)`]+?)`?\)")
NOT_FOUND = re.compile(r"查不到|找不到|没(?:有)?(?:找到|查到|搜到|检索到)|未(?:找到|查到|检索到)|库里没有|不在(?:本)?库|没有[^。\n]{0,24}(?:记录|信息|数据)")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
DOSE = re.compile(r"\d+(?:\.\d+)?\s*(?:mg|g|ml|mL|毫克|克|毫升|片|粒)")


@pytest.fixture(scope="module")
def world():
    lab = lab_dir("u5-expert")
    project = B.project(lab, "fx-rx-stamp")
    (project / ".gitignore").write_text("_work/\n_raw/\n_masked/\n_mask_review/\n_private/\nwork/\nkb/\n", encoding="utf-8")
    res = B.build(project, lab)
    assert res.returncode == 0, res.stderr.decode()
    (build,) = B.builds(project)
    return A.check_and_publish(project, lab, build), build


def ask(world_, question: str, label: str) -> tuple[str, list[dict], dict]:
    ledger = Ledger()
    ledger.reserve(label, CAP_USD)
    evidence = evidence_dir(label)
    project = world_.project
    argv = [real_claude(), "-p", "--agent", "proj-klode-expert", "--output-format", "stream-json", "--verbose",
            "--allowedTools", TOOLS, "--no-session-persistence", "--max-budget-usd", f"{CAP_USD:.2f}", question]
    proc = subprocess.run(argv, cwd=project, env=A.real_env(), capture_output=True, timeout=900,
                          stdin=subprocess.DEVNULL)
    (evidence / "stream.jsonl").write_bytes(proc.stdout)
    (evidence / "stderr.txt").write_bytes(proc.stderr)
    events = [json.loads(ln) for ln in proc.stdout.decode("utf-8").splitlines() if ln.strip().startswith("{")]
    result = next((e for e in reversed(events) if e.get("type") == "result"), {})
    ledger.book(label, CAP_USD, result.get("total_cost_usd"))
    (evidence / "answer.txt").write_text(str(result.get("result", "")), encoding="utf-8")
    assert result.get("is_error") is False, (result, proc.stderr.decode()[-2000:])
    return str(result["result"]), events, result


def test_u5_expert_qa(world):
    """U5-02 (L3 S1): the three doses, each line with its anchor, each anchor found by the real klode."""
    assert os.environ.get("KLODE_KIT_REAL") == "1"
    world_, build = world
    answer, events, result = ask(world_, "示例片最近三次的剂量", "u5-02")
    # an anchor is a verbatim piece of a library line; quoting the whole line includes its trailing record mark
    anchors = [re.sub(r"\s*⟨[^⟩]*⟩$", "", a.strip()) for a in ANCHOR.findall(answer)]
    # the answer lines are the bullets that open with a date (the expert agent's format: "- 2026-09-19 ... (grep: ...)");
    # the agent may add notes as bullets of its own, they do not open with a date
    lines = [ln for ln in answer.splitlines() if re.match(r"\s*(?:[-*•]|\d+[.、)])\s*\**\d{4}-\d{2}-\d{2}", ln)]
    assert len(lines) >= 3, answer
    assert all(ANCHOR.search(ln) for ln in lines), answer  # every answer line carries an anchor
    assert set(PRESENT) <= {a for a in anchors}, (anchors, answer)  # the three doses are all quoted
    card = A.card_with(build, PRESENT[0])
    for phrase in anchors:
        found = A.klode(build, "verify", card, phrase)
        assert found.returncode == 0 and "FOUND" in found.stdout and "NOT-FOUND" not in found.stdout, (phrase, found.stdout)
    # the host's own record: the answer came through the library's tools
    used = [b["name"] for e in events if e.get("type") == "assistant"
            for b in (e.get("message", {}).get("content") or []) if isinstance(b, dict) and b.get("type") == "tool_use"]
    assert any(n.startswith("mcp__proj-klode__") for n in used), used


def test_u5_expert_not_found(world):
    """U5-03 (L3 S3): a drug the library does not hold -> the reply says the library has nothing and contains no dose.

    04 says the reply is "查不到". The agent file asks for those words; the model sometimes says the same thing in
    other words ("库里没有阿莫西林的记录", observed 2026-10-27). The hard assertion is the meaning (an explicit statement
    that nothing was found, and no dose value anywhere); whether the literal words were used is written to the evidence
    (``literal.txt``) and reported in 08."""
    assert os.environ.get("KLODE_KIT_REAL") == "1"
    world_, _build = world
    answer, _events, _result = ask(world_, "阿莫西林最近三次的剂量", "u5-03")
    (evidence_dir("u5-03-literal") / "literal.txt").write_text(f"{'查不到' in answer}\n", encoding="utf-8")
    assert NOT_FOUND.search(answer), answer
    assert not DOSE.search(answer), answer
    assert "(grep:" not in answer  # nothing is quoted: there is nothing to quote
