"""WI-00c2c real-host part (gate G2): the Codex route's isolation evidence is the deterministic host check -
``codex sandbox -P klode-kit-ocr`` with exactly the production ``-c`` set - not a model probe (user decision
2026-10-23, plan A). Synthetic data only: the fixture image registered in ``tests/fixtures/MANIFEST.json``,
a tmp copy of ``tests/fixtures/synth_project`` and random-nonce canaries (project + ``$HOME``).

- ``test_codex_host_check_real``: no model call. Both canaries must be refused by the sandbox (exit != 0, no
  nonce), the positive control (``<send dir>/prompt.txt``) must be read byte for byte; receipt PASS.
- ``test_codex_ocr_production_argv``: ONE real ``codex exec`` with the unmodified production invocation; the
  final agent message must validate against ``schemas/ocr_cloud_output.json``. At most ``OCR_CALL_CAP``
  such calls are ever booked for this WI in the ledger; beyond that the test fails without calling.

Run: ``KLODE_KIT_REAL=1 KLODE_KIT_GATE_ACK=G2-<yyyymmdd> .venv/bin/python -m pytest -m model_integration
tests/model/test_codex_host_check.py``. Evidence: ``work/isolation/<run>/`` (gitignored); canaries and send
directories are moved into ``_trash/wi-00c2c-<run>/``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from _support.flows import REPO
from klode_kit.contract.schema import issues
from klode_kit.ocr import cloud_argv as CA
from klode_kit.ocr import isolation_check as IC
from klode_kit.ocr import isolation_probe as IP

pytestmark = [pytest.mark.model_integration, pytest.mark.gate_cloud]

RUN_ID = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
WI = "WI-00c2c"
OCR_CALL_CAP = 3
WORK = REPO / "work" / "isolation" / RUN_ID
TRASH = REPO / "_trash" / f"{WI.lower()}-{RUN_ID}"
LEDGER = REPO / "work" / "isolation" / "ledger.jsonl"
PNG = REPO / "tests" / "fixtures" / "fx-labs-1x" / "images" / "p001.png"
SYNTH_PROJECT = REPO / "tests" / "fixtures" / "synth_project"
MODEL, EFFORT = "gpt-6-sol", "low"


def _require_real() -> None:
    if os.environ.get("KLODE_KIT_REAL") != "1":
        pytest.fail("model_integration needs KLODE_KIT_REAL=1 (missing dependency is a FAIL, never a skip)")
    if shutil.which("codex", path=_real_path_env()["PATH"]) is None:
        pytest.fail("codex not on PATH")


def _real_path_env() -> dict[str, str]:
    env = dict(os.environ)
    fakes = str(REPO / "tests" / "fakes" / "bin")
    env["PATH"] = os.pathsep.join(p for p in env.get("PATH", "").split(os.pathsep) if p and p != fakes)
    return env


def _ledger_rows() -> list[dict]:
    if not LEDGER.exists():
        return []
    return [json.loads(x) for x in LEDGER.read_text(encoding="utf-8").splitlines() if x.strip()]


def _book(label: str, usage: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"run": RUN_ID, "wi": WI, "label": label, "route": "codex", **usage}) + "\n")


def test_codex_host_check_real(tmp_path):
    """Paired real path of test_codex_profile_00c2c.py: real `codex sandbox -P` with the production -c set."""
    _require_real()
    project = tmp_path / "synth_project"
    shutil.copytree(SYNTH_PROJECT, project)
    nonce = IP.new_nonce()
    canaries = [IP.plant_canary(project, nonce), IP.plant_canary(Path.home(), nonce)]
    run = IP.run_host_check(project=project, canaries=canaries, nonce=nonce, png=PNG, model=MODEL, effort=EFFORT,
                            source_env=_real_path_env(), trash_dir=TRASH / "host", out_dir=WORK / "codex-host",
                            timeout_s=120)
    _book("host_check", {"cost_usd": 0.0, "model_calls": 0, "verdict": run.verdict})

    r = run.receipt
    assert not any(c.exists() for c in canaries)  # moved to _trash
    assert issues("isolation_receipt", r) == []
    targets = r["host_check"]["targets"]
    assert sorted((t["role"], t["outcome"]) for t in targets) == [
        ("canary", "denied"), ("canary", "denied"), ("control", "ok")], targets
    for t in targets:
        if t["role"] == "canary":
            assert t["returncode"] not in (0, None)
    assert nonce not in json.dumps(r)
    assert run.verdict == "PASS"
    assert r["cloud_isolation"] == "PASS", [c for c in r["checks"] if not c["ok"]]
    assert r["argv_sha"] == IC.argv_sha(list(run.argv))
    version = subprocess.run(["codex", "--version"], capture_output=True, text=True, env=_real_path_env()).stdout
    assert r["cli_version"] == re.search(r"\d+\.\d+\.\d+", version).group(0)


def test_codex_ocr_production_argv(tmp_path):
    """One real OCR call with the unmodified production argv: the answer is a valid ocr_cloud_output."""
    _require_real()
    done = [x for x in _ledger_rows() if x.get("wi") == WI and x.get("label") == "ocr"]
    if len(done) >= OCR_CALL_CAP:
        pytest.fail(f"{WI}: {len(done)} real codex exec calls already booked (cap {OCR_CALL_CAP}) - stop (G2)")
    project = tmp_path / "synth_project"
    shutil.copytree(SYNTH_PROJECT, project)
    png = PNG.read_bytes()
    send = CA.prepare_send_dir(png, IP.sha256_bytes(png))
    try:
        inv = CA.codex(send, model=MODEL, effort=EFFORT, source_env=_real_path_env())
        review = IC.check(inv, project=project)
        assert review.ok, review.failed
        execution = IP.execute(inv, timeout_s=900)
    finally:
        out = WORK / "codex-ocr"
        out.mkdir(parents=True, exist_ok=True)
        IP.move_to_trash([send], TRASH / "ocr")
    (out / "argv.json").write_text(json.dumps(list(inv.argv), ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "events.jsonl").write_text(execution.stdout, encoding="utf-8")
    (out / "stderr.txt").write_text(execution.stderr, encoding="utf-8")
    _book("ocr", IP.usage("codex", execution.events))

    assert execution.returncode == 0, execution.stderr[-2000:]
    messages = [e["item"]["text"] for e in execution.events
                if e.get("type") == "item.completed" and (e.get("item") or {}).get("type") == "agent_message"]
    assert messages, execution.stdout[:2000]
    answer = json.loads(messages[-1])
    schema = json.loads(CA.output_schema_text())  # the exact file passed as --output-schema
    assert [e.message for e in Draft202012Validator(schema).iter_errors(answer)] == []
    assert len(answer["lines"]) >= 1
