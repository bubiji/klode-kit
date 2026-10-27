"""WI-31: matrix 06-11 / 06-12 (gate G2) with the one setup line their originals lack.

``tests/model/test_adapter_claude_live.py`` and ``test_adapter_codex_live.py`` (WI-06a / 06b, written, never run) call
``read_page(...)`` without ``source_env``. In a test session ``tests/fakes/bin`` is first on ``PATH`` (conftest), the
sender then reviews the *fake* ``claude`` / ``codex`` and refuses before any call - ``isolation_review_failed:
cli:version`` (observed 2026-10-27, no model call, no cost). The fix is one argument, ``source_env=A.real_env()`` (the
process environment without the fakes), and a recorded test line may only be changed with the user's approval - so
the originals are untouched (their failure is reported) and this file is the same two cases with that argument.
Everything else is the originals' body, assertion for assertion.

Lane model_integration, ``gate_cloud``; one Claude call and one Codex call, ledger limit $2.00 each (reservation $1.00).
"""

from __future__ import annotations

import json
import os
from decimal import Decimal

import pytest

from _support import accept_fx as A
from _support.flows import REPO
from _support.host import Ledger as HostLedger
from _support.host import evidence_dir
from _support.mask_replay import make_batch
from klode_kit.audit import mask
from klode_kit.contract import schema
from klode_kit.ocr import adapters_claude as AC
from klode_kit.ocr import adapters_codex as AX
from klode_kit.ocr.cloud_budget import Ledger

pytestmark = [pytest.mark.model_integration, pytest.mark.gate_cloud]

FIXTURES = REPO / "tests" / "fixtures"
RECEIPTS = REPO / "docs" / "spikes" / "receipts"
CLAUDE_RECEIPT = RECEIPTS / "claude-isolation_receipt-20261022T224341Z.json"
CODEX_RECEIPT = RECEIPTS / "codex-isolation_receipt-20261022T234046Z.json"
CLAUDE_RECORDING = REPO / "tests" / "recordings" / "claude" / \
    "3e68e97d5936c3c9df501b6009171de839302556c1ad08fe5200834240d46f80.json"
CODEX_RECORDING = REPO / "tests" / "recordings" / "codex" / \
    "3e68e97d5936c3c9df501b6009171de839302556c1ad08fe5200834240d46f80.effort-max.json"


def _shape(obj):
    if isinstance(obj, dict):
        return {k: _shape(v) for k, v in sorted(obj.items())}
    if isinstance(obj, list):
        return sorted({json.dumps(_shape(v), sort_keys=True) for v in obj})
    return type(obj).__name__


def _masked_page(tmp_path):
    project = tmp_path / "synthetic-project"
    project.mkdir()
    batch = make_batch(project, [FIXTURES / "fx-labs-1x" / "images" / "p001.png"])
    (row,) = mask.mask_batch(project, batch)  # real Apple Vision
    return project, row, project / "_masked" / row["path"]


def test_live_vs_replay_shape_claude(tmp_path):
    assert os.environ.get("KLODE_KIT_REAL") == "1"
    project, row, image = _masked_page(tmp_path)
    ledger = Ledger(2.0, reserve_usd=Decimal("1.0"))
    live = AC.read_page(image, row["der_sha256"], page_id=row["page_id"], project=project,
                        receipt_dir=tmp_path / "receipts", isolation_receipt=CLAUDE_RECEIPT, ledger=ledger,
                        source_env=A.real_env())
    HostLedger().book("g2-claude-live", 1.0, live.receipt.get("cost_usd"))
    (evidence_dir("g2-claude-live") / "receipt.json").write_text(json.dumps(live.receipt, indent=1, default=str))
    assert live.rows, "the live call returned no lines"
    for r in live.rows:
        schema.validate("ocr_line", r)
    assert live.receipt["status"] == "done"
    assert live.receipt["cost_usd"] > 0
    assert live.receipt["model_actual"]

    rec = json.loads(CLAUDE_RECORDING.read_text(encoding="utf-8"))
    parsed = AC.parse_result(rec["stdout"])
    replay_rows = AC.to_rows(parsed.lines, page_id=row["page_id"], der_sha256=row["der_sha256"])
    assert _shape(live.rows) == _shape(replay_rows)
    assert {k for k in live.receipt} >= {"model", "effort", "model_actual", "cost_usd", "attempts", "status"}


def test_live_vs_replay_shape_codex(tmp_path):
    assert os.environ.get("KLODE_KIT_REAL") == "1"
    project, row, image = _masked_page(tmp_path)
    ledger = Ledger(2.0, reserve_usd=Decimal("1.0"))
    live = AX.read_page(image, row["der_sha256"], page_id=row["page_id"], project=project,
                        receipt_dir=tmp_path / "receipts", isolation_receipt=CODEX_RECEIPT, ledger=ledger,
                        source_env=A.real_env())
    HostLedger().book("g2-codex-live", 1.0, live.receipt.get("cost_usd"))
    (evidence_dir("g2-codex-live") / "receipt.json").write_text(json.dumps(live.receipt, indent=1, default=str))
    assert live.rows, "the live call returned no lines"
    for r in live.rows:
        schema.validate("ocr_line", r)
    assert live.receipt["status"] == "done"
    assert live.receipt["effort"] == "max"
    assert live.receipt["tokens"].get("input_tokens", 0) > 0
    assert live.receipt["cost_usd"] > 0

    rec = json.loads(CODEX_RECORDING.read_text(encoding="utf-8"))
    parsed = AX.parse_events(rec["stdout"])
    replay_rows = AX.to_rows(parsed.lines, page_id=row["page_id"], der_sha256=row["der_sha256"])
    assert _shape(live.rows) == _shape(replay_rows)
    assert {k for k in live.receipt} >= {"model", "effort", "model_actual", "cost_usd", "tokens", "attempts",
                                         "status"}
