"""WI-06a edges, added after the first green (coverage of paths the matrix rows do not name):

- ``parse_result`` fallbacks: a JSON value that is not an object; the ``result`` text as JSON when there is no
  ``structured_output``; neither -> parse_error after the one retry;
- an interrupt while the CLI runs: the reservation is charged (the call may have cost money), the interrupt
  propagates;
- kb-ocr: a tampered masked image -> the sender refuses (60) -> kb-ocr exit 60, cloud block stop 'privacy', zero
  calls; a corrupt ``_masked/MANIFEST.jsonl`` registers nothing -> zero calls; ``--isolation-receipt ROUTE=PATH``
  is honoured (and malformed -> exit 2); a killed run resumes without paying again for the finished page
  (cloud rows name the masked image, S5).

Boundaries as in test_adapter_claude.py / test_kb_ocr_claude.py (FakeCloud, ReplayVision).
"""

from __future__ import annotations

import _thread
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from decimal import Decimal
from pathlib import Path

import pytest

from _support.flows import base_env
from _support.kb_ocr import FIXTURES, jsonl, only_batch, receipt
from _support.send_fake import FIXTURE_PNG, sha256
from klode_kit.ocr import adapters_claude as AC
from klode_kit.ocr import run_batch
from klode_kit.ocr.cloud_budget import Ledger
from contract.test_adapter_claude import LINES as A_LINES
from contract.test_adapter_claude import Rig, read_receipts, result_stdout
from contract.test_kb_ocr_claude import IMAGES, LINES, KbRig

pytestmark = pytest.mark.contract_replay


@pytest.fixture
def systmp(tmp_path, monkeypatch):
    d = tmp_path / "systmp"
    d.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(d))
    return d


@pytest.fixture
def rig(tmp_path, systmp) -> Rig:
    return Rig(tmp_path)


@pytest.fixture
def kb(tmp_path, systmp) -> KbRig:
    return KbRig(tmp_path)


def _result_text_stdout(text: str) -> str:
    return json.dumps({"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.02,
                       "result": text}, ensure_ascii=False) + "\n"


# --------------------------------------------------------------------------- parse_result


def test_result_text_fallback_accepted(rig):
    """No structured_output, but the result text is schema-valid JSON -> rows from it."""
    text = json.dumps({"lines": [{"text": t, "uncertain": False} for t in A_LINES]}, ensure_ascii=False)
    rig.fake.set(stdout=_result_text_stdout(text))
    out = rig.read(Ledger(1.0))
    assert [r["text"] for r in out.rows] == A_LINES
    assert len(rig.fake.model_calls()) == 1


@pytest.mark.parametrize("bad", ['[{"text": "x", "uncertain": false}]\n', _result_text_stdout("not json at all"),
                                 json.dumps({"type": "result", "is_error": False}) + "\n"],
                         ids=["json-array", "result-not-json", "no-output-at-all"])
def test_unusable_output_parse_error_after_retry(rig, bad):
    rig.fake.set(stdout=bad)
    with pytest.raises(AC.ParseFailed):  # no cost reported: each attempt is charged its 1.0 reservation
        rig.read(Ledger(5.0))
    assert len(rig.fake.model_calls()) == 2
    assert read_receipts(rig.receipts)[0]["status"] == "parse_error"


def test_parse_result_reports_problem_and_cost():
    parsed = AC.parse_result(json.dumps({"total_cost_usd": 0.5, "structured_output": {"lines": "x"}}))
    assert parsed.lines is None and parsed.cost_usd == 0.5 and "ocr_cloud_output" in parsed.problem
    assert AC.parse_result("[]").problem == "stdout is not a JSON object"


# --------------------------------------------------------------------------- interrupt


def test_interrupt_during_call_charges_reservation(rig):
    """S3: KeyboardInterrupt while the CLI runs -> it propagates; the reservation is charged (cost unknown)."""
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


# --------------------------------------------------------------------------- kb-ocr edges


def test_tampered_masked_image_exit60_zero_calls(kb):
    """S3: the masked file changed after masking -> the sender refuses (privacy, 60) before any call; kb-ocr
    exits 60, pages route_failed 'privacy_refused', cloud stop 'privacy'."""
    inbox = kb.inbox(IMAGES[:2])
    masked = kb.masked(inbox)
    first = sorted(masked.values(), key=lambda r: r["page_id"])[0]
    target = kb.project / "_masked" / first["path"]
    target.write_bytes(target.read_bytes() + b"tampered")
    assert kb.run(inbox) == 60
    rec = receipt(kb.project)
    for page in rec["pages"]:
        assert page["routes"]["claude"]["status"] == "route_failed"
        assert page["routes"]["claude"]["error"].startswith("privacy_refused")
    assert rec["cloud"]["stop"] == "privacy"
    assert kb.fake.calls() == []


def test_corrupt_mask_manifest_registers_nothing(kb):
    inbox = kb.inbox(IMAGES[:1])
    kb.masked(inbox)
    (kb.project / "_masked" / "MANIFEST.jsonl").write_text("{not json\n", encoding="utf-8")
    assert kb.run(inbox) == 0
    route = receipt(kb.project)["pages"][0]["routes"]["claude"]
    assert route["status"] == "route_failed" and route["error"].startswith("route_unavailable")
    assert kb.fake.calls() == []


def test_cli_isolation_receipt_option(kb, monkeypatch):
    """``--isolation-receipt claude=PATH`` is what the sender checks: a valid one -> the page is read; a missing
    one -> isolation_refused (zero model calls); a malformed option -> exit 2."""
    inbox = kb.inbox(IMAGES[:1])
    kb.masked(inbox)
    monkeypatch.setenv("PATH", kb.env["PATH"])
    base = ["--batch", str(inbox), "--project", str(kb.project), "--routes", "claude"]
    assert run_batch.main([*base, "--isolation-receipt", "claude"]) == 2
    assert run_batch.main([*base, "--isolation-receipt", "vision=x"]) == 2
    missing = kb.tmp / "no-such-receipt.json"
    assert run_batch.main([*base, "--isolation-receipt", f"claude={missing}"], routes_enabled={"claude": True}) == 0
    route = receipt(kb.project)["pages"][0]["routes"]["claude"]
    assert route["status"] == "route_failed" and "isolation_refused" in route["error"]
    assert kb.fake.model_calls() == []
    assert run_batch.main([*base, "--isolation-receipt", f"claude={kb.iso}"], routes_enabled={"claude": True}) == 0
    assert receipt(kb.project)["pages"][0]["routes"]["claude"]["status"] == "done"
    assert len(kb.fake.model_calls()) == 1


def test_killed_run_resumes_without_paying_twice(kb):
    """S5: kb-ocr killed right after the first page's rows are in place -> the rerun continues the attempt; the
    finished page (rows naming the masked image) is not sent again: 2 pages, 2 model calls in total."""
    inbox = kb.inbox(IMAGES[:2])
    kb.masked(inbox)
    env = base_env({"PATH": kb.env["PATH"], "HOME": str(kb.home), "TMPDIR": str(kb.systmp), "LANG": "C.UTF-8",
                    "USER": "synthetic", "KLODE_KIT_TEST_SEAMS": "1"}, drop=("KLODE_KIT_REAL",))
    argv = [sys.executable, "-m", "klode_kit", "kb-ocr", "--batch", str(inbox), "--project", str(kb.project),
            "--routes", "claude", "--isolation-receipt", f"claude={kb.iso}"]
    killed = subprocess.run(argv, env={**env, "KLODE_KIT_CRASH_AT": "run_batch.page_done"}, capture_output=True,
                            timeout=120)
    assert killed.returncode == -9, killed.stderr.decode()
    assert len(kb.fake.model_calls()) == 1
    done = subprocess.run(argv, env=env, capture_output=True, timeout=120)
    assert done.returncode == 0, done.stderr.decode()
    assert b"continuing interrupted attempt" in done.stderr
    assert len(kb.fake.model_calls()) == 2
    rec = receipt(kb.project)
    assert [p["routes"]["claude"]["status"] for p in rec["pages"]] == ["done", "done"]
    for page in rec["pages"]:
        assert [r["text"] for r in jsonl(only_batch(kb.project) / page["routes"]["claude"]["file"])] == LINES
    assert {sha for c in kb.fake.model_calls() for sha in c["pngs"].values()} == {
        d["der_sha256"] for d in jsonl(only_batch(kb.project) / "derived.jsonl") if d["path"].startswith("_masked/")}
    assert not {sha256(p.read_bytes()) for p in IMAGES[:2]} & {
        sha for c in kb.fake.model_calls() for sha in c["pngs"].values()}
    assert os.path.exists(only_batch(kb.project) / "receipts" / "cloud_ledger.json")
    ledger = json.loads((only_batch(kb.project) / "receipts" / "cloud_ledger.json").read_text(encoding="utf-8"))
    assert ledger["calls"] == 2


def test_unused_fixture_png_is_registered():
    """Guard for the rigs above: the fixture image the adapter rig sends is synthetic and registered."""
    from klode_kit.ocr import isolation_check as IC

    assert sha256(FIXTURE_PNG.read_bytes()) in IC.fixture_shas()
    assert (FIXTURES / "fx-labs-1x" / "images" / "p001.png") == FIXTURE_PNG
