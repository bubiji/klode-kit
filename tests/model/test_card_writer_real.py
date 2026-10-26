"""WI-12 matrix 12-04 (gate G2): the shipped ``agents/klode-card-writer.md`` writes one real card; gate 1 passes.

The paired real path of the replayed bridge cases (tests/contract/test_bridge_run.py) and of the static agent checks
(tests/unit/test_agents_static.py): the production bridge argv, the shipped agent definition (not a test double) and the
task instructions the build really sends (``prompts/write_card.md`` + the card id / source), on the synthetic handbook
``fx-book-aurora`` (registered in tests/fixtures/MANIFEST.json), chapter 1. One writer call, cap $3.00 per attempt, one
attempt, ledger limit $10 (``_support.host.Ledger``).

NOT RUN in WI-12 (the manual gate): without ``KLODE_KIT_REAL=1`` and ``KLODE_KIT_GATE_ACK=G2-<yyyymmdd>`` the session
stops with a usage error (exit 2) before any test runs. Run when authorised::

    KLODE_KIT_REAL=1 KLODE_KIT_GATE_ACK=G2-<yyyymmdd> .venv/bin/python -m pytest -m model_integration \
        tests/model/test_card_writer_real.py
"""

from __future__ import annotations

import os
from importlib import resources

import pytest

from _support.flows import REPO
from _support.host import Ledger, evidence_dir, lab_dir, real_claude
from klode_kit import bridge
from klode_kit.config import load
from klode_kit.contract import schema as contract
from klode_kit.devtools import spike_check_card

pytestmark = [pytest.mark.model_integration, pytest.mark.gate_cloud]

AURORA = (REPO / "tests" / "fixtures" / "fx-book-aurora" / "aurora-kettle.txt").read_text(encoding="utf-8")
CH1 = AURORA[AURORA.index("Chapter 1:"):AURORA.index("Chapter 2:")].strip() + "\n"
BUILD_ID = "b-20261026T000000Z-0123abcd"
CAP_USD = 3.0


def test_real_writer_card_check_pass():
    """12-04 (L3, S1): the shipped writer, driven by the production bridge, writes ``cards_content/ch01.md`` and
    ``check-cards`` (gate 1) passes; the writer receipt is contract-valid."""
    if os.environ.get("KLODE_KIT_REAL") != "1":
        pytest.fail("model_integration needs KLODE_KIT_REAL=1 (missing dependency is a FAIL, never a skip)")
    ledger = Ledger()
    ledger.reserve("12-04", CAP_USD)
    lab = lab_dir("card-writer-real")
    evidence = evidence_dir("12-04")
    build = lab / "builds" / BUILD_ID
    (build / "library" / "book").mkdir(parents=True)
    (build / "cards_content").mkdir()
    (build / "library" / "book" / "ch01.txt").write_text(CH1, encoding="utf-8")
    guide = (resources.files("klode_kit") / "prompts" / "write_card.md").read_text(encoding="utf-8")
    task = bridge.Task(task_id="ch01", build_dir=build, card="cards_content/ch01.md", instructions=(
        f"{guide}\n\nCard id: ch01\nSource text: library/book/ch01.txt\n"
        "Write cards_content/ch01.md (aliases line, ## Thin, ## Full)."))
    options = bridge.Options.from_config(load(), claude=real_claude(), concurrency=1, max_rounds=1,
                                         max_budget_usd=CAP_USD, agents_dir=REPO / "agents")
    receipt = bridge.run([task], options, receipt_path=evidence / "bridge_receipt.json")
    ledger.book("12-04", CAP_USD, receipt["cost_usd"] if receipt["cost_complete"] else None)
    rec = receipt["tasks"][0]
    if (build / task.card).is_file():
        (evidence / "card.md").write_bytes((build / task.card).read_bytes())
    assert rec["status"] == "done", rec
    assert spike_check_card.check(build, task.card) == []
    assert contract.issues("writer_receipt", rec["writer"]) == []
    assert not [d for d in rec["permission_denials"]], "the writer should not have tried anything the rules deny"
