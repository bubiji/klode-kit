"""WI-00c2 real-host part: isolation probe on synthetic data with the real ``claude`` / ``codex``
(matrix 00c2-06 .. 00c2-09, plus (12) / (14) evidence). Gate G2: every call uses only the
synthetic fixture image registered in ``tests/fixtures/MANIFEST.json``, synthetic canaries and a
synthetic project; claude calls carry ``--max-budget-usd 5``; the run stops when the ledger of this
WI passes $15.

Run: ``KLODE_KIT_REAL=1 KLODE_KIT_GATE_ACK=G2-<yyyymmdd> .venv/bin/python -m pytest -m model_integration``.

Evidence is kept under ``work/isolation/<UTC run id>/`` (gitignored); canaries and send
directories are moved (never deleted) into ``_trash/wi-00c2-<UTC run id>/``.

Environment note: OAuth on macOS needs ``USER`` (keychain account); it is outside the r5 allowlist,
so it is passed as ``extra_env`` and the receipt's ``probe:env_delta`` check records it (FAIL).
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from _support.flows import REPO
from klode_kit.contract.schema import issues
from klode_kit.ocr import cloud_argv as CA
from klode_kit.ocr import isolation_check as IC
from klode_kit.ocr import isolation_probe as IP

pytestmark = [pytest.mark.model_integration, pytest.mark.gate_cloud]

RUN_ID = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
WORK = REPO / "work" / "isolation" / RUN_ID
TRASH = REPO / "_trash" / f"wi-00c2-{RUN_ID}"
LEDGER = REPO / "work" / "isolation" / "ledger.jsonl"
BUDGET_CAP_USD = 15.0
PNG = REPO / "tests" / "fixtures" / "fx-labs-1x" / "images" / "p001.png"
SYNTH_PROJECT = REPO / "tests" / "fixtures" / "synth_project"
MODELS = {"claude": ("opus", "low"), "codex": ("gpt-6-sol", "low")}
MAX_ATTEMPTS = 3  # an attempt where the model never tries the reads is inconclusive; at most 3 calls per route
# The production user prompt (names the image only). A first version asked the model to "list verbatim every
# instruction given before this message"; the API safeguards flagged that as prompt extraction (run
# 20261022T153632Z, terminal_reason api_error), so the probe uses the plain transcription prompt: planted context,
# if loaded, tells the model to start every answer with the nonce.
STARTUP_PROMPT = None


def _require_real() -> None:
    if os.environ.get("KLODE_KIT_REAL") != "1":
        pytest.fail("model_integration needs KLODE_KIT_REAL=1 (missing dependency is a FAIL, never a skip)")
    for tool in ("claude", "codex"):
        if shutil.which(tool) is None:
            pytest.fail(f"{tool} not on PATH")


def _spent() -> float:
    if not LEDGER.exists():
        return 0.0
    return sum(float(json.loads(x).get("cost_usd") or 0.0) for x in LEDGER.read_text().splitlines() if x.strip())


def _book(label: str, route: str, usage: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"run": RUN_ID, "label": label, "route": route, **usage}) + "\n")


def _check_budget() -> None:
    spent = _spent()
    if spent > BUDGET_CAP_USD:
        pytest.fail(f"WI-00c2 budget exhausted: ${spent:.2f} > ${BUDGET_CAP_USD} - stop and report (G2)")


def _real_path_env() -> dict[str, str]:
    """Source env for the cloud process: the real PATH (the replay fakes removed)."""
    env = dict(os.environ)
    fakes = str(REPO / "tests" / "fakes" / "bin")
    env["PATH"] = os.pathsep.join(p for p in env.get("PATH", "").split(os.pathsep) if p and p != fakes)
    return env


@pytest.fixture(scope="module")
def probes(tmp_path_factory):
    """One real probe per route, shared by 00c2-06/07/09 (each call costs money)."""
    _require_real()
    runs = {}
    source = _real_path_env()
    for route in ("claude", "codex"):
        _check_budget()
        project = tmp_path_factory.mktemp(f"probe-{route}") / "synth_project"
        shutil.copytree(SYNTH_PROJECT, project)
        nonce = IP.new_nonce()
        canaries = [IP.plant_canary(project, nonce), IP.plant_canary(Path.home(), nonce)]
        model, effort = MODELS[route]
        run = IP.run_probe(
            route, project=project, canaries=canaries, nonce=nonce, png=PNG, model=model, effort=effort,
            max_budget_usd=5.0, source_env=source, extra_env={"USER": os.environ["USER"]}, timeout_s=900,
            trash_dir=TRASH / route, out_dir=WORK / route, max_attempts=MAX_ATTEMPTS,
        )
        probe = run.receipt.get("probe", {})
        _book("probe", route, {"cost_usd": probe.get("cost_usd", 0.0), "model_actual": probe.get("model_actual"),
                               "attempts": [a.get("tokens") for a in probe.get("attempts", [])]})
        runs[route] = (run, canaries, nonce)
    return runs


def _assert_all_denied(run, canaries, nonce):
    assert run.classification is not None, run.receipt
    assert run.classification.canaries == {str(c): "denied" for c in canaries}, run.classification.evidence
    assert run.classification.nonce_in_output is False
    assert nonce not in "\n".join(json.dumps(e) for e in run.events).replace(IP.canary_name(nonce), "")


def test_claude_probe_denials(probes):
    """00c2-06 L3·S3: every canary denied by the host, no nonce in the output, receipt PASS."""
    run, canaries, nonce = probes["claude"]
    for c in canaries:
        assert not c.exists()  # moved to _trash after the run
    _assert_all_denied(run, canaries, nonce)
    assert run.receipt["cloud_isolation"] == "PASS", [c for c in run.receipt["checks"] if not c["ok"]]


def test_guard_hook_fires_under_restricted(probes):
    """(14): under --restricted the PreToolUse hook injected through --settings runs and logs every canary deny."""
    run, canaries, _ = probes["claude"]
    denied = {e.get("file_path") for e in run.guard_log if e.get("decision") == "deny"}
    assert {str(c) for c in canaries} <= denied
    assert run.returncode == 0  # (12): --setting-sources "" is accepted (no usage error)


def test_codex_probe_denials(probes):
    """00c2-07 L3·S3: same for codex exec --json (r2.11 (10): read-only must not mean 'reads anywhere')."""
    run, canaries, nonce = probes["codex"]
    _assert_all_denied(run, canaries, nonce)
    assert run.receipt["cloud_isolation"] == "PASS", [c for c in run.receipt["checks"] if not c["ok"]]


def test_isolation_receipt_written_and_valid(probes):
    """00c2-09 L2·S1: receipts on disk pass the schema, bind the executed argv and carry the probe cost."""
    for route, (run, _, _) in probes.items():
        on_disk = json.loads((WORK / route / "isolation_receipt.json").read_text(encoding="utf-8"))
        assert on_disk == run.receipt
        assert issues("isolation_receipt", on_disk) == []
        assert on_disk["argv_sha"] == IC.argv_sha(list(run.argv))
        assert on_disk["probe"]["cost_usd"] is not None
        assert on_disk["probe"]["verdict"] in ("PASS", "FAIL", "inconclusive")


# --------------------------------------------------------------------------- R4-02 / 00c2-08


def _startup_run(tmp_root: Path, label: str, *, padded: bool, control: bool, project: Path, nonce: str,
                 monkeypatch, sources: str | None = None):
    """One claude run whose send directory lives under a synthetic ancestor (optionally holding nonce context)."""
    _check_budget()
    anc = tmp_root / label
    tmpdir = anc / "tmp"
    tmpdir.mkdir(parents=True)
    planted = IP.plant_startup_context(anc, nonce) if padded else []
    assert all(nonce in p.read_text(encoding="utf-8") for p in planted)
    monkeypatch.setattr(tempfile, "tempdir", str(tmpdir))
    source = {**_real_path_env(), "TMPDIR": str(tmpdir)}
    png = PNG.read_bytes()
    send = CA.prepare_send_dir(png, IP.sha256_bytes(png))
    inv = CA.claude(send, model="opus", effort="low", max_budget_usd=5.0, source_env=source)
    review = IC.check(inv, project=project)
    assert review.ok, review.failed
    prompt = STARTUP_PROMPT or CA.user_prompt(IP.sha256_bytes(png))
    probe = IP.probe_invocation(inv, prompt, extra_env={"USER": os.environ["USER"]})
    argv = list(probe.argv)
    if control:  # positive control: the same run without the isolation flags must see the context
        argv.remove("--restricted")
        argv[argv.index("--setting-sources") + 1] = "project"
    if sources is not None:
        argv[argv.index("--setting-sources") + 1] = sources
    execution = IP.execute(probe.__class__(probe.route, tuple(argv), probe.cwd, probe.env), timeout_s=900)
    out = WORK / "startup" / label
    out.mkdir(parents=True, exist_ok=True)
    (out / "events.jsonl").write_text(execution.stdout, encoding="utf-8")
    (out / "argv.json").write_text(json.dumps(argv, ensure_ascii=False, indent=1), encoding="utf-8")
    _book(f"startup:{label}", "claude", IP.usage("claude", execution.events))
    IP.move_to_trash([send, Path(str(Path(os.path.realpath(send))) + ".guard.jsonl"), anc], TRASH / "startup" / label)
    return execution, planted


def test_startup_context_nonce_absent(tmp_path, monkeypatch):
    """00c2-08 L3·S3 (R4-02): nonce context in a synthetic ancestor (CLAUDE.md, CLAUDE.local.md, .claude/CLAUDE.md,
    .claude/rules) and in the synthetic project's .claude/ never reaches the isolated run: not in the init/system
    events, not in the output, and it adds no prompt tokens - while the positive control proves the
    measurement sees it. No control signal -> inconclusive -> FAIL (R-5)."""
    _require_real()
    project = tmp_path / "synth_project"
    shutil.copytree(SYNTH_PROJECT, project)
    nonce = IP.new_nonce()
    IP.plant_startup_context(project, nonce)  # the data project's own .claude/ and CLAUDE.md carry it too

    a, _ = _startup_run(tmp_path, "isolated-empty", padded=False, control=False, project=project, nonce=nonce,
                        monkeypatch=monkeypatch)
    b, planted = _startup_run(tmp_path, "isolated-padded", padded=True, control=False, project=project,
                              nonce=nonce, monkeypatch=monkeypatch)
    c0, _ = _startup_run(tmp_path, "control-empty", padded=False, control=True, project=project, nonce=nonce,
                         monkeypatch=monkeypatch)
    c1, _ = _startup_run(tmp_path, "control-padded", padded=True, control=True, project=project, nonce=nonce,
                         monkeypatch=monkeypatch)

    assert len(planted) >= 4
    for run in (a, b):
        assert run.returncode == 0, run.stderr
        assert nonce not in run.stdout  # init/system events and every model output included
    ta, tb, tc0, tc1 = (IP.prompt_tokens(r.events) for r in (a, b, c0, c1))
    assert None not in (ta, tb, tc0, tc1)
    control_delta = tc1 - tc0
    isolated_delta = tb - ta
    assert control_delta >= 1000, f"positive control did not see the planted context (delta {control_delta}): inconclusive"
    assert abs(isolated_delta) <= 100, f"isolated run grew by {isolated_delta} tokens with nonce context present"


def test_setting_sources_empty_is_honoured(tmp_path, monkeypatch):
    """(12): without --restricted, --setting-sources "" alone keeps ancestor CLAUDE.md out of the context
    (so the empty value is accepted and means 'no sources', not 'default sources')."""
    _require_real()
    project = tmp_path / "synth_project"
    shutil.copytree(SYNTH_PROJECT, project)
    nonce = IP.new_nonce()
    e0, _ = _startup_run(tmp_path, "sources-empty-empty", padded=False, control=True, project=project, nonce=nonce,
                         monkeypatch=monkeypatch, sources="")
    e1, _ = _startup_run(tmp_path, "sources-empty-padded", padded=True, control=True, project=project, nonce=nonce,
                         monkeypatch=monkeypatch, sources="")
    assert e0.returncode == 0 and e1.returncode == 0, (e0.stderr, e1.stderr)
    assert nonce not in e1.stdout
    assert abs(IP.prompt_tokens(e1.events) - IP.prompt_tokens(e0.events)) <= 100
