"""WI-12 matrix 12-05 (gates G2 + G5, R4-03): the host discovers the expert agent generated into a data project.

A synthetic data project is made below the trusted lab directory ``tests/host/_lab/`` (gitignored, G5); ``gen-agent``
writes ``<id>-klode-expert.md`` into its ``.claude/agents/`` (exactly where publish copies it); ``claude -p
--output-format stream-json --verbose`` is started there with a trivial prompt and the evidence is the host's own
``system``/``init`` event - its ``agents`` list - not anything the model says. One call, cap $0.50, ledger limit $10.

The static half (the file passes Claude Code's own loading rules) is unit-tested in tests/unit/test_agents_static.py.
Naming: the generator names the agent ``<kb id>-klode-expert`` (WI-02d, rule kept from the user's original script); the
matrix's ``<kb>-expert`` is read as that name.

NOT RUN in WI-12 (the manual gate): without ``KLODE_KIT_REAL=1`` and ``KLODE_KIT_GATE_ACK=G2-<yyyymmdd>`` the session
stops with a usage error (exit 2) before any test runs. Run when authorised::

    KLODE_KIT_REAL=1 KLODE_KIT_GATE_ACK=G2-<yyyymmdd> .venv/bin/python -m pytest -m host_acceptance \
        tests/host/test_expert_discovery.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess

import pytest

from _support.flows import REPO
from _support.host import Ledger, evidence_dir, lab_dir, real_claude

pytestmark = [pytest.mark.host_acceptance, pytest.mark.gate_cloud]

CAP_USD = 0.5


def _kb_helpers():
    spec = importlib.util.spec_from_file_location("kk_test_gen_agent", REPO / "tests" / "unit" / "test_gen_agent.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_host_discovers_expert(tmp_path):
    """12-05 (L3, S1): the ``init`` event of a session started in the data project lists ``<kb id>-klode-expert``."""
    if os.environ.get("KLODE_KIT_REAL") != "1":
        pytest.fail("host_acceptance needs KLODE_KIT_REAL=1 (missing dependency is a FAIL, never a skip)")
    ledger = Ledger()
    ledger.reserve("12-05", CAP_USD)
    helpers = _kb_helpers()
    kb = helpers.make_kb(tmp_path, kid="labs3")
    lab = lab_dir("expert-discovery")
    evidence = evidence_dir("12-05")
    project = lab / "proj"
    agent = project / ".claude" / "agents" / "labs3-klode-expert.md"
    assert helpers.gen(kb, agent, "--build-id", "b-12-05") == 0
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join(p for p in env["PATH"].split(os.pathsep)
                                  if p != str(REPO / "tests" / "fakes" / "bin"))
    argv = [real_claude(), "-p", "--output-format", "stream-json", "--verbose", "--max-budget-usd", f"{CAP_USD:.2f}",
            "--no-session-persistence", "Reply with the single word OK."]
    proc = subprocess.run(argv, cwd=project, env=env, capture_output=True, timeout=300, stdin=subprocess.DEVNULL)
    (evidence / "stream.jsonl").write_bytes(proc.stdout)
    (evidence / "stderr.txt").write_bytes(proc.stderr)
    events = [json.loads(ln) for ln in proc.stdout.decode("utf-8").splitlines() if ln.strip().startswith("{")]
    result = next((e for e in events if e.get("type") == "result"), {})
    ledger.book("12-05", CAP_USD, result.get("total_cost_usd"))
    init = next((e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), None)
    assert init is not None, proc.stderr.decode("utf-8", "replace")[-2000:]
    assert "labs3-klode-expert" in (init.get("agents") or []), init.get("agents")
