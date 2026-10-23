"""WI-06b edges, added after the first green (paths the matrix rows do not name):

- an interrupt while the codex CLI runs: it propagates, the reservation is charged (cost unknown), nothing is left
  outstanding in the ledger;
- a zero-line answer after a parse failure keeps both attempts' tokens in the receipt.

Boundaries as in test_adapter_codex.py (FakeCloud).
"""

from __future__ import annotations

import _thread
import tempfile
import threading
import time
from decimal import Decimal

import pytest

from klode_kit.ocr.cloud_budget import Ledger
from contract.test_adapter_codex import MALFORMED, Rig, codex_stdout, read_receipts

pytestmark = pytest.mark.contract_replay


@pytest.fixture
def rig(tmp_path, monkeypatch) -> Rig:
    d = tmp_path / "systmp"
    d.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(d))
    return Rig(tmp_path)


def test_interrupt_during_codex_call_charges_reservation(rig):
    rig.fake.set(sleep_s=30, started=True)
    ledger = Ledger(1.0, reserve_usd=Decimal("0.4"))

    def interrupt_when_started():
        deadline = time.monotonic() + 30
        while not rig.fake.started() and time.monotonic() < deadline:
            time.sleep(0.05)
        _thread.interrupt_main()

    threading.Thread(target=interrupt_when_started, daemon=True).start()
    with pytest.raises(KeyboardInterrupt):
        rig.read(ledger, timeout_s=60)
    assert ledger.spent_usd == Decimal("0.4")
    assert ledger.outstanding_usd == 0


def test_parse_failure_then_empty_page_tokens_summed(rig):
    rig.fake.set(stdout_seq=[MALFORMED, codex_stdout([], input_tokens=300, output_tokens=0)])
    out = rig.read(Ledger(1.0))
    assert out.rows == []
    (rec,) = read_receipts(rig.receipts)
    assert rec["status"] == "done"
    assert rec["tokens"]["input_tokens"] == 1300
    assert [a["tokens"]["input_tokens"] for a in rec["attempts"]] == [1000, 300]
