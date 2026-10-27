"""WI-06b matrix 06-12 (gate G2): the Codex adapter against the real ``codex`` CLI - the paired real path of the
replayed adapter cases (tests/contract/test_adapter_codex.py, test_kb_ocr_codex.py).

Synthetic data only: ``fx-labs-1x`` p001 (registered in ``tests/fixtures/MANIFEST.json``) is masked by real Apple
Vision (WI-05a ``mask_batch``) in a tmp synthetic project, and the masked image - registered in that project's
``_masked/MANIFEST.jsonl`` - is sent through ``read_page`` -> ``send`` with the stored codex isolation receipt
(production argv: permission profile, effort max). One call, ledger limit $2.00 (reservation $1.00; codex charges
its token estimate).

Assertion: the live rows and receipt have the same keys and value types as the replay of the WI-00c3 effort-max
recording through the same adapter code (``parse_events`` + ``to_rows``), rows are ``ocr_line``-valid, and the
receipt has token usage and a positive estimated cost.

Not run in WI-06b (no real cloud call authorised). Run when authorised::

    KLODE_KIT_REAL=1 KLODE_KIT_GATE_ACK=G2-<yyyymmdd> .venv/bin/python -m pytest -m model_integration \
        tests/model/test_adapter_codex_live.py
"""

from __future__ import annotations

import json
import os
from decimal import Decimal

import pytest

from _support import accept_fx as A
from _support.flows import REPO
from _support.mask_replay import make_batch
from klode_kit.audit import mask
from klode_kit.contract import schema
from klode_kit.ocr import adapters_codex as AX
from klode_kit.ocr.cloud_budget import Ledger

pytestmark = [pytest.mark.model_integration, pytest.mark.gate_cloud]

FIXTURES = REPO / "tests" / "fixtures"
RECEIPT = REPO / "docs" / "spikes" / "receipts" / "codex-isolation_receipt-20261022T234046Z.json"
RECORDING = REPO / "tests" / "recordings" / "codex" / \
    "3e68e97d5936c3c9df501b6009171de839302556c1ad08fe5200834240d46f80.effort-max.json"


def _shape(obj):
    if isinstance(obj, dict):
        return {k: _shape(v) for k, v in sorted(obj.items())}
    if isinstance(obj, list):
        return sorted({json.dumps(_shape(v), sort_keys=True) for v in obj})
    return type(obj).__name__


def test_live_vs_replay_shape_codex(tmp_path):
    assert os.environ.get("KLODE_KIT_REAL") == "1"
    project = tmp_path / "synthetic-project"
    project.mkdir()
    batch = make_batch(project, [FIXTURES / "fx-labs-1x" / "images" / "p001.png"])
    (row,) = mask.mask_batch(project, batch)  # real Apple Vision
    image = project / "_masked" / row["path"]
    ledger = Ledger(2.0, reserve_usd=Decimal("1.0"))
    live = AX.read_page(image, row["der_sha256"], page_id=row["page_id"], project=project,
                        receipt_dir=tmp_path / "receipts", isolation_receipt=RECEIPT, ledger=ledger,
                        source_env=A.real_env())
    assert live.rows, "the live call returned no lines"
    for r in live.rows:
        schema.validate("ocr_line", r)
    assert live.receipt["status"] == "done"
    assert live.receipt["effort"] == "max"
    assert live.receipt["tokens"].get("input_tokens", 0) > 0
    assert live.receipt["cost_usd"] > 0

    rec = json.loads(RECORDING.read_text(encoding="utf-8"))
    parsed = AX.parse_events(rec["stdout"])
    replay_rows = AX.to_rows(parsed.lines, page_id=row["page_id"], der_sha256=row["der_sha256"])
    assert _shape(live.rows) == _shape(replay_rows)
    assert {k for k in live.receipt} >= {"model", "effort", "model_actual", "cost_usd", "tokens", "attempts",
                                         "status"}
