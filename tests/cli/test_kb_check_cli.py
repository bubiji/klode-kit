"""WI-10a: ``kb-check`` over a real build with the real klode 0.5.0 and klode-mcp (matrix 10a-01, 10a-02, 10a-09).

The build is the finished ``fx-labs-3x`` build of the real ``klode-kit build`` (deterministic cards, no model).
A missing ``klode`` / ``klode-mcp`` FAILS (never skips): the lane is about the real path, and the contract lane
pairs these cases with the receipt / exit-code assertions (``tests/contract/test_kb_check_build.py``).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from _support import build_fx as B
from _support import kb_check_fx as KX
from _support.write_card_fx import SCAFFOLD
from klode_kit.build import run_build as RB
from klode_kit.contract import schema as contract

pytestmark = pytest.mark.cli_integration

CRITERIA = ("build_receipt", "manifest", "write_receipt", "cards", "zoom", "aliases", "anchors", "anchor_text",
            "card_provenance", "strict", "desk", "holdout", "smoke_mcp")


def test_all_pass_receipt(tmp_path):
    """10a-09 (L3, S1): a sound build passes every criterion; the receipt is written into the build, the tree stays
    read-only and its MANIFEST valid; running it again changes nothing (idempotent, S5)."""
    kb, build = KX.good_kb(tmp_path)
    RB.make_readonly(build)
    proc, receipt = KX.check(kb)
    assert proc.returncode == 0, proc.stderr.decode()
    assert contract.issues("check_receipt", receipt) == []
    assert receipt["status"] == "PASS" and receipt["build_id"] == build.name and receipt["kb_id"] == kb.name
    checks = KX.by_name(receipt)
    assert tuple(checks) == CRITERIA
    assert {c["status"] for c in checks.values()} == {"PASS"}
    assert receipt["mcp"] == {"mcp_config_written": False, "mcp_direct_smoke": True, "mcp_host_visible": "not_run"}
    assert receipt["u5_ready"] is False and receipt["plugin"]["entry"] == KX.ENTRY
    assert KX.written(build) == receipt
    assert RB.verify_manifest(build) == [] and B.writable_bits(build) == []
    again, second = KX.check(kb)
    assert again.returncode == 0 and second["status"] == "PASS" and second["checks"] == receipt["checks"]
    assert RB.verify_manifest(build) == [] and B.writable_bits(build) == []


def test_stub_card_fails(tmp_path):
    """10a-01 (L3, S3): a published card that is still the klode scaffold (zoom stub, never written) FAILs the
    zoom check and goes back to L2; the run ends with exit 20 (03 B6: ``klode check --strict`` alone lets it by)."""
    kb, build = KX.good_kb(tmp_path)
    source = (build / "library" / "items" / "hemoglobin.txt").read_text(encoding="utf-8")
    import hashlib

    (build / "library" / "cards" / "hemoglobin.md").write_text(
        SCAFFOLD.format(cid="hemoglobin", shelf="items", sha=hashlib.sha256(source.encode()).hexdigest()),
        encoding="utf-8")
    KX.reseal(build)
    proc, receipt = KX.check(kb)
    assert proc.returncode == 20, proc.stderr.decode()
    checks = KX.by_name(receipt)
    assert receipt["status"] == "FAIL"
    assert checks["zoom"]["status"] == "FAIL" and checks["zoom"]["detail"].startswith("L2:")
    assert "hemoglobin" in checks["zoom"]["detail"]
    assert checks["manifest"]["status"] == "PASS"
    assert KX.written(build) == receipt


def test_folded_anchor_fails(tmp_path):
    """10a-02 (L3, S3): an anchor whose punctuation was folded (an en dash for the hyphen of the date) is not in
    the source verbatim: the anchor-text check FAILs and goes back to L3 (fix-anchors); exit 20."""
    kb, build = KX.good_kb(tmp_path)
    real = "血红蛋白 128 g/L 2026-07-18 ⟨r-9393fb51d1ac⟩"
    folded = real.replace("2026-07-18", "2026–07–18")
    for rel in ("library/cards/hemoglobin.md", "cards_content/hemoglobin.md"):
        path = build / rel
        text = path.read_text(encoding="utf-8")
        assert real in text
        path.write_text(text.replace(real, folded), encoding="utf-8")
    KX.reseal(build)
    proc, receipt = KX.check(kb)
    assert proc.returncode == 20, proc.stderr.decode()
    checks = KX.by_name(receipt)
    assert checks["anchor_text"]["status"] == "FAIL" and checks["anchor_text"]["detail"].startswith("L3:")
    assert "hemoglobin" in checks["anchor_text"]["detail"]


def test_klode_missing_is_environment_exit_40(tmp_path):
    """No ``klode`` / ``klode-mcp`` on PATH: exit 40 (environment), a receipt that says so, never a traceback."""
    kb, build = KX.good_kb(tmp_path)
    RB.make_readonly(build)
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    proc, receipt = KX.check(kb, extra_env={"PATH": f"{empty}{os.pathsep}/usr/bin{os.pathsep}/bin"})
    assert proc.returncode == 40, proc.stderr.decode()
    assert b"Traceback" not in proc.stderr
    checks = KX.by_name(receipt)
    assert checks["strict"]["status"] == "FAIL" and checks["strict"]["detail"].startswith("40:")
    assert checks["smoke_mcp"]["status"] == "FAIL" and checks["smoke_mcp"]["detail"].startswith("40:")
    assert receipt["status"] == "FAIL"


def test_holdout_below_gate_goes_back_to_l1(tmp_path):
    """The holdout gate (>= 5 questions and >= 0.8 hit): too few questions or misses FAIL the holdout check, back to
    L1, whatever the build's own receipt said."""
    kb, build = KX.good_kb(tmp_path)
    rows = (build / "src" / "holdout.jsonl").read_text(encoding="utf-8").splitlines()
    (build / "src" / "holdout.jsonl").write_text("\n".join(rows[:3]) + "\n", encoding="utf-8")
    freeze = json.loads((build / "src" / "holdout.freeze.json").read_text(encoding="utf-8"))
    import hashlib

    freeze["sha256"] = hashlib.sha256((build / "src" / "holdout.jsonl").read_bytes()).hexdigest()
    (build / "src" / "holdout.freeze.json").write_text(json.dumps(freeze), encoding="utf-8")
    KX.reseal(build)
    proc, receipt = KX.check(kb)
    assert proc.returncode == 20, proc.stderr.decode()
    holdout = KX.by_name(receipt)["holdout"]
    assert holdout["status"] == "FAIL" and holdout["detail"].startswith("L1:")


def test_strict_failure_citation_rot_goes_back_to_l3(tmp_path):
    """A card anchor that is gone from the source (citation-rot) is a strict error: back to L3."""
    kb, build = KX.good_kb(tmp_path)
    for rel in ("library/cards/wbc.md", "cards_content/wbc.md"):
        path = build / rel
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("(grep: `", "(grep: `X", 1), encoding="utf-8")
    KX.reseal(build)
    proc, receipt = KX.check(kb)
    assert proc.returncode == 20, proc.stderr.decode()
    checks = KX.by_name(receipt)
    assert checks["strict"]["status"] == "FAIL" and checks["strict"]["detail"].startswith("L3:")
    assert "wbc" in checks["strict"]["detail"]
