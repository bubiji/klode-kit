"""WI-12 (contract_replay): the kb-verifier run against the replay fake ``claude`` (matrix 12-03).

``klode-kit kb-advice --run`` starts ``claude -p --agent kb-verifier`` with cwd = the audit revision ``a<N>`` (the crops
and the candidates it needs are there; nothing else), tools limited to ``Read,Grep,Glob``, and turns the structured
answer into ``a<N>/advice.json``. The fake ``claude`` (tests/fakes/bin, first on PATH, ``KLODE_KIT_FAKE_SCENARIO``) plays
the model: no real call. Paired real path: none for the verifier (its host behaviour is model-dependent and runs only
under the G2 gate; the writer's paired real test is ``tests/model/test_card_writer_real.py``, 12-04).

Law (matrix 12, S3 + 08c-16 / F7): whatever the verifier says - good advice, advice for a field that is not on the page,
a failure, garbage, a stray file in its working directory - every byte of ``records/`` is as it was before the run, and
the run itself adds nothing to the project but ``a<N>/advice.json`` (and not even that when it fails).
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from _support.confirm_fx import FIXTURES, audited, write_record
from _support.replay import log_entries
from klode_kit.audit import advice as A
from klode_kit.audit import confirm_page as CP
from klode_kit.contract import schema as contract

pytestmark = pytest.mark.contract_replay

U8 = FIXTURES / "fx-u8"
REPO = Path(__file__).resolve().parents[2]
VALID_ACK = "G2-20261026"


def result(advice, *, cost=0.0123, is_error=False, denials=()):
    return json.dumps({"type": "result", "subtype": "success", "is_error": is_error, "num_turns": 3,
                       "total_cost_usd": cost, "permission_denials": list(denials), "result": "done",
                       "structured_output": {"advice": advice}})


@pytest.fixture()
def world(tmp_path):
    project = tmp_path / "proj"
    batch, adir = audited(project, U8)
    recs = {f["field_id"].split("/")[0]
            for line in (adir / "candidates.jsonl").read_text(encoding="utf-8").splitlines()
            for f in json.loads(line)["fields"]}
    for k, rec in enumerate(sorted(recs)):
        write_record(project, {"rec_id": rec, "doc_id": "d-000000000000", "page_id": "d-000000000000/p001",
                               "record_idx": k, "type": "lab", "status": "pending_confirm", "candidates": []})
    return project, batch, adir


def snapshot(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def records_of(snap: dict[str, bytes]) -> dict[str, bytes]:
    return {k: v for k, v in snap.items() if k.startswith("records/")}


def play(monkeypatch, tmp_path: Path, *steps: dict) -> None:
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps({"steps": list(steps)}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("KLODE_KIT_FAKE_SCENARIO", str(path))


def advice_for(adir: Path, k: int = 2) -> list[dict]:
    return [{"field_id": f["field_id"], "suggestion": "6.2", "evidence": "原图该行为 6.2，其余路读数与之不同"}
            for f in A.shown_fields(adir)[:k]]


def run(project: Path, batch: str, *extra: str) -> int:
    return A.main(["--batch", batch, "--project", str(project), "--run", *extra])


# --------------------------------------------------------------------------- 12-03


def test_verifier_never_changes_status(world, monkeypatch, tmp_path, fake_log, capsys):
    """12-03 (L2, S3): a verifier run leaves ``records/_by_id/**`` byte-identical; ``advice.json`` passes the schema."""
    project, batch, adir = world
    doc = advice_for(adir)
    play(monkeypatch, tmp_path, {"stdout": result(doc)})
    before = snapshot(project)
    assert any(k.startswith("records/_by_id/") for k in before)
    assert run(project, batch) == 0
    after = snapshot(project)
    assert records_of(after) == records_of(before)
    assert set(after) - set(before) == {str(adir.relative_to(project) / "advice.json")}
    assert {k: v for k, v in after.items() if k in before} == before
    written = json.loads((adir / "advice.json").read_text(encoding="utf-8"))
    assert written == doc
    assert A.issues(written) == []
    assert json.loads(json.dumps(A.schema()))["type"] == "array"
    out = capsys.readouterr().out
    assert str(adir / "advice.json") in out
    # and the page that follows shows the hint without choosing for the human
    page = CP.render(project, batch).decode("utf-8")
    assert page.count("kk-advice") >= len(doc)
    assert " checked" not in page


def test_the_verifier_is_started_read_only_in_the_audit_directory(world, monkeypatch, tmp_path, fake_log):
    project, batch, adir = world
    play(monkeypatch, tmp_path, {"stdout": result(advice_for(adir))})
    assert run(project, batch, "--max-budget-usd", "0.75") == 0
    (call,) = log_entries(fake_log)
    argv = call["argv"]
    assert Path(call["cwd"]).resolve() == adir.resolve()
    assert argv[0] == "-p"

    def after(flag: str) -> str:
        return argv[argv.index(flag) + 1]
    assert after("--agent") == "kb-verifier"
    assert after("--permission-mode") == "dontAsk"
    assert after("--allowedTools").split(",") == ["Read", "Grep", "Glob"]
    assert {"Edit", "Write", "Bash", "NotebookEdit"} <= set(after("--disallowedTools").split(","))
    assert after("--output-format") == "json"
    assert after("--max-budget-usd") == "0.75"
    assert "--no-session-persistence" in argv and "--force" not in argv
    inline = json.loads(after("--agents"))["kb-verifier"]
    assert "Bash" not in inline.get("tools", []) and "Edit" not in inline.get("tools", [])
    wrapper = json.loads(after("--json-schema"))
    assert wrapper["required"] == ["advice"] and "$schema" not in wrapper and "$id" not in wrapper
    prompt = argv[-1]
    shown = A.shown_fields(adir)
    for f in shown:
        assert f["field_id"] in prompt
        for route, value in f["seen"].items():
            assert route in prompt and str(value) in prompt
        if f["crop"]:
            assert f["crop"] in prompt
    assert str(project) not in prompt, "no absolute path of the data project goes into the prompt"


@pytest.mark.parametrize("scenario,code", [
    pytest.param({"stdout": result([{"field_id": "r-000000000000/r001-number-0", "suggestion": "1", "evidence": "e"}])},
                 20, id="advice-for-a-field-that-is-not-on-the-page"),
    pytest.param({"stdout": result([{"field_id": "x", "suggestion": "1", "evidence": "e"}])}, 20, id="schema-violation"),
    pytest.param({"stdout": result("not a list")}, 20, id="advice-not-a-list"),
    pytest.param({"stdout": '{"type": "result", "structured_output": {"advice": ['}, 20, id="truncated-json"),
    pytest.param({"stdout": "not json at all"}, 20, id="garbage"),
    pytest.param({"stdout": json.dumps({"type": "result", "is_error": False, "result": "no structured output"})},
                 20, id="no-structured-output"),
    pytest.param({"stdout": result([], is_error=True)}, 20, id="is-error"),
    pytest.param({"stdout": "", "stderr": "boom", "exit_code": 1}, 20, id="claude-exit-1"),
    pytest.param({"stdout": result([]), "write_files": {"notes.txt": "stray"}}, 20, id="stray-file-in-cwd"),
    pytest.param({"stdout": result([]), "write_files": {"crops/new.png": "stray"}}, 20, id="stray-file-in-crops"),
])
def test_every_failure_leaves_the_project_untouched(world, monkeypatch, tmp_path, fake_log, capsys, scenario, code):
    """S3 over the failure modes: nothing is written, no record byte moves, the exit code says the verifier failed."""
    project, batch, adir = world
    play(monkeypatch, tmp_path, scenario)
    before = snapshot(project)
    assert run(project, batch) == code
    after = snapshot(project)
    assert records_of(after) == records_of(before)
    assert not (adir / "advice.json").exists()
    assert {k: v for k, v in after.items() if k in before} == before
    stray = set(after) - set(before)
    assert stray <= {str(adir.relative_to(project) / "notes.txt"), str(adir.relative_to(project) / "crops" / "new.png")}
    assert capsys.readouterr().err.strip(), "a failure must say why"


def test_a_good_empty_answer_is_recorded_and_costs_nothing_more(world, monkeypatch, tmp_path):
    project, batch, adir = world
    play(monkeypatch, tmp_path, {"stdout": result([])})
    assert run(project, batch) == 0
    assert json.loads((adir / "advice.json").read_text(encoding="utf-8")) == []


def test_a_second_run_with_other_advice_is_refused(world, monkeypatch, tmp_path, fake_log):
    project, batch, adir = world
    first = advice_for(adir, 1)
    play(monkeypatch, tmp_path, {"stdout": result(first)})
    assert run(project, batch) == 0
    before = snapshot(project)
    play(monkeypatch, tmp_path, {"stdout": result(advice_for(adir, 2))})
    assert run(project, batch) == 30
    assert snapshot(project) == before


def test_nothing_in_dispute_means_the_verifier_is_not_started(tmp_path, monkeypatch, fake_log):
    """A revision whose every field agrees has nothing to advise on: no call, an empty advice.json."""
    project = tmp_path / "proj"
    batch, adir = audited(project, FIXTURES / "fx-labs-3x")
    assert A.shown_fields(adir) == []
    assert run(project, batch) == 0
    assert log_entries(fake_log) == []
    assert json.loads((adir / "advice.json").read_text(encoding="utf-8")) == []


# --------------------------------------------------------------------------- the G2 gate


def pseudo_real(root: Path, log: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    exe = root / "claude"
    exe.write_text(f'#!/bin/sh\necho "start $$" >> "{log}"\necho "{{}}"\nexit 0\n', encoding="utf-8")
    exe.chmod(0o755)
    return exe


@pytest.mark.parametrize("env", [
    {}, {"KLODE_KIT_REAL": "0", "KLODE_KIT_GATE_ACK": VALID_ACK}, {"KLODE_KIT_REAL": "1"},
    {"KLODE_KIT_REAL": "1", "KLODE_KIT_GATE_ACK": "G3-20261026"}, {"KLODE_KIT_REAL": "1", "KLODE_KIT_GATE_ACK": "G2-2026"},
])
def test_a_real_claude_is_never_started_with_the_gate_closed(world, monkeypatch, tmp_path, env):
    """Gate G2 (exit 61, zero calls): the same refusal as the bridge and the sender, observed on a pseudo-real claude."""
    project, batch, adir = world
    log = tmp_path / "real.log"
    exe = pseudo_real(tmp_path / "realbin", log)
    for key in ("KLODE_KIT_REAL", "KLODE_KIT_GATE_ACK"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    before = snapshot(project)
    assert run(project, batch, "--claude", str(exe)) == 61
    assert not log.exists()
    assert snapshot(project) == before


def test_the_pseudo_real_claude_does_start_when_the_gate_is_open(world, monkeypatch, tmp_path):
    """Positive control for the test above: the same harness records a start once the gate is open."""
    project, batch, adir = world
    log = tmp_path / "real.log"
    exe = pseudo_real(tmp_path / "realbin", log)
    monkeypatch.setenv("KLODE_KIT_REAL", "1")
    monkeypatch.setenv("KLODE_KIT_GATE_ACK", VALID_ACK)
    assert run(project, batch, "--claude", str(exe)) == 20       # it prints "{}": started, answer unusable
    assert log.read_text(encoding="utf-8").count("start") == 1
    assert not (adir / "advice.json").exists()


def test_no_claude_at_all_is_a_configuration_exit(world, monkeypatch):
    project, batch, adir = world
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("KLODE_KIT_REAL", "1")
    monkeypatch.setenv("KLODE_KIT_GATE_ACK", VALID_ACK)
    assert run(project, batch, "--claude", "no-such-claude-binary") == 40


def test_the_shipped_verifier_definition_is_what_the_argv_carries(world, monkeypatch, tmp_path, fake_log):
    project, batch, adir = world
    play(monkeypatch, tmp_path, {"stdout": result([])})
    assert run(project, batch) == 0
    (call,) = log_entries(fake_log)
    inline = json.loads(call["argv"][call["argv"].index("--agents") + 1])
    shipped = (REPO / "agents" / "kb-verifier.md").read_text(encoding="utf-8").split("\n---\n", 1)[1].strip()
    assert inline["kb-verifier"]["prompt"] == shipped
    assert "advice" not in contract.NAMES  # deliberately outside the sixteen pinned contract names
