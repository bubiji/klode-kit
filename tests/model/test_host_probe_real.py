"""WI-11b matrix 11b-09 (gate G2 + G5): the host probe against the real ``claude`` - the paired real path of the replayed
stream-json cases in ``tests/unit/test_u5_probe.py`` (the replay fake of ``claude`` is the mocked boundary there).

``claude -p --output-format stream-json --verbose`` is started in a tmp synthetic data project below the trusted lab
directory ``tests/host/_lab/`` (gitignored, G5) whose ``.mcp.json`` / ``settings.local.json`` were written by the
registrar; the model is asked to call ``kb_version`` once, and the ``tool_result`` that belongs to that ``tool_use`` id
must name the published build (R4-04). Synthetic data only. One call, cap $1.00, ledger limit $2.00.

NOT RUN in WI-11b (the manual gate): run when authorised::

    KLODE_KIT_REAL=1 KLODE_KIT_GATE_ACK=G2-<yyyymmdd> .venv/bin/python -m pytest -m model_integration \
        tests/model/test_host_probe_real.py
"""

from __future__ import annotations

import os
import shutil

import pytest

from _support import mcp_fx as M
from _support.host import EVIDENCE, Ledger, lab_dir, real_claude
from klode_kit import u5_probe

pytestmark = [pytest.mark.model_integration, pytest.mark.gate_cloud]

CAP_USD = 1.0
WI_LIMIT_USD = 2.0


def test_host_probe_tool_result(tmp_path):
    """11b-09 (L3, S1): the real host reads the published build's id from the tool_result of its own kb_version call."""
    if os.environ.get("KLODE_KIT_REAL") != "1":
        pytest.fail("model_integration needs KLODE_KIT_REAL=1 (missing dependency is a FAIL, never a skip)")
    claude = real_claude()
    ledger = Ledger(EVIDENCE / "costs-wi11b.jsonl")
    if ledger.total() + CAP_USD > WI_LIMIT_USD + 1e-9:
        pytest.fail(f"WI-11b ledger {ledger.total():.2f} + cap {CAP_USD} > limit {WI_LIMIT_USD}")
    world = M.marked(tmp_path)
    M.publish(world, "B")  # registers into the tmp project and the tmp KLODE_REGISTRY
    lab = lab_dir("u5-host")
    shutil.copytree(world.project, lab / "proj", symlinks=True)
    project = lab / "proj"
    env = dict(os.environ)  # the real HOME is only read (credentials, trust); every write above went to tmp
    env["PATH"] = os.pathsep.join(p for p in env["PATH"].split(os.pathsep) if p != str(M.REPO / "tests" / "fakes" / "bin"))
    ledger.reserve("11b-09", CAP_USD)
    out = u5_probe.host_probe(project, "proj-klode", env=env, claude=claude, max_budget_usd=CAP_USD, timeout=300)
    ledger.book("11b-09", CAP_USD, out.get("cost_usd"))
    assert out["host_state"] == "connected", out
    assert out["build_id"] == world.ids["B"], out
