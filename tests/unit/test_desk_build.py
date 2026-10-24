"""WI-02c: ``build/desk_build`` - the desk (matrix 02c-03/04/05/07; B3 at unit level; the two iron laws).

Specification (03 A7/A8/B3/G5; 02 r2.1 row 1.3, r2.6 ``desk.holdout_min/holdout_pass``, r2.8 L1; klode-kb
SKILL.md desk steps ②-⑤ and the two iron laws; vendored from the user's klode-build ``desk_build.py``):

- Kept: ``src/desk.json`` is the single source of truth (``threshold``; per dimension ``dimension / title /
  question / cards / cues / tests``; every listed card must exist). ``--apply`` turns ``[frameworks] enabled`` on
  and **generates** ``library/frameworks/_syntheses/_diagnostics.md`` from the spec (header + one ``| cues | dim |``
  row per dimension). ``--prompts`` writes one worker prompt per dimension to ``_work/desk_prompts/<dim>.txt``.
  ``--status`` lists which dimension files exist (exit 1 when any is missing). ``--verify`` = check_synthesis all
  PASS + ``klode check --strict`` "0 errors" + diagnose routing.
- **B3**: routing is read from ``klode --json -c <toml> diagnose <q>`` (``value[0][0]``), so a dimension without
  a hyphen (``safety``) is judged right and a hyphenated word in a core question is never mistaken for one.
- **1.3**: the tuning set (``desk.json`` ``tests``) and the frozen holdout set (``src/holdout.jsonl``, one
  ``{"q", "gold": <dimension>}`` per line; ``--holdout`` overrides) are scored separately and printed on two lines
  (``diagnose 调优集：h/t ...`` and ``diagnose 留出集：h/t ...``). The gate is the holdout: at least
  ``desk.holdout_min`` (5) questions and ``hit/total >= desk.holdout_pass`` (0.8). The tuning score is reported only.
- The holdout sha256 is frozen in ``src/holdout.freeze.json`` on the first ``--verify``; a later change without
  ``--refreeze --reason R`` is refused (exit 30, freeze unchanged); with them it is accepted and the reason is
  recorded in the freeze history and in the verify receipt (``<kb>/desk_receipt.json``, ``--receipt`` overrides,
  ``klode-kit.desk_verify/1``, no wall-clock fields). ``--refreeze`` without ``--reason`` is a usage error (2).
- Iron law 1: no desk_build command writes a dimension's synthesis file (only the worker does, before handback).
  Iron law 2: ``_diagnostics.md`` is generated, never hand-edited - ``--verify`` fails when it differs from what
  the spec renders.
- Exit codes: 0 pass; 1 gate fail; 2 usage; 30 spec/holdout contract (missing field or card, bad dimension name or
  cue, malformed / duplicate / unknown-dimension / tuning-leaked holdout line, unfrozen holdout change); 40 klode
  missing or not answering JSON.

Law: the holdout gate is a function of (hit, total) only - pass iff total >= min and hit/total >= pass_ratio
(S4 over generated pairs); every desk command leaves dimension synthesis files byte-identical (S5).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from _support.desk_fx import FakeKlode, aurora_holdout, aurora_spec, make_kb, spec_with, write_holdout
from klode_kit.build import desk_build as D

pytestmark = pytest.mark.unit


@pytest.fixture
def klode(tmp_path, monkeypatch) -> FakeKlode:
    fk = FakeKlode(tmp_path / "fakebin")
    fk.install(monkeypatch)
    return fk


def must(p: Path) -> Path:
    assert p.is_file(), f"expected {p.name} to exist"
    return p


def receipt(kb: Path) -> dict:
    return json.loads(must(kb / "desk_receipt.json").read_text(encoding="utf-8"))


def score_lines(out: str) -> tuple[list[str], list[str]]:
    lines = out.splitlines()
    return ([x for x in lines if x.startswith("diagnose 调优集：")], [x for x in lines if x.startswith("diagnose 留出集：")])


def five_holdout(spec: dict) -> list[dict]:
    dims = [d["dimension"] for d in spec["dimensions"]]
    return [{"q": f"holdout question {k}?", "gold": dims[k % len(dims)]} for k in range(5)]


def route_all(klode: FakeKlode, spec: dict, holdout: list[dict], wrong_holdout: int = 0, wrong_tune: int = 0) -> None:
    """Answer every tuning question correctly except the first ``wrong_tune``; same for the holdout."""
    dims = [d["dimension"] for d in spec["dimensions"]]
    answers: dict = {}
    tune = [(q, d["dimension"]) for d in spec["dimensions"] for q in d["tests"]]
    for k, (q, gold) in enumerate(tune):
        answers[q] = [x for x in dims if x != gold][:1] if k < wrong_tune else [gold]
    for k, h in enumerate(holdout):
        answers[h["q"]] = None if k < wrong_holdout else [h["gold"]]
    klode.route(answers)


# --------------------------------------------------------------------------- 02c-07  two score lines


def test_separate_scores_printed(tmp_path, klode, capsys):
    """fx-book-aurora: 3 tuning questions right; holdout 4/5 (the darkened-shell question goes to construction)."""
    spec, holdout = aurora_spec(), aurora_holdout()
    kb = make_kb(tmp_path / "kb", spec, holdout)
    assert D.main([str(kb), "--apply"]) == 0
    route_all(klode, spec, holdout)
    klode.route({"How is a darkened copper shell restored?": ["construction", "night-market-care"]})
    capsys.readouterr()
    assert D.main([str(kb), "--verify"]) == 0
    out = capsys.readouterr().out
    tune, hold = score_lines(out)
    assert len(tune) == 1 and "3/3" in tune[0]
    assert len(hold) == 1 and "4/5" in hold[0]
    assert "书桌验收：通过" in out
    r = receipt(kb)
    assert (r["tune"]["hit"], r["tune"]["total"], r["holdout"]["hit"], r["holdout"]["total"]) == (3, 3, 4, 5)
    miss = [x for x in r["holdout"]["results"] if not x["ok"]]
    assert miss == [{"q": "How is a darkened copper shell restored?", "expect": "night-market-care",
                     "got": "construction", "ok": False}]


# --------------------------------------------------------------------------- 02c-03  tuning perfect, holdout fails


def test_tune_pass_holdout_fail(tmp_path, klode, capsys):
    spec = spec_with(n_dims=5, tests_per_dim=1)
    holdout = five_holdout(spec)
    kb = make_kb(tmp_path / "kb", spec, holdout)
    D.main([str(kb), "--apply"])
    route_all(klode, spec, holdout, wrong_holdout=2)
    capsys.readouterr()
    assert D.main([str(kb), "--verify"]) == 1
    out = capsys.readouterr().out
    tune, hold = score_lines(out)
    assert "5/5" in tune[0] and "3/5" in hold[0]
    assert "书桌验收：未通过" in out
    r = receipt(kb)
    assert r["status"] == "fail"
    assert r["tune"]["ok"] is True and r["holdout"]["pass"] is False


# --------------------------------------------------------------------------- 02c-04  holdout threshold boundary


@pytest.mark.parametrize(("n_holdout", "wrong", "rc"), [(5, 1, 0), (5, 2, 1), (4, 0, 1), (6, 1, 0), (6, 2, 1)])
def test_holdout_threshold_boundary(tmp_path, klode, capsys, n_holdout, wrong, rc):
    """4/5 PASS, 3/5 FAIL, 4 questions all right FAIL (fewer than 5); 5/6 PASS, 4/6 FAIL."""
    spec = spec_with(n_dims=3)
    dims = [d["dimension"] for d in spec["dimensions"]]
    holdout = [{"q": f"holdout question {k}?", "gold": dims[k % 3]} for k in range(n_holdout)]
    kb = make_kb(tmp_path / "kb", spec, holdout)
    D.main([str(kb), "--apply"])
    route_all(klode, spec, holdout, wrong_holdout=wrong)
    capsys.readouterr()
    assert D.main([str(kb), "--verify"]) == rc
    out = capsys.readouterr().out
    assert f"{n_holdout - wrong}/{n_holdout}" in score_lines(out)[1][0]
    if n_holdout < 5:
        assert "题数不足" in out
    assert receipt(kb)["holdout"]["pass"] is (rc == 0)


def test_holdout_missing_file_fails(tmp_path, klode, capsys):
    spec = spec_with(n_dims=3)
    kb = make_kb(tmp_path / "kb", spec, five_holdout(spec))
    D.main([str(kb), "--apply"])
    route_all(klode, spec, [])
    (kb / "src" / "holdout.jsonl").rename(tmp_path / "moved-holdout.jsonl")
    capsys.readouterr()
    assert D.main([str(kb), "--verify"]) == 1
    out = capsys.readouterr().out
    assert "0/0" in score_lines(out)[1][0] and "题数不足" in out


def test_tuning_score_is_reported_not_gated(tmp_path, klode, capsys):
    """1.3: the tuning set below its threshold is reported (``tune.ok`` false) but the holdout decides."""
    spec = spec_with(n_dims=3)
    holdout = five_holdout(spec)
    kb = make_kb(tmp_path / "kb", spec, holdout)
    D.main([str(kb), "--apply"])
    route_all(klode, spec, holdout, wrong_tune=2)
    capsys.readouterr()
    assert D.main([str(kb), "--verify"]) == 0
    assert "1/3" in score_lines(capsys.readouterr().out)[0][0]
    assert receipt(kb)["tune"]["ok"] is False


@settings(max_examples=200, deadline=None)
@given(total=st.integers(min_value=0, max_value=40), data=st.data())
def test_holdout_gate_law(total, data):
    """S4: pass iff total >= 5 and hit/total >= 0.8; one more hit never turns PASS into FAIL."""
    hit = data.draw(st.integers(min_value=0, max_value=total))
    ok = D.holdout_gate(hit, total, holdout_min=5, pass_ratio=0.8)
    assert ok is (total >= 5 and hit * 5 >= total * 4)
    if ok and hit < total:
        assert D.holdout_gate(hit + 1, total, holdout_min=5, pass_ratio=0.8)


# --------------------------------------------------------------------------- 02c-05  frozen holdout sha


def test_holdout_frozen_sha(tmp_path, klode, capsys):
    spec = spec_with(n_dims=3)
    holdout = five_holdout(spec)
    kb = make_kb(tmp_path / "kb", spec, holdout)
    D.main([str(kb), "--apply"])
    route_all(klode, spec, holdout)
    assert D.main([str(kb), "--verify"]) == 0
    freeze = kb / "src" / "holdout.freeze.json"
    frozen = must(freeze).read_bytes()
    first = receipt(kb)
    assert first["freeze"]["initial"] is True
    assert first["freeze"]["sha256"] == first["holdout"]["sha256"]

    changed = holdout[:4] + [{"q": "a reworded holdout question?", "gold": holdout[4]["gold"]}]
    write_holdout(kb, changed)
    route_all(klode, spec, changed)
    receipt_before = must(kb / "desk_receipt.json").read_bytes()
    capsys.readouterr()
    assert D.main([str(kb), "--verify"]) == 30
    err = capsys.readouterr().err
    assert "--refreeze" in err
    assert freeze.read_bytes() == frozen
    assert (kb / "desk_receipt.json").read_bytes() == receipt_before

    assert D.main([str(kb), "--verify", "--refreeze"]) == 2  # a reason is mandatory
    assert freeze.read_bytes() == frozen

    assert D.main([str(kb), "--verify", "--refreeze", "--reason", "typo in question 5"]) == 0
    r = receipt(kb)
    assert r["freeze"]["refrozen"] is True and r["freeze"]["reason"] == "typo in question 5"
    doc = json.loads(freeze.read_text(encoding="utf-8"))
    assert doc["sha256"] == r["holdout"]["sha256"] != first["holdout"]["sha256"]
    assert doc["history"][-1]["reason"] == "typo in question 5"

    capsys.readouterr()
    assert D.main([str(kb), "--verify"]) == 0  # the new sha is now the frozen one
    assert receipt(kb)["freeze"]["refrozen"] is False


# --------------------------------------------------------------------------- B3 at unit level


def test_b3_dimension_without_hyphen_judged_from_json(tmp_path, klode, capsys):
    """The prose output names ``safety`` and then a core question full of hyphenated words; JSON decides."""
    spec = spec_with(n_dims=3, dim_names=["construction", "whistle-valve", "safety"])
    holdout = five_holdout(spec)
    kb = make_kb(tmp_path / "kb", spec, holdout)
    D.main([str(kb), "--apply"])
    route_all(klode, spec, holdout)
    capsys.readouterr()
    assert D.main([str(kb), "--verify"]) == 0
    r = receipt(kb)
    safety = [x for x in r["tune"]["results"] + r["holdout"]["results"] if x["expect"] == "safety"]
    assert safety and all(x["got"] == "safety" and x["ok"] for x in safety)
    assert D.diagnose_top("klode", kb / "library.toml", "unrouted question") is None


def test_klode_missing_exit40(tmp_path, monkeypatch, capsys):
    spec = spec_with(n_dims=3)
    kb = make_kb(tmp_path / "kb", spec, five_holdout(spec), applied=True)
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    assert D.main([str(kb), "--verify"]) == 40
    assert "klode" in capsys.readouterr().err


def test_klode_non_json_exit40(tmp_path, klode, capsys):
    spec = spec_with(n_dims=3)
    kb = make_kb(tmp_path / "kb", spec, five_holdout(spec))
    D.main([str(kb), "--apply"])
    klode.set(raw_diagnose_stdout="Traceback (most recent call last):\n  boom\n", raw_diagnose_rc=1)
    capsys.readouterr()
    assert D.main([str(kb), "--verify"]) == 40


def test_strict_errors_fail(tmp_path, klode, capsys):
    spec = spec_with(n_dims=3)
    holdout = five_holdout(spec)
    kb = make_kb(tmp_path / "kb", spec, holdout)
    D.main([str(kb), "--apply"])
    route_all(klode, spec, holdout)
    klode.set(strict_stdout="library check — 3 cards\n  ERROR [F] citation-rot\n\nFAIL: 1 error(s), 0 warning(s)",
              strict_rc=1)
    capsys.readouterr()
    assert D.main([str(kb), "--verify"]) == 1
    r = receipt(kb)
    assert r["strict"]["pass"] is False and r["status"] == "fail"


def test_synthesis_failure_fails_verify(tmp_path, klode, capsys):
    spec = spec_with(n_dims=3)
    holdout = five_holdout(spec)
    kb = make_kb(tmp_path / "kb", spec, holdout)
    D.main([str(kb), "--apply"])
    route_all(klode, spec, holdout)
    syn = kb / "library" / "frameworks" / "_syntheses" / "whistle.md"
    syn.write_text(syn.read_text(encoding="utf-8").replace("## Provenance", "## 来源"), encoding="utf-8")
    capsys.readouterr()
    assert D.main([str(kb), "--verify"]) == 1
    assert receipt(kb)["synthesis"]["pass"] is False


# --------------------------------------------------------------------------- apply / diagnostics (iron law 2)


def test_apply_enables_frameworks_and_generates_diagnostics(tmp_path, capsys):
    spec = aurora_spec()
    kb = make_kb(tmp_path / "kb", spec)
    assert D.main([str(kb), "--apply"]) == 0
    toml = (kb / "library.toml").read_text(encoding="utf-8")
    assert "[frameworks]              # optional per-dimension distillation layer\nenabled = true\n" in toml
    assert toml.count("enabled = true") == 2  # bibliography's untouched, frameworks turned on
    diag = (kb / "library" / "frameworks" / "_syntheses" / "_diagnostics.md").read_text(encoding="utf-8")
    rows = [ln for ln in diag.splitlines() if ln.startswith("| ") and not ln.startswith("| symptom")]
    assert rows == [f"| {', '.join(d['cues'])} | {d['dimension']} |" for d in spec["dimensions"]]
    assert diag == D.render_diagnostics(spec)
    assert "不要手改" in diag


def test_apply_idempotent(tmp_path):
    kb = make_kb(tmp_path / "kb")
    D.main([str(kb), "--apply"])
    snap = {p: p.read_bytes() for p in kb.rglob("*") if p.is_file()}
    assert D.main([str(kb), "--apply"]) == 0
    assert {p: p.read_bytes() for p in kb.rglob("*") if p.is_file()} == snap


def test_hand_edited_diagnostics_fails_verify_and_apply_restores(tmp_path, klode, capsys):
    spec = spec_with(n_dims=3)
    holdout = five_holdout(spec)
    kb = make_kb(tmp_path / "kb", spec, holdout)
    D.main([str(kb), "--apply"])
    route_all(klode, spec, holdout)
    diag = kb / "library" / "frameworks" / "_syntheses" / "_diagnostics.md"
    diag.write_text(diag.read_text(encoding="utf-8") + "| holdout question 0 | construction |\n", encoding="utf-8")
    capsys.readouterr()
    assert D.main([str(kb), "--verify"]) == 1
    assert "_diagnostics.md" in capsys.readouterr().out
    assert receipt(kb)["diagnostics"]["current"] is False
    assert D.main([str(kb), "--apply"]) == 0
    assert diag.read_text(encoding="utf-8") == D.render_diagnostics(spec)


def test_apply_without_frameworks_section_exit40(tmp_path, capsys):
    kb = make_kb(tmp_path / "kb")
    toml = kb / "library.toml"
    toml.write_text(toml.read_text(encoding="utf-8").split("[frameworks]")[0], encoding="utf-8")
    assert D.main([str(kb), "--apply"]) == 40


# --------------------------------------------------------------------------- iron law 1


def test_desk_commands_never_touch_dimension_syntheses(tmp_path, klode, capsys):
    """S5: apply / prompts / status / verify leave every ``_syntheses/<dim>.md`` byte-identical - even a half-written
    one (a worker still at work: the file exists, but it is not done)."""
    spec = spec_with(n_dims=3)
    holdout = five_holdout(spec)
    kb = make_kb(tmp_path / "kb", spec, holdout)
    syn = kb / "library" / "frameworks" / "_syntheses"
    (syn / "whistle.md").write_text("---\ndimension: whistle\n(worker still writing", encoding="utf-8")
    route_all(klode, spec, holdout)
    before = {p.name: p.read_bytes() for p in syn.glob("*.md") if not p.name.startswith("_")}
    for argv in (["--apply"], ["--prompts"], ["--status"], ["--verify"], ["--apply"]):
        D.main([str(kb), *argv])
    after = {p.name: p.read_bytes() for p in syn.glob("*.md") if not p.name.startswith("_")}
    assert after == before


def test_status_lists_presence_only(tmp_path, capsys):
    spec = spec_with(n_dims=3)
    kb = make_kb(tmp_path / "kb", spec, syntheses=False)
    syn = kb / "library" / "frameworks" / "_syntheses"
    syn.mkdir(parents=True)
    (syn / "construction.md").write_text("half", encoding="utf-8")
    assert D.main([str(kb), "--status"]) == 1
    out = capsys.readouterr().out
    assert "construction" in out and "whistle" in out and "不是完成判据" in out
    for d in spec["dimensions"]:
        (syn / f"{d['dimension']}.md").write_text("x", encoding="utf-8")
    assert D.main([str(kb), "--status"]) == 0


def test_prompts_one_per_dimension(tmp_path, capsys):
    spec = aurora_spec()
    kb = make_kb(tmp_path / "kb", spec)
    assert D.main([str(kb), "--prompts"]) == 0
    out_dir = kb / "_work" / "desk_prompts"
    assert sorted(p.name for p in out_dir.iterdir()) == sorted(f"{d['dimension']}.txt" for d in spec["dimensions"])
    txt = (out_dir / "construction.txt").read_text(encoding="utf-8")
    assert "dimension=construction" in txt and "ch01" in txt and spec["dimensions"][0]["question"] in txt
    assert "klode_kit.build.check_synthesis" in txt
    assert ".claude/skills" not in txt and "synthesis.md" in txt


# --------------------------------------------------------------------------- spec and holdout guards


def test_spec_guards_exit30(tmp_path, capsys):
    base = spec_with(n_dims=3)
    cases = []
    s = json.loads(json.dumps(base)); del s["dimensions"][0]["cues"]; cases.append(s)
    s = json.loads(json.dumps(base)); s["dimensions"][1]["cards"] = ["ch99"]; cases.append(s)
    s = json.loads(json.dumps(base)); s["dimensions"][2]["dimension"] = "../escape"; cases.append(s)
    s = json.loads(json.dumps(base)); s["dimensions"][0]["cues"] = ["a | b"]; cases.append(s)
    s = json.loads(json.dumps(base)); s["dimensions"][0]["cues"] = ["a, b"]; cases.append(s)
    s = json.loads(json.dumps(base)); s["dimensions"][1]["dimension"] = s["dimensions"][0]["dimension"]; cases.append(s)
    for k, spec in enumerate(cases):
        kb = make_kb(tmp_path / f"kb{k}", base)
        (kb / "src" / "desk.json").write_text(json.dumps(spec), encoding="utf-8")
        assert D.main([str(kb), "--apply"]) == 30, spec
    kb = make_kb(tmp_path / "nospec", base)
    (kb / "src" / "desk.json").rename(tmp_path / "moved-desk.json")
    assert D.main([str(kb), "--status"]) == 30


def test_holdout_guards_exit30(tmp_path, klode, capsys):
    spec = spec_with(n_dims=3)
    tune_q = spec["dimensions"][0]["tests"][0]
    bad = {
        "unknown dimension": five_holdout(spec)[:4] + [{"q": "x?", "gold": "no-such-dimension"}],
        "duplicate": five_holdout(spec)[:4] + [five_holdout(spec)[0]],
        "leaked tuning question": five_holdout(spec)[:4] + [{"q": tune_q, "gold": "construction"}],
    }
    for k, (name, holdout) in enumerate(bad.items()):
        kb = make_kb(tmp_path / f"kb{k}", spec, holdout)
        D.main([str(kb), "--apply"])
        route_all(klode, spec, holdout)
        assert D.main([str(kb), "--verify"]) == 30, name
        assert not (kb / "src" / "holdout.freeze.json").exists()
    kb = make_kb(tmp_path / "kbm", spec, five_holdout(spec))
    D.main([str(kb), "--apply"])
    (kb / "src" / "holdout.jsonl").write_text("not json\n", encoding="utf-8")
    assert D.main([str(kb), "--verify"]) == 30


def test_usage_errors(tmp_path, capsys):
    kb = make_kb(tmp_path / "kb")
    assert D.main([str(kb)]) == 2  # one action required
    assert D.main([str(kb), "--apply", "--verify"]) == 2
    assert D.main([str(kb), "--apply", "--refreeze", "--reason", "x"]) == 2  # refreeze only with --verify


def test_holdout_override_and_receipt_path(tmp_path, klode, capsys):
    spec = spec_with(n_dims=3)
    holdout = five_holdout(spec)
    kb = make_kb(tmp_path / "kb", spec, holdout)
    D.main([str(kb), "--apply"])
    alt = tmp_path / "alt-holdout.jsonl"
    alt.write_text((kb / "src" / "holdout.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
    (kb / "src" / "holdout.jsonl").rename(tmp_path / "moved.jsonl")
    route_all(klode, spec, holdout)
    rec = tmp_path / "out" / "verify.json"
    assert D.main([str(kb), "--verify", "--holdout", str(alt), "--receipt", str(rec)]) == 0
    doc = json.loads(must(rec).read_text(encoding="utf-8"))
    assert doc["schema"] == "klode-kit.desk_verify/1" and doc["holdout"]["total"] == 5


def test_verify_receipt_deterministic(tmp_path, klode, capsys):
    spec = spec_with(n_dims=3)
    holdout = five_holdout(spec)
    kb = make_kb(tmp_path / "kb", spec, holdout)
    D.main([str(kb), "--apply"])
    route_all(klode, spec, holdout)
    D.main([str(kb), "--verify"])
    D.main([str(kb), "--verify"])
    first = must(kb / "desk_receipt.json").read_bytes()
    D.main([str(kb), "--verify"])
    assert (kb / "desk_receipt.json").read_bytes() == first


def test_project_config_overrides_holdout_gate(tmp_path, klode, capsys):
    """``desk.holdout_min`` / ``desk.holdout_pass`` come from the configuration (klode-kit.toml overrides)."""
    spec = spec_with(n_dims=3)
    holdout = five_holdout(spec)[:4]
    kb = make_kb(tmp_path / "kb", spec, holdout)
    D.main([str(kb), "--apply"])
    route_all(klode, spec, holdout)
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "klode-kit.toml").write_text("[desk]\nholdout_min = 4\n", encoding="utf-8")
    assert D.main([str(kb), "--verify"]) == 1
    assert D.main([str(kb), "--verify", "--project", str(proj)]) == 0
