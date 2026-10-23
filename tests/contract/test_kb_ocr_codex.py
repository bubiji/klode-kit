"""WI-06b: the codex route of ``kb-ocr`` runs the real codex adapter instead of the ``route_unavailable``
placeholder (matrix 06-02/06-03 page level, 06-06 batch level, 06-10 two routes; plan WI-06b; design 02 r2.3, r2.6
exit 50, r3.3).

Rules under test (``run_batch.run(..., cloud=CloudOptions(...))``; no replay seam) - the claude route's rules
(test_kb_ocr_claude.py) hold for codex too:

- only WI-05a masked images are sent; rows are ``ocr_line`` with ``engine: codex`` bound to the masked image;
- one budget ledger per batch shared by claude and codex: once a reservation is refused no cloud call follows on
  any route, unsent pages are ``route_failed`` (``budget_exceeded``), kb-ocr exits 50;
- ``parse_error`` is a page state of its own; a closed G2 gate makes the route ``route_failed`` with zero calls;
- a project that lowers ``codex.effort`` below the lock ``max`` -> the codex route is ``route_failed``
  (``effort_locked: ...``) with zero calls, and the page's fix action says how to restore it.

Boundaries: ``FakeCloud`` (one fake serving both CLIs, ``stdout_by_tool``) and ``ReplayVision`` (masking).
Paired real path: 06-12 (model lane).
"""

from __future__ import annotations

import tempfile
from decimal import Decimal
from pathlib import Path

import pytest

from _support.kb_ocr import jsonl, only_batch, receipt
from _support.send_fake import FakeCloud, isolation_receipt
from klode_kit.contract import schema
from klode_kit.ocr import run_batch
from contract.test_adapter_codex import MALFORMED, codex_stdout
from contract.test_kb_ocr_claude import IMAGES, KbRig, result_stdout

pytestmark = pytest.mark.contract_replay

LINES = ["示例医院 检验报告单", "白细胞计数 6.5"]


class CodexKbRig(KbRig):
    """KbRig with a codex isolation receipt too; the fake answers codex with an event stream (0.04 per call:
    1000 input + 500 output tokens) and claude with a result object (0.04)."""

    def __init__(self, tmp: Path, **kw):
        super().__init__(tmp, **kw)
        other = FakeCloud(tmp / "fake-iso-codex")
        iso = isolation_receipt("codex", other, self.home, tmp / "iso-x", tmp / "iso-codex.json")
        self.options.isolation_receipts = {**self.options.isolation_receipts, "codex": iso}
        self.fake.set(stdout_by_tool={"codex": codex_stdout(LINES), "claude": result_stdout(LINES)})

    def run(self, inbox: Path, routes=("codex",), **kw) -> int:
        kw.setdefault("routes_enabled", {"claude": True, "codex": True})
        kw.setdefault("cloud", self.options)
        return run_batch.run(self.project, inbox, list(routes), **kw)

    def calls_of(self, tool: str) -> list[dict]:
        return [c for c in self.fake.model_calls() if c["tool"] == tool]


@pytest.fixture
def systmp(tmp_path, monkeypatch):
    d = tmp_path / "systmp"
    d.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(d))
    return d


@pytest.fixture
def kb(tmp_path, systmp) -> CodexKbRig:
    return CodexKbRig(tmp_path)


def test_codex_route_sends_masked_images(kb):
    """S1/S3: the codex route is no longer a placeholder - it sends each page's masked image (never the original),
    rows are ocr_line (engine codex, bbox/tokens null) bound to the masked der_sha256; route done, cloud block
    counts 2 calls at the estimated 0.04 each."""
    inbox = kb.inbox(IMAGES[:2])
    masked = kb.masked(inbox)
    assert kb.fake.calls() == []
    assert kb.run(inbox) == 0
    rec = receipt(kb.project)
    sent = {sha for call in kb.calls_of("codex") for sha in call["pngs"].values()}
    assert sent == {r["der_sha256"] for r in masked.values()}
    assert not sent & {p["der_sha256"] for p in rec["pages"]}
    batch = only_batch(kb.project)
    for page in rec["pages"]:
        route = page["routes"]["codex"]
        assert route["status"] == "done", route
        rows = jsonl(batch / route["file"])
        assert [r["text"] for r in rows] == LINES
        for idx, row in enumerate(rows):
            schema.validate("ocr_line", row)
            assert (row["engine"], row["line_idx"], row["bbox_px"], row["tokens"]) == ("codex", idx, None, None)
            assert row["der_sha256"] == masked[page["page_id"]]["der_sha256"]
    assert rec["cloud"] == {"limit_usd": 5.0, "spent_usd": 0.08, "calls": 2, "stop": None}
    assert rec["issues"] == []
    assert sorted(p.name.split("-")[1] for p in (batch / "receipts" / "cloud").glob("read-*.json")) == ["codex"] * 2


def test_codex_unmasked_zero_calls(kb):
    inbox = kb.inbox(IMAGES[:1])
    assert kb.run(inbox) == 0
    route = receipt(kb.project)["pages"][0]["routes"]["codex"]
    assert route["status"] == "route_failed"
    assert route["error"].startswith("route_unavailable")
    assert "WI-06b" not in route["error"]
    assert kb.fake.calls() == []


def test_codex_budget_stop_exit50_batch(kb):
    """06-06 (S3) at batch level on codex: five masked pages, limit 0.10, 0.04 per call -> exit 50, exactly 3
    calls, pages 4-5 route_failed budget_exceeded, cloud block spent 0.12 stop budget."""
    kb.limit(0.10)
    inbox = kb.inbox(IMAGES)
    kb.masked(inbox)
    assert kb.run(inbox) == 50
    assert len(kb.calls_of("codex")) == 3
    rec = receipt(kb.project)
    assert [p["routes"]["codex"]["status"] for p in rec["pages"]] == ["done"] * 3 + ["route_failed"] * 2
    for page in rec["pages"][3:]:
        assert page["routes"]["codex"]["error"].startswith("budget_exceeded")
    assert rec["cloud"]["stop"] == "budget"
    assert Decimal(str(rec["cloud"]["spent_usd"])) == Decimal("0.12")


def test_claude_and_codex_share_one_ledger(kb):
    """06-10 at batch level: routes claude then codex, limit 0.10, 0.04 per call on both -> claude spends 0.08 on
    two pages, codex gets one call (0.12 > 0.10), then the batch stops: exit 50; 3 calls in total; the codex
    page that was not sent is route_failed budget_exceeded."""
    kb.limit(0.10)
    inbox = kb.inbox(IMAGES[:2])
    kb.masked(inbox)
    assert kb.run(inbox, routes=("claude", "codex")) == 50
    assert (len(kb.calls_of("claude")), len(kb.calls_of("codex"))) == (2, 1)
    rec = receipt(kb.project)
    assert [p["routes"]["claude"]["status"] for p in rec["pages"]] == ["done", "done"]
    assert [p["routes"]["codex"]["status"] for p in rec["pages"]] == ["done", "route_failed"]
    assert rec["pages"][1]["routes"]["codex"]["error"].startswith("budget_exceeded")
    assert rec["cloud"]["calls"] == 3
    assert Decimal(str(rec["cloud"]["spent_usd"])) == Decimal("0.12")


def test_codex_parse_error_is_a_page_state(kb):
    kb.fake.set(stdout_by_tool={"codex": MALFORMED})
    inbox = kb.inbox(IMAGES[:1])
    kb.masked(inbox)
    assert kb.run(inbox) == 0
    page = receipt(kb.project)["pages"][0]
    assert page["routes"]["codex"]["status"] == "parse_error"
    assert [u["reason"] for u in page["uncertain"]] == ["parse_error"]
    assert len(kb.calls_of("codex")) == 2


def test_codex_timeout_route_failed(kb):
    """06-03 at batch level: codex outlives the timeout -> route_failed after 1 + route_retry calls."""
    kb.fake.set(sleep_s=5)
    kb.options.timeout_s = 1
    (kb.project / "klode-kit.toml").write_text("route_retry = 1\n", encoding="utf-8")
    inbox = kb.inbox(IMAGES[:1])
    kb.masked(inbox)
    assert kb.run(inbox) == 0
    page = receipt(kb.project)["pages"][0]
    assert page["routes"]["codex"]["status"] == "route_failed"
    assert page["routes"]["codex"]["tries"] == 2
    assert len(kb.calls_of("codex")) == 2


def test_codex_gate_closed_route_failed_zero_calls(tmp_path, systmp, monkeypatch):
    kb = CodexKbRig(tmp_path, pseudo_real=True)
    monkeypatch.setenv("KLODE_KIT_REAL", "0")
    inbox = kb.inbox(IMAGES[:2])
    kb.masked(inbox)
    assert kb.run(inbox) == 0
    for page in receipt(kb.project)["pages"]:
        route = page["routes"]["codex"]
        assert route["status"] == "route_failed"
        assert "gate_closed" in route["error"]
    assert kb.fake.calls() == []


def test_codex_effort_lock_route_failed_zero_calls(kb):
    """S3: klode-kit.toml lowers codex.effort -> the codex route is route_failed 'effort_locked' for every page,
    zero processes started, exit 0; the fix action names the setting; claude is unaffected."""
    inbox = kb.inbox(IMAGES[:1])
    kb.masked(inbox)
    (kb.project / "klode-kit.toml").write_text('[codex]\neffort = "high"\n', encoding="utf-8")
    assert kb.run(inbox, routes=("claude", "codex")) == 0
    page = receipt(kb.project)["pages"][0]
    assert page["routes"]["codex"]["status"] == "route_failed"
    assert page["routes"]["codex"]["error"].startswith("effort_locked")
    (unc,) = [u for u in page["uncertain"] if u["engine"] == "codex"]
    assert "codex.effort" in unc["fix_action"]
    assert kb.calls_of("codex") == []
    assert page["routes"]["claude"]["status"] == "done"
