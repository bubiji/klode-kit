"""WI-41 part A: the field-extraction fixes of the third confirm pass (P37) - unit lane (spec
``.claude/tdd-guardian/wi-41-spec.md``). All lines are invented, shaped like the real faults.

One test per rule A1-A7 with its negatives, then the cross-route case (two routes read one printed line as
``72--182`` and ``72-182``; align + vote must see one agreeing cell).

Law (S4/S5/S6): the offsets contract holds for every new shape (``line[start:end] == value``, generated), a range
written with a doubled or spaced separator has the same ``norm`` as the plain one (metamorphic), and a thousands
number's ``norm`` is its digits without commas (generated).
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from klode_kit.audit import align as A
from klode_kit.audit import compare as C
from klode_kit.audit import fields as F
from klode_kit.audit.fields import extract

pytestmark = pytest.mark.unit

PAGE = "d-000000000000/p001"
DER = "d" * 64


def kvn(line: str) -> list[tuple[str, str, str]]:
    return [(f.kind, f.value, f.norm) for f in extract(line)]


def nums(line: str) -> list[tuple[str, str]]:
    return [(f.value, f.norm) for f in extract(line) if f.kind == "number"]


def kinds(line: str, kind: str) -> list[str]:
    return [f.value for f in extract(line) if f.kind == kind]


def test_rules_revision_is_two():
    assert getattr(F, "FIELDS_RULES", 1) == 2


# --------------------------------------------------------------------------- A1 ranges


@pytest.mark.parametrize("line,value,norm", [
    ("参考 72--182", "72--182", "72-182"),
    ("参考 3.5 - 5.3", "3.5 - 5.3", "3.5-5.3"),
    ("参考 (9.40--15.50)", "9.40--15.50", "9.40-15.50"),
    ("参考 4.5～ 8", "4.5～ 8", "4.5-8"),
    ("参考 4.5 ～ 8", "4.5 ～ 8", "4.5-8"),
    ("参考 3.5－5.3", "3.5－5.3", "3.5-5.3"),
    ("参考 3.5 – 5.3", "3.5 – 5.3", "3.5-5.3"),
    ("参考 3.5—— 5.3", "3.5—— 5.3", "3.5-5.3"),
])
def test_range_with_doubled_or_spaced_separator_is_one_number(line, value, norm):
    assert nums(line) == [(value, norm)]


def test_plain_range_is_unchanged_and_parentheses_stay_out_of_the_value():
    assert nums("参考 72-182") == [("72-182", "72-182")]
    (f,) = [f for f in extract("(9.40--15.50)") if f.kind == "number"]
    assert (f.start, f.end) == (1, 12)


def test_three_separators_or_a_missing_right_number_are_not_one_range():
    assert nums("72---182") != [("72---182", "72-182")]
    assert nums("128 - 胆固醇") == [("128", "128")]


# --------------------------------------------------------------------------- A2 thousands


@pytest.mark.parametrize("line,value,norm", [
    ("总额 3,200", "3,200", "3200"),
    ("金额 ¥31,168", "31,168", "31168"),
    ("金额 ￥31，168", "31，168", "31168"),
    ("费用 10,065", "10,065", "10065"),
    ("费用 1,234,567.50", "1,234,567.50", "1234567.50"),
])
def test_thousands_separator_is_one_number_without_commas(line, value, norm):
    assert nums(line) == [(value, norm)]


def test_comma_not_followed_by_exactly_three_digits_is_not_a_separator():
    assert nums("1,2") == [("1", "1"), ("2", "2")]
    assert nums("1,23") == [("1", "1"), ("23", "23")]
    assert [v for v, _ in nums("1,2345")] == ["1", "2345"]


def test_thousands_number_with_glued_unit_keeps_its_unit():
    (f,) = [f for f in extract("剂量 1,000mg") if f.kind == "number"]
    assert (f.value, f.norm, f.unit) == ("1,000mg", "1000mg", "mg")


# --------------------------------------------------------------------------- A3 spaced decimal point


@pytest.mark.parametrize("line,value,norm", [
    ("血红蛋白 10. 1", "10. 1", "10.1"),
    ("血红蛋白 90. 0", "90. 0", "90.0"),
    ("10. 1", "10. 1", "10.1"),
    ("血红蛋白 10. 12 偏高", "10. 12", "10.12"),
])
def test_spaced_decimal_point_is_one_number(line, value, norm):
    assert nums(line) == [(value, norm)]


def test_spaced_decimal_point_needs_one_space_and_one_or_two_digits():
    assert [v for v, _ in nums("血红蛋白 10.  1")] == ["10", "1"]
    assert [v for v, _ in nums("血红蛋白 10. 123")] == ["10", "123"]
    assert [v for v, _ in nums("血红蛋白 1234. 5")] == ["1234", "5"]


def test_list_marker_followed_by_a_word_is_never_joined():
    assert nums("1. 血红蛋白 128") == [("1", "1"), ("128", "128")]
    assert [v for v, _ in nums("2. 尿素 7.3")] == ["2", "7.3"]


# --------------------------------------------------------------------------- A4 letter flags never inside a unit


@pytest.mark.parametrize("line", ["白细胞 6.2 10^9/μL", "白细胞 6.2 /μL", "白细胞 6.2 /µL", "白细胞 6.2 x10^9/L",
                                  "白细胞 6.2 10^9/L", "血红蛋白 128 g/L", "血小板 210 mL/μL"])
def test_l_inside_a_unit_is_not_a_flag(line):
    assert kinds(line, "flag") == []


@pytest.mark.parametrize("line", ["血红蛋白 128 μL", "字母 αL", "ABL 5", "H5", "5H", "5.2/H"])
def test_h_l_touching_a_letter_digit_slash_or_caret_is_not_a_flag(line):
    assert kinds(line, "flag") == []


def test_free_standing_letter_flags_still_count():
    assert kinds("血红蛋白 128 L", "flag") == ["L"]
    assert kinds("血红蛋白 128 H 参考", "flag") == ["H"]
    assert kinds("血红蛋白 128L 偏低", "flag") == []  # glued to the digit: part of nothing, not a flag


# --------------------------------------------------------------------------- A5 powers of ten


@pytest.mark.parametrize("line,value,norm", [
    ("白细胞 X10^4/μL", "X10^4/μL", "10^4/μL"),
    ("白细胞 x10^9/L", "x10^9/L", "10^9/L"),
    ("白细胞 *10^12/L", "*10^12/L", "10^12/L"),
    ("白细胞 ×10^3/μL", "×10^3/μL", "10^3/μL"),
    ("白细胞 10^9/μL", "10^9/μL", "10^9/μL"),
])
def test_power_of_ten_with_letter_x_or_mu_l_denominator_is_one_unit(line, value, norm):
    assert [(f.kind, f.value, f.norm) for f in extract(line) if f.kind != "drug"][-1:] == [("unit", value, norm)]
    assert kinds(line, "number") == [] and kinds(line, "flag") == []


def test_x10_4_per_mu_l_makes_no_number_and_no_flag():
    assert [f.kind for f in extract("血小板 X10^4/μL")] == ["unit"]


def test_ten_tilde_nine_per_l_is_still_not_a_unit():
    assert [f.kind for f in extract("白细胞 10~9/L") if f.kind == "unit"] == []


def test_glued_power_with_letter_x_belongs_to_the_number():
    (f,) = [f for f in extract("白细胞 4.2x10^9/L") if f.kind == "number"]
    assert (f.value, f.unit) == ("4.2x10^9/L", "10^9/L")


# --------------------------------------------------------------------------- A6 symbol flags inside words


@pytest.mark.parametrize("line", ["*[基] 项目", "22.5mg*4", "※注 请见背面", "项目*乙", "A*B"])
def test_star_or_reference_mark_inside_a_word_is_not_a_flag(line):
    assert kinds(line, "flag") == []


@pytest.mark.parametrize("line", ["血糖 5.2 *", "血糖 5.2*", "血糖 5.2 *)", "血糖 (5.2)*", "血糖 5.2↑", "血糖 5.2 ↓"])
def test_star_after_digit_or_space_and_before_space_or_end_is_still_a_flag(line):
    assert len(kinds(line, "flag")) == 1


def test_repeated_star_is_still_no_flag():
    assert kinds("血糖 5.2 ***", "flag") == []


# --------------------------------------------------------------------------- A7 digit glued to a CJK label


@pytest.mark.parametrize("line", ["物価対応料1", "物価対応料1 点", "評価料(I)2", "評価料(I)2 点", "诊疗(3+N联动)",
                                  "加算(3+N联动) 12"])
def test_digit_glued_to_a_cjk_item_name_is_not_a_number(line):
    assert [v for v, _ in nums(line) if v not in ("12",)] == []


def test_real_value_after_the_label_still_counts():
    assert nums("加算(3+N联动) 12") == [("12", "12")]
    assert nums("物価対応料1 点 15") == [("15", "15")]


def test_number_glued_to_cjk_with_a_unit_still_counts():
    (f,) = [f for f in extract("血红蛋白128g/L") if f.kind == "number"]
    assert (f.value, f.unit) == ("128g/L", "g/L")


def test_number_after_a_space_or_ascii_label_still_counts():
    assert nums("血红蛋白 128") == [("128", "128")]
    assert nums("Hb128") == []  # unchanged: a digit touching ASCII letters was never a number


# --------------------------------------------------------------------------- laws


NUM = st.integers(0, 999).map(str)
SEPS = st.sampled_from(["-", "--", " - ", "～", "～ ", " ~ ", "—", "－－", " – "])


@settings(max_examples=120, deadline=None)
@given(a=NUM, b=NUM, sep=SEPS)
def test_range_norm_does_not_depend_on_how_the_separator_is_written(a, b, sep):
    line = f"参考 {a}{sep}{b}"
    (f,) = [f for f in extract(line) if f.kind == "number"]
    assert f.norm == f"{a}-{b}" and f.value == f"{a}{sep}{b}"


@settings(max_examples=120, deadline=None)
@given(head=st.integers(1, 999), groups=st.lists(st.integers(0, 999), min_size=1, max_size=3))
def test_thousands_norm_is_the_digits_without_commas(head, groups):
    text = str(head) + "".join(f",{g:03d}" for g in groups)
    (f,) = [f for f in extract(f"金额 {text}") if f.kind == "number"]
    assert f.value == text and f.norm == text.replace(",", "")


LINES = st.lists(st.sampled_from(["72--182", "3.5 - 5.3", "(9.40--15.50)", "¥31,168", "10. 1", "X10^4/μL", "*10^12/L",
                                  "*[基]", "22.5mg*4", "物価対応料1", "評価料(I)2", "5.2 *", "128 L", "/μL", "1,2",
                                  "血红蛋白128g/L", "2026-10-22"]), min_size=1, max_size=5).map(" ".join)


@settings(max_examples=150, deadline=None)
@given(line=LINES)
def test_offsets_contract_and_no_overlap_for_every_new_shape(line):
    prev_end = 0
    for f in extract(line):
        assert line[f.start:f.end] == f.value
        assert f.start >= prev_end
        prev_end = f.end


# --------------------------------------------------------------------------- cross-route


COLS = (40, 330, 470, 620, 690)


def geo_line(engine: str, idx: int, text: str, row: int) -> dict:
    """A geometric line (exact tokens in fixed columns); ``row`` sets the y position."""
    import re

    top = 100 + 50 * row
    toks = []
    for i, m in enumerate(re.finditer(r"\S+", text)):
        x0 = COLS[i] if i < len(COLS) else COLS[-1] + 60 * (i - len(COLS) + 1)
        toks.append({"text": m.group(), "bbox_px": [x0, top, x0 + 12 * len(m.group()), top + 24],
                     "span": [m.start(), m.end()], "geometry": "exact"})
    bbox = [toks[0]["bbox_px"][0], top, toks[-1]["bbox_px"][2], top + 24]
    return {"engine": engine, "page_id": PAGE, "der_sha256": DER, "line_idx": idx, "text": text, "bbox_px": bbox,
            "tokens": toks}


def cloud(engine: str, texts: list[str]) -> list[dict]:
    return [{"engine": engine, "page_id": PAGE, "der_sha256": DER, "line_idx": i, "text": t, "bbox_px": None,
             "tokens": None} for i, t in enumerate(texts)]


def vote_of(vision_lines: list[str], claude_lines: list[str]) -> C.Comparison:
    routes = {"vision": [geo_line("vision", i, t, i) for i, t in enumerate(vision_lines)],
              "claude": cloud("claude", claude_lines)}
    return C.compare(A.align(routes), routes=["vision", "claude"])


def test_two_routes_reading_the_same_range_differently_written_agree_in_one_cell():
    cmp = vote_of(["血红蛋白 128 g/L 72-182", "尿素 7.3 mmol/L 3.5-5.3"],
                  ["血红蛋白 128 g/L 72--182", "尿素 7.3 mmol/L 3.5 - 5.3"])
    ranges = [v for v in cmp.cells if v.kind == "number" and v.k == 1]
    assert len(ranges) == 2
    assert [v.verdict for v in ranges] == [C.AGREE, C.AGREE]
    assert sorted(v.votes[0][0] for v in ranges) == ["3.5-5.3", "72-182"]
    assert all(v.verdict == C.AGREE for v in cmp.cells)


def test_before_the_fix_shape_would_have_split_the_slots():
    # a doubled-separator route and a plain route: the number cells line up one to one (no extra slot k=2)
    cmp = vote_of(["血红蛋白 128 g/L 72-182"], ["血红蛋白 128 g/L 72--182"])
    assert sorted((v.kind, v.k) for v in cmp.cells if v.kind == "number") == [("number", 0), ("number", 1)]
