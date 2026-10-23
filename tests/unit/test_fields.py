"""WI-07a: field extraction (matrix 07a-01..08; plan WI-07a).

Contract under test (``klode_kit.audit.fields``):

- ``extract(line) -> list[Field]``; ``Field(kind, value, start, end, norm, unit=None, dose=None)``;
  ``kind`` is one of ``number | date | drug | unit | flag``; fields come back in line order and never overlap.
- ``value`` is always the literal source text: ``line[start:end] == value`` (full-width stays full-width);
  ``norm`` is the comparison form (NFKC-folded; dates ISO ``YYYY-MM-DD``; powers of ten ``10^N/L``;
  range separators ``-``).
- A unit glued to a number (no whitespace between, e.g. ``10mg``, ``4.2×10^9/L``) belongs to the number cell:
  one ``number`` field whose ``unit`` is the normalised unit (rx: the unit column is empty, WI-00a deviation 7).
  A unit standing on its own (``128 g/L``) is a ``unit`` field.
- A drug name from ``config/fields/drugs.txt`` followed (after whitespace) by a glued-unit number carries that
  number's value as ``dose``.

Law (07a-01): ``line[s:e] == value`` for every field (S4). Metamorphic (07a-02): a line and its full-width
version give the same kinds and the same NFKC values / norms (S6).
"""

from __future__ import annotations

import unicodedata

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from klode_kit.audit.fields import Field, extract

pytestmark = pytest.mark.unit


def nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", s)


def kv(fields: list[Field]) -> list[tuple[str, str]]:
    return [(f.kind, f.value) for f in fields]


def to_fullwidth(s: str) -> str:
    out = []
    for ch in s:
        if ch == " ":
            out.append("　")
        elif 0x21 <= ord(ch) <= 0x7E:
            out.append(chr(ord(ch) + 0xFEE0))
        else:
            out.append(ch)
    return "".join(out)


# --------------------------------------------------------------------------- generators

_decimal = st.builds(lambda i, f: f"{i}.{f}", st.integers(0, 999), st.from_regex(r"\d{1,3}", fullmatch=True))
_plain_number = st.one_of(st.integers(0, 99999).map(str), _decimal)
_number = st.one_of(
    _plain_number,
    st.builds(lambda a, sep, b: f"{a}{sep}{b}", _plain_number, st.sampled_from(["-", "~"]), _plain_number),
    _plain_number.map(lambda n: f"<{n}"),
    _plain_number.map(lambda n: f">{n}"),
)
_unit = st.sampled_from(["g/L", "mmol/L", "μmol/L", "U/L", "%", "fL", "mg", "10^9/L", "10^12/L", "×10^9/L"])
_dose = st.builds(lambda n, u: f"{n}{u}", _plain_number, st.sampled_from(["mg", "g", "μg", "mL", "IU"]))
_date = st.builds(
    lambda y, m, d, form: form.format(y=y, m=m, d=d),
    st.integers(1990, 2099),
    st.integers(1, 12),
    st.integers(1, 28),
    st.sampled_from(["{y:04d}-{m:02d}-{d:02d}", "{y:04d}/{m}/{d}", "{y:04d}年{m}月{d}日"]),
)
_drug = st.sampled_from(["示例片", "示例胶囊"])
_flag = st.sampled_from(["H", "L", "↑", "↓", "*"])
_filler = st.sampled_from(["血红蛋白", "白细胞", "结果", "参考范围", "HGB", "每日"])

_piece = st.one_of(
    st.tuples(st.just("number"), _number),
    st.tuples(st.just("unit"), _unit),
    st.tuples(st.just("number"), _dose),
    st.tuples(st.just("date"), _date),
    st.tuples(st.just("drug"), _drug),
    st.tuples(st.just("flag"), _flag),
    st.tuples(st.just(None), _filler),
)
_sep = st.sampled_from([" ", "  ", "\t", "　"])


@st.composite
def lines(draw, fullwidth_pieces: bool = True):
    """A line made of pieces joined by whitespace; returns (line, [(kind, piece_text)] for non-filler pieces)."""
    pieces = draw(st.lists(_piece, min_size=1, max_size=8))
    out, expected = "", []
    for i, (kind, text) in enumerate(pieces):
        if fullwidth_pieces and draw(st.booleans()):
            text = to_fullwidth(text)
        if i:
            out += draw(_sep)
        out += text
        if kind is not None:
            expected.append((kind, text))
    return out, expected


# --------------------------------------------------------------------------- 07a-01 span law


@settings(max_examples=400, deadline=None)
@given(lines())
@example(("示例片 10mg 每日1次", [("drug", "示例片"), ("number", "10mg")]))
@example(("血红蛋白　１２８　ｇ／Ｌ", [("number", "１２８"), ("unit", "ｇ／Ｌ")]))
def test_span_property(case):
    """07a-01 (L1-S4): every field's span reproduces its value exactly; fields are ordered and disjoint."""
    line, _ = case
    fields = extract(line)
    for f in fields:
        assert line[f.start:f.end] == f.value
        assert f.kind in ("number", "date", "drug", "unit", "flag")
    for a, b in zip(fields, fields[1:]):
        assert a.end <= b.start


@settings(max_examples=400, deadline=None)
@given(lines())
def test_pieces_recovered_property(case):
    """07a-01b (L1-S4): a line joined from known pieces yields exactly those pieces, in order, with their kinds."""
    line, expected = case
    assert kv(extract(line)) == expected


# --------------------------------------------------------------------------- 07a-02 full-width metamorphic


@settings(max_examples=400, deadline=None)
@given(lines(fullwidth_pieces=False))
@example(("4.2×10^9/L <0.1 2026/9/18 H", []))
def test_fullwidth_metamorphic(case):
    """07a-02 (L1-S6): a line and its full-width version give the same kinds, NFKC values and norms."""
    half, _ = case
    full = to_fullwidth(half)
    a, b = extract(half), extract(full)
    assert [f.kind for f in a] == [f.kind for f in b]
    assert [nfkc(f.value) for f in a] == [nfkc(f.value) for f in b]
    assert [f.norm for f in a] == [f.norm for f in b]
    assert [f.unit for f in a] == [f.unit for f in b]
    for f in b:
        assert full[f.start:f.end] == f.value


# --------------------------------------------------------------------------- 07a-03 .. 08 examples


def test_10_5_vs_1_05_distinct():
    """07a-03 (L1-S3): 10.5 and 1.05 are different values (the fx-redo misread must stay visible)."""
    a, b = extract("10.5"), extract("1.05")
    assert kv(a) == [("number", "10.5")]
    assert kv(b) == [("number", "1.05")]
    assert a[0].value != b[0].value
    assert a[0].norm != b[0].norm
    ra, rb = extract("示例片 10.5mg"), extract("示例片 1.05mg")
    assert ra[1].value == "10.5mg" and rb[1].value == "1.05mg"
    assert ra[1].norm != rb[1].norm


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("1.05", [("number", "1.05", "1.05", None)]),
        ("3.5-5.5", [("number", "3.5-5.5", "3.5-5.5", None)]),
        ("3.5~5.5", [("number", "3.5~5.5", "3.5-5.5", None)]),
        ("3.5～5.5", [("number", "3.5～5.5", "3.5-5.5", None)]),
        ("4.2×10^9/L", [("number", "4.2×10^9/L", "4.2×10^9/L", "10^9/L")]),
        ("<0.1", [("number", "<0.1", "<0.1", None)]),
        ("10^9/L", [("unit", "10^9/L", "10^9/L", None)]),
        ("×10^9/L", [("unit", "×10^9/L", "10^9/L", None)]),
        # WI-04a F-2: MLX LaTeX flattened; fx-lowres renders {10}^{\times 9} -> 10^×9/L
        ("10^×9/L", [("unit", "10^×9/L", "10^9/L", None)]),
        ("10^×12/L", [("unit", "10^×12/L", "10^12/L", None)]),
        ("6.2 10^×9/L", [("number", "6.2", "6.2", None), ("unit", "10^×9/L", "10^9/L", None)]),
        ("128 g/L", [("number", "128", "128", None), ("unit", "g/L", "g/L", None)]),
        ("4.35 mmol/L", [("number", "4.35", "4.35", None), ("unit", "mmol/L", "mmol/L", None)]),
        ("78.5 %", [("number", "78.5", "78.5", None), ("unit", "%", "%", None)]),
    ],
)
def test_recognize_number_forms(line, expected):
    """07a-04 (L1-S2): decimals, ranges, powers of ten (incl. F-2 forms), comparators; kind, value, norm, unit."""
    got = extract(line)
    assert [(f.kind, f.value, f.norm, f.unit) for f in got] == expected
    for f in got:
        assert line[f.start:f.end] == f.value


@pytest.mark.parametrize(
    ("line", "raw"),
    [
        ("2026-09-18", "2026-09-18"),
        ("2026/9/18", "2026/9/18"),
        ("2026年9月18日", "2026年9月18日"),
        ("日期：2026-09-18", "2026-09-18"),
        ("日期：2026/9/18 复诊", "2026/9/18"),
        ("采样 2026年9月18日 上午", "2026年9月18日"),
    ],
)
def test_dates_three_forms(line, raw):
    """07a-05 (L1-S2): three date spellings normalise to 2026-09-18 and the span points at the source text."""
    dates = [f for f in extract(line) if f.kind == "date"]
    assert len(dates) == 1
    d = dates[0]
    assert d.value == raw
    assert d.norm == "2026-09-18"
    assert line[d.start:d.end] == raw
    assert d.start == line.index(raw)


def test_impossible_date_is_not_a_date():
    """07a-05b (L1-S3): month 13 / day 45 is not a date."""
    assert [f for f in extract("2026-13-45") if f.kind == "date"] == []


@pytest.mark.parametrize("flag", ["H", "L", "↑", "↓", "*"])
def test_flags_boundary(flag):
    """07a-06 (L1-S2): each flag on its own is one flag field."""
    got = extract(flag)
    assert [(f.kind, f.value, f.norm) for f in got] == [("flag", flag, flag)]


def test_flag_letter_inside_word_is_not_flag():
    """07a-06 (L1-S2): the H of HGB (and the L of g/L) are not flags; flags in a table row are."""
    assert extract("HGB") == []
    assert [f for f in extract("HGB 128 g/L") if f.kind == "flag"] == []
    assert kv(extract("中性粒细胞百分比 78.5 % H 40-75")) == [
        ("number", "78.5"), ("unit", "%"), ("flag", "H"), ("number", "40-75")]
    assert kv(extract("淋巴细胞百分比 15.2 % L 20-50")) == [
        ("number", "15.2"), ("unit", "%"), ("flag", "L"), ("number", "20-50")]
    assert kv(extract("78.5↑")) == [("number", "78.5"), ("flag", "↑")]


def test_drug_dose():
    """07a-07 (L1-S1): drug name + glued dose; the unit is part of the value cell (rx, unit column empty)."""
    got = extract("示例片 10mg")
    assert kv(got) == [("drug", "示例片"), ("number", "10mg")]
    drug, dose = got
    assert drug.dose == "10mg"
    assert dose.unit == "mg"
    assert dose.norm == "10mg"
    assert "示例片 10mg"[drug.start:drug.end] == "示例片"
    row = extract("示例片　0.25g　必要时")
    assert kv(row) == [("drug", "示例片"), ("number", "0.25g")]
    assert row[0].dose == "0.25g"


def test_drug_without_dose():
    """07a-07b (L1-S2): a drug with no glued-unit number after it has no dose."""
    got = extract("示例片 每日1次")
    assert got[0].kind == "drug" and got[0].dose is None


@pytest.mark.parametrize("line", ["", "-----", "   ", "　\t", "——", "结果 单位 提示 参考范围"])
def test_empty_and_noise_line(line):
    """07a-08 (L1-S2): empty, separator-only and header-only lines yield no fields."""
    assert extract(line) == []


def test_identifier_digits_are_not_numbers():
    """07a-09 (L1-S3): the digits of an identifier such as T-0000001 are not a measured number."""
    assert extract("编号：T-0000001") == []


# --------------------------------------------------------------------------- added after the red (edges)


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("10⁹/L", [("unit", "10⁹/L", "10^9/L")]),
        ("×10¹²/L", [("unit", "×10¹²/L", "10^12/L")]),
        ("***", []),
        ("↑↑", []),
        ("5mgs", []),
        ("mmol/Lx", []),
        ("g/L H", [("unit", "g/L", "g/L"), ("flag", "H", "H")]),
    ],
)
def test_edge_forms(line, expected):
    """07a-13 (L1-S2): superscript powers stay superscript in the value; repeated symbols and units running
    into letters are not fields."""
    got = extract(line)
    assert [(f.kind, f.value, f.norm) for f in got] == expected
    for f in got:
        assert line[f.start:f.end] == f.value
