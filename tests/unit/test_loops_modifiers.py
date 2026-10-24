"""WI-09c: the modifiers of the loops and the plumbing around them, unit lane (added while implementing; the
recorded cases are in ``test_loops.py``).

Real commands through the real launcher (``fix-anchors``, ``cue-suggest`` need no ``klode``), hand-made build
directories, and the bridge / build option guards that carry the run budget and the repair spec.
"""

from __future__ import annotations

import copy
import io
import json
import os
from pathlib import Path

import pytest

from _support import build_fx as B
from _support import desk_fx as DX
from _support.flows import base_env
from klode_kit import loops as L
from klode_kit.bridge import BridgeUsageError, Options
from klode_kit.build import run_build as RB

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- L3: fix-anchors on a copy


def kb_with_card(root: Path, anchor_in_card: str, source: str) -> Path:
    (root / "cards_content").mkdir(parents=True)
    (root / "library" / "book").mkdir(parents=True)
    (root / "library" / "book" / "ch01.txt").write_text(source, encoding="utf-8")
    (root / "cards_content" / "ch01.md").write_text(
        f"aliases: [a]\n## Thin\nx (grep: `{anchor_in_card}`).\n## Full\n### P\n- p (grep: `{anchor_in_card}`)\n",
        encoding="utf-8")
    return root


def test_l3_fixes_a_copy_and_leaves_the_build_untouched(tmp_path):
    """The typographic slip is repaired in ``r01/fixed/ch01.md``; the build's own card stays byte for byte."""
    build = kb_with_card(tmp_path / "b-1", "the kettle's copper shell is thin", "the kettle’s copper shell is thin and long")
    before = (build / "cards_content" / "ch01.md").read_bytes()
    state = L._State(None, None)
    rec = L._run_l3(tmp_path, tmp_path / "r01", build, ["ch01"], state, None, base_env(), io.StringIO())
    assert (rec["loop"], rec["step"], rec["exit_code"], rec["fixed"], rec["left"]) == ("L3", "fix_anchors", 0, ["ch01"], [])
    fixed = (tmp_path / "r01" / "fixed" / "ch01.md").read_text(encoding="utf-8")
    assert "kettle’s copper shell" in fixed and "kettle's" not in fixed
    assert (build / "cards_content" / "ch01.md").read_bytes() == before
    assert state.fixed == {"ch01": "r01/fixed/ch01.md"} and state.l3_tried == {"ch01"}


def test_l3_reports_an_anchor_it_cannot_fix_as_left(tmp_path):
    build = kb_with_card(tmp_path / "b-1", "nothing like this is in the source", "the kettle is copper")
    state = L._State(None, None)
    rec = L._run_l3(tmp_path, tmp_path / "r01", build, ["ch01"], state, None, base_env(), io.StringIO())
    assert (rec["exit_code"], rec["fixed"], rec["left"]) == (1, [], ["ch01"])
    assert state.fixed == {} and not (tmp_path / "r01" / "fixed").exists()


# --------------------------------------------------------------------------- L1: cue-suggest on a copy of the desk


def desk_kb(root: Path, *, miss: bool = True) -> Path:
    spec = copy.deepcopy(B.DESK_SPEC)
    dim = next(d for d in spec["dimensions"] if d["dimension"] == "whistle-valve")
    dim["cues"] = ["zzz-dummy"]
    dim["tests"] = ["How does the valve cap and the reed give the whistle valve a two-tone note?"]
    kb = DX.make_kb(root, spec, B.DESK_HOLDOUT)
    results = [{"q": dim["tests"][0], "expect": "whistle-valve", "got": None, "ok": not miss}]
    (kb / "desk_receipt.json").write_text(json.dumps({"tune": {"hit": 0, "total": 1, "results": results}}),
                                          encoding="utf-8")
    return kb


def test_l1_appends_the_cues_to_a_copy_and_hands_the_unread_holdout_on(tmp_path):
    kb = desk_kb(tmp_path / "b-1")
    original = (kb / "src" / "desk.json").read_bytes()
    state = L._State(None, None)
    rec = L._run_l1(tmp_path, tmp_path / "r01", kb, state, None, base_env(), io.StringIO())
    assert rec["step"] == "cue_suggest" and rec["exit_code"] == 0
    assert {"reed", "valve cap", "whistle valve"} <= set(rec["suggestions"]["whistle-valve"])
    assert (kb / "src" / "desk.json").read_bytes() == original
    new = json.loads((tmp_path / "r01" / "desk.json").read_text(encoding="utf-8"))
    cues = next(d for d in new["dimensions"] if d["dimension"] == "whistle-valve")["cues"]
    assert cues[0] == "zzz-dummy" and "reed" in cues
    assert state.desk == tmp_path / "r01" / "desk.json" and state.holdout == kb / "src" / "holdout.jsonl"


def test_l1_without_a_suggestion_changes_nothing(tmp_path):
    """No tuning question missed -> cue-suggest exits 1: no new desk is handed on (the no-progress signal)."""
    kb = desk_kb(tmp_path / "b-1", miss=False)
    state = L._State(None, None)
    rec = L._run_l1(tmp_path, tmp_path / "r01", kb, state, None, base_env(), io.StringIO())
    assert (rec["exit_code"], rec["suggestions"]) == (1, {})
    assert state.desk is None and state.holdout is None


def test_l1_without_a_desk_spec_is_a_usage_failure_not_a_crash(tmp_path):
    (tmp_path / "b-1").mkdir()
    rec = L._run_l1(tmp_path, tmp_path / "r01", tmp_path / "b-1", L._State(None, None), None, base_env(), io.StringIO())
    assert rec["exit_code"] == 2 and rec["suggestions"] == {}


def test_invalidated_dimensions_are_those_citing_a_redone_card(tmp_path):
    kb = DX.make_kb(tmp_path / "b-1", copy.deepcopy(B.DESK_SPEC), B.DESK_HOLDOUT)
    state = L._State(None, None)
    assert L._invalidated(state, kb, ["ch02"]) == ["whistle-valve"]
    assert L._invalidated(state, kb, ["ch01", "ch03"]) == ["construction", "safety"]
    assert L._invalidated(state, kb, []) == [] and L._invalidated(state, tmp_path, ["ch02"]) == []


# --------------------------------------------------------------------------- build: carry / overlay / guards


def bare_build(tmp: Path, repair: dict) -> RB.Build:
    """A ``Build`` with just what the repair helpers read: project, build dir, options."""
    b = object.__new__(RB.Build)
    b.project, b.dir = tmp, tmp / "kb" / "x" / "builds" / "b-new"
    b.opts = RB.Options(repair_doc=repair)
    (tmp / "kb" / "x" / "builds" / "b-old" / "cards_content").mkdir(parents=True, exist_ok=True)
    b.dir.mkdir(parents=True, exist_ok=True)
    return b


def test_carry_copies_only_the_cards_that_are_not_redispatched(tmp_path):
    old = tmp_path / "kb" / "x" / "builds" / "b-old" / "cards_content"
    old.mkdir(parents=True)
    for cid in ("ch01", "ch02"):
        (old / f"{cid}.md").write_text(f"old {cid}", encoding="utf-8")
    b = bare_build(tmp_path, {"from_build": "kb/x/builds/b-old"})
    assert b._carry_cards(["ch01", "ch02", "ch03"], {"ch02": ["p"]}) == ["ch01"]  # ch03 has no card to carry
    assert (b.dir / "cards_content" / "ch01.md").read_text(encoding="utf-8") == "old ch01"
    assert not (b.dir / "cards_content" / "ch02.md").exists()
    b._place_previous_card("ch02")
    assert (b.dir / "cards_content" / "ch02.md").read_text(encoding="utf-8") == "old ch02"
    b._place_previous_card("ch03")  # nothing to place
    assert not (b.dir / "cards_content" / "ch03.md").exists()


def test_overlay_puts_the_fixed_cards_over_the_generated_ones(tmp_path):
    (tmp_path / "r01" / "fixed").mkdir(parents=True)
    (tmp_path / "r01" / "fixed" / "ch01.md").write_text("fixed", encoding="utf-8")
    b = bare_build(tmp_path, {"from_build": "kb/x/builds/b-old",
                              "fixed": {"ch01": "r01/fixed/ch01.md", "ch09": "r01/fixed/missing.md"}})
    (b.dir / "cards_content").mkdir()
    (b.dir / "cards_content" / "ch01.md").write_text("generated", encoding="utf-8")
    b._overlay_fixed()
    assert (b.dir / "cards_content" / "ch01.md").read_text(encoding="utf-8") == "fixed"
    assert not (b.dir / "cards_content" / "ch09.md").exists()
    assert bare_build(tmp_path / "other", {})._from_build() is None


def run_build_usage(tmp: Path, *args: str) -> tuple[int, dict]:
    out = io.StringIO()
    code = RB.run(tmp, stdout=out, out=io.StringIO(), **dict(zip(args[::2], args[1::2])))
    return code, json.loads(out.getvalue().splitlines()[-1])


@pytest.mark.parametrize("doc", ["not json", json.dumps([1]), json.dumps({"schema": "other/1", "from_build": "x"}),
                                 json.dumps({"schema": RB.REPAIR_SCHEMA})])
def test_a_bad_repair_spec_is_a_usage_error(tmp_path, doc):
    spec = tmp_path / "repair.json"
    spec.write_text(doc, encoding="utf-8")
    code, summary = run_build_usage(tmp_path, "repair", str(spec))
    assert (code, summary["reason"]) == (2, "usage") and "--repair" in summary["detail"]
    code, summary = run_build_usage(tmp_path, "repair", str(tmp_path / "missing.json"))
    assert (code, summary["reason"]) == (2, "usage")


@pytest.mark.parametrize("budget", [0, -1.0, float("inf"), float("nan"), True, "2"])
def test_a_bad_run_budget_is_a_usage_error(tmp_path, budget):
    code, summary = run_build_usage(tmp_path, "budget_usd", budget)
    assert (code, summary["reason"]) == (2, "usage") and "--budget-usd" in summary["detail"]


def test_loops_guards_come_before_any_build(tmp_path):
    out = io.StringIO()
    assert L.run(tmp_path / "missing", stdout=out, out=io.StringIO()) == 2  # the build's own usage error
    assert json.loads(out.getvalue().splitlines()[-1])["reason"] == "usage"
    out = io.StringIO()
    assert L.run(tmp_path, budget_usd=0, stdout=out, out=io.StringIO()) == 2
    assert json.loads(out.getvalue().splitlines()[-1])["detail"].startswith("limit_usd")
    (tmp_path / "klode-kit.toml").write_text("[loops]\nmax_total_rounds = 0\n", encoding="utf-8")
    out = io.StringIO()
    assert L.run(tmp_path, stdout=out, out=io.StringIO()) == 40
    assert json.loads(out.getvalue().splitlines()[-1])["reason"] == "config"
    assert not (tmp_path / "kb").exists()  # no build, no loop directory


# --------------------------------------------------------------------------- bridge options


@pytest.mark.parametrize("field,value", [("budget_usd", 0), ("budget_usd", -1), ("budget_usd", float("inf")),
                                         ("reserve_usd", 0), ("reserve_usd", True), ("reserve_usd", "1")])
def test_bridge_budget_options_must_be_positive_numbers(field, value):
    with pytest.raises(BridgeUsageError):
        Options(concurrency=1, timeout_s=1, max_rounds=1, **{field: value})


def test_bridge_budget_is_optional_and_off_by_default():
    o = Options(concurrency=1, timeout_s=1, max_rounds=1)
    assert o.budget_usd is None and o.reserve_usd == 1.0
    assert Options(concurrency=1, timeout_s=1, max_rounds=1, budget_usd=2).budget_usd == 2


def test_desk_scores_and_round_cost_tolerate_missing_files(tmp_path):
    assert L.desk_scores(tmp_path) is None
    (tmp_path / "desk_receipt.json").write_text(json.dumps({"tune": {}}), encoding="utf-8")
    assert L.desk_scores(tmp_path) is None
    assert L._round_cost(None) == 0 and L._round_cost(tmp_path) == 0
    (tmp_path / "receipts").mkdir()
    (tmp_path / "receipts" / "dispatch_writers.json").write_text(json.dumps({"cost_usd": 0.25}), encoding="utf-8")
    (tmp_path / "receipts" / "writer_run.json").write_text(json.dumps({"cost_usd": 9}), encoding="utf-8")  # not a step
    assert float(L._round_cost(tmp_path)) == 0.25
