"""WI-13a host acceptance (matrix 13a-04, L3 S1; gates G2 and G5): ``claude -p --plugin-dir . "/klode-kit:kb-check
--kb <kb>"`` on a finished build of the synthetic ``fx-labs-3x`` writes a check receipt whose plugin block is the
plugin's own: ``name`` / ``version`` from ``.claude-plugin/plugin.json``, ``entry`` ``skill:/klode-kit:kb-check`` and
``root_sha`` equal to the release sha.

Written, not run by the implementer: the test carries ``gate_cloud``, so without ``KLODE_KIT_REAL=1`` and
``KLODE_KIT_GATE_ACK=G2-<yyyymmdd>`` the whole session is refused (exit 2, zero calls); with them it makes one real
``claude`` call (cap below, booked in the WI ledger ``work/host-spike/costs.jsonl``). The run is the user's decision.

Evidence comes from host artefacts - the receipt file the command wrote below the lab knowledge base - not from what a
model says (the skill renders the command at invocation time; the model only repeats its output).

``release_sha`` is computed here from the release definition (design r3.4 / R3-08: the run files of the published
package, ``sorted(relpath)``, ``sha256(relpath + "\\0" + content)`` each, then the sha of the concatenated hex
digests; ``__pycache__``, ``*.pyc`` and ``.DS_Store`` are build artefacts and not run files), taken from WI-15b's
``release_manifest``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from _support import kb_check_fx as K
from _support.flows import REPO
from _support.host import Ledger, evidence_dir, lab_dir, run_claude_p
from klode_kit.contract import schema as contract
from klode_kit.devtools.release_manifest import release_sha  # user-approved 2026-10-27: one release definition

PLUGIN = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
CAP_USD = 1.0


@pytest.fixture(scope="module")
def ledger() -> Ledger:
    return Ledger()


@pytest.mark.host_acceptance
@pytest.mark.gate_cloud
def test_kb_check_entry(ledger):
    """13a-04: the plugin block of the receipt written by the host-run skill."""
    lab = lab_dir("kb-check-entry")
    evidence = evidence_dir("13a-04")
    kb, build = K.good_kb(lab)  # a copy of a real, finished build of fx-labs-3x, inside the trusted repository
    K.reseal(build)
    expected = release_sha(REPO)
    proc, _ = run_claude_p(["--plugin-dir", str(REPO), "--output-format", "json", "--model", "sonnet",
                            "--no-session-persistence", f"/klode-kit:kb-check --kb {kb}"],
                           cwd=lab, cap=CAP_USD, label="kb-check-entry", ledger=ledger, evidence=evidence)
    written = build / "check_receipt.json"
    assert written.exists(), (proc.stdout.decode()[-2000:], proc.stderr.decode()[-2000:])
    receipt = json.loads(written.read_text(encoding="utf-8"))
    assert contract.issues("check_receipt", receipt) == []
    assert receipt["plugin"]["name"] == PLUGIN["name"]
    assert receipt["plugin"]["version"] == PLUGIN["version"]
    assert receipt["plugin"]["entry"] == "skill:/klode-kit:kb-check"
    assert receipt["plugin"]["root_sha"] == expected
