"""WI-01c: ``deident`` text edges added while implementing (after the red receipt; no recorded line changed).

- strings that look like dates but are not calendar dates (or fall outside 1900..2100) are never moved;
- overlapping identifier matches are replaced once, by one pseudonym covering the longest match;
- the key file: a second ``load_key`` (create or not) returns the stored key, never a new one.
"""

from __future__ import annotations

import pytest

from klode_kit.build import deident as D

pytestmark = pytest.mark.unit

KEY = D.Key(salt=bytes(range(32)), offset_days=500)


@pytest.mark.parametrize("text", ["2026-13-45", "2026/02/30", "2026.00.10", "2026年2月30日", "2026年13月1日",
                                  "20261399", "20260230", "1800-01-01", "2200年1月1日", "21000230"])
def test_non_dates_never_move(text):
    """(S3) under ``dateshift`` a date-shaped string that is no calendar date stays as it is, and is not a date."""
    assert D.scrub(f"复查 {text} 后", KEY, "dateshift") == f"复查 {text} 后"
    assert D.find_dates(text) == []


def test_overlapping_identifiers_replaced_once():
    """(S1) a mobile number that is also the token of a phone label, and a code whose digits are also a 9+ digit
    run, each become exactly one pseudonym."""
    mobile = "".join(["1", "39", "0000", "1111"])
    for text in (f"联系电话：{mobile}", "门诊号 AB123456789"):
        spans = D.find_identifiers(text)
        assert len(spans) == 1
        out = D.scrub(text, KEY, "pseudonym")
        assert out.count("[ID-") == 1 and not any(ch.isdigit() for ch in out)


def test_load_key_returns_stored_key(tmp_path):
    """(S5) create, then load with and without ``create``: the same key every time, the file unchanged."""
    first = D.load_key(tmp_path)
    raw = (tmp_path / D.KEY_REL).read_bytes()
    again, stored = D.load_key(tmp_path), D.load_key(tmp_path, create=False)
    assert (again.salt, again.offset_days) == (stored.salt, stored.offset_days) == (first.salt, first.offset_days)
    assert (tmp_path / D.KEY_REL).read_bytes() == raw
