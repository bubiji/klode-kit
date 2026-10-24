"""WI-02a: ``build/check_cards`` - gate 1, vendored (matrix 02a-07; 03 A2; 02 r3.4 R2-02; plan WI-02a).

Specification:

- Rules kept from the user's klode-build ``check_cards.py`` (03 A2): F1 first line ``aliases: [...]`` with >= 10
  entries; F2 ``## Thin`` with an anchor; F3 ``## Full`` with a ``###`` subsection and an anchor on every top-level
  ``- `` bullet; A1 anchor >= 15 characters; A2 found verbatim in the source exactly once (``#N``: at least N
  times); A3 no newline, no outer whitespace. A missing card or source is ``MISSING``.
- The **policy comes from where the source lives**, never from the card's own ``kind:`` line (R2-02 remainder):
  a source under ``library/items/`` or ``library/records/`` is **deterministic** - every non-empty line of the
  source (an observation line of records_to_txt) carries exactly one Full anchor, anchors == lines, no line twice
  (``COV``; ``min_anchors_full`` does not apply); any other source is **authored** - at least
  ``cards.min_anchors_full`` (5) anchors in Full (``MIN_FULL``). A card id with sources on more than one shelf is
  ``SRC`` (ambiguous, fails). ``--policy auto`` (default) uses the derived policy; an explicit
  ``--policy authored|deterministic`` that disagrees with the source adds ``POLICY`` and changes nothing else.
- ``check_card(kb, cid, policy="auto") -> dict``: ``{id, card, source, policy, kind_declared, anchors, coverage,
  problems, pass}``; ``problems`` are strings starting with the rule code.
- CLI ``klode-kit check-cards (CARD ... | --all) [--kb DIR] [--json] [--policy P]``; CARD is
  ``cards_content/<id>.md`` (as the writer is told) or a bare id. Exit 0 all pass, 1 any problem, 2 usage.

Laws (criticalPath S4): deterministic coverage - for any set of observation lines and any multiset of anchored
lines, PASS iff every line is anchored exactly once, and the reported coverage is distinct-covered / lines;
A2 - an anchor drawn from the source fails A2 iff its occurrence count is not 1.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from _support.flows import REPO
from klode_kit.build import check_cards as C

pytestmark = pytest.mark.unit

SOURCE = (REPO / "tests" / "fixtures" / "fx-book-aurora" / "aurora-kettle.txt").read_text(encoding="utf-8")
CH1 = SOURCE[SOURCE.index("Chapter 1:"):SOURCE.index("Chapter 2:")]
A = [
    "copper shell that is hammered thin and then annealed twice",
    "Annealing relaxes the metal so that the shell can expand",
    "A brass rim is folded over the upper edge of the shell",
    "A domed base flexes outward when heated",
    "Makers tin the inside of the shell with a thin food-safe layer",
    "Retinning is needed roughly once every ten years of daily use",
]
ALIASES = ("aliases: [copper shell, kettle shell, annealing, brass rim, domed base, tinning, retinning, "
           "food-safe layer, hammered copper, kettle body]\n")
OBS = [
    "血红蛋白 135 g/L 2026-07-01 ⟨r-aaaaaaaaaaaa⟩",
    "血红蛋白 128 g/L 2026-08-01 ⟨r-bbbbbbbbbbbb⟩",
    "血红蛋白 128 g/L 2026-09-01 ⟨r-cccccccccccc⟩",
]


def authored(aliases: str = ALIASES, thin: str | None = None, full: list[str] | None = None, kind: str = "") -> str:
    thin = thin if thin is not None else f"The shell is copper (grep: `{A[0]}`).\n"
    full = full if full is not None else [f"- point {i} (grep: `{a}`)" for i, a in enumerate(A)]
    return aliases + kind + "\n# The Copper Shell\n\n## Thin\n" + thin + "\n## Full\n### Shell\n" + "\n".join(full) + "\n"


def deterministic(lines: list[str], kind: str = "", thin_anchor: str | None = None) -> str:
    thin = f"Latest value (grep: `{thin_anchor or lines[0]}`).\n"
    full = "".join(f"- {k} (grep: `{ln}`)\n" for k, ln in enumerate(lines))
    return ALIASES + kind + "\n# 血红蛋白\n\n## Thin\n" + thin + "\n## Full\n### 逐次\n" + full


def make_kb(root: Path, shelf: str, source: str, content: str, cid: str = "ch01") -> Path:
    kb = root / "kb"
    (kb / "library" / shelf).mkdir(parents=True, exist_ok=True)
    (kb / "cards_content").mkdir(exist_ok=True)
    (kb / "library" / shelf / f"{cid}.txt").write_text(source, encoding="utf-8")
    (kb / "cards_content" / f"{cid}.md").write_text(content, encoding="utf-8")
    return kb


def codes(result: dict) -> set[str]:
    return {p.split(" ", 1)[0] for p in result["problems"]}


# --------------------------------------------------------------------------- original rules (03 A2)


def test_good_authored_card_passes(tmp_path):
    kb = make_kb(tmp_path, "book", CH1, authored())
    r = C.check_card(kb, "ch01")
    assert (r["pass"], r["problems"], r["policy"], r["anchors"]) == (True, [], "authored", 7)
    assert r["source"] == "library/book/ch01.txt"
    assert r["card"] == "cards_content/ch01.md"


@pytest.mark.parametrize("text, rule", [
    pytest.param(authored(aliases="aliases: [a, b, c]\n"), "F1", id="F1-too-few"),
    pytest.param(authored(aliases="title: x\n"), "F1", id="F1-no-line"),
    pytest.param(authored(thin="no anchors here\n"), "F2", id="F2-no-anchor"),
    pytest.param(authored().replace("## Thin\n", "## Thinner\n"), "F2", id="F2-no-thin"),
    pytest.param(authored().replace("## Full\n", "## Fuller\n"), "F3", id="F3-no-full"),
    pytest.param(authored().replace("### Shell\n", ""), "F3", id="F3-no-subsection"),
    pytest.param(authored(full=[f"- p (grep: `{a}`)" for a in A] + ["- bare bullet"]), "F3", id="F3-bare-bullet"),
    pytest.param(authored(full=[f"- p (grep: `{a}`)" for a in A] + ["- s (grep: `the shell`)"]), "A1", id="A1"),
    pytest.param(authored(full=[f"- p (grep: `{a}`)" for a in A] + ["- x (grep: `not in this chapter at all`)"]),
                 "A2", id="A2-not-found"),
    pytest.param(authored(full=[f"- p (grep: `{a}`)" for a in A] + ["- x (grep: ` so that the shell can`)"]),
                 "A3", id="A3"),
    pytest.param(authored(full=[f"- p (grep: `{a}`)" for a in A[:4]]), "MIN_FULL", id="MIN_FULL-4"),
])
def test_each_rule_fails(tmp_path, text, rule):
    kb = make_kb(tmp_path, "book", CH1, text)
    r = C.check_card(kb, "ch01")
    assert r["pass"] is False
    assert rule in codes(r), r["problems"]


def test_indented_bullet_not_checked_as_before(tmp_path):
    """03 §5 minor: F3 looks at top-level ``- `` bullets only (kept as in the original)."""
    kb = make_kb(tmp_path, "book", CH1, authored(full=[f"- p (grep: `{a}`)" for a in A] + ["  - nested, no anchor"]))
    assert C.check_card(kb, "ch01")["pass"] is True


def test_hash_n(tmp_path):
    amb = "the shell can expand without"
    src = CH1 + "\n" + amb + " cracking.\n"
    base = [f"- p (grep: `{a}`)" for a in A]
    kb = make_kb(tmp_path, "book", src, authored(full=base + [f"- y (grep: `{amb}`)"]))
    assert "A2" in codes(C.check_card(kb, "ch01"))
    (kb / "cards_content" / "ch01.md").write_text(authored(full=base + [f"- y (grep: `{amb}` #2)"]), encoding="utf-8")
    assert C.check_card(kb, "ch01")["pass"] is True
    (kb / "cards_content" / "ch01.md").write_text(authored(full=base + [f"- y (grep: `{amb}` #3)"]), encoding="utf-8")
    assert "A2" in codes(C.check_card(kb, "ch01"))


def test_missing_card_or_source(tmp_path):
    kb = make_kb(tmp_path, "book", CH1, authored())
    r = C.check_card(kb, "ch09")
    assert r["pass"] is False and codes(r) == {"MISSING"}
    (kb / "cards_content" / "ch02.md").write_text(authored(), encoding="utf-8")
    r = C.check_card(kb, "ch02")
    assert r["pass"] is False and codes(r) == {"MISSING"} and r["source"] is None


def test_check_one_original_shape(tmp_path):
    """``check_one(kb, cid)`` keeps the original return shape: ``["<cid>: <problem>", ...]``."""
    kb = make_kb(tmp_path, "book", CH1, authored(aliases="aliases: [a]\n"))
    probs = C.check_one(kb, "ch01")
    assert probs and all(p.startswith("ch01: ") for p in probs)
    assert any(p.startswith("ch01: F1") for p in probs)


# --------------------------------------------------------------------------- 02a-07 policy from source


def test_policy_from_source_not_kind(tmp_path):
    """02a-07 (L1/S3): the source is under library/items/ and the card says ``kind: authored`` - the card is still
    judged by the deterministic rule: 3 anchors over 3 lines PASS (authored would need 5); a card anchoring one
    line twice and missing another FAILs on coverage 2/3, not on MIN_FULL."""
    src = "\n".join(OBS) + "\n"
    kb = make_kb(tmp_path, "items", src, deterministic(OBS, kind="kind: authored\n"), cid="hgb")
    r = C.check_card(kb, "hgb", policy="auto")
    assert r["policy"] == "deterministic"
    assert r["kind_declared"] == "authored"
    assert (r["pass"], r["problems"]) == (True, [])
    assert r["coverage"] == {"covered": 3, "lines": 3}

    (kb / "cards_content" / "hgb.md").write_text(
        deterministic([OBS[0], OBS[1], OBS[1]], kind="kind: authored\n"), encoding="utf-8")
    r = C.check_card(kb, "hgb", policy="auto")
    assert r["pass"] is False
    assert "COV" in codes(r) and "MIN_FULL" not in codes(r)
    assert r["coverage"] == {"covered": 2, "lines": 3}
    assert any("2/3" in p for p in r["problems"])


def test_records_shelf_is_deterministic(tmp_path):
    src = "\n".join(OBS[:2]) + "\n"
    kb = make_kb(tmp_path, "records", src, deterministic(OBS[:2]), cid="r-aaaaaaaaaaaa")
    r = C.check_card(kb, "r-aaaaaaaaaaaa")
    assert (r["policy"], r["pass"], r["kind_declared"]) == ("deterministic", True, None)


def test_authored_shelf_ignores_declared_deterministic(tmp_path):
    """A book card claiming ``kind: deterministic`` still needs 5 Full anchors."""
    kb = make_kb(tmp_path, "book", CH1, authored(full=[f"- p (grep: `{a}`)" for a in A[:3]],
                                                 kind="kind: deterministic\n"))
    r = C.check_card(kb, "ch01")
    assert (r["policy"], r["kind_declared"], r["pass"]) == ("authored", "deterministic", False)
    assert "MIN_FULL" in codes(r) and "COV" not in codes(r)


def test_explicit_policy_cannot_override_source(tmp_path):
    src = "\n".join(OBS) + "\n"
    kb = make_kb(tmp_path, "items", src, deterministic(OBS), cid="hgb")
    r = C.check_card(kb, "hgb", policy="authored")
    assert r["policy"] == "deterministic"
    assert r["pass"] is False and codes(r) == {"POLICY"}
    assert C.check_card(kb, "hgb", policy="deterministic")["pass"] is True
    with pytest.raises(ValueError):
        C.check_card(kb, "hgb", policy="lenient")


def test_ambiguous_source_shelves_fail(tmp_path):
    src = "\n".join(OBS) + "\n"
    kb = make_kb(tmp_path, "items", src, deterministic(OBS), cid="hgb")
    (kb / "library" / "book").mkdir()
    (kb / "library" / "book" / "hgb.txt").write_text(src, encoding="utf-8")
    r = C.check_card(kb, "hgb")
    assert r["pass"] is False and "SRC" in codes(r)


def test_deterministic_blank_lines_and_anchor_counts(tmp_path):
    """Blank lines are not observations; an extra Full anchor on a line already covered is a COV problem."""
    src = OBS[0] + "\n\n" + OBS[1] + "\n"
    kb = make_kb(tmp_path, "items", src, deterministic(OBS[:2]), cid="hgb")
    assert C.check_card(kb, "hgb")["coverage"] == {"covered": 2, "lines": 2}
    content = deterministic(OBS[:2]) + f"- again (grep: `{OBS[1][:20]}`)\n"
    (kb / "cards_content" / "hgb.md").write_text(content, encoding="utf-8")
    r = C.check_card(kb, "hgb")
    assert r["pass"] is False and "COV" in codes(r)
    assert r["coverage"] == {"covered": 2, "lines": 2}


# --------------------------------------------------------------------------- S4 laws

OBS_LINE = st.builds(lambda item, v, d: f"{item} {v} mmol/L 2026-{d:02d}-15 ⟨r-{item}{v}{d}⟩",
                     st.sampled_from(["葡萄糖", "肌酐", "尿酸"]), st.integers(10, 999), st.integers(1, 12))


@settings(max_examples=150, deadline=None)
@given(lines=st.lists(OBS_LINE, min_size=1, max_size=8, unique=True), data=st.data())
def test_deterministic_coverage_law(lines, data):
    """S4: for any observation lines and any multiset of anchored lines: PASS iff each line anchored exactly once;
    coverage = distinct anchored lines / lines."""
    picks = data.draw(st.lists(st.sampled_from(range(len(lines))), min_size=1, max_size=len(lines) + 2))
    anchored = [lines[i] for i in picks]
    with tempfile.TemporaryDirectory() as tmp:
        kb = make_kb(Path(tmp), "items", "\n".join(lines) + "\n", deterministic(anchored, thin_anchor=lines[0]),
                     cid="x")
        r = C.check_card(kb, "x")
        exact = sorted(picks) == list(range(len(lines)))
        assert r["pass"] is exact, r["problems"]
        assert r["coverage"] == {"covered": len(set(picks)), "lines": len(lines)}
        assert ("COV" in codes(r)) is (not exact)


WORDS = st.lists(st.sampled_from(["copper", "shell", "brass", "rim", "kettle", "steam"]), min_size=4, max_size=40)


@settings(max_examples=150, deadline=None)
@given(words=WORDS, data=st.data())
def test_a2_law(words, data):
    """S4: an anchor cut from the source (>= 15 chars) fails A2 iff it occurs in the source a number of times != 1."""
    text = " ".join(words)
    start = data.draw(st.integers(0, max(0, len(text) - 15)))
    end = data.draw(st.integers(min(len(text), start + 15), len(text)))
    q = text[start:end].strip()
    if len(q) < 15:
        return
    full = [f"- p{i} (grep: `{a}`)" for i, a in enumerate(A)]
    content = authored(full=full + [f"- q (grep: `{q}`)"])
    with tempfile.TemporaryDirectory() as tmp:
        kb = make_kb(Path(tmp), "book", CH1 + "\n" + text + "\n", content)
        r = C.check_card(kb, "ch01")
        assert ("A2" in codes(r)) is ((CH1 + "\n" + text + "\n").count(q) != 1)


# --------------------------------------------------------------------------- CLI


def run(capsys, argv):
    code = C.main(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


def test_cli_text_and_json(tmp_path, capsys):
    kb = make_kb(tmp_path, "book", CH1, authored())
    (kb / "cards_content" / "ch02.md").write_text(authored(aliases="aliases: [a]\n"), encoding="utf-8")
    (kb / "library" / "book" / "ch02.txt").write_text(CH1, encoding="utf-8")

    code, out, _ = run(capsys, ["cards_content/ch01.md", "--kb", str(kb)])
    assert code == 0 and out.startswith("PASS  ch01")
    code, out, _ = run(capsys, ["--all", "--kb", str(kb)])
    assert code == 1
    assert "PASS  ch01" in out and "FAIL  ch02" in out and "   ch02: F1" in out
    code, out, _ = run(capsys, ["--all", "--kb", str(kb), "--json"])
    assert code == 1
    doc = json.loads(out)
    assert doc["pass"] is False
    assert [(c["id"], c["pass"]) for c in doc["cards"]] == [("ch01", True), ("ch02", False)]
    code, out, _ = run(capsys, ["ch01", "--kb", str(kb), "--json", "--policy", "auto"])
    assert code == 0 and json.loads(out)["cards"][0]["policy"] == "authored"


def test_cli_cwd_default(tmp_path, capsys, monkeypatch):
    kb = make_kb(tmp_path, "book", CH1, authored())
    monkeypatch.chdir(kb)
    code, out, _ = run(capsys, ["cards_content/ch01.md"])
    assert code == 0 and "PASS" in out


@pytest.mark.parametrize("argv", [[], ["../x.md"], ["cards_content/../x.md"], ["a/b.md"], ["ch01", "--all"],
                                  ["ch01", "--policy", "lenient"], ["--bogus"]])
def test_cli_usage(tmp_path, capsys, argv):
    kb = make_kb(tmp_path, "book", CH1, authored())
    code, _, _ = run(capsys, [*argv, "--kb", str(kb)])
    assert code == 2


def test_cli_all_with_no_cards(tmp_path, capsys):
    kb = tmp_path / "kb"
    (kb / "cards_content").mkdir(parents=True)
    code, out, err = run(capsys, ["--all", "--kb", str(kb)])
    assert code == 1 and "cards_content" in err


def test_json_schema_accepts_output(tmp_path, capsys):
    from jsonschema import Draft202012Validator

    kb = make_kb(tmp_path, "items", "\n".join(OBS) + "\n", deterministic(OBS[:2]), cid="hgb")
    code, out, _ = run(capsys, ["hgb", "--kb", str(kb), "--json"])
    assert code == 1
    doc = json.loads(out)
    Draft202012Validator.check_schema(C.JSON_SCHEMA)
    assert list(Draft202012Validator(C.JSON_SCHEMA).iter_errors(doc)) == []
    card = doc["cards"][0]
    assert card["coverage"] == {"covered": 2, "lines": 3}
    assert card["problems"] and all(set(p) == {"code", "message"} for p in card["problems"])
