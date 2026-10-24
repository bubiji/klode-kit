"""WI-09c: the three repair loops, unit lane (matrix 09c-07, 09c-09 and the pure parts of 09c-04 / 09c-05 / 09c-06).

Laws (S4): the number of rounds is monotone and bounded by ``loops.max_total_rounds`` and every loop terminates;
the problem-list hash does not depend on the order of the problems or on their message text.
The chains with real builds are in ``tests/contract/test_loops_chain.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from klode_kit import loops as L
from klode_kit.build.run_build import STEPS

pytestmark = pytest.mark.unit


def prob(loop="L2", step="gate1", code="A2", card="ch01", dimension=None, detail="x"):
    return {"loop": loop, "step": step, "code": code, "card": card, "dimension": dimension, "detail": detail}


# --------------------------------------------------------------------------- 09c-07: rounds are bounded


@settings(max_examples=150, deadline=None)
@given(hashes=st.lists(st.sampled_from(["a", "b", "c", "d"]), min_size=0, max_size=12),
       max_rounds=st.integers(1, 8), no_progress=st.integers(1, 4), pass_at=st.one_of(st.none(), st.integers(1, 12)))
def test_rounds_monotone_bounded(hashes, max_rounds, no_progress, pass_at):
    """09c-07 (S4): whatever the failure sequence, the rounds are numbered 1, 2, 3 ... without gaps, never more than
    ``max_total_rounds``, and the run ends: the attempt callback is never asked for a round the sequence cannot serve."""
    limits = L.Limits(max_rounds, no_progress)
    asked: list[int] = []

    def attempt(n: int):
        asked.append(n)
        if pass_at is not None and n >= pass_at:
            return None
        if n > len(hashes):
            raise AssertionError("the run did not stop although the failures ran out of the sequence")
        return hashes[n - 1]

    # pad the sequence: a run that must go on needs a hash for every possible round
    hashes = (hashes + ["z"] * 20)[: max(len(hashes), 20)]
    res = L.run_rounds(attempt, limits)
    assert asked == list(range(1, len(asked) + 1))
    assert res.rounds == len(asked) <= max_rounds
    assert res.passed == (res.reason is None)
    assert res.exit_code == (0 if res.passed else 50)
    if res.passed:
        assert pass_at is not None and res.rounds == pass_at
    else:
        assert res.reason in ("max_rounds", "no_progress")
        assert len(res.hashes) == res.rounds
        if res.reason == "no_progress":
            assert len(set(res.hashes[-no_progress:])) == 1 and res.rounds >= no_progress
        else:
            assert res.rounds == max_rounds


def test_rounds_never_exceed_the_bound_even_when_nothing_repeats():
    """09c-07 (S3): every round fails with a new problem list - the run still stops at the bound."""
    res = L.run_rounds(lambda n: f"h{n}", L.Limits(6, 2))
    assert (res.rounds, res.passed, res.reason, res.exit_code) == (6, False, "max_rounds", 50)
    assert res.hashes == [f"h{n}" for n in range(1, 7)]


# --------------------------------------------------------------------------- 09c-09: the boundary


def test_max_rounds_boundary():
    """09c-09 (S2): round 6 is allowed, round 7 is not (default ``max_total_rounds`` = 6, design r2.6)."""
    limits = L.Limits()
    assert (limits.max_total_rounds, limits.no_progress_rounds) == (6, 2)
    assert L.may_run_round(1, limits) and L.may_run_round(6, limits)
    assert not L.may_run_round(7, limits) and not L.may_run_round(0, limits)
    hashes = [f"h{k}" for k in range(1, 6)]
    assert L.decide(hashes, limits) == L.Decision(True, None)  # 5 rounds done: the 6th may run
    assert L.decide(hashes + ["h6"], limits) == L.Decision(False, "max_rounds")  # 6 done: no 7th


def test_limits_come_from_the_configuration_keys():
    from klode_kit import config as C

    cfg = C.load(None)
    assert L.Limits.from_config(cfg) == L.Limits(cfg["loops.max_total_rounds"], cfg["loops.no_progress_rounds"])


@pytest.mark.parametrize("bad", [(0, 2), (6, 0), (-1, 2), (6, True), (2.5, 2)])
def test_limits_reject_non_positive_or_non_integer(bad):
    with pytest.raises(ValueError):
        L.Limits(*bad)


# --------------------------------------------------------------------------- termination rules


def test_same_hash_twice_in_a_row_is_no_progress_but_a_repeat_after_a_change_is_not():
    limits = L.Limits(6, 2)
    assert L.decide(["a"], limits).proceed
    assert L.decide(["a", "a"], limits) == L.Decision(False, "no_progress")
    assert L.decide(["a", "b", "a"], limits).proceed
    assert L.decide(["a", "b", "b"], limits) == L.Decision(False, "no_progress")
    three = L.Limits(6, 3)
    assert L.decide(["a", "a"], three).proceed and L.decide(["a", "a", "a"], three) == L.Decision(False, "no_progress")


def test_over_budget_stops_before_another_round_and_wins_over_the_other_reasons():
    limits = L.Limits(6, 2)
    assert L.decide(["a"], limits, over_budget=True) == L.Decision(False, "budget_exhausted")
    assert L.decide(["a", "a"], limits, over_budget=True) == L.Decision(False, "budget_exhausted")
    res = L.run_rounds(lambda n: f"h{n}", limits, over_budget=lambda: True)
    assert (res.rounds, res.reason, res.exit_code) == (1, "budget_exhausted", 50)


def test_a_passing_first_round_is_exit_0_with_one_round():
    res = L.run_rounds(lambda n: None, L.Limits())
    assert (res.rounds, res.passed, res.reason, res.exit_code, res.hashes) == (1, True, None, 0, [])


# --------------------------------------------------------------------------- the problem-list hash


def test_hash_ignores_order_message_and_duplicates_but_not_content():
    a, b = prob(card="ch01", detail="first wording"), prob(card="ch02", code="F2")
    h = L.problems_hash([a, b])
    assert h == L.problems_hash([b, a]) == L.problems_hash([b, a, a])
    assert h == L.problems_hash([{**a, "detail": "another wording, 12:00:01"}, b])
    for changed in ({"card": "ch03"}, {"code": "A3"}, {"step": "write_card"}, {"loop": "L3"}, {"dimension": "safety"}):
        assert L.problems_hash([{**a, **changed}, b]) != h
    assert L.problems_hash([a]) != L.problems_hash([a, b]) and L.problems_hash([]) != L.problems_hash([a])
    assert len(h) == 64 and int(h, 16) >= 0


@settings(max_examples=80, deadline=None)
@given(st.lists(st.fixed_dictionaries({"loop": st.sampled_from(["L1", "L2", "L3"]), "step": st.sampled_from(list(STEPS)),
                                       "code": st.text("AFZ0123", min_size=1, max_size=3),
                                       "card": st.one_of(st.none(), st.sampled_from(["ch01", "ch02"])),
                                       "dimension": st.none(), "detail": st.text(max_size=8)}), max_size=6),
       st.randoms())
def test_hash_is_a_function_of_the_set_of_problems(problems, rnd):
    """The hash is permutation invariant (S4)."""
    shuffled = list(problems)
    rnd.shuffle(shuffled)
    assert L.problems_hash(problems) == L.problems_hash(shuffled)


# --------------------------------------------------------------------------- classify (09c-04 / 05 inputs)


def failed_build(tmp: Path, step: str, *, exit_code: int = 20, reports: dict[str, str] | None = None,
                 detail: str = "", extra: dict[str, str] | None = None) -> Path:
    bd = tmp / "b-20260101T000000Z-aaaaaaaa"
    (bd / "receipts").mkdir(parents=True)
    k = STEPS.index(step)
    steps = [{"step": s, "status": "PASS"} for s in STEPS[:k]] + [{"step": step, "status": "FAIL", "detail": detail}]
    steps += [{"step": s, "status": "NOT_RUN"} for s in STEPS[k + 1:]]
    (bd / "build_receipt.json").write_text(json.dumps({"status": "FAIL", "steps": steps}), encoding="utf-8")
    (bd / "receipts" / f"{step}.json").write_text(json.dumps({"step": step, "exit_code": exit_code}), encoding="utf-8")
    for name, text in (reports or {}).items():
        (bd / "reports").mkdir(exist_ok=True)
        (bd / "reports" / name).write_text(text, encoding="utf-8")
    for rel, text in (extra or {}).items():
        (bd / rel).parent.mkdir(parents=True, exist_ok=True)
        (bd / rel).write_text(text, encoding="utf-8")
    return bd


def keyset(problems):
    return sorted((p["loop"], p["card"], p["code"]) for p in problems)


def test_strict_citation_rot_lines_are_l3_and_other_card_errors_are_l2(tmp_path):
    """09c-03 / 09c-05 inputs: ``[F citation-rot]`` per card -> L3; another bracketed error on a card -> L2."""
    text = ("[F citation-rot] cards/ch01.md: grep `copper shell` no longer in library/book/ch01.txt\n"
            "[F citation-rot?] library/cards/ch03.md: grep `x y z` not found in library/book/ch03.txt\n"
            "[C card-orphan] cards/ch02.md: no source\n"
            "klode check --strict: 3 errors, 1 warning(s)\n")
    bd = failed_build(tmp_path, "check_strict", reports={"check_strict.txt": text})
    assert keyset(L.classify(bd)) == [("L2", "ch02", "card-orphan"), ("L3", "ch01", "citation-rot"),
                                      ("L3", "ch03", "citation-rot?")]
    assert all(p["step"] == "check_strict" for p in L.classify(bd))


def test_gate1_report_gives_one_l2_problem_per_card_problem(tmp_path):
    report = {"schema": "klode-kit.check_cards/1", "pass": False, "cards": [
        {"id": "ch01", "pass": True, "problems": []},
        {"id": "ch02", "pass": False, "problems": [{"code": "A2", "message": "anchor not found"},
                                                   {"code": "F1", "message": "aliases"}]}]}
    bd = failed_build(tmp_path, "gate1", reports={"gate1.txt": json.dumps(report)})
    assert keyset(L.classify(bd)) == [("L2", "ch02", "A2"), ("L2", "ch02", "F1")]


def test_a_failure_without_a_report_is_one_card_less_l2_problem(tmp_path):
    bd = failed_build(tmp_path, "gate1", detail="check-cards exit 1")
    (p,) = L.classify(bd)
    assert (p["loop"], p["step"], p["card"]) == ("L2", "gate1", None) and "check-cards exit 1" in p["detail"]
    bd2 = failed_build(tmp_path / "x", "write_card", detail="write-card exit 1")
    assert keyset(L.classify(bd2)) == [("L2", None, "write_card_failed")]


def test_writer_tasks_that_are_not_done_are_l2_problems_of_their_cards(tmp_path):
    run = {"tasks": [{"task_id": "ch01", "status": "done"},
                     {"task_id": "ch02", "status": "failed", "error": "exit 1"},
                     {"task_id": "ch03", "status": "budget", "error": "budget used up"}]}
    bd = failed_build(tmp_path, "dispatch_writers", extra={"receipts/writer_run.json": json.dumps(run)})
    assert keyset(L.classify(bd)) == [("L2", "ch02", "writer_failed"), ("L2", "ch03", "writer_budget")]


def test_desk_verify_holdout_miss_is_l1_with_the_tuning_misses(tmp_path):
    rec = {"status": "fail", "synthesis": {"pass": True}, "strict": {"pass": True, "last_line": ""},
           "diagnostics": {"current": True},
           "tune": {"hit": 2, "total": 3, "threshold": 0.8, "ok": False,
                    "results": [{"q": "q1", "expect": "d1", "got": "d1", "ok": True},
                                {"q": "q2", "expect": "d2", "got": None, "ok": False},
                                {"q": "q3", "expect": "d2", "got": "d2", "ok": True}]},
           "holdout": {"pass": False, "hit": 3, "total": 5, "results": []}}
    bd = failed_build(tmp_path, "desk_verify", extra={"desk_receipt.json": json.dumps(rec)})
    probs = L.classify(bd)
    assert keyset(probs) == [("L1", None, "holdout_below:3/5"), ("L1", None, "tuning_miss")]
    assert {p["dimension"] for p in probs if p["code"] == "tuning_miss"} == {"d2"}


def test_desk_verify_failing_for_another_reason_is_not_l1(tmp_path):
    rec = {"status": "fail", "synthesis": {"pass": False}, "strict": {"pass": True, "last_line": ""},
           "diagnostics": {"current": True}, "tune": {"hit": 0, "total": 0, "threshold": 0.8, "ok": False, "results": []},
           "holdout": {"pass": True, "hit": 5, "total": 5, "results": []}}
    bd = failed_build(tmp_path, "desk_verify", extra={"desk_receipt.json": json.dumps(rec)})
    assert keyset(L.classify(bd)) == [("L2", None, "desk_verify_failed")]


def test_missing_syntheses_name_their_dimensions(tmp_path):
    bd = failed_build(tmp_path, "desk_apply", detail="desk syntheses missing for: construction, safety")
    probs = L.classify(bd)
    assert [(p["loop"], p["code"], p["dimension"]) for p in probs] == [("L2", "synthesis_missing", "construction"),
                                                                       ("L2", "synthesis_missing", "safety")]


@pytest.mark.parametrize("step", ["export", "init_kb", "ingest", "split_manifest", "klode_build"])
def test_failures_before_the_cards_exist_are_not_loopable(tmp_path, step):
    assert L.classify(failed_build(tmp_path, step)) == []


@pytest.mark.parametrize("code", [30, 40, 50, 60, 61])
def test_environment_class_exit_codes_never_classify(tmp_path, code):
    """A build that ended with 30 / 40 / 50 / 60 / 61 has no loop problems whatever step failed."""
    bd = failed_build(tmp_path, "gate1", exit_code=code, reports={"gate1.txt": "{}"})
    assert L.classify(bd) == []


def test_a_passed_build_has_no_problems(tmp_path):
    bd = tmp_path / "b-x"
    bd.mkdir()
    (bd / "build_receipt.json").write_text(json.dumps({"status": "PASS", "steps": [{"step": "export", "status": "PASS"}]}),
                                           encoding="utf-8")
    assert L.classify(bd) == []


# --------------------------------------------------------------------------- plan: order and escalation


def test_l3_comes_before_l2_and_l1_last():
    """09c-05 (S2): L2 and L3 problems at once - the plan starts with L3."""
    problems = [prob(loop="L2", card="ch02"), prob(loop="L1", card=None, code="tuning_miss", dimension="d"),
                prob(loop="L3", step="check_strict", code="citation-rot", card="ch01")]
    acts = L.plan(problems)
    assert [a["loop"] for a in acts] == ["L3", "L2", "L1"]
    assert acts[0]["cards"] == ["ch01"] and acts[1]["cards"] == ["ch02"] and acts[1]["escalated_from_l3"] == []


def test_an_l3_problem_that_was_already_tried_escalates_to_l2():
    """09c-04 (S3): the same card in L3 again after its fix - L2 takes it."""
    problems = [prob(loop="L3", step="check_strict", code="citation-rot", card="ch01")]
    acts = L.plan(problems, l3_tried={"ch01"})
    assert [a["loop"] for a in acts] == ["L2"]
    assert acts[0]["cards"] == ["ch01"] and acts[0]["escalated_from_l3"] == ["ch01"]


def test_card_less_l2_problem_is_a_rebuild_without_cards():
    acts = L.plan([prob(card=None, code="gate1_failed")])
    assert acts == [{"loop": "L2", "cards": [], "escalated_from_l3": []}]


def test_no_problems_no_actions():
    assert L.plan([]) == []
