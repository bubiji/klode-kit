"""WI-33k: the item label is the whole label prefix of the row (real-data pilot P15).

Note: ``item_label`` is the resolved slug (lower-case, ``-`` for spaces), so expectations are slugs.

Law: ``item_label`` is the row text before its first value field (number/date/drug/unit), starting at the first
word piece, cut at a colon, pieces joined without whitespace when either side is CJK or the left piece ends with
``-``, with one space between two Latin words. It never contains a value field's text and never drops a
non-digit piece before the first value. Majority over routes is unchanged.

Matrix (lane unit, pure functions on plain rows): the three real pdftotext shapes (S1), Latin pair and colon cut
(S1/S2), multi-route majority with a split label (S2), property over generated prefixes (S4).
"""

from __future__ import annotations

import re

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from klode_kit.audit import align as A
from klode_kit.audit import fields as F
from unit.test_align import cloud, geo_line

pytestmark = pytest.mark.unit


def labels(*texts: str) -> list[str | None]:
    return [r.item_label for r in A.align({"pdftext": cloud("pdftext", list(texts))}).rows]


def test_real_pdftotext_shapes_keep_the_whole_name():
    assert labels("D- 二聚体测定 1.59 (mg/L) 0-0.5") == ["d-二聚体测定"]
    assert labels("B 型钠尿肽 120 pg/mL") == ["b型钠尿肽"]
    assert labels("r- 谷氨酰转移酶 25 (U/L) 7-60") == ["r-谷氨酰转移酶"]


def test_different_items_do_not_merge_under_one_short_label():
    al = A.align({"pdftext": cloud("pdftext", ["B 型钠尿肽 120 pg/mL", "B 淋巴细胞 12 %"])})
    assert len({r.item_label for r in al.rows}) == 2


def test_two_latin_words_keep_one_space_and_colon_still_cuts():
    assert labels("Free  T4 1.2 ng/dL") == ["free-t4"]
    assert labels("姓名：测试患者甲 1") == ["姓名"]
    assert labels("* 血红 蛋白 128 g/L") == ["hemoglobin"]


def test_new_items_use_the_full_label():
    al = A.align({"pdftext": cloud("pdftext", ["D- 二聚体测定 1.59 mg/L"])})
    assert al.new_items == ("d-二聚体测定",)


def test_multi_route_majority_is_unchanged():
    texts = ["血红蛋白 128 g/L", "尿素 7.3 mmol/L"]
    tess = [geo_line("tesseract", 0, "血红 蛋白 128 g/L", 0), geo_line("tesseract", 1, texts[1], 1)]
    vis = [geo_line("vision", i, t, i, geometry="approx") for i, t in enumerate(texts)]
    al = A.align({"tesseract": tess, "vision": vis, "claude": cloud("claude", texts)})
    assert al.rows[0].item_label == "hemoglobin"


PIECE = st.sampled_from(["D-", "B", "r-", "型钠尿肽", "二聚体", "Free", "T", "总", "ALT", "某某", "指数"])
VALUE = st.sampled_from(["1.59 (mg/L)", "120 pg/mL", "25 U/L", "7.3"])


@settings(max_examples=60, deadline=None)
@given(st.lists(PIECE, min_size=1, max_size=4), VALUE)
def test_label_never_holds_a_value_and_never_drops_a_piece(pieces, value):
    text = " ".join(pieces) + " " + value
    [label] = labels(text)
    squashed = re.sub(r"[\s-]+", "", label)
    assert squashed == re.sub(r"[\s-]+", "", "".join(pieces)).lower()
    first = F.extract(text)[0]
    assert re.sub(r"\W", "", first.value) not in label
