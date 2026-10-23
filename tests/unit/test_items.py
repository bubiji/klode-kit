"""WI-07b: ``items.yaml`` loader (matrix 07b-10, 07b-11; design 02 r2.2-C 项目归一).

Contract under test (``klode_kit.items``):

- ``load(path=None) -> Items``; ``None`` loads the packaged ``klode_kit/config/items.yaml``; ``default()`` is the
  cached packaged table. The file is a strict YAML subset (no new runtime dependency)::

      # comment
      <slug>:                       # [a-z0-9][a-z0-9_-]*
        category: <text>            # optional
        aliases: [<a>, <b>, ...]    # flow list on one line; items may be "double-quoted"

  Anything else is a ``ConfigError`` (exit 40) naming the line.
- ``Items.resolve(label) -> (slug, known)``: aliases and slugs compare after NFKC + casefold + whitespace
  removal; an unknown label resolves to ``slugify(label)`` with ``known = False``.
- ``Items.new_items(labels) -> list[str]``: slugs of the unknown labels, first-seen order, no repeats.
- One alias (after normalisation) under two items, or an alias equal to another item's slug, is a
  ``ConfigError`` with ``exit_code == 40``.

No law: a lookup table with validation; the matrix rows are examples and guards (S2/S3).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from klode_kit import items as I
from klode_kit.config import ConfigError

pytestmark = pytest.mark.unit


def write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "items.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_items_alias_normalize_and_new_items():
    """07b-10 (L1-S2): HGB normalises to hemoglobin (also Hb, full-width ＨＧＢ, padded 血红蛋白); the unknown item
    「某某指数」 resolves to its own slug and is reported once in new_items."""
    items = I.default()
    assert items.resolve("HGB") == ("hemoglobin", True)
    assert items.resolve("Hb") == ("hemoglobin", True)
    assert items.resolve("ＨＧＢ") == ("hemoglobin", True)
    assert items.resolve(" 血红 蛋白 ") == ("hemoglobin", True)
    assert items.resolve("hemoglobin") == ("hemoglobin", True)
    assert items.resolve("某某指数") == ("某某指数", False)
    assert items.new_items(["HGB", "某某指数", "血红蛋白", "某某指数", "Other Index"]) == ["某某指数", "other-index"]
    assert items.category("hemoglobin") == "血常规"
    assert items.category("nope") is None


def test_packaged_table_covers_fixture_items():
    """The packaged table knows every item the synthetic fixtures use, with the fx-chain slugs."""
    items = I.default()
    want = {"血红蛋白": "hemoglobin", "白细胞": "wbc", "红细胞": "rbc", "血小板": "platelet", "尿素": "urea",
            "中性粒细胞百分比": "neutrophil-pct", "淋巴细胞百分比": "lymphocyte-pct", "示例指标": "sample-index",
            "示例片": "sample-tablet"}
    assert {k: items.resolve(k) for k in want} == {k: (v, True) for k, v in want.items()}
    assert I.load() is not I.default() and I.load().resolve("WBC") == ("wbc", True)


def test_items_duplicate_alias_rejected(tmp_path):
    """07b-11 (L1-S3): one alias under two items -> ConfigError, exit code 40; also when the two spellings only
    differ by width / case, and when an alias equals another item's slug."""
    for body in (
        "hemoglobin:\n  aliases: [血红蛋白, HGB]\nother:\n  aliases: [HGB]\n",
        "hemoglobin:\n  aliases: [血红蛋白, HGB]\nother:\n  aliases: [ｈｇｂ]\n",
        "hemoglobin:\n  aliases: [血红蛋白]\nother:\n  aliases: [Hemoglobin]\n",
    ):
        with pytest.raises(ConfigError) as exc:
            I.load(write(tmp_path, body))
        assert exc.value.exit_code == 40


def test_items_subset_parser(tmp_path):
    """Comments, blank lines, quoted aliases and an item without category load; repeats inside one item are
    harmless."""
    p = write(tmp_path, '# items\n\nurea:\n  category: 肝肾  # kidney\n  aliases: ["尿素", BUN, BUN]\n'
                        "k:\n  aliases: [钾]\n")
    items = I.load(p)
    assert items.resolve("bun") == ("urea", True)
    assert items.resolve("钾") == ("k", True)
    assert items.category("urea") == "肝肾" and items.category("k") is None


@pytest.mark.parametrize("body, line", [
    ("Bad Slug:\n  aliases: [x]\n", 1),
    ("ok:\n  aliases: x, y\n", 2),
    ("ok:\n  colour: red\n", 2),
    ("  aliases: [x]\n", 1),
    ("ok:\n  aliases: [x, ]\n", 2),
    ("ok:\n  aliases: [x]\nok:\n  aliases: [y]\n", 3),
    ("ok:\n  aliases: [x]\n  aliases: [y]\n", 3),
])
def test_items_malformed_rejected_with_line(tmp_path, body, line):
    with pytest.raises(ConfigError) as exc:
        I.load(write(tmp_path, body))
    assert exc.value.exit_code == 40
    assert f":{line}" in str(exc.value)


def test_items_missing_file_and_empty_label(tmp_path):
    with pytest.raises(ConfigError):
        I.load(tmp_path / "absent.yaml")
    with pytest.raises(ValueError):
        I.default().resolve("  ")


@pytest.mark.parametrize("label, slug", [
    ("Other Index", "other-index"),
    ("某某 指数", "某某-指数"),
    ("Neutrophil %", "neutrophil"),
    ("ＡＬＴ（谷丙）", "alt-谷丙"),
])
def test_slugify(label, slug):
    assert I.slugify(label) == slug
