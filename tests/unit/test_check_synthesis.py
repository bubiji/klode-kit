"""WI-02c: ``build/check_synthesis`` - the desk synthesis gate (matrix 02c-02, plus the retained S1-S6 judgments).

Specification (03 A7/B4; 02 §3.3 B4; vendored from the user's klode-build ``check_synthesis.py``):

- Kept: S1 front-matter has dimension/title/cards/status/tier and ``dimension`` == file name; S2 every card in
  ``cards:`` exists; S3 ``## Craft`` with the three ``###`` sections 机制/动作/可选项 and ``## Provenance``; S4 every
  动作 item starts ``- **<ASCII-led bold head>``; S5 every anchor is the very string of an anchor in a listed card
  ("anchors are only copied from cards"); S6 no engine words in Craft. Output ``PASS|FAIL  <dim>  (<n> 锚点...)``
  then the problems; exit 0 only when every checked dimension passes. No dimension argument = every
  ``_syntheses/*.md`` not starting with ``_``.
- **B4**: the 动作 section regex used ``.*`` under ``re.S`` in the heading, which swallowed the rest of the file
  and captured an empty body, so S4 never ran: a purely Chinese bold head passed.
- New: nothing to check (no synthesis file at all) is a FAIL, not a silent PASS.

No law: a pure per-file judgment (each case pins one rule).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from _support.desk_fx import ANCHORS, make_kb, synthesis
from klode_kit.build import check_synthesis as CS

pytestmark = pytest.mark.unit


@pytest.fixture
def kb(tmp_path) -> Path:
    return make_kb(tmp_path / "kb")


def syn_path(kb: Path, dim: str) -> Path:
    return kb / "library" / "frameworks" / "_syntheses" / f"{dim}.md"


# --------------------------------------------------------------------------- 02c-02  B4 reproducer


def test_b4_cn_action_head(kb, capsys):
    """03 §2 step 21: ``- **壶、底座、电线不可浸水。**`` passed the original gate. It must be reported as S4."""
    a = ANCHORS["ch01"]
    text = synthesis("construction", ["ch01"], a[:3],
                     actions=[f"- **A1 anneal-twice 退火两次。** (grep: `{a[0]}`)",
                              f"- **壶、底座、电线不可浸水。** (grep: `{a[1]}`)"])
    syn_path(kb, "construction").write_text(text, encoding="utf-8")
    problems = CS.check_one(kb, "construction")
    s4 = [p for p in problems if "S4" in p]
    assert len(s4) == 1, problems
    assert "壶、底座、电线不可浸水" in s4[0]
    assert CS.main([str(kb), "construction"]) == 1
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("FAIL  construction")


def test_b4_every_cn_head_reported_and_ascii_heads_pass(kb):
    """Each purely Chinese head is its own S4 problem; ASCII-led heads (also digits) are fine."""
    a = ANCHORS["ch01"]
    actions = [f"- **A1 ok 中文。** (grep: `{a[0]}`)", f"- **2nd-step 中文。** (grep: `{a[1]}`)",
               f"- **退火。** (grep: `{a[2]}`)", f"- **检查底座。** (grep: `{a[3]}`)", "- 没有加粗头的条目"]
    syn_path(kb, "construction").write_text(synthesis("construction", ["ch01"], a[:4], actions=actions),
                                            encoding="utf-8")
    s4 = [p for p in CS.check_one(kb, "construction") if "S4" in p]
    assert len(s4) == 3
    assert any("退火" in p for p in s4) and any("检查底座" in p for p in s4) and any("没有加粗头" in p for p in s4)


def test_s4_section_ends_at_next_heading(kb):
    """Lines after the 动作 section (next ### or ##) are not judged as action items."""
    a = ANCHORS["ch01"]
    text = synthesis("construction", ["ch01"], a[:3]).replace(
        "## Provenance\n", "## Provenance\n- 纯中文条目不在动作小节里\n")
    syn_path(kb, "construction").write_text(text, encoding="utf-8")
    assert CS.check_one(kb, "construction") == []


# --------------------------------------------------------------------------- retained S1-S6


def test_passing_synthesis_reports_anchor_count(kb, capsys):
    assert CS.main([str(kb), "construction"]) == 0
    out = capsys.readouterr().out
    assert out.strip() == "PASS  construction  (7 锚点)"


def test_all_dimensions_by_default_skip_underscore(kb, capsys):
    (kb / "library" / "frameworks" / "_syntheses" / "_diagnostics.md").write_text("| a | b |\n", encoding="utf-8")
    assert CS.main([str(kb)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [ln.split()[1] for ln in lines] == ["construction", "night-market-care", "whistle"]


def test_s1_front_matter(kb):
    p = syn_path(kb, "construction")
    p.write_text(p.read_text(encoding="utf-8").replace("tier: 1\n", "").replace(
        "dimension: construction", "dimension: other"), encoding="utf-8")
    probs = CS.check_one(kb, "construction")
    assert any("S1" in x and "tier" in x for x in probs)
    assert any("S1" in x and "other" in x for x in probs)
    p.write_text("no front matter\n", encoding="utf-8")
    assert CS.check_one(kb, "construction") == ["construction: S1 缺 front-matter"]


def test_s2_cards_exist_and_nonempty(kb):
    p = syn_path(kb, "construction")
    p.write_text(p.read_text(encoding="utf-8").replace("cards: [ch01]", "cards: [ch01, ch99]"), encoding="utf-8")
    assert any("S2" in x and "ch99" in x for x in CS.check_one(kb, "construction"))
    p.write_text(p.read_text(encoding="utf-8").replace("cards: [ch01, ch99]", "cards: []"), encoding="utf-8")
    assert any("S2 cards: 为空" in x for x in CS.check_one(kb, "construction"))


def test_s3_sections(kb):
    p = syn_path(kb, "construction")
    p.write_text(p.read_text(encoding="utf-8").replace("### 可选项", "### 其他").replace("## Provenance", "## 来源"),
                 encoding="utf-8")
    probs = CS.check_one(kb, "construction")
    assert any("S3" in x and "可选项" in x for x in probs)
    assert any("S3 缺 ## Provenance" in x for x in probs)


def test_s5_anchor_must_be_copied_from_listed_cards(kb):
    """An anchor re-cut from the source (or copied from a card not in ``cards:``) is S5."""
    recut = "The aurora kettle is built around a copper shell"  # in the source, not in the card
    other_card = ANCHORS["ch02"][0]
    text = synthesis("construction", ["ch01"], ANCHORS["ch01"][:3] + [recut, other_card])
    syn_path(kb, "construction").write_text(text, encoding="utf-8")
    s5 = [x for x in CS.check_one(kb, "construction") if "S5" in x]
    assert len(s5) == 2  # one problem per occurrence
    assert any(recut[:30] in x for x in s5) and any(other_card[:30] in x for x in s5)


def test_s6_engine_words_in_craft(kb):
    p = syn_path(kb, "construction")
    p.write_text(synthesis("construction", ["ch01"], ANCHORS["ch01"][:3], craft_extra="- the scorers say so\n"),
                 encoding="utf-8")
    assert any("S6" in x and "scorers" in x for x in CS.check_one(kb, "construction"))


def test_missing_file_and_nothing_to_check(tmp_path, capsys):
    kb = make_kb(tmp_path / "kb", syntheses=False)
    assert CS.check_one(kb, "construction") == ["construction: 文件不存在"]
    assert CS.main([str(kb), "construction"]) == 1
    capsys.readouterr()
    assert CS.main([str(kb)]) == 1  # no synthesis at all: FAIL, never a silent PASS
    assert "没有" in capsys.readouterr().out


def test_usage(capsys):
    assert CS.main([]) == 2
