"""WI-07b: alignment edge cases (added after the recorded red, for the S6 critical-path branch bar).

Same public surface as ``test_align.py``: ``align(routes)`` -> rows / pairs / cells / uncertain.
- Vertical CJK text columns (taller than twice their width, >= 2 non-ASCII characters) never join a horizontal
  row; they are rows of their own after the horizontal rows, left to right, and two columns share a row only
  when they overlap horizontally too (fx-vertical shape).
- A boxless line of a token route does not seed a row; it pairs by text like a cloud line.
- An empty line matches nothing; a row whose text has no label (only numbers) has ``item_label`` None.
- The label skips leading pieces without a letter (``*`` markers) or with a digit.
- On an exact row, a field no token covers keeps ``col``/``geom`` None but is not ``no_geometry``.
"""

from __future__ import annotations

import pytest

from klode_kit import items as I
from klode_kit.audit import align as A
from klode_kit.config import ConfigError
from unit.test_align import cloud, cloud_line, geo_line

pytestmark = pytest.mark.unit

ROWS = ["血红蛋白 128 g/L", "尿素 7.3 mmol/L", "白细胞 6.2 10^9/L"]


def column(engine: str, idx: int, text: str, x0: int, *, geometry: str = "exact") -> dict:
    """A vertical text column: one character every 30 px from y=100, 24 px wide."""
    toks = [{"text": ch, "bbox_px": [x0, 100 + 30 * i, x0 + 24, 124 + 30 * i], "span": [i, i + 1],
             "geometry": geometry} for i, ch in enumerate(text)]
    return {"engine": engine, "page_id": "d-000000000000/p001", "der_sha256": "d" * 64, "line_idx": idx,
            "text": text, "bbox_px": [x0, 100, x0 + 24, 124 + 30 * (len(text) - 1)], "tokens": toks}


def test_vertical_columns_are_rows_after_the_table():
    vision = [geo_line("vision", i, t, i) for i, t in enumerate(ROWS)]
    vision += [column("vision", 3, "合成样本仅供测试", 900), column("vision", 4, "示例专用章", 960)]
    tess = [geo_line("tesseract", i, t, i) for i, t in enumerate(ROWS)] + [column("tesseract", 3, "合成样本仅供测试", 902)]
    al = A.align({"vision": vision, "tesseract": tess,
                  "claude": cloud("claude", [*ROWS, "合成样本仅供测试", "示例专用章"])})
    assert len(al.rows) == 5
    assert [dict(r.lines).get("vision") for r in al.rows] == [(0,), (1,), (2,), (3,), (4,)]
    assert dict(al.rows[3].lines)["tesseract"] == (3,)
    assert [p.rows for p in al.pairs["claude"]] == [(0,), (1,), (2,), (3,), (4,)]
    assert al.uncertain == (("unaligned:tesseract", 4),)
    assert {c.row for c in al.cells} == {0, 1, 2}


def test_boxless_line_of_token_route_pairs_by_text():
    tess = [geo_line("tesseract", i, t, i) for i, t in enumerate(ROWS)]
    vision = [geo_line("vision", 0, ROWS[0], 0, geometry="approx"),
              cloud_line("vision", 1, ROWS[1]) | {"tokens": []},
              geo_line("vision", 2, ROWS[2], 2, geometry="approx")]
    al = A.align({"vision": vision, "tesseract": tess})
    assert [p.rows for p in al.pairs["vision"]] == [(0,), (1,), (2,)]
    c = next(c for c in al.cells if (c.row, c.kind) == (1, "number"))
    assert c.candidate("vision").bbox_px is None and c.geom == "tesseract"


def test_empty_line_matches_nothing():
    al = A.align({"vision": [geo_line("vision", 0, ROWS[0], 0)], "claude": cloud("claude", ["", ROWS[0]])})
    assert al.extra["claude"] == (0,)
    assert [p.rows for p in al.pairs["claude"]] == [(0,)]


def test_labels_skip_markers_and_numbers():
    texts = ["* 血红蛋白 128 g/L", "128 64", "某某指数 3.2"]
    al = A.align({"vision": [geo_line("vision", i, t, i) for i, t in enumerate(texts)]})
    assert [r.item_label for r in al.rows] == ["hemoglobin", None, "某某指数"]
    assert al.new_items == ("某某指数",)
    assert {c.item_label for c in al.cells if c.row == 1} == {None}


def test_field_without_covering_token_on_exact_row():
    line = geo_line("vision", 0, ROWS[0], 0)
    line["tokens"] = line["tokens"][:1]          # only 血红蛋白 has a box
    al = A.align({"vision": [line]})
    num = next(c for c in al.cells if c.kind == "number")
    assert (num.col, num.geom) == (None, None)
    assert A.NO_GEOMETRY not in num.uncertain
    assert num.candidate("vision").bbox_px == tuple(line["bbox_px"])


def test_items_edges(tmp_path):
    with pytest.raises(ValueError):
        I.slugify("%%")
    p = tmp_path / "items.yaml"
    p.write_text("x:\n  aliases: []\n", encoding="utf-8")
    assert I.load(p).resolve("X") == ("x", True)
    p.write_text("", encoding="utf-8")
    assert I.load(p).items == ()
    with pytest.raises(ConfigError):
        I.Items([I.Item("a", None, ("同",)), I.Item("b", None, ("同",))])
