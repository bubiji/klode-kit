"""WI-06a: the claude route of ``kb-ocr`` (WI-04b) runs the real adapter instead of the route_unavailable
placeholder (matrix 06-03 page level, 06-06/06-07 batch level; plan WI-06a; design 02 r2.3, r2.6 exit 50, r3.3).

Rules under test (``run_batch.run(..., cloud=CloudOptions(...))``; no replay seam):

- the cloud route sends only WI-05a masked images: for each page the ``_masked/`` line of
  ``work/<batch>/derived.jsonl`` (registered by ``mask.mask_batch``); a page without one is ``route_failed``
  (``route_unavailable: ...``) with zero calls - the upright or original image is never sent;
- rows are ``ocr_line`` with ``der_sha256`` = the masked image (its derived line traces it to the page);
- per batch one budget ledger (``cloud.max_cost_usd_per_batch``), persisted in the batch so reruns keep counting:
  once a reservation is refused the batch stops sending, the unsent pages are ``route_failed``
  (``budget_exceeded``), the batch receipt's ``cloud`` block records limit / spent / calls / stop, and kb-ocr
  exits 50;
- ``parse_error`` (two malformed answers) is a page state of its own, not retried again by ``route_retry``;
- a closed G2 gate / failed isolation (send exit 61) makes the route ``route_failed`` with zero model calls.

Boundaries: ``FakeCloud`` (cloud CLI) and ``ReplayVision`` (WI-05a masking). Paired real path: 06-11 (model).
"""

from __future__ import annotations

import json
import tempfile
from decimal import Decimal
from pathlib import Path

import pytest

from _support.kb_ocr import FIXTURES, jsonl, make_inbox, only_batch, receipt
from _support.mask_replay import ReplayVision
from _support.send_fake import FakeCloud, isolation_receipt, routes_state, sha256, source_env
from klode_kit.audit import mask
from klode_kit.contract import schema
from klode_kit.ocr import run_batch

pytestmark = pytest.mark.contract_replay

ENABLED = {"claude": True, "codex": True}


def result_stdout(lines, cost=0.04, model="claude-opus-5-5") -> str:
    return json.dumps({"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": cost,
                       "modelUsage": {model: {"costUSD": cost}},
                       "structured_output": {"lines": [{"text": t, "uncertain": False} for t in lines]}},
                      ensure_ascii=False) + "\n"


MALFORMED = json.dumps({"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.01,
                        "structured_output": {"rows": []}}) + "\n"
LINES = ["示例医院 检验报告单", "白细胞计数 6.5"]
IMAGES = [FIXTURES / "fx-u1-3labs" / "images" / f"p00{i}.png" for i in (1, 2, 3)] + [
    FIXTURES / "fx-rx-stamp" / "images" / "p001.png", FIXTURES / "fx-lowres" / "images" / "p001.png"]


class KbRig:
    def __init__(self, tmp: Path, *, pseudo_real: bool = False, **state):
        self.tmp = tmp
        self.fake = FakeCloud(tmp / "fake", pseudo_real=pseudo_real, stdout=result_stdout(LINES), **state)
        self.home = tmp / "home"
        self.home.mkdir(exist_ok=True)
        self.systmp = tmp / "systmp"
        self.project = tmp / "数据项目"
        self.project.mkdir(exist_ok=True)
        self.env = source_env(self.fake, self.home, self.systmp)
        other = FakeCloud(tmp / "fake-iso")
        self.iso = isolation_receipt("claude", other, self.home, tmp / "iso", tmp / "iso-claude.json")
        self.options = run_batch.CloudOptions(source_env=self.env, isolation_receipts={"claude": self.iso},
                                              routes_state=routes_state(tmp / "routes.json"))

    def limit(self, usd: float) -> None:
        (self.project / "klode-kit.toml").write_text(f"[cloud]\nmax_cost_usd_per_batch = {usd}\n", encoding="utf-8")

    def inbox(self, images) -> Path:
        return make_inbox(self.project / "_inbox", images, [f"{i:03d}_{p.parent.parent.name}.png"
                                                           for i, p in enumerate(images)])

    def run(self, inbox: Path, **kw) -> int:
        kw.setdefault("routes_enabled", ENABLED)
        kw.setdefault("cloud", self.options)
        return run_batch.run(self.project, inbox, ["claude"], **kw)

    def masked(self, inbox: Path) -> dict[str, dict]:
        """First run (derived.jsonl, no masks yet), then WI-05a masking; returns page_id -> MANIFEST row."""
        assert self.run(inbox) == 0
        rows = mask.mask_batch(self.project, only_batch(self.project).name, engine=ReplayVision())
        return {r["page_id"]: r for r in rows}


@pytest.fixture
def systmp(tmp_path, monkeypatch):
    d = tmp_path / "systmp"
    d.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(d))
    return d


@pytest.fixture
def kb(tmp_path, systmp) -> KbRig:
    return KbRig(tmp_path)


def test_sends_only_masked_images(kb):
    """S1/S3: after masking, the claude route sends each page's masked image - never the upright/original one;
    rows are ocr_line bound to the masked der_sha256, whose derived line names the page; the route is done and
    the batch receipt's cloud block counts the calls and the spend."""
    inbox = kb.inbox(IMAGES[:2])
    masked = kb.masked(inbox)
    assert kb.fake.calls() == []  # the unmasked first run sent nothing
    assert kb.run(inbox) == 0
    rec = receipt(kb.project)
    sent = {sha for call in kb.fake.model_calls() for sha in call["pngs"].values()}
    assert sent == {r["der_sha256"] for r in masked.values()}
    assert not sent & {p["der_sha256"] for p in rec["pages"]}
    assert not sent & {sha256(p.read_bytes()) for p in IMAGES[:2]}
    batch = only_batch(kb.project)
    derived = {d["der_sha256"]: d for d in jsonl(batch / "derived.jsonl")}
    for page in rec["pages"]:
        route = page["routes"]["claude"]
        assert route["status"] == "done", route
        rows = jsonl(batch / route["file"])
        assert [r["text"] for r in rows] == LINES
        for idx, row in enumerate(rows):
            schema.validate("ocr_line", row)
            assert (row["engine"], row["line_idx"], row["bbox_px"], row["tokens"]) == ("claude", idx, None, None)
            assert row["page_id"] == page["page_id"]
            assert row["der_sha256"] == masked[page["page_id"]]["der_sha256"]
            assert derived[row["der_sha256"]]["path"].startswith("_masked/")
            assert derived[row["der_sha256"]]["parent"] == page["img_sha256"]
    assert rec["cloud"] == {"limit_usd": 5.0, "spent_usd": 0.08, "calls": 2, "stop": None}
    assert rec["issues"] == []


def test_unmasked_pages_zero_calls(kb):
    """S3: no masked image for the batch -> route_failed 'route_unavailable', no tries, no call at all (not even
    --version), the page is uncertain with a fix naming the masker."""
    inbox = kb.inbox(IMAGES[:1])
    assert kb.run(inbox) == 0
    page = receipt(kb.project)["pages"][0]
    route = page["routes"]["claude"]
    assert route["status"] == "route_failed"
    assert route["error"].startswith("route_unavailable")
    assert "tries" not in route
    assert kb.fake.calls() == []
    (unc,) = page["uncertain"]
    assert unc["reason"] == "route_failed" and "mask" in unc["fix_action"]


def test_one_unmasked_page_not_sent(kb):
    """S3, per page: a page whose masked line is gone from derived.jsonl is route_failed with zero calls for it,
    while the masked page is sent."""
    inbox = kb.inbox(IMAGES[:2])
    masked = kb.masked(inbox)
    batch = only_batch(kb.project)
    lines = jsonl(batch / "derived.jsonl")
    drop = sorted(masked)[1]
    kept = [d for d in lines if not (d["path"].startswith("_masked/") and d["page_id"] == drop)]
    (batch / "derived.jsonl").write_text("".join(json.dumps(d, ensure_ascii=False) + "\n" for d in kept),
                                         encoding="utf-8")
    assert kb.run(inbox) == 0
    pages = {p["page_id"]: p for p in receipt(kb.project)["pages"]}
    assert pages[drop]["routes"]["claude"]["status"] == "route_failed"
    assert pages[drop]["routes"]["claude"]["error"].startswith("route_unavailable")
    other = next(pid for pid in masked if pid != drop)
    assert pages[other]["routes"]["claude"]["status"] == "done"
    sent = [sha for call in kb.fake.model_calls() for sha in call["pngs"].values()]
    assert sent == [masked[other]["der_sha256"]]


def test_budget_stop_exit50_batch(kb):
    """06-06 (S3) at batch level: five masked pages, limit 0.10, 0.04 per call -> kb-ocr exit 50, exactly 3
    calls; pages 4-5 route_failed 'budget_exceeded' (listed as issues); receipt cloud block: spent 0.12, stop
    budget. A rerun of the same batch keeps the spend (persisted ledger): exit 50 again, zero new calls."""
    kb.limit(0.10)
    inbox = kb.inbox(IMAGES)
    kb.masked(inbox)
    assert kb.run(inbox) == 50
    assert len(kb.fake.model_calls()) == 3
    rec = receipt(kb.project)
    states = [p["routes"]["claude"]["status"] for p in rec["pages"]]
    assert states == ["done", "done", "done", "route_failed", "route_failed"]
    for page in rec["pages"][3:]:
        assert page["routes"]["claude"]["error"].startswith("budget_exceeded")
        assert f"route_failed:claude:{page['key']}" in rec["issues"]
    assert rec["cloud"]["stop"] == "budget"
    assert Decimal(str(rec["cloud"]["spent_usd"])) == Decimal("0.12")
    assert rec["cloud"]["calls"] == 3

    assert kb.run(inbox) == 50
    assert len(kb.fake.model_calls()) == 3


def test_budget_boundary_exact_batch(kb):
    """06-07 (S2) at batch level: two pages of 0.05 against 0.10 -> spend exactly the limit, exit 0, both done."""
    kb.limit(0.10)
    kb.fake.set(stdout=result_stdout(LINES, cost=0.05))
    inbox = kb.inbox(IMAGES[:2])
    kb.masked(inbox)
    assert kb.run(inbox) == 0
    rec = receipt(kb.project)
    assert [p["routes"]["claude"]["status"] for p in rec["pages"]] == ["done", "done"]
    assert rec["cloud"]["spent_usd"] == pytest.approx(0.10)
    assert rec["cloud"]["stop"] is None


def test_parse_error_is_a_page_state(kb):
    """06-04 at batch level: always malformed -> page state parse_error (uncertain reason parse_error), two calls
    for the page - the adapter's one retry, not multiplied by route_retry."""
    kb.fake.set(stdout=MALFORMED)
    inbox = kb.inbox(IMAGES[:1])
    kb.masked(inbox)
    assert kb.run(inbox) == 0
    page = receipt(kb.project)["pages"][0]
    assert page["routes"]["claude"]["status"] == "parse_error"
    assert [u["reason"] for u in page["uncertain"]] == ["parse_error"]
    assert len(kb.fake.model_calls()) == 2


def test_timeout_is_route_failed_page_uncertain(kb):
    """06-03 at batch level: the CLI outlives cloud.timeout_s -> route_failed after 1 + route_retry calls; the page
    is uncertain (route_failed)."""
    kb.fake.set(sleep_s=5)
    kb.options.timeout_s = 1
    (kb.project / "klode-kit.toml").write_text("route_retry = 1\n", encoding="utf-8")
    inbox = kb.inbox(IMAGES[:1])
    kb.masked(inbox)
    assert kb.run(inbox) == 0
    page = receipt(kb.project)["pages"][0]
    assert page["routes"]["claude"]["status"] == "route_failed"
    assert page["routes"]["claude"]["tries"] == 2
    assert [u["reason"] for u in page["uncertain"]] == ["route_failed"]
    assert len(kb.fake.model_calls()) == 2


def test_gate_closed_route_failed_zero_calls(tmp_path, systmp, monkeypatch):
    """S3: a non-fake CLI with the G2 gate closed -> the route is route_failed (isolation refusal) for every page,
    zero calls of that CLI, kb-ocr still exits 0 (a route problem is recorded, not fatal)."""
    kb = KbRig(tmp_path, pseudo_real=True)
    monkeypatch.setenv("KLODE_KIT_REAL", "0")
    inbox = kb.inbox(IMAGES[:2])
    kb.masked(inbox)
    assert kb.run(inbox) == 0
    for page in receipt(kb.project)["pages"]:
        route = page["routes"]["claude"]
        assert route["status"] == "route_failed"
        assert "gate_closed" in route["error"]
    assert kb.fake.calls() == []


def test_done_attempt_reused_without_calls(kb):
    """State: a finished claude attempt is reused on rerun (hashes match) - no second call, same rows."""
    inbox = kb.inbox(IMAGES[:1])
    kb.masked(inbox)
    assert kb.run(inbox) == 0
    first = receipt(kb.project)["pages"][0]["routes"]["claude"]
    assert kb.run(inbox) == 0
    assert receipt(kb.project)["pages"][0]["routes"]["claude"] == first
    assert len(kb.fake.model_calls()) == 1
