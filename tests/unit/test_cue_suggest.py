"""WI-02c: ``build/cue_suggest`` - the L1 routing-loop modifier (matrix 02c-06; new, 02 r2.8 L1 row).

Specification (02 r2.8 L1: "deterministic: question tokens ∩ card aliases; reads the tuning set only, holdout
misses are reported, never fixed"; klode-kb SKILL.md iron law 2):

- Input: ``src/desk.json`` (``--spec`` overrides), the aliases of each dimension's cards (``library/cards/<id>.md``
  front-matter ``aliases: [...]``) and optionally the desk verify receipt (``--receipt``): then only the tuning
  questions it marks as missed are considered, and receipt entries whose question is not a tuning question of the
  spec are ignored. Without a receipt every tuning question is considered.
- For a tuning question of dimension D, a cue is suggested for D when it is an alias of one of D's cards that
  occurs in the question (lower-case substring, as klode's diagnose matches cues), is at least 2 characters, holds
  no ``,`` or ``|``, is not already a cue of any dimension, and does not occur in a tuning question of another
  dimension (it would pull that one away). Suggestions come only from card aliases, never from question text.
- Output ``{"schema": "klode-kit.cue_suggest/1", "suggestions": {dim: [cue, ...]}, "unresolved": [q, ...]}``,
  printed as JSON; order = spec dimension order, then card order, then alias order. ``--write`` appends the
  suggestions to the spec's cues. Exit 0 when something is suggested, 1 when nothing is (no progress), 2 usage,
  30 spec contract.
- **The holdout set is never read**: ``src/holdout.jsonl`` is not opened, and holdout results in the receipt do
  not count.

Law (S6, metamorphic): any change to the holdout file - and to the holdout part of the receipt - leaves the output
byte-identical. Writing the suggestions and suggesting again yields nothing new (S5).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from _support.desk_fx import make_kb
from klode_kit.build import cue_suggest as CU

pytestmark = pytest.mark.unit


def spec() -> dict:
    return {"threshold": 0.8, "dimensions": [
        {"dimension": "construction", "title": "C", "question": "Q", "cards": ["ch01"], "cues": ["copper shell"],
         "tests": ["What does the tinning protect, and when is retinning due?", "Why is a brass rim folded over?"]},
        {"dimension": "whistle", "title": "W", "question": "Q", "cards": ["ch02"], "cues": ["whistle"],
         "tests": ["How is the valve cap opened while pouring?", "Does the reed change pitch with scale?"]},
        {"dimension": "night-market-care", "title": "N", "question": "Q", "cards": ["ch03"], "cues": ["night market"],
         "tests": ["What goes in the covered jug, and does the spout matter?"]},
    ]}


@pytest.fixture
def kb(tmp_path) -> Path:
    return make_kb(tmp_path / "kb", spec())


def run(kb: Path, *extra: str, capsys) -> tuple[int, dict]:
    rc = CU.main([str(kb), *extra])
    return rc, json.loads(capsys.readouterr().out)


def test_suggests_card_aliases_found_in_tuning_questions(kb, capsys):
    rc, out = run(kb, capsys=capsys)
    assert rc == 0
    assert out["schema"] == "klode-kit.cue_suggest/1"
    # "spout" is a ch02 alias that occurs in a night-market-care question: never suggested for whistle;
    # for night-market-care only its own card's aliases count ("covered jug").
    assert out["suggestions"] == {
        "construction": ["brass rim", "tinning", "retinning"],
        "whistle": ["reed", "valve cap", "pitch"],
        "night-market-care": ["covered jug"],
    }
    assert out["unresolved"] == []


def test_existing_cue_and_conflicting_alias_skipped(tmp_path, capsys):
    s = spec()
    s["dimensions"][1]["cues"].append("Brass Rim")          # already a cue (case-insensitive), of another dimension
    s["dimensions"][1]["tests"].append("Is tinning used near the whistle?")  # "tinning" would pull this away
    kb = make_kb(tmp_path / "kb", s)
    rc, out = run(kb, capsys=capsys)
    assert rc == 0
    assert out["suggestions"]["construction"] == ["retinning"]
    assert "Is tinning used near the whistle?" in out["unresolved"]


def test_receipt_restricts_to_missed_tuning_questions(kb, tmp_path, capsys):
    s = spec()
    rec = {"schema": "klode-kit.desk_verify/1",
           "tune": {"results": [
               {"q": s["dimensions"][0]["tests"][1], "expect": "construction", "got": None, "ok": False},
               {"q": s["dimensions"][0]["tests"][0], "expect": "construction", "got": "construction", "ok": True},
               {"q": "Is the valve cap a holdout question smuggled in?", "expect": "whistle", "got": None, "ok": False},
           ]},
           "holdout": {"results": [{"q": "Does the reed need a vinegar soak?", "expect": "whistle", "got": None,
                                    "ok": False}]}}
    p = tmp_path / "desk_receipt.json"
    p.write_text(json.dumps(rec), encoding="utf-8")
    rc, out = run(kb, "--receipt", str(p), capsys=capsys)
    assert rc == 0
    assert out["suggestions"] == {"construction": ["brass rim"]}


def test_nothing_to_suggest_exit1_and_write_converges(kb, capsys):
    rc, first = run(kb, "--write", capsys=capsys)
    assert rc == 0
    cues = {d["dimension"]: d["cues"] for d in json.loads((kb / "src" / "desk.json").read_text("utf-8"))["dimensions"]}
    assert cues["construction"] == ["copper shell", "brass rim", "tinning", "retinning"]
    rc, second = run(kb, capsys=capsys)
    assert rc == 1 and second["suggestions"] == {}


def test_unresolved_lists_misses_without_alias(tmp_path, capsys):
    s = spec()
    s["dimensions"][0]["tests"] = ["Why does it last so long?"]
    s["dimensions"][1]["tests"] = []
    s["dimensions"][2]["tests"] = []
    kb = make_kb(tmp_path / "kb", s)
    rc, out = run(kb, capsys=capsys)
    assert rc == 1
    assert out == {"schema": "klode-kit.cue_suggest/1", "suggestions": {}, "unresolved": ["Why does it last so long?"]}


def test_spec_errors(tmp_path, capsys):
    kb = make_kb(tmp_path / "kb", spec())
    (kb / "src" / "desk.json").write_text("{", encoding="utf-8")
    assert CU.main([str(kb)]) == 30
    assert CU.main([]) == 2


# --------------------------------------------------------------------------- 02c-06  metamorphic: holdout-blind


HOLDOUT_TEXT = st.one_of(
    st.text(max_size=400),
    st.lists(st.fixed_dictionaries({"q": st.sampled_from(
        ["Does the reed need a vinegar soak?", "What is the spring steel for?", "Where is the ember bed?",
         "domed base hammered copper kettle body", "anything at all"]),
        "gold": st.sampled_from(["construction", "whistle", "night-market-care", "nope"])}), max_size=8)
    .map(lambda xs: "".join(json.dumps(x) + "\n" for x in xs)),
)


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(holdout=HOLDOUT_TEXT, receipt_holdout=st.lists(st.dictionaries(st.text(max_size=5), st.text(max_size=20)),
                                                      max_size=4))
def test_cue_suggest_ignores_holdout(tmp_path_factory, holdout, receipt_holdout):
    kb = make_kb(tmp_path_factory.mktemp("kb"), spec())
    rec = kb / "desk_receipt.json"
    base_receipt = {"tune": {"results": [{"q": t, "expect": d["dimension"], "got": None, "ok": False}
                                         for d in spec()["dimensions"] for t in d["tests"]]},
                    "holdout": {"results": []}}
    rec.write_text(json.dumps(base_receipt), encoding="utf-8")
    before = CU.suggest(kb, receipt=rec)
    (kb / "src" / "holdout.jsonl").write_text(holdout, encoding="utf-8")
    rec.write_text(json.dumps({**base_receipt, "holdout": {"results": receipt_holdout}}), encoding="utf-8")
    after = CU.suggest(kb, receipt=rec)
    assert json.dumps(after, ensure_ascii=False) == json.dumps(before, ensure_ascii=False)
    (kb / "src" / "holdout.jsonl").rename(kb / "moved-holdout.jsonl")  # no holdout file at all: same output
    assert CU.suggest(kb, receipt=rec) == before
