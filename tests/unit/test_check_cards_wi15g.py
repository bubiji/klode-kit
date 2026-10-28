"""WI-15g: ``build/check_cards`` pinned exactly (mutation-gate survivors of WI-15c, score 0.794 -> >= 0.80).

``test_check_cards.py`` checks which rule codes fire. It does not pin what the user actually sees: the exact text of
each problem (rule messages, the 50-character anchor tag and its ellipsis boundary, the 10-line caps of the COV
detail), the exact JSON bytes of ``--json`` (indent, raw non-ASCII, the ``code`` / ``message`` split), the exact
text lines, the ``--help`` / usage-error wording, the keys of ``judge()``'s result, ``--project`` reaching the
thresholds, and the explicit thresholds that ``check_card`` must not overwrite from the configuration.

Law: none new - the coverage and A2 laws are held by ``test_check_cards.py`` (hypothesis). These are S1/S2 pins.

Mutants left alone as equivalent, with the reason:
``_line_index``: ``count("\\n", None, pos)`` is ``count("\\n", 0, pos)``; ``_coverage``: ``any(c > 2 ...)`` as the third
term of the COV condition - if every observation line is hit and the anchor count equals the line count, no line can
be hit twice, so the term is redundant; ``_judge``: ``anchors: 1`` / ``pass: True`` / ``"kind_declared"`` renamed in
the initial dict of ``judge()`` - overwritten on every path, the key *set* is pinned instead; ``raw.split("\\n## ", )``
and ``split("\\n## ", 2)`` - only element ``[0]`` is used; ``"UTF-8"`` is the same codec as ``"utf-8"``;
``kb / "CARDS_CONTENT"`` on the case-insensitive macOS file system (the only supported platform) is the same
directory; ``type=Path`` / ``default=None`` removed from ``--project`` (``config.load`` wraps in ``Path`` anyway);
``zip(...)[:3]`` in ``_json_card`` (``zip`` of two names).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from klode_kit.build import check_cards as C

from _support.flows import REPO

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


def run(capsys, argv):
    code = C.main(argv)
    out = capsys.readouterr()
    return code, out.out, out.err

pytestmark = pytest.mark.unit


def obs_lines(n: int) -> list[str]:
    return [f"observation number {k:02d} reads normal range" for k in range(n)]


def custom_card(full_bullets: list[str], first: str, aliases: str = ALIASES, extra_head: str = "") -> str:
    return (aliases + extra_head + "\n# T\n\n## Thin\nx (grep: `" + first + "`)\n\n## Full\n### s\n"
            + "".join(f"- {b}\n" for b in full_bullets))


def anchor(q: str, nth: int | None = None) -> str:
    return f"k (grep: `{q}`" + (f" #{nth}" if nth else "") + ")"


# --------------------------------------------------------------------------- _coverage


def cov_problem(kb: Path, cid: str = "d") -> tuple[dict, str]:
    r = C.check_card(kb, cid)
    cov = [p for p in r["problems"] if p.startswith("COV ")]
    return r, (cov[0] if cov else "")


def test_cov_message_has_missing_twice_and_count_details_joined_exactly(tmp_path):
    lines = obs_lines(3)
    kb = make_kb(tmp_path, "items", "\n".join(lines) + "\n", deterministic([lines[0], lines[0]], thin_anchor=lines[0]),
                 cid="d")
    r, cov = cov_problem(kb)
    assert cov == ("COV 观测覆盖率 1/3（每个观测行恰好一个 Full 锚点）：缺锚点的行 [2, 3]；重复锚定的行 [1]；"
                   "Full 锚点 2 个 ≠ 观测行 3 行")
    assert r["coverage"] == {"covered": 1, "lines": 3}


def test_cov_extra_anchor_alone_is_a_count_mismatch(tmp_path):
    lines = obs_lines(3)
    kb = make_kb(tmp_path, "items", "\n".join(lines) + "\n",
                 deterministic([*lines, "this anchor text is nowhere in the source"], thin_anchor=lines[0]), cid="d")
    r, cov = cov_problem(kb)
    assert cov == "COV 观测覆盖率 3/3（每个观测行恰好一个 Full 锚点）：Full 锚点 4 个 ≠ 观测行 3 行"
    assert r["coverage"] == {"covered": 3, "lines": 3}


def test_cov_unfound_anchor_alone_is_missing_not_count(tmp_path):
    lines = obs_lines(3)
    kb = make_kb(tmp_path, "items", "\n".join(lines) + "\n",
                 deterministic([lines[0], lines[1], "this anchor text is nowhere in the source"], thin_anchor=lines[0]),
                 cid="d")
    _, cov = cov_problem(kb)
    assert cov == "COV 观测覆盖率 2/3（每个观测行恰好一个 Full 锚点）：缺锚点的行 [3]"


def test_cov_missing_list_is_capped_at_ten(tmp_path):
    lines = obs_lines(12)
    kb = make_kb(tmp_path, "items", "\n".join(lines) + "\n", deterministic(lines[:1], thin_anchor=lines[0]), cid="d")
    _, cov = cov_problem(kb)
    assert cov.endswith("缺锚点的行 [2, 3, 4, 5, 6, 7, 8, 9, 10, 11]；Full 锚点 1 个 ≠ 观测行 12 行")


def test_cov_twice_list_is_capped_at_ten(tmp_path):
    lines = obs_lines(11)
    kb = make_kb(tmp_path, "items", "\n".join(lines) + "\n", deterministic(lines + lines, thin_anchor=lines[0]), cid="d")
    r, cov = cov_problem(kb)
    assert cov == ("COV 观测覆盖率 11/11（每个观测行恰好一个 Full 锚点）：重复锚定的行 [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]；"
                   "Full 锚点 22 个 ≠ 观测行 11 行")
    assert r["coverage"] == {"covered": 11, "lines": 11}


def test_cov_a_line_anchored_three_times_is_listed_once_per_line(tmp_path):
    lines = obs_lines(2)
    kb = make_kb(tmp_path, "items", "\n".join(lines) + "\n",
                 deterministic([lines[0], lines[0], lines[0], lines[1]], thin_anchor=lines[0]), cid="d")
    _, cov = cov_problem(kb)
    assert cov == "COV 观测覆盖率 2/2（每个观测行恰好一个 Full 锚点）：重复锚定的行 [1]；Full 锚点 4 个 ≠ 观测行 2 行"


def test_cov_empty_and_multiline_anchors_are_counted_but_never_hit_a_line(tmp_path):
    lines = obs_lines(3)
    src = "\n".join(lines) + "\n"
    bullets = [anchor(""), anchor(lines[0] + "\n" + lines[1]), anchor(lines[1]), anchor(lines[2])]
    kb = make_kb(tmp_path, "items", src, custom_card(bullets, lines[0]), cid="d")
    r, cov = cov_problem(kb)
    assert r["coverage"] == {"covered": 2, "lines": 3}
    assert cov == "COV 观测覆盖率 2/3（每个观测行恰好一个 Full 锚点）：缺锚点的行 [1]；Full 锚点 4 个 ≠ 观测行 3 行"


def test_cov_empty_anchor_alone_does_not_cover_line_one(tmp_path):
    lines = obs_lines(2)
    kb = make_kb(tmp_path, "items", "\n".join(lines) + "\n", custom_card([anchor(""), anchor(lines[1])], lines[0]),
                 cid="d")
    r, cov = cov_problem(kb)
    assert r["coverage"] == {"covered": 1, "lines": 2}
    assert cov == "COV 观测覆盖率 1/2（每个观测行恰好一个 Full 锚点）：缺锚点的行 [1]"
    assert r["anchors"] == 3


def test_cov_source_with_leading_blank_line_indexes_lines_from_the_start(tmp_path):
    lines = obs_lines(3)
    kb = make_kb(tmp_path, "items", "\n" + "\n".join(lines) + "\n", deterministic(lines, thin_anchor=lines[0]), cid="d")
    r = C.check_card(kb, "d")
    assert (r["pass"], r["problems"], r["coverage"]) == (True, [], {"covered": 3, "lines": 3})


def test_cov_nth_occurrence_counts_the_line_of_that_occurrence(tmp_path):
    lines = [obs_lines(1)[0], obs_lines(1)[0], "a different observation line 22"]
    kb = make_kb(tmp_path, "items", "\n".join(lines) + "\n",
                 custom_card([anchor(lines[0], 1), anchor(lines[0], 2), anchor(lines[2])], lines[0]), cid="d")
    r = C.check_card(kb, "d")
    assert r["coverage"] == {"covered": 3, "lines": 3}
    assert [p for p in r["problems"] if p.startswith("COV")] == []


# --------------------------------------------------------------------------- check_card / judge


def test_check_card_unknown_policy_message():
    with pytest.raises(ValueError) as e:
        C.check_card(Path("."), "x", "lenient")
    assert str(e.value) == "policy must be one of ('auto', 'authored', 'deterministic'), got 'lenient'"


def test_judge_unknown_policy_message():
    with pytest.raises(ValueError) as e:
        C.judge(Path("."), "x", "raw", "lenient", min_aliases=1, min_anchors_full=1)
    assert str(e.value) == "policy must be one of ('auto', 'authored', 'deterministic'), got 'lenient'"


def test_explicit_min_aliases_is_not_overwritten_by_the_configuration(tmp_path):
    kb = make_kb(tmp_path, "book", CH1, authored(aliases="aliases: [a, b, c]\n"))
    r = C.check_card(kb, "ch01", min_aliases=3)  # min_anchors_full left to the configuration (5)
    assert (r["pass"], r["problems"]) == (True, [])


def test_explicit_min_anchors_full_is_not_overwritten_by_the_configuration(tmp_path):
    kb = make_kb(tmp_path, "book", CH1, authored(full=[f"- point (grep: `{A[0]}`)"][0:1]))
    r = C.check_card(kb, "ch01", min_anchors_full=1)  # min_aliases left to the configuration (10)
    assert (r["pass"], r["problems"]) == (True, [])
    r = C.check_card(kb, "ch01", min_anchors_full=2)
    assert r["problems"] == ["MIN_FULL Full 段只有 1 个锚点，需 ≥ 2"]


def test_both_thresholds_default_to_the_configuration(tmp_path):
    kb = make_kb(tmp_path, "book", CH1, authored(aliases="aliases: [a, b, c]\n", full=[f"- p (grep: `{A[0]}`)"]))
    r = C.check_card(kb, "ch01")
    assert r["problems"] == ["F1 aliases 只有 3 个，需 ≥ 10", "MIN_FULL Full 段只有 1 个锚点，需 ≥ 5"]


def test_missing_card_result_is_exact(tmp_path):
    kb = tmp_path / "kb"
    kb.mkdir()
    assert C.check_card(kb, "nope") == {
        "id": "nope", "card": "cards_content/nope.md", "source": None, "policy": None, "kind_declared": None,
        "anchors": 0, "coverage": None, "pass": False, "problems": ["MISSING cards_content 文件不存在"]}


def test_judge_result_keys_and_card_default_and_override(tmp_path):
    kb = tmp_path / "kb"
    kb.mkdir()
    r = C.judge(kb, "zz", "aliases: [a]\n", min_aliases=1, min_anchors_full=1)
    assert list(r) == ["id", "card", "source", "policy", "kind_declared", "anchors", "coverage", "pass", "problems"]
    assert r == {"id": "zz", "card": "cards_content/zz.md", "source": None, "policy": None, "kind_declared": None,
                 "anchors": 0, "coverage": None, "pass": False, "problems": ["MISSING 找不到源文本 library/*/zz.txt"]}
    r2 = C.judge(kb, "zz", "aliases: [a]\n", min_aliases=1, min_anchors_full=1, card="published/zz.md")
    assert r2["card"] == "published/zz.md"


def test_kind_declared_is_read_from_the_head_only(tmp_path):
    body = authored(thin="kind: deterministic\nThe shell is copper (grep: `%s`).\n" % A[0])
    kb = make_kb(tmp_path, "book", CH1, body)
    assert C.check_card(kb, "ch01")["kind_declared"] is None
    kb2 = make_kb(tmp_path / "w2", "book", CH1, authored(kind="kind: authored\n"))
    assert C.check_card(kb2, "ch01")["kind_declared"] == "authored"


def test_kind_in_a_later_section_is_ignored_even_with_several_sections(tmp_path):
    body = authored() + "\n## Extra\nkind: items\n"
    kb = make_kb(tmp_path, "book", CH1, body)
    assert C.check_card(kb, "ch01")["kind_declared"] is None


def test_multiple_shelves_message_lists_both_sources_comma_separated(tmp_path):
    kb = make_kb(tmp_path, "book", CH1, authored())
    (kb / "library" / "items").mkdir()
    (kb / "library" / "items" / "ch01.txt").write_text(CH1, encoding="utf-8")
    r = C.check_card(kb, "ch01")
    assert r["problems"] == ["SRC 同名源在多个书架：library/book/ch01.txt, library/items/ch01.txt"]
    assert r["source"] == "library/book/ch01.txt"
    assert (r["policy"], r["pass"]) == (None, False)


def test_aliases_list_may_span_lines(tmp_path):
    al = "aliases: [copper shell, kettle shell, annealing, brass rim,\n domed base, tinning, retinning,\n food-safe layer, hammered copper, kettle body]\n"
    kb = make_kb(tmp_path, "book", CH1, authored(aliases=al))
    r = C.check_card(kb, "ch01")
    assert (r["pass"], r["problems"]) == (True, [])


def test_structure_problem_messages_are_exact(tmp_path):
    kb = make_kb(tmp_path, "book", CH1, "title: x\n# T\n")
    r = C.check_card(kb, "ch01")
    assert r["problems"] == ["F1 首行必须是 aliases: [...]", "F2 缺 ## Thin 段", "F3 缺 ## Full 段"]
    kb2 = make_kb(tmp_path / "b", "book", CH1, ALIASES + "\n## Thin\nno anchor\n\n## Full\n### s\n")
    r2 = C.check_card(kb2, "ch01")
    assert r2["problems"][0] == "F2 Thin 段没有锚点"


def test_full_without_subsection_message_and_subsection_anywhere_counts(tmp_path):
    kb = make_kb(tmp_path, "book", CH1, authored().replace("### Shell\n", ""))
    assert C.check_card(kb, "ch01")["problems"] == ["F3 Full 段没有 ### 小节"]
    late = authored().replace("### Shell\n", "") + "### Late\n"
    kb2 = make_kb(tmp_path / "late", "book", CH1, late)
    assert C.check_card(kb2, "ch01")["problems"] == []


def test_bullet_without_anchor_is_quoted_up_to_sixty_characters(tmp_path):
    long = "x" * 70
    kb = make_kb(tmp_path, "book", CH1, authored(full=[f"- point {i} (grep: `{a}`)" for i, a in enumerate(A)]
                                                  + [f"- {long}"]))
    r = C.check_card(kb, "ch01")
    assert r["problems"] == [f"F3 要点无锚点：- {'x' * 58}…"]


# --------------------------------------------------------------------------- A1 / A2 / A3 messages


def anchor_card(qs: list[str]) -> str:
    return authored(full=[f"- p{i} (grep: `{q}`)" for i, q in enumerate(qs)] + [f"- ok (grep: `{A[k]}`)" for k in range(5)])


def test_anchor_tag_is_cut_at_fifty_characters_with_ellipsis_only_beyond(tmp_path):
    q49 = "n" * 49 + "!"  # 50 chars... built below to be exact
    q50 = "a" * 50
    q51 = "b" * 51
    q60 = "c" * 60
    short = "dd"
    kb = make_kb(tmp_path, "book", CH1, anchor_card([q50, q51, q60, short]))
    probs = C.check_card(kb, "ch01")["problems"]
    assert probs == [
        f"A2 NOT FOUND：`{q50}`",
        f"A2 NOT FOUND：`{'b' * 50}…`",
        f"A2 NOT FOUND：`{'c' * 50}…`",
        "A1 锚点太短（2 字符）：`dd`",
        "A2 NOT FOUND：`dd`",
    ]
    assert len(q49) == 50


def test_anchor_of_exactly_fifteen_characters_is_long_enough(tmp_path):
    q15 = "copper shell th"  # 15 characters, present in the chapter
    assert len(q15) == 15 and q15 in CH1
    q14 = q15[:14]
    kb = make_kb(tmp_path, "book", CH1, anchor_card([q15, q14]))
    probs = C.check_card(kb, "ch01")["problems"]
    assert [p for p in probs if p.startswith("A1")] == ["A1 锚点太短（14 字符）：`copper shell t`"]


def test_a3_newline_and_whitespace_messages_and_the_scan_goes_on(tmp_path):
    thin = "x (grep: `first line of text\nsecond line of text`)\n"
    body = authored(thin=thin, full=[f"- p (grep: ` padded anchor text here `)", "- q (grep: `this anchor is not in the chapter`)"]
                    + [f"- ok (grep: `{A[k]}`)" for k in range(5)])
    kb = make_kb(tmp_path, "book", CH1, body)
    probs = C.check_card(kb, "ch01")["problems"]
    assert probs == [
        "A3 锚点含换行或首尾空白：`first line of text\nsecond line of text`",
        "A3 锚点含换行或首尾空白：`" + " padded anchor text here " + "`",
        "A2 NOT FOUND：`this anchor is not in the chapter`",
    ]


def test_a2_ambiguous_and_too_few_occurrences_messages(tmp_path):
    src = "the repeated phrase appears here\nthe repeated phrase appears here\n"
    bullets = [f"- a (grep: `the repeated phrase appears here`)", "- b (grep: `the repeated phrase appears here` #3)"]
    bullets += [f"- c{i} (grep: `the repeated phrase appears here` #2)" for i in range(4)]
    kb = make_kb(tmp_path, "book", src, authored(thin="t (grep: `the repeated phrase appears here` #2)\n", full=bullets))
    probs = C.check_card(kb, "ch01")["problems"]
    assert probs == ["A2 AMBIGUOUS（命中 2 处，加长或加 #N）：`the repeated phrase appears here`",
                     "A2 #3 但只命中 2 处：`the repeated phrase appears here`"]


# --------------------------------------------------------------------------- encoding (no locale dependence)


def test_card_and_source_are_read_as_utf8_whatever_the_locale(tmp_path):
    lines = ["血红蛋白 135 g/L 2026-07-01 ⟨r-aaaaaaaaaaaa⟩", "血红蛋白 128 g/L 2026-08-01 ⟨r-bbbbbbbbbbbb⟩"]
    kb = make_kb(tmp_path, "items", "\n".join(lines) + "\n", deterministic(lines), cid="hgb")
    code = ("import json, sys\nfrom klode_kit.build import check_cards as C\n"
            "print(json.dumps(C.check_card(sys.argv[1], 'hgb'), ensure_ascii=True))\n")
    env = {**os.environ, "LC_ALL": "C", "LANG": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0",
           "PYTHONIOENCODING": "ascii"}
    p = subprocess.run([sys.executable, "-c", code, str(kb)], env=env, capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    r = json.loads(p.stdout)
    assert (r["pass"], r["problems"], r["coverage"]) == (True, [], {"covered": 2, "lines": 2})


# --------------------------------------------------------------------------- _json_card


def test_json_card_splits_code_from_message_on_the_first_single_space_only():
    raw = {"id": "x", "problems": ["F1  two spaces first", "A2 NOT FOUND：`a b c`", "LONE"]}
    out = C._json_card(raw)
    assert out["problems"] == [{"code": "F1", "message": " two spaces first"},
                               {"code": "A2", "message": "NOT FOUND：`a b c`"},
                               {"code": "LONE", "message": ""}]
    assert raw["problems"][0] == "F1  two spaces first"  # input untouched
    assert out["id"] == "x"


# --------------------------------------------------------------------------- CLI


def bad_kb(tmp_path: Path) -> Path:
    kb = make_kb(tmp_path, "book", CH1, authored())
    (kb / "cards_content" / "ch02.md").write_text(authored(aliases="aliases: [a]\n", full=[f"- p (grep: `{A[0]}`)"]),
                                                  encoding="utf-8")
    (kb / "library" / "book" / "ch02.txt").write_text(CH1, encoding="utf-8")
    return kb


def test_text_output_is_exact(tmp_path, capsys):
    kb = bad_kb(tmp_path)
    code, out, err = run(capsys, ["--all", "--kb", str(kb)])
    assert code == 1 and err == ""
    assert out == ("PASS  ch01  (7 锚点)\n"
                   "FAIL  ch02  (2 锚点，2 个问题)\n"
                   "   ch02: F1 aliases 只有 1 个，需 ≥ 10\n"
                   "   ch02: MIN_FULL Full 段只有 1 个锚点，需 ≥ 5\n")


def test_json_output_is_indent_one_raw_unicode_and_splits_problems(tmp_path, capsys):
    kb = bad_kb(tmp_path)
    code, out, _ = run(capsys, ["--all", "--kb", str(kb), "--json"])
    assert code == 1
    doc = json.loads(out)
    assert out == json.dumps(doc, ensure_ascii=False, indent=1) + "\n"
    assert out.startswith('{\n "schema": "klode-kit.check_cards/1",\n "kb": ')
    assert "缺" not in out and "只有 1 个锚点" in out  # raw non-ASCII, not \u escapes
    assert doc["kb"] == str(kb)
    assert doc["cards"][1]["problems"] == [{"code": "F1", "message": "aliases 只有 1 个，需 ≥ 10"},
                                           {"code": "MIN_FULL", "message": "Full 段只有 1 个锚点，需 ≥ 5"}]


def test_policy_option_reaches_check_card(tmp_path, capsys):
    kb = make_kb(tmp_path, "book", CH1, authored())
    code, out, _ = run(capsys, ["ch01", "--kb", str(kb), "--policy", "deterministic"])
    assert code == 1
    assert "   ch01: POLICY 要求 deterministic，但源 library/book/ch01.txt 决定为 authored（按 authored 判定）\n" in out
    code, out, _ = run(capsys, ["ch01", "--kb", str(kb), "--policy", "authored"])
    assert code == 0


def test_project_override_reaches_check_card_thresholds(tmp_path, capsys):
    kb = make_kb(tmp_path, "book", CH1, authored(aliases="aliases: [a, b, c]\n", full=[f"- p (grep: `{A[0]}`)"]))
    code, out, _ = run(capsys, ["ch01", "--kb", str(kb)])
    assert code == 1 and "F1 aliases 只有 3 个，需 ≥ 10" in out and "MIN_FULL" in out
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "klode-kit.toml").write_text("[cards]\nmin_aliases = 3\nmin_anchors_full = 1\n", encoding="utf-8")
    code, out, _ = run(capsys, ["ch01", "--kb", str(kb), "--project", str(proj)])
    assert (code, out) == (0, "PASS  ch01  (2 锚点)\n")
    code, out, _ = run(capsys, ["--all", "--kb", str(kb), "--project", str(proj)])
    assert (code, out) == (0, "PASS  ch01  (2 锚点)\n")


def test_all_with_no_cards_message_is_exact(tmp_path, capsys):
    kb = tmp_path / "kb"
    (kb / "cards_content").mkdir(parents=True)
    code, out, err = run(capsys, ["--all", "--kb", str(kb)])
    assert (code, out) == (1, "")
    assert err == f"check-cards: no card in {kb / 'cards_content'}\n"


def test_missing_card_message_is_exact_in_text(tmp_path, capsys):
    kb = tmp_path / "kb"
    kb.mkdir()
    code, out, _ = run(capsys, ["ghost", "--kb", str(kb)])
    assert code == 1
    assert out == "FAIL  ghost  (0 锚点，1 个问题)\n   ghost: MISSING cards_content 文件不存在\n"


def test_usage_errors_are_worded_and_name_the_program(tmp_path, capsys):
    kb = make_kb(tmp_path, "book", CH1, authored())
    code, _, err = run(capsys, ["ch01", "--all", "--kb", str(kb)])
    assert code == 2
    assert err.startswith("usage: klode-kit check-cards [-h] [--all] [--kb KB] [--json]")
    assert "[CARD ...]" in err
    assert err.rstrip().endswith("klode-kit check-cards: error: give CARD ... or --all (not both)")
    code, _, err = run(capsys, ["../x.md", "--kb", str(kb)])
    assert code == 2
    assert err.rstrip().endswith("klode-kit check-cards: error: card must be cards_content/<id>.md or <id>: '../x.md'")


@pytest.mark.parametrize("arg", ["cards_content/ch01", "CARDS_CONTENT/ch01"])
def test_cards_content_prefix_without_md_is_a_usage_error(tmp_path, capsys, arg):
    kb = make_kb(tmp_path, "book", CH1, authored())
    code, _, err = run(capsys, [arg, "--kb", str(kb)])
    assert code == 2
    assert f"card must be cards_content/<id>.md or <id>: {arg!r}" in err


def test_help_exits_zero_and_documents_every_option(capsys, monkeypatch):
    monkeypatch.setenv("COLUMNS", "300")
    code, out, err = run(capsys, ["--help"])
    assert (code, err) == (0, "")
    text = " ".join(out.split())
    assert text.startswith("usage: klode-kit check-cards [-h] [--all] [--kb KB] [--json]")
    assert "[CARD ...]" in text
    assert "Gate 1: check ``cards_content/<id>.md`` and test every grep anchor against its source (WI-02a; vendored)." in text
    for frag in ("CARD cards_content/<id>.md or <id>", "--all check every cards_content/*.md",
                 "--kb KB build / library directory (default: cwd)", "--json print the result as JSON",
                 "--policy {auto,authored,deterministic} expected policy; the source's shelf decides (default auto) --project",
                 "--project PROJECT data project with klode-kit.toml overrides"):
        assert frag in text, frag
