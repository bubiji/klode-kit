"""WI-35b: kb-check's holdout row honours the build's model-routing acceptance (D-46). When the build was accepted
with ``--desk-routing`` (``build_inputs.json`` ``desk_routing`` = sha, ``desk_receipt.json`` ``routing.path``),
kb-check re-scores the holdout with that same file; a missing or changed file is a FAIL, never a silent fallback to
the keyword score. A build without routing keeps the keyword gate."""

from __future__ import annotations

import hashlib
import json

import pytest

from klode_kit.check import kb_check

pytestmark = pytest.mark.unit

CFG = {"desk.holdout_min": 5, "desk.holdout_pass": 0.8}


def _build(tmp_path, *, routing_sha=None, receipt_routing=None):
    b = tmp_path / "build"
    (b / "src").mkdir(parents=True)
    inputs = {} if routing_sha is None else {"desk_routing": routing_sha}
    (b / "build_inputs.json").write_text(json.dumps(inputs), encoding="utf-8")
    rec = {} if receipt_routing is None else {"routing": receipt_routing}
    (b / "desk_receipt.json").write_text(json.dumps(rec), encoding="utf-8")
    return b


def _fake_verify(seen, *, routed_hit):
    def verify(build, spec, receipt=None, holdout_min=5, pass_ratio=0.8, routing=None, **kw):
        seen["routing"] = routing
        doc = {"synthesis": {"pass": True}, "diagnostics": {"current": True},
               "holdout": {"hit": 1, "total": 12}}
        if routing is not None:
            doc["routing"] = {"hit": routed_hit, "total": 12, "pass": routed_hit >= 10}
        receipt.write_text(json.dumps(doc), encoding="utf-8")
        return 0
    return verify


@pytest.fixture
def patched(monkeypatch):
    seen = {}
    monkeypatch.setattr(kb_check.desk_build, "load", lambda build: {"dimensions": []})
    return seen, monkeypatch


def test_build_accepted_by_routing_is_rescored_with_that_file(tmp_path, patched):
    seen, mp = patched
    f = tmp_path / "routing.json"
    f.write_text('{"answers": []}', encoding="utf-8")
    sha = hashlib.sha256(f.read_bytes()).hexdigest()
    b = _build(tmp_path, routing_sha=sha, receipt_routing={"path": str(f), "sha256": sha})
    mp.setattr(kb_check.desk_build, "verify", _fake_verify(seen, routed_hit=12))
    desk, holdout = kb_check.desk_checks(b, CFG, "klode")
    assert seen["routing"] == f
    assert holdout.status == "PASS" and "12/12" in holdout.detail


def test_changed_routing_file_fails(tmp_path, patched):
    seen, mp = patched
    f = tmp_path / "routing.json"
    f.write_text('{"answers": []}', encoding="utf-8")
    b = _build(tmp_path, routing_sha="0" * 64, receipt_routing={"path": str(f), "sha256": "0" * 64})
    mp.setattr(kb_check.desk_build, "verify", _fake_verify(seen, routed_hit=12))
    _, holdout = kb_check.desk_checks(b, CFG, "klode")
    assert holdout.status == "FAIL" and "routing" in holdout.detail


def test_missing_routing_file_fails(tmp_path, patched):
    seen, mp = patched
    b = _build(tmp_path, routing_sha="a" * 64, receipt_routing={"path": str(tmp_path / "gone.json"), "sha256": "a" * 64})
    mp.setattr(kb_check.desk_build, "verify", _fake_verify(seen, routed_hit=12))
    _, holdout = kb_check.desk_checks(b, CFG, "klode")
    assert holdout.status == "FAIL" and "routing" in holdout.detail


def test_build_without_routing_keeps_keyword_gate(tmp_path, patched):
    seen, mp = patched
    b = _build(tmp_path)
    mp.setattr(kb_check.desk_build, "verify", _fake_verify(seen, routed_hit=12))
    _, holdout = kb_check.desk_checks(b, CFG, "klode")
    assert seen["routing"] is None
    assert holdout.status == "FAIL" and "1/12" in holdout.detail
