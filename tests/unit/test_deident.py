"""WI-01c: ``deident`` - two de-identification modes (plan WI-01c; matrix 01c-01..08; design 02 §3.4 脱敏行,
r2.1 1.2, r3.1 segment 3).

Contract under test (``klode_kit.build.deident``):

- ``pseudonym(value, salt, kind="name") -> str``: a stable pseudonym (salted hash): the same input and salt give
  the same pseudonym, different inputs different ones, another salt another one.
- ``shift_date(value, days) -> str``: the date moved by ``days``; a value that is not a calendar date is a
  ``DeidentError``.
- ``scrub(text, key, mode) -> str``: names / identifiers -> pseudonyms in both modes; dates move by the key's
  single offset only in ``dateshift``.
- ``load_key(project, create=True) -> Key`` (``salt``, ``offset_days``): the key lives only in
  ``<project>/_private/deident_key.json``.
- ``records_to_txt.export(project, out, deident=<mode>)``: the export view under a mode. ``pseudonym`` (the
  default mode of the design) leaves dates alone; ``dateshift`` moves every date of the export - txt, the L1 cards
  built from it, the records shelf - by the one offset of the project key.

Fixtures: the real stages on ``fx-labs-3x`` (``export_fx.pipeline``) and the OCR lines of every fixture. No mock.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from _support import export_fx as X
from klode_kit.build import deident as D
from klode_kit.build import records_to_l1 as L
from klode_kit.build import records_to_txt as R
from klode_kit.devtools import privacy_scan as P

pytestmark = pytest.mark.unit

LABS3 = X.FIXTURES / "fx-labs-3x"
ISO = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
CJK_NAME = st.text(st.characters(min_codepoint=0x4E00, max_codepoint=0x9FA5), min_size=1, max_size=6)
OFFSETS = st.one_of(st.integers(min_value=-3650, max_value=-1), st.integers(min_value=1, max_value=3650))


def proj(tmp_path: Path) -> Path:
    p = tmp_path / "proj"
    p.mkdir()
    return p


def shifted_iso(day: str, days: int) -> str:
    return (dt.date.fromisoformat(day) + dt.timedelta(days=days)).isoformat()


def forms(day: str) -> list[str]:
    """The five spellings records_to_l1 writes for a date (ISO first)."""
    y, m, d = day.split("-")
    return [day, f"{y}/{m}/{d}", f"{y}.{m}.{d}", f"{y}{m}{d}", f"{y}年{int(m)}月{int(d)}日"]


def move_dates(text: str, days: int, originals: set[str]) -> str:
    """``text`` with every spelling of every original date replaced by the spelling of the moved date."""
    pairs = [(a, b) for day in sorted(originals) for a, b in zip(forms(day), forms(shifted_iso(day, days)))]
    marks = {}
    for i, (a, _) in enumerate(pairs):
        mark = f"\x00{i}\x00"
        marks[mark] = pairs[i][1]
        text = text.replace(a, mark)
    for mark, b in marks.items():
        text = text.replace(mark, b)
    return text


# --------------------------------------------------------------------------- 01c-01


@settings(max_examples=80, deadline=None)
@given(st.lists(CJK_NAME, min_size=1, max_size=30), st.binary(min_size=16, max_size=32))
def test_pseudonym_stable_and_injective(names, salt):
    """01c-01 (S5): de-identifying twice gives the same pseudonym for the same name; within the sample, different
    names give different pseudonyms; the pseudonym never holds the name."""
    first = [D.pseudonym(n, salt) for n in names]
    second = [D.pseudonym(n, salt) for n in names]
    assert first == second
    by_name = dict(zip(names, first))
    assert len(set(by_name.values())) == len(by_name)
    assert all(n not in p for n, p in by_name.items())
    key = D.Key(salt=salt, offset_days=400)
    texts = [f"姓名：{n}" for n in names]
    assert [D.scrub(t, key, "pseudonym") for t in texts] == [D.scrub(t, key, "pseudonym") for t in texts]


# --------------------------------------------------------------------------- 01c-02


def test_pseudonym_dates_untouched(tmp_path):
    """01c-02 (S1): fx-labs-3x exported in ``pseudonym`` mode: every exported byte equals the plain export (the
    fixture's dates included) and no export map row carries a ``dateshift`` transform."""
    project = proj(tmp_path)
    X.pipeline(project, LABS3)
    plain = R.export(project)
    pseudo = R.export(project, deident="pseudonym")
    assert pseudo["files"] == plain["files"]
    assert pseudo["items_index"] == plain["items_index"]
    days = {p["date"] for p in json.loads((LABS3 / "scenario.json").read_text(encoding="utf-8"))["pages"]}
    shown = {m.group(0) for text in pseudo["files"].values() for m in ISO.finditer(text)}
    assert shown == days
    assert not any(t["kind"] == "dateshift" for row in pseudo["export_map"] for t in row["transforms"])


# --------------------------------------------------------------------------- 01c-03


@settings(max_examples=120, deadline=None)
@given(st.lists(st.dates(min_value=dt.date(1950, 1, 1), max_value=dt.date(2090, 12, 31)), min_size=2,
                max_size=25), OFFSETS)
def test_dateshift_preserves_order_and_intervals(days, offset):
    """01c-03 (S5): one offset applied to a list of dates keeps their order and every adjacent interval."""
    moved = [dt.date.fromisoformat(D.shift_date(d.isoformat(), offset)) for d in days]
    for (a, b), (ma, mb) in zip(zip(days, days[1:]), zip(moved, moved[1:])):
        assert (mb - ma) == (b - a)
        assert (a < b) == (ma < mb) and (a == b) == (ma == mb)
    assert sorted(range(len(days)), key=lambda i: (days[i], i)) == sorted(range(len(moved)),
                                                                           key=lambda i: (moved[i], i))


# --------------------------------------------------------------------------- 01c-04


def test_dateshift_single_offset_across_views(tmp_path):
    """01c-04 (S5): fx-labs-3x exported in ``dateshift`` mode. The items txt, the records txt, the items index and
    the L1 cards generated from that export are exactly the plain views with every date (in every spelling) moved
    by the project key's one offset; every ``dateshift`` transform moves its date by that same offset."""
    project = proj(tmp_path)
    X.pipeline(project, LABS3)
    plain_out, shift_out = tmp_path / "plain", tmp_path / "shift"
    plain = R.export(project, plain_out)
    shifted = R.export(project, shift_out, deident="dateshift")
    offset = D.load_key(project, create=False).offset_days
    assert offset != 0
    originals = {m.group(0) for text in plain["files"].values() for m in ISO.finditer(text)}
    assert len(originals) == 3

    assert set(shifted["files"]) == set(plain["files"])
    for rel, text in plain["files"].items():
        assert shifted["files"][rel] == move_dates(text, offset, originals), rel
    assert {s: [dict(e, date=shifted_iso(e["date"], offset)) for e in v]
            for s, v in plain["items_index"].items()} == shifted["items_index"]

    plain_cards = L.generate(plain_out)["cards"]
    shift_cards = L.generate(shift_out)["cards"]
    assert set(shift_cards) == set(plain_cards) and any(c.startswith("r-") for c in shift_cards)
    for cid, card in plain_cards.items():
        assert shift_cards[cid] == move_dates(card, offset, originals), cid

    moves = [t for row in shifted["export_map"] for t in row["transforms"] if t["kind"] == "dateshift"]
    assert moves
    assert {(dt.date.fromisoformat(t["to"]) - dt.date.fromisoformat(t["from"])).days for t in moves} == {offset}


# --------------------------------------------------------------------------- 01c-05


def test_key_only_in_private(tmp_path, capsys):
    """01c-05 (S3): a ``dateshift`` export plus its L1 cards inside the project: the key is stored in
    ``_private/deident_key.json`` (owner-only) and its salt - hex or raw bytes - is found in no other file, not in
    the returned result and not in anything printed."""
    project = proj(tmp_path)
    X.pipeline(project, LABS3)
    out = project / "builds" / "b1"
    result = R.export(project, out, deident="dateshift")
    L.write(out)
    key = D.load_key(project, create=False)
    key_file = project / "_private" / "deident_key.json"
    assert key_file.is_file() and (key_file.stat().st_mode & 0o077) == 0
    secrets = [key.salt.hex().encode(), key.salt.hex().upper().encode(), key.salt]
    assert key.salt.hex().encode() in key_file.read_bytes()
    named = [p for p in tmp_path.rglob("*") if p.is_file() and "deident_key" in p.name]
    assert named == [key_file]
    leaks = [p for p in tmp_path.rglob("*") if p.is_file() and p != key_file
             and any(s in p.read_bytes() for s in secrets)]
    assert leaks == []
    captured = capsys.readouterr()
    for blob in (json.dumps(result).encode(), captured.out.encode(), captured.err.encode()):
        assert not any(s in blob for s in secrets)


# --------------------------------------------------------------------------- 01c-06


def _ocr_texts() -> list[str]:
    texts = []
    for path in sorted(X.FIXTURES.glob("*/engine*/*/*.jsonl")):
        for raw in path.read_text(encoding="utf-8").splitlines():
            if raw.strip():
                texts.append(json.loads(raw)["text"])
    return texts


def test_pii_zero_hits(tmp_path):
    """01c-06 (S3): the OCR lines of every fixture, made real-looking (the pseudonym names replaced by ordinary
    names, ID and mobile numbers added) - the privacy rules hit before; after ``scrub`` in either mode they hit
    nothing and none of the names / numbers is left. The fx-labs-3x export in either mode hits nothing either."""
    texts = _ocr_texts()
    assert len(texts) > 100
    names = ["虚构" + c for c in "一二三四五六七八九十"]
    id18 = "".join(["1101", "0519", "8001", "0112", "34"])
    mobile = "".join(["1", "38", "0013", "8000"])
    sample = []
    for i, text in enumerate(texts):
        sample.append(re.sub(r"测试患者[甲乙丙丁戊己庚辛壬癸]", names[i % len(names)], text))
    sample += [f"身份证号：{id18}", f"联系电话 {mobile}", f"患者{mobile}复诊"]
    raw = "\n".join(sample)
    assert {f.kind for f in P.scan_text(raw, "sample")} == {"cn_id", "cn_mobile", "name_label"}
    key = D.Key(salt=b"\x01" * 32, offset_days=-731)
    for mode in D.MODES:
        clean = D.scrub(raw, key, mode)
        assert P.scan_text(clean, "sample") == [], mode
        for secret in names + [id18, mobile, "测试患者甲", "T-0000001"]:
            assert secret not in clean, (mode, secret)

    project = proj(tmp_path)
    X.pipeline(project, LABS3)
    for mode in D.MODES:
        res = R.export(project, deident=mode)
        blob = "\n".join(res["files"].values()) + json.dumps(res["export_map"], ensure_ascii=False)
        assert P.scan_text(blob, mode) == []


# --------------------------------------------------------------------------- 01c-07


def test_salt_change_changes_pseudonym():
    """01c-07 (S2): the same name or identifier under two different salts gets two different pseudonyms."""
    s1, s2 = b"\x00" * 32, b"\x00" * 31 + b"\x01"
    for value, kind in (("虚构一", "name"), ("T-0000001", "id"), ("某", "name")):
        assert D.pseudonym(value, s1, kind) != D.pseudonym(value, s2, kind)
    k1, k2 = D.Key(salt=s1, offset_days=400), D.Key(salt=s2, offset_days=400)
    text = "姓名：" + "虚构一\n编号：T-0000001"
    assert D.scrub(text, k1, "pseudonym") != D.scrub(text, k2, "pseudonym")


# --------------------------------------------------------------------------- 01c-08


@pytest.mark.parametrize("offset", [1, -1, 59, 60, 365, -365, 366, -366, 1461, -1095, 730, -60])
def test_dateshift_calendar_boundaries(offset):
    """01c-08 (S2): year end (2026-12-31) and leap day (2028-02-29) moved by offsets crossing year ends and Feb 29
    stay valid ISO dates and keep their interval (425 days)."""
    a, b = "2026-12-31", "2028-02-29"
    ma, mb = D.shift_date(a, offset), D.shift_date(b, offset)
    assert ma == shifted_iso(a, offset) and mb == shifted_iso(b, offset)
    assert (dt.date.fromisoformat(mb) - dt.date.fromisoformat(ma)).days == 425


def test_dateshift_calendar_boundaries_literal():
    """01c-08 (S2): the boundary results spelled out."""
    assert D.shift_date("2026-12-31", 1) == "2027-01-01"
    assert D.shift_date("2028-02-29", 1) == "2028-03-01"
    assert D.shift_date("2028-02-29", -365) == "2027-03-01"
    assert D.shift_date("2028-03-01", -1) == "2028-02-29"
