"""WI-09c: the three repair loops over real builds (matrix 09c-01 .. 09c-06, 09c-08 and the environment-class chain
of 09a-08; design 02 r2.8, handover section 2).

Real processes everywhere, like the 09b build tests: the project's own commands through the launcher, the real
``klode`` of PATH, the fake ``claude`` replaying card writers. Failures are injected at the double boundary by
``tests/_support/loops_fx.Faults`` (a launcher wrapper and a ``klode`` wrapper failing named calls a given number of
times). Laws checked on the way: every round is a new immutable build (the old ones stay, read-only, their MANIFEST
holds), the rounds are bounded, spend never exceeds the budget.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from _support import build_fx as B
from _support import loops_fx as F
from klode_kit import loops as L
from klode_kit.build import run_build as RB

pytestmark = pytest.mark.contract_replay


def book_project(tmp: Path) -> Path:
    project = tmp / "proj"
    project.mkdir()
    return project


def book_args(tmp: Path) -> list[str]:
    return ["--kb", "kb/demo-klode", *B.book_args(tmp)]


def summary(res):
    return B.summary(res)


def immutable(project: Path) -> None:
    """Every build directory of the project is complete: read-only and its MANIFEST verifies."""
    for bd in B.builds(project):
        assert B.writable_bits(bd) == [], bd
        assert RB.verify_manifest(bd) == [], bd


def by_step(build: Path) -> dict[str, str]:
    return {s["step"]: s["status"] for s in B.receipt(build)["steps"]}


def action(rnd: dict, loop: str) -> dict:
    (a,) = [x for x in rnd["actions"] if x["loop"] == loop]
    return a


# --------------------------------------------------------------------------- 09c-01


def test_l1_chain(tmp_path):
    """09c-01 (S1): the holdout is below the gate -> cue-suggest (tuning set only) -> desk apply in a new build ->
    verify; the tuning and the holdout scores are recorded apart; the holdout file is never touched."""
    project = book_project(tmp_path)
    faults = F.Faults(tmp_path)
    res = F.run_loops(project, tmp_path, faults, "--kb", "kb/demo-klode", *F.l1_desk_args(tmp_path),
                      extra_env=F.writer_scenario(tmp_path))
    assert res.returncode == 0, res.stderr.decode()
    first, second = B.builds(project)
    assert by_step(first)["desk_verify"] == "FAIL" and by_step(first)["gen_agent"] == "NOT_RUN"
    assert set(by_step(second).values()) == {"PASS"}
    rec = F.loops_receipt(project)
    assert (rec["status"], rec["exit_code"], rec["reason"], len(rec["rounds"])) == ("PASS", 0, "ok", 2)
    r1, r2 = rec["rounds"]
    assert (r1["build_id"], r2["build_id"]) == (first.name, second.name)
    a = action(r1, "L1")
    assert a["step"] == "cue_suggest" and a["exit_code"] == 0
    assert {"reed", "valve cap"} <= set(a["suggestions"]["whistle-valve"])
    assert r1["actions"] == [a] and r2["actions"] == []
    # the two sets are recorded apart: tuning misses one question, the holdout 3 of 5 (gate 0.8) - then 5 of 5
    assert r1["scores"]["tune"] == {"hit": 2, "total": 3} and r1["scores"]["holdout"] == {"hit": 3, "total": 5}
    assert r2["scores"]["holdout"] == {"hit": 5, "total": 5} and r2["scores"]["tune"] == {"hit": 3, "total": 3}
    # chain order: error (verify) -> modifier (cue-suggest) -> republish (gen-desk / desk-build --apply) -> re-check
    calls = [c["argv"] for c in faults.calls() if c["tool"] == "launcher"]
    i_cue = next(i for i, a in enumerate(calls) if a[:1] == ["cue-suggest"])
    verifies = [i for i, a in enumerate(calls) if a[:1] == ["desk-build"] and "--verify" in a]
    assert verifies[0] < i_cue < verifies[1]  # verify (error) -> cue-suggest (modifier) -> ... -> verify (re-check)
    assert any(a[:1] == ["gen-desk"] for a in calls[i_cue:]) and any(
        a[:1] == ["desk-build"] and "--apply" in a for a in calls[i_cue:])  # republished: desk apply in the new build
    assert calls[i_cue].count("--write") == 1
    # the new build has the cues, the first one keeps its desk; the holdout is byte-identical and frozen equal
    d1 = json.loads((first / "src" / "desk.json").read_text(encoding="utf-8"))
    d2 = json.loads((second / "src" / "desk.json").read_text(encoding="utf-8"))
    cues = lambda d: next(x for x in d["dimensions"] if x["dimension"] == "whistle-valve")["cues"]  # noqa: E731
    assert cues(d1) == ["zzz-dummy-cue"] and set(a["suggestions"]["whistle-valve"]) <= set(cues(d2))
    assert (first / "src" / "holdout.jsonl").read_bytes() == (second / "src" / "holdout.jsonl").read_bytes()
    assert (first / "src" / "holdout.freeze.json").read_bytes() == (second / "src" / "holdout.freeze.json").read_bytes()
    # the cards did not change, so no writer was called again
    assert len(F.claude_calls(tmp_path)) == 3
    immutable(project)


# --------------------------------------------------------------------------- 09c-02


def test_l2_chain(tmp_path):
    """09c-02 (S1): gate 1 fails on one card -> only that writer is dispatched again with the problem list pasted
    back -> gate 1 / write-card / strict in a new build; the synthesis that cites the card is invalidated and redone."""
    project = book_project(tmp_path)
    faults = F.Faults(tmp_path)
    res = F.run_loops(project, tmp_path, faults, *book_args(tmp_path),
                      extra_env=F.writer_scenario(tmp_path, bad=["ch02"]))
    assert res.returncode == 0, res.stderr.decode()
    first, second = B.builds(project)
    assert by_step(first)["gate1"] == "FAIL" and by_step(first)["write_card"] == "NOT_RUN"
    assert set(by_step(second).values()) == {"PASS"}
    rec = F.loops_receipt(project)
    r1 = rec["rounds"][0]
    a = action(r1, "L2")
    assert (a["step"], a["cards"]) == ("redispatch_writers", ["ch02"])
    assert a["invalidated_dimensions"] == ["whistle-valve"]  # the synthesis of the dimension citing ch02 (S5)
    assert {p["card"] for p in r1["problems"]} == {"ch02"} and r1["problems"][0]["loop"] == "L2"
    calls = F.claude_calls(tmp_path)
    assert sorted(F.card_of(c) for c in calls) == ["ch01", "ch02", "ch02", "ch03"]  # 3 first, then only ch02
    repair_prompt = next(c for c in calls[3:])["argv"][-1]
    assert L.REPAIR_MARKER in repair_prompt and "ch02" in repair_prompt and r1["problems"][0]["code"] in repair_prompt
    assert all(L.REPAIR_MARKER not in c["argv"][-1] for c in calls[:3])
    # the repair spec is on disk beside the round; it names the old build and the redispatched card only
    spec = json.loads((F.loops_dir(project) / "r01" / "repair.json").read_text(encoding="utf-8"))
    assert spec["schema"] == L.REPAIR_SCHEMA and list(spec["redispatch"]) == ["ch02"]
    assert spec["from_build"].endswith(first.name)
    # downstream was redone in the new build: desk apply / check-synthesis / verify all ran and passed
    assert [by_step(second)[s] for s in ("desk_apply", "desk_verify", "check_strict")] == ["PASS"] * 3
    # the good cards of round 1 were carried over, byte for byte; the new ch02 is the writer's repaired one
    for cid in ("ch01", "ch03"):
        assert (first / "cards_content" / f"{cid}.md").read_bytes() == (second / "cards_content" / f"{cid}.md").read_bytes()
    assert (first / "cards_content" / "ch02.md").read_bytes() != (second / "cards_content" / "ch02.md").read_bytes()
    immutable(project)


# --------------------------------------------------------------------------- 09c-03


def test_l3_chain(tmp_path):
    """09c-03 (S1): strict reports citation-rot -> fix-anchors on a copy of the cards -> write-card again in a new
    build -> strict passes. A mechanical fix needs no writer call."""
    project = book_project(tmp_path)
    faults = F.Faults(tmp_path, [{"tool": "klode", "tokens": ["check", "--strict"], "times": 1, "exit": 1,
                                  "stdout": F.STRICT_ROT.format(card="ch01")}])
    res = F.run_loops(project, tmp_path, faults, *book_args(tmp_path), extra_env=F.writer_scenario(tmp_path))
    assert res.returncode == 0, res.stderr.decode()
    first, second = B.builds(project)
    assert by_step(first)["check_strict"] == "FAIL" and by_step(first)["write_card"] == "PASS"
    assert set(by_step(second).values()) == {"PASS"}
    rec = F.loops_receipt(project)
    r1 = rec["rounds"][0]
    a = action(r1, "L3")
    assert (a["step"], a["cards"], a["exit_code"], a["left"]) == ("fix_anchors", ["ch01"], 0, [])
    assert [(p["loop"], p["card"], p["code"]) for p in r1["problems"]] == [("L3", "ch01", "citation-rot")]
    cmds = faults.commands()
    assert cmds.count("write-card") == 2 and cmds.count("fix-anchors") == 1
    writes = [i for i, c in enumerate(cmds) if c == "write-card"]
    assert writes[0] < cmds.index("fix-anchors") < writes[1]  # error -> fix-anchors -> write-card again
    klode_cmds = faults.commands("klode")
    assert klode_cmds.count("check") >= 2  # strict again in the new build
    assert len(F.claude_calls(tmp_path)) == 3  # L3 does not call a writer
    assert (first / "cards_content" / "ch01.md").read_bytes() == (second / "cards_content" / "ch01.md").read_bytes()
    immutable(project)


# --------------------------------------------------------------------------- 09c-04


def test_l3_escalates_l2(tmp_path):
    """09c-04 (S3): fix-anchors leaves an anchor it cannot fix -> the same round escalates that card to L2: the
    writer is dispatched again with the problem list; the receipt shows L3 then the escalated L2."""
    project = book_project(tmp_path)
    left = "ch01: 修好 0，仍找不到 1\n   ✗ `copper shell that is`\n"
    faults = F.Faults(tmp_path, [
        {"tool": "klode", "tokens": ["check", "--strict"], "times": 1, "exit": 1, "stdout": F.STRICT_ROT.format(card="ch01")},
        {"tool": "launcher", "tokens": ["fix-anchors"], "times": 1, "exit": 1, "stdout": left, "stderr": ""}])
    res = F.run_loops(project, tmp_path, faults, *book_args(tmp_path), extra_env=F.writer_scenario(tmp_path))
    assert res.returncode == 0, res.stderr.decode()
    rec = F.loops_receipt(project)
    r1 = rec["rounds"][0]
    assert [x["loop"] for x in r1["actions"]] == ["L3", "L2"]
    l3, l2 = r1["actions"]
    assert (l3["exit_code"], l3["left"]) == (1, ["ch01"])
    assert (l2["step"], l2["cards"], l2["escalated_from_l3"]) == ("redispatch_writers", ["ch01"], ["ch01"])
    assert sorted(F.card_of(c) for c in F.claude_calls(tmp_path)) == ["ch01", "ch01", "ch02", "ch03"]
    assert len(B.builds(project)) == 2 and rec["status"] == "PASS"
    immutable(project)


# --------------------------------------------------------------------------- 09c-05


def test_l2_l3_simultaneous_l3_first(tmp_path):
    """09c-05 (S2): one strict run reports a rotten citation on ch01 and another error on ch02 - the round's record
    lists the L3 action before the L2 action, and L3 ran (fix-anchors) before the writer was called again."""
    project = book_project(tmp_path)
    strict = ("[F citation-rot] cards/ch01.md: grep `copper shell that is` no longer in library/book/ch01.txt\n"
              "[C card-orphan] cards/ch02.md: the source is gone\n"
              "klode check --strict: 2 errors, 0 warning(s)\n")
    faults = F.Faults(tmp_path, [{"tool": "klode", "tokens": ["check", "--strict"], "times": 1, "exit": 1,
                                  "stdout": strict}])
    res = F.run_loops(project, tmp_path, faults, *book_args(tmp_path), extra_env=F.writer_scenario(tmp_path))
    assert res.returncode == 0, res.stderr.decode()
    r1 = F.loops_receipt(project)["rounds"][0]
    assert [x["loop"] for x in r1["actions"]] == ["L3", "L2"]
    l3, l2 = r1["actions"]
    assert l3["cards"] == ["ch01"] and l2["cards"] == ["ch02"] and l2["escalated_from_l3"] == []
    assert l3["ended"] <= l2["started"]
    calls = F.claude_calls(tmp_path)
    assert sorted(F.card_of(c) for c in calls) == ["ch01", "ch02", "ch02", "ch03"]
    assert (F.loops_dir(project) / "r01" / "fixed").is_dir() or l3["fixed"] == []
    immutable(project)


# --------------------------------------------------------------------------- 09c-06


def test_no_progress_terminates(tmp_path):
    """09c-06 (S3): the same problem list in two rounds in a row -> exit 50 without a third build; the receipt keeps
    the money spent and the problems that remain."""
    project = book_project(tmp_path)
    faults = F.Faults(tmp_path, [{"tool": "launcher", "tokens": ["check-cards"], "times": None, "exit": 1}])
    res = F.run_loops(project, tmp_path, faults, *book_args(tmp_path),
                      extra_env=F.writer_scenario(tmp_path, cost=0.25))
    assert res.returncode == 50, res.stderr.decode()
    s = summary(res)
    assert (s["exit_code"], s["reason"]) == (50, "no_progress")
    assert len(B.builds(project)) == 2
    rec = F.loops_receipt(project)
    assert (rec["status"], rec["exit_code"], rec["reason"], len(rec["rounds"])) == ("FAIL", 50, "no_progress", 2)
    assert rec["rounds"][0]["problems_hash"] == rec["rounds"][1]["problems_hash"]
    assert rec["spent_usd"] == pytest.approx(0.75)  # three writers in round 1; round 2 carries the cards over
    assert len(F.claude_calls(tmp_path)) == 3
    assert rec["remaining_problems"] and all(p["step"] == "gate1" for p in rec["remaining_problems"])
    assert rec["final_build_id"] == B.builds(project)[-1].name
    assert not (F.loops_dir(project) / "r02" / "repair.json").exists()  # no third round was prepared
    immutable(project)


def test_max_total_rounds_ends_a_run_that_keeps_changing_nothing_for_the_hash(tmp_path):
    """09c-09 through a real run (S2): ``max_total_rounds = 2`` with the no-progress window wider than the run - the
    second failing round ends it (exit 50, reason max_rounds) and no third build is made."""
    project = B.project(tmp_path)
    (project / "klode-kit.toml").write_text("[loops]\nmax_total_rounds = 2\nno_progress_rounds = 5\n", encoding="utf-8")
    faults = F.Faults(tmp_path, [{"tool": "launcher", "tokens": ["check-cards"], "times": None, "exit": 1}])
    res = F.run_loops(project, tmp_path, faults, "--kb", "kb/main")
    assert res.returncode == 50, res.stderr.decode()
    rec = F.loops_receipt(project)
    assert (rec["reason"], len(rec["rounds"]), rec["max_total_rounds"]) == ("max_rounds", 2, 2)
    assert len(B.builds(project)) == 2 and rec["spent_usd"] == 0
    immutable(project)


def test_deterministic_cards_are_regenerated_not_dispatched(tmp_path):
    """L2 on the medical path (S1): no model is involved - the repair round regenerates the cards in a new build
    (``records-to-l1`` again) and the run passes once the transient failure is gone."""
    project = B.project(tmp_path)
    faults = F.Faults(tmp_path, [{"tool": "launcher", "tokens": ["check-cards"], "times": 1, "exit": 1}])
    res = F.run_loops(project, tmp_path, faults, "--kb", "kb/main", extra_env={"KLODE_KIT_FAKE_LOG": str(tmp_path / "claude-calls.jsonl")})
    assert res.returncode == 0, res.stderr.decode()
    rec = F.loops_receipt(project)
    a = action(rec["rounds"][0], "L2")
    assert a["step"] == "regenerate" and a["cards"] == []
    assert faults.commands().count("records-to-l1") == 2
    assert F.claude_calls(tmp_path) == []
    assert len(B.builds(project)) == 2
    immutable(project)


# --------------------------------------------------------------------------- 09c-08


def test_budget_reservation_before_dispatch(tmp_path):
    """09c-08 (S4): four writers may run at once, the budget covers two calls of 1 USD - only two are dispatched,
    the third is refused before it starts, spend is within the budget, the run ends with exit 50 (no loop)."""
    project = book_project(tmp_path)
    res = F.run_loops(project, tmp_path, None, "--budget-usd", "2", *book_args(tmp_path),
                      extra_env=F.writer_scenario(tmp_path, cost=1.0))
    assert res.returncode == 50, res.stderr.decode()
    assert len(F.claude_calls(tmp_path)) == 2
    (bd,) = B.builds(project)
    run = json.loads((bd / "receipts" / "writer_run.json").read_text(encoding="utf-8"))
    assert sorted(t["status"] for t in run["tasks"]) == ["budget", "done", "done"]
    assert run["cost_usd"] <= 2.0 and run["budget"]["limit_usd"] == 2.0 and run["budget"]["spent_usd"] <= 2.0
    rec = F.loops_receipt(project)
    assert (rec["status"], rec["exit_code"], rec["reason"], len(rec["rounds"])) == ("FAIL", 50, "budget_exhausted", 1)
    assert rec["spent_usd"] <= 2.0 and rec["budget_usd"] == 2.0
    undone = [t["task_id"] for t in run["tasks"] if t["status"] == "budget"]
    assert [p["card"] for p in rec["remaining_problems"]] == undone
    assert rec["remaining_problems"][0]["code"] == "writer_budget"
    immutable(project)


def test_budget_below_one_reservation_admits_one_call(tmp_path):
    """A budget of 0.5 USD: the reservation is min(1 USD, limit) = 0.5, so one call is admitted; its cost uses the
    budget up and the others, waiting for it, are refused."""
    project = book_project(tmp_path)
    res = F.run_loops(project, tmp_path, None, "--budget-usd", "0.5", *book_args(tmp_path),
                      extra_env=F.writer_scenario(tmp_path, cost=0.5))
    assert res.returncode == 50, res.stderr.decode()
    assert len(F.claude_calls(tmp_path)) == 1
    assert F.loops_receipt(project)["spent_usd"] == pytest.approx(0.5)


# --------------------------------------------------------------------------- 09a-08, full chain


def test_authentication_failure_never_enters_a_loop(tmp_path):
    """09a-08 through the loops (S3): the writers answer "not logged in" - exit 40 at the first round, no repair
    spec, no second build; the receipt says environment."""
    project = book_project(tmp_path)
    scenario = tmp_path / "auth.json"
    scenario.write_text(json.dumps({"steps": [{"stdout": "", "stderr": "Not logged in · Please run /login\n",
                                               "exit_code": 1}]}), encoding="utf-8")
    res = F.run_loops(project, tmp_path, None, *book_args(tmp_path),
                      extra_env={"KLODE_KIT_FAKE_SCENARIO": str(scenario), "KLODE_KIT_FAKE_LOG": str(tmp_path / "c.jsonl")})
    assert res.returncode == 40, res.stderr.decode()
    assert len(B.builds(project)) == 1
    rec = F.loops_receipt(project)
    assert (rec["exit_code"], rec["reason"], len(rec["rounds"])) == (40, "auth", 1)
    assert rec["rounds"][0]["actions"] == [] and rec["rounds"][0]["problems"] == []
    assert not list(F.loops_dir(project).glob("r0*/repair.json"))
    immutable(project)


def test_closed_gate_never_enters_a_loop(tmp_path):
    """09a-08 through the loops (S3): a real-looking ``claude`` behind the closed G2 gate - exit 61, one round."""
    project = book_project(tmp_path)
    bindir = tmp_path / "realish"
    bindir.mkdir()
    (bindir / "claude").write_text("#!/bin/sh\necho real >&2\nexit 3\n", encoding="utf-8")
    (bindir / "claude").chmod(0o755)
    klode_dir = str(Path(shutil.which("klode")).parent)
    path = ":".join([str(bindir), klode_dir, "/usr/bin", "/bin", str(Path(sys.executable).parent)])
    res = F.run_loops(project, tmp_path, None, *book_args(tmp_path), extra_env={"PATH": path})
    assert res.returncode == 61, res.stderr.decode()
    rec = F.loops_receipt(project)
    assert (rec["exit_code"], len(rec["rounds"]), len(B.builds(project))) == (61, 1, 1)
    assert rec["rounds"][0]["actions"] == []


# --------------------------------------------------------------------------- pipeline wiring


def test_pipeline_build_stage_runs_through_the_loops(tmp_path):
    """The pipeline's build stage is the build with the loops: the loop receipt exists, the stage product is the
    final build's receipt."""
    from _support import pipeline_fx as PF
    from _support import writeback_fx as W
    from klode_kit.audit import confirm_page as CP

    rig = PF.Rig(tmp_path, "fx-labs-3x")
    first = rig.run()
    if first.returncode == 10:
        adir = rig.batch_dir / "audit" / "a1"
        choices = W.write_choices(tmp_path / "choices.json",
                                  CP.export_choices((adir / "confirm.html").read_bytes(), W.picks(adir)))
        first = rig.run("--choices", str(choices), "--stage", "build")
    else:
        first = rig.run("--stage", "build")
    assert first.returncode == 0, first.stderr.decode()
    (bd,) = B.builds(rig.project)
    build_calls = [c["argv"] for c in rig.calls("build")]
    assert len(build_calls) == 1 and "--loops" in build_calls[0]
    rec = F.loops_receipt(rig.project)
    assert (rec["status"], len(rec["rounds"]), rec["final_build_id"]) == ("PASS", 1, bd.name)
    stage = rig.receipt("build")
    assert [o["path"] for o in stage["outputs"]] == [bd.relative_to(rig.project).as_posix() + "/build_receipt.json"]
