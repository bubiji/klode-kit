"""WI-02c edge cases (added after the red receipts): the remaining spec / holdout / freeze guards of desk_build,
broken klode answers, and the error exits of check_synthesis, cue_suggest and eval_compare.

Same specification as tests/unit/test_desk_build.py, test_cue_suggest.py and test_eval_compare.py; no law beyond
theirs (guards: each case pins one refusal and its exit code, and that a refusal writes nothing).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from _support.desk_fx import FakeKlode, make_kb, spec_with, synthesis, ANCHORS
from klode_kit.build import check_synthesis as CS
from klode_kit.build import cue_suggest as CU
from klode_kit.build import desk_build as D
from klode_kit.build import eval_compare as E

pytestmark = pytest.mark.unit


@pytest.fixture
def klode(tmp_path, monkeypatch) -> FakeKlode:
    fk = FakeKlode(tmp_path / "fakebin")
    fk.install(monkeypatch)
    return fk


def five(spec: dict) -> list[dict]:
    dims = [d["dimension"] for d in spec["dimensions"]]
    return [{"q": f"holdout question {k}?", "gold": dims[k % len(dims)]} for k in range(5)]


def routed(klode: FakeKlode, spec: dict, holdout: list[dict]) -> None:
    ans = {q: [d["dimension"]] for d in spec["dimensions"] for q in d["tests"]}
    ans.update({h["q"]: [h["gold"]] for h in holdout})
    klode.route(ans)


@pytest.mark.parametrize("mutate", [
    lambda s: {"dimensions": []},
    lambda s: [1, 2],
    lambda s: {**s, "dimensions": ["not an object"]},
    lambda s: {**s, "dimensions": [{**s["dimensions"][0], "title": 3}]},
    lambda s: {**s, "dimensions": [{**s["dimensions"][0], "tests": "one string"}]},
    lambda s: {**s, "dimensions": [{**s["dimensions"][0], "cues": [" "]}]},
    lambda s: {**s, "threshold": 1.5},
    lambda s: {**s, "threshold": True},
])
def test_more_spec_guards(tmp_path, mutate):
    base = spec_with(n_dims=3)
    kb = make_kb(tmp_path / "kb", base)
    (kb / "src" / "desk.json").write_text(json.dumps(mutate(base)), encoding="utf-8")
    before = (kb / "library.toml").read_bytes()
    assert D.main([str(kb), "--apply"]) == 30
    assert (kb / "library.toml").read_bytes() == before


def test_spec_not_json(tmp_path):
    kb = make_kb(tmp_path / "kb")
    (kb / "src" / "desk.json").write_text("{", encoding="utf-8")
    assert D.main([str(kb), "--prompts"]) == 30
    assert not (kb / "_work").exists()


def test_holdout_shape_and_encoding_guards(tmp_path, klode):
    spec = spec_with(n_dims=3)
    for k, raw in enumerate([b'{"q": "x?"}\n', b'["q", "gold"]\n', b'{"q": "  ", "gold": "construction"}\n',
                             b"\xff\xfe not utf-8\n"]):
        kb = make_kb(tmp_path / f"kb{k}", spec, five(spec))
        D.main([str(kb), "--apply"])
        (kb / "src" / "holdout.jsonl").write_bytes(raw)
        assert D.main([str(kb), "--verify"]) == 30, raw
        assert not (kb / "desk_receipt.json").exists()


def test_blank_holdout_lines_ignored(tmp_path, klode):
    spec = spec_with(n_dims=3)
    holdout = five(spec)
    kb = make_kb(tmp_path / "kb", spec, holdout)
    D.main([str(kb), "--apply"])
    p = kb / "src" / "holdout.jsonl"
    p.write_text("\n" + p.read_text(encoding="utf-8").replace("\n", "\n\n"), encoding="utf-8")
    routed(klode, spec, holdout)
    assert D.main([str(kb), "--verify"]) == 0
    assert json.loads((kb / "desk_receipt.json").read_text(encoding="utf-8"))["holdout"]["total"] == 5


def test_damaged_freeze_refused_unless_refreeze(tmp_path, klode, capsys):
    spec = spec_with(n_dims=3)
    holdout = five(spec)
    kb = make_kb(tmp_path / "kb", spec, holdout)
    D.main([str(kb), "--apply"])
    routed(klode, spec, holdout)
    freeze = kb / "src" / "holdout.freeze.json"
    for damaged in ("not json", json.dumps({"schema": "x", "sha256": "0" * 64, "history": []})):
        freeze.write_text(damaged, encoding="utf-8")
        assert D.main([str(kb), "--verify"]) == 30
        assert freeze.read_text(encoding="utf-8") == damaged
    assert D.main([str(kb), "--verify", "--refreeze", "--reason", "restore the freeze record"]) == 0
    doc = json.loads(freeze.read_text(encoding="utf-8"))
    assert doc["history"] == [{"sha256": doc["sha256"], "reason": "restore the freeze record"}]
    r = json.loads((kb / "desk_receipt.json").read_text(encoding="utf-8"))
    assert r["freeze"] == {"sha256": doc["sha256"], "initial": True, "refrozen": True,
                           "reason": "restore the freeze record"}


@pytest.mark.parametrize("stdout", [
    json.dumps({"value": {"not": "a list"}}),
    json.dumps({"value": [[3, "core"]]}),
    json.dumps({"value": [[]]}),
    json.dumps({"no": "value"}),
])
def test_malformed_diagnose_json_exit40(tmp_path, klode, stdout):
    spec = spec_with(n_dims=3)
    kb = make_kb(tmp_path / "kb", spec, five(spec))
    D.main([str(kb), "--apply"])
    klode.set(raw_diagnose_stdout=stdout)
    assert D.main([str(kb), "--verify"]) == 40
    assert not (kb / "desk_receipt.json").exists()
    assert not (kb / "src" / "holdout.freeze.json").exists()


def test_reason_without_refreeze_and_config_error(tmp_path, klode, capsys):
    spec = spec_with(n_dims=3)
    kb = make_kb(tmp_path / "kb", spec, five(spec))
    assert D.main([str(kb), "--verify", "--reason", "why"]) == 2
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "klode-kit.toml").write_text("[desk]\nholdout_min = \"five\"\n", encoding="utf-8")
    assert D.main([str(kb), "--verify", "--project", str(proj)]) == 40
    assert "configuration" in capsys.readouterr().err


def test_check_synthesis_missing_craft_and_help(tmp_path, capsys):
    kb = make_kb(tmp_path / "kb")
    p = kb / "library" / "frameworks" / "_syntheses" / "construction.md"
    p.write_text(synthesis("construction", ["ch01"], ANCHORS["ch01"][:3]).replace("## Craft", "## Body"),
                 encoding="utf-8")
    assert "construction: S3 缺 ## Craft" in CS.check_one(kb, "construction")
    assert CS.main(["--help"]) == 0
    assert "S5" in capsys.readouterr().out


def test_cue_suggest_bad_receipt_exit30(tmp_path, capsys):
    kb = make_kb(tmp_path / "kb", spec_with(n_dims=3))
    bad = tmp_path / "r.json"
    bad.write_text(json.dumps({"tune": {}}), encoding="utf-8")
    assert CU.main([str(kb), "--receipt", str(bad)]) == 30
    assert CU.main([str(kb), "--receipt", str(tmp_path / "absent.json")]) == 30


def test_cue_suggest_card_without_aliases(tmp_path, capsys):
    spec = {"threshold": 0.8, "dimensions": [
        {"dimension": "construction", "title": "C", "question": "Q", "cards": ["ch01"], "cues": ["x1"],
         "tests": ["copper shell question?"]}]}
    kb = make_kb(tmp_path / "kb", spec)
    (kb / "library" / "cards" / "ch01.md").write_text("no front matter\n", encoding="utf-8")
    assert CU.card_aliases(kb, "ch01") == []
    assert CU.card_aliases(kb, "ch99") == []
    assert CU.main([str(kb)]) == 1


def test_eval_compare_usage_and_broken_diagnose(tmp_path, klode, capsys):
    gold = tmp_path / "gold.jsonl"
    gold.write_text(json.dumps({"q": "a", "gold": ["ch01"]}) + "\n", encoding="utf-8")
    assert E.main(["--gold", str(gold), "--kb", "no-equals-sign"]) == 2
    desk = tmp_path / "desk.jsonl"
    desk.write_text(json.dumps({"q": "b", "gold": "safety"}) + "\n", encoding="utf-8")
    klode.set(raw_diagnose_stdout="not json")
    assert E.main(["--gold", str(gold), "--kb", f"demo={tmp_path / 'library.toml'}", "--desk-gold", str(desk)]) == 40
    out = capsys.readouterr()
    assert "| a | — |" not in out.out  # nothing printed when the report cannot be finished
    assert "diagnose" in out.err
