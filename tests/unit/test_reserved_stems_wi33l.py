"""WI-33l (real-data finding P20): an item slug equal to a name klode reserves in the cards directory (``INDEX.md``,
compared case-insensitively because macOS file systems are) must not become a card file name: ``index`` would be
the same file as klode's board ``cards/INDEX.md`` and write-card fails ("找不到 scaffold 标记线")."""

from __future__ import annotations

import pytest
from hypothesis import given, strategies as st

from klode_kit import items

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("slug", ["index", "INDEX", "Index"])
def test_reserved_index_is_hashed(slug):
    stem = items.file_stem(slug)
    assert stem.lower() != "index"
    assert stem.startswith("u-")


def test_ordinary_slugs_unchanged():
    assert items.file_stem("hemoglobin") == "hemoglobin"
    assert items.file_stem("indexes") == "indexes"


@given(st.from_regex(r"[A-Za-z0-9][A-Za-z0-9-]{2,12}", fullmatch=True))
def test_no_stem_is_a_reserved_name(slug):
    assert items.file_stem(slug).lower() not in items.RESERVED_STEMS
