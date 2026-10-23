"""WI-00c3 matrix 00c3-10: the four-route spike never sends without a valid isolation receipt.

Entry point ``python -m klode_kit.eval.spike4`` (one cloud call for one registered synthetic image):

    --route claude|codex --png FILE --project DIR --receipt FILE [--routes-state FILE]
    --model M --effort E [--max-budget-usd X] --out FILE --trash DIR [--timeout-s N]

Order of the gate (each step only runs when the previous one passed):

1. ``KLODE_KIT_REAL`` must be ``1`` - otherwise exit 2 and nothing is prepared;
2. the receipt file must be a schema-valid ``cloud_isolation: PASS`` receipt for this route, every check ok;
3. ``routes_enabled`` must enable the route;
4. ``isolation_check.check`` of the exact invocation must pass (this is the first step that starts a process:
   ``<cli> --version``);
5. the current CLI version (and for claude the pinned settings sha) must equal the receipt's.

Any failure of 2-5 is exit 61 (``ISOLATION_FAIL_EXIT``), the reason on stderr, no model call; the send
directory is moved (never deleted) into ``--trash``. On success the raw output is written to ``--out``.

The fake ``claude`` (tests/fakes/bin) logs every start; "no model call" is asserted on that log.
"""

from __future__ import annotations

import json
import os
import shutil

import pytest

from _support.flows import REPO, base_env, run_module
from _support.replay import log_entries, write_scenario
from klode_kit.ocr import cloud_argv as CA
from klode_kit.ocr import isolation_probe as IP

pytestmark = pytest.mark.unit

PNG = REPO / "tests" / "fixtures" / "fx-rx-stamp" / "images" / "p001.png"
RECEIPTS = REPO / "docs" / "spikes" / "receipts"
CLAUDE_RECEIPT = RECEIPTS / "claude-isolation_receipt-20261022T224341Z.json"
CODEX_RECEIPT = RECEIPTS / "codex-isolation_receipt-20261022T234046Z.json"
ROUTES_STATE = REPO / "src" / "klode_kit" / "config" / "routes_enabled.json"


@pytest.fixture
def lab(tmp_path, fake_log):
    tmp = tmp_path / "systmp"
    tmp.mkdir(mode=0o700)
    project = tmp_path / "synth_project"
    shutil.copytree(REPO / "tests" / "fixtures" / "synth_project", project)
    replay = tmp_path / "replay"
    replay.mkdir()
    env = base_env({"KLODE_KIT_REAL": "1", "TMPDIR": str(tmp), "KLODE_KIT_REPLAY_DIR": str(replay),
                    "KLODE_KIT_FAKE_LOG": str(fake_log)})
    # The cloud process gets only the r5 allowlist (PATH HOME TMPDIR LANG ...), so the fake cannot see the two
    # KLODE_KIT_* variables there; a synthetic HOME tells it where to log and replay (tests/fakes/bin/_fake_cli.py).
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / ".klode-kit-fake.json").write_text(json.dumps({"log": str(fake_log), "replay_dir": str(replay)}))
    env["HOME"] = str(fake_home)
    return {"tmp": tmp, "project": project, "replay": replay, "env": env, "log": fake_log,
            "trash": tmp_path / "trash", "out": tmp_path / "out" / "rec.json", "root": tmp_path}


def _args(lab, *, receipt, route="claude", state=ROUTES_STATE):
    args = ["--route", route, "--png", str(PNG), "--project", str(lab["project"]), "--receipt", str(receipt),
            "--routes-state", str(state), "--out", str(lab["out"]), "--trash", str(lab["trash"]),
            "--timeout-s", "60"]
    if route == "claude":
        args += ["--model", "opus", "--effort", "low", "--max-budget-usd", "5"]
    else:
        args += ["--model", "gpt-6-sol", "--effort", "low"]
    return args


def _spike(lab, args, env=None):
    return run_module("klode_kit.eval.spike4", args, lab["env"] if env is None else env, timeout=120)


def _no_send_dir_left(lab) -> bool:
    return not any(p.name.startswith(CA.SEND_PREFIX) for p in lab["tmp"].iterdir())


def _write_receipt(lab, name: str, data: dict):
    path = lab["root"] / name
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_live_requires_isolation_receipt(lab):
    """00c3-10 (L2/S3): KLODE_KIT_REAL=1 without a receipt -> exit 61, the fake claude was never started."""
    proc = _spike(lab, _args(lab, receipt=lab["root"] / "missing.json"))
    assert proc.returncode == IP.ISOLATION_FAIL_EXIT, proc.stderr.decode()
    assert log_entries(lab["log"]) == []
    assert b"no_valid_receipt" in proc.stderr
    assert not lab["out"].exists()
    assert _no_send_dir_left(lab)


@pytest.mark.parametrize("mutate", [
    pytest.param(lambda r: {**r, "cloud_isolation": "FAIL"}, id="verdict-fail"),
    pytest.param(lambda r: {**r, "checks": [{**r["checks"][0], "ok": False}, *r["checks"][1:]]}, id="check-not-ok"),
    pytest.param(lambda r: {k: v for k, v in r.items() if k != "argv_sha"}, id="schema-invalid"),
    pytest.param(lambda r: {**r, "route": "codex"}, id="other-route"),
])
def test_invalid_receipt_blocks_before_any_process(lab, mutate):
    """S3: every non-PASS / malformed / other-route receipt is exit 61 with an empty fake log."""
    good = json.loads(CLAUDE_RECEIPT.read_text(encoding="utf-8"))
    path = _write_receipt(lab, "r.json", mutate(good))
    proc = _spike(lab, _args(lab, receipt=path))
    assert proc.returncode == IP.ISOLATION_FAIL_EXIT
    assert log_entries(lab["log"]) == []
    assert not lab["out"].exists()


def test_unparseable_receipt_blocks(lab):
    path = lab["root"] / "garbage.json"
    path.write_text("{not json", encoding="utf-8")
    proc = _spike(lab, _args(lab, receipt=path))
    assert proc.returncode == IP.ISOLATION_FAIL_EXIT
    assert log_entries(lab["log"]) == []


def test_codex_receipt_does_not_open_claude_route(lab):
    proc = _spike(lab, _args(lab, receipt=CODEX_RECEIPT, route="claude"))
    assert proc.returncode == IP.ISOLATION_FAIL_EXIT
    assert log_entries(lab["log"]) == []


def test_disabled_route_blocks(lab):
    state = json.loads(ROUTES_STATE.read_text(encoding="utf-8"))
    state["routes_enabled"]["claude"] = False
    path = _write_receipt(lab, "routes.json", state)
    proc = _spike(lab, _args(lab, receipt=CLAUDE_RECEIPT, state=path))
    assert proc.returncode == IP.ISOLATION_FAIL_EXIT
    assert b"route_disabled" in proc.stderr
    assert log_entries(lab["log"]) == []
    assert _no_send_dir_left(lab)


def test_not_real_is_usage_error_and_prepares_nothing(lab):
    env = dict(lab["env"])
    env.pop("KLODE_KIT_REAL")
    proc = _spike(lab, _args(lab, receipt=CLAUDE_RECEIPT), env=env)
    assert proc.returncode == 2
    assert log_entries(lab["log"]) == []
    assert list(lab["tmp"].iterdir()) == []


def _claude_ocr_args(lab) -> list[str]:
    """argv[1:] of the production claude invocation for PNG (claude's argv does not contain the send dir)."""
    import tempfile

    png = PNG.read_bytes()
    old = tempfile.tempdir
    tempfile.tempdir = str(lab["root"])
    try:
        send = CA.prepare_send_dir(png, IP.sha256_bytes(png))
    finally:
        tempfile.tempdir = old
    inv = CA.claude(send, model="opus", effort="low", max_budget_usd=5.0)
    IP.move_to_trash([send], lab["root"] / "argv-trash")
    return list(inv.argv[1:])


def test_version_drift_blocks_after_review_without_model_call(lab):
    """S3: the CLI reports a version other than the receipt's -> 61; only `claude --version` ran."""
    write_scenario(lab["replay"], "claude", ["--version"], "2.1.286 (Claude Code)\n")
    proc = _spike(lab, _args(lab, receipt=CLAUDE_RECEIPT))
    assert proc.returncode == IP.ISOLATION_FAIL_EXIT
    assert b"receipt_binding" in proc.stderr
    started = [e["argv"] for e in log_entries(lab["log"])]
    assert started and all(a == ["--version"] for a in started)
    assert not lab["out"].exists()
    assert _no_send_dir_left(lab)


def test_valid_receipt_sends_once_and_records(lab):
    """Success path with the fake claude: one OCR start, raw output recorded, send dir moved to trash."""
    write_scenario(lab["replay"], "claude", ["--version"], "2.1.285 (Claude Code)\n")
    result = {"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.0123,
              "permission_denials": [], "structured_output": {"lines": [{"text": "示例片 10mg 每日1次", "uncertain": False}]}}
    write_scenario(lab["replay"], "claude", _claude_ocr_args(lab), json.dumps(result, ensure_ascii=False))
    proc = _spike(lab, _args(lab, receipt=CLAUDE_RECEIPT))
    assert proc.returncode == 0, proc.stderr.decode()
    calls = [e for e in log_entries(lab["log"]) if e["argv"] != ["--version"]]
    assert len(calls) == 1
    rec = json.loads(lab["out"].read_text(encoding="utf-8"))
    assert rec["recorded"] is True and rec["engine"] == "claude"
    assert rec["der_sha256"] == IP.sha256_bytes(PNG.read_bytes())
    assert rec["exit_code"] == 0
    assert rec["lines"] == ["示例片 10mg 每日1次"]
    assert rec["cost_usd"] == pytest.approx(0.0123)
    assert _no_send_dir_left(lab)
    assert str(lab["tmp"]) not in lab["out"].read_text(encoding="utf-8")


def test_call_without_answer_is_exit_1_and_still_recorded(lab):
    """The call ran (gate passed) but returned no structured answer: exit 1, the raw output is kept as evidence."""
    write_scenario(lab["replay"], "claude", ["--version"], "2.1.285 (Claude Code)\n")
    write_scenario(lab["replay"], "claude", _claude_ocr_args(lab), "", stderr="boom", exit_code=1)
    proc = _spike(lab, _args(lab, receipt=CLAUDE_RECEIPT))
    assert proc.returncode == 1
    rec = json.loads(lab["out"].read_text(encoding="utf-8"))
    assert rec["exit_code"] == 1 and rec["lines"] is None and rec["stderr"] == "boom"
    assert _no_send_dir_left(lab)


def test_transcribed_lines_codex_and_malformed():
    from klode_kit.eval import spike4 as S

    msg = {"type": "item.completed", "item": {"type": "agent_message",
                                              "text": json.dumps({"lines": [{"text": "a", "uncertain": False}]})}}
    assert S.transcribed_lines("codex", [{"type": "thread.started"}, msg]) == ["a"]
    bad = {"type": "item.completed", "item": {"type": "agent_message", "text": "not json"}}
    assert S.transcribed_lines("codex", [bad]) is None
    assert S.transcribed_lines("codex", []) is None
    assert S.transcribed_lines("claude", [{"type": "result", "structured_output": {"lines": "x"}}]) is None
    assert S.transcribed_lines("claude", []) is None


def test_bad_arguments_are_usage_errors(lab):
    proc = _spike(lab, ["--route", "gemini"])
    assert proc.returncode == 2
    assert log_entries(lab["log"]) == []


def test_guard_log_is_kept_masked_and_moved(tmp_path):
    """The hook's `<send dir>.guard.jsonl` is evidence: its entries go into the recording (send dir masked) and the
    file is moved into the trash, never deleted and never left in TMPDIR."""
    from klode_kit.eval import spike4 as S

    send = tmp_path / "klode-kit-send-abc"
    send.mkdir()
    log = tmp_path / "klode-kit-send-abc.guard.jsonl"
    log.write_text(json.dumps({"decision": "allow", "tool_name": "Read", "file_path": f"{send}/x.png"}) + "\n"
                   + json.dumps({"decision": "allow", "tool_name": "StructuredOutput", "file_path": None}) + "\n",
                   encoding="utf-8")
    entries = S.collect_guard_log(send, tmp_path / "trash")
    assert [(e["decision"], e["tool_name"]) for e in entries] == [("allow", "Read"), ("allow", "StructuredOutput")]
    assert entries[0]["file_path"] == "<send_dir>/x.png"
    assert not log.exists()
    assert (tmp_path / "trash" / log.name).read_text(encoding="utf-8").count("\n") == 2
    assert S.collect_guard_log(tmp_path / "klode-kit-send-none", tmp_path / "trash") == []
