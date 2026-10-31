"""WI-01c: ``deident`` edges beyond the matrix rows (guards, the key file, the export transforms).

- the export under a mode registers what it changed in ``export_map.transforms``: ``dateshift`` only on date fields
  and only in ``dateshift`` mode, ``layout`` for a scrubbed label, nothing on a value / unit; the 00b3 validator
  (``provenance.check_transforms`` / ``apply`` / ``unapply``) finds no violation and recovers the audited values;
- what cannot be de-identified without touching a field the design forbids touching (an identifier left in a kept
  field - D-39: rows holding one are dropped first, see test_deident_drop - a date outside a date field under ``dateshift``, a date field that is not a date) excludes the record
  (``deident_unsafe``) instead of leaking it;
- the key: created once (owner-only file, ``_private/.gitignore``), reused, malformed -> ``DeidentError`` without
  echoing its content; an unknown mode is refused before anything is written.

Records are written by hand in the writeback's format (``export_fx.put_record``). No mock.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from _support import export_fx as X
from klode_kit.build import deident as D
from klode_kit.build import records_to_txt as R
from klode_kit.contract import provenance as PV
from klode_kit.contract import schema
from klode_kit.devtools import privacy_scan as P

pytestmark = pytest.mark.unit

A1 = "aaaaaaaaaaaa/a1"
REC_A, REC_B, REC_C = (f"r-{c * 12}" for c in "abc")
KEY = D.Key(salt=bytes(range(32)), offset_days=500)
TOKEN = re.compile(r"\[[A-Z]+-[a-z]+\]")


def proj(tmp_path: Path) -> Path:
    p = tmp_path / "proj"
    p.mkdir()
    return p


def chain_record(project: Path, rec: str, date: str, rows: list[tuple[str, str, str]]) -> dict[str, str]:
    """A verified fx-chain record: ``rows`` = [(item, value, unit)] plus the record date field."""
    fields = {f"{rec}/date": date}
    for n, (item, value, unit) in enumerate(rows):
        fields.update({f"{rec}/labs[{n}].item": item, f"{rec}/labs[{n}].value": value,
                       f"{rec}/labs[{n}].unit": unit})
    X.put_record(project, rec, {1: {"status": "verified", "audit_rev": A1, "fields": fields}}, effective=1,
                 date=date)
    return fields


def pipe_record(project: Path, rec: str, fields: dict[str, str], *, date: str | None) -> None:
    X.put_record(project, rec, {1: {"status": "verified", "audit_rev": A1, "fields": fields}}, effective=1,
                 date=date)


# --------------------------------------------------------------------------- export transforms


@pytest.mark.parametrize("mode", D.MODES)
def test_export_transforms_pass_the_validator(tmp_path, mode):
    """(S5) fx-chain records with an aliased item: every export row validates against the contract; the 00b3
    validator reports no violation; ``apply(transforms, audited values)`` is exactly the text at each position and
    ``unapply`` gives back the audited values; ``dateshift`` appears only in ``dateshift`` mode, on date fields."""
    project = proj(tmp_path)
    audited = {}
    audited[REC_A] = chain_record(project, REC_A, "2026-09-18", [("HGB", "128", "g/L"), ("白细胞", "6.2", "10^9/L")])
    audited[REC_B] = chain_record(project, REC_B, "2026-10-01", [("血红蛋白", "135", "g/L")])
    res = R.export(project, deident=mode)
    kinds = set()
    for row in res["export_map"]:
        assert schema.issues("export_map", row) == []
        assert PV.check_transforms(row["transforms"], deident_mode=mode) == []
        values = audited[row["rec_id"]]
        shown = PV.apply(row["transforms"], values)
        for fid, (s, e) in row["positions"].items():
            assert row["text"][s:e] == shown[fid]
        assert PV.unapply(row["transforms"], shown) == values
        for t in row["transforms"]:
            kinds.add(t["kind"])
            assert PV.slot_kind(t.get("field_id", "x/x")) not in PV.NUMERIC_KINDS
            if t["kind"] == "dateshift":
                assert PV.slot_kind(t["field_id"]) == "date"
                assert (dt.date.fromisoformat(t["to"]) - dt.date.fromisoformat(t["from"])).days == \
                    D.load_key(project, create=False).offset_days
    assert kinds == ({"alias", "dateshift"} if mode == "dateshift" else {"alias"})


def test_label_identifier_scrubbed_with_layout_transform(tmp_path):
    """(S1) a row label holding an identifier: the line shows the pseudonym (same in both modes and on a rerun),
    the raw identifier is nowhere in the output, and the row carries a ``layout`` transform naming the change."""
    project = proj(tmp_path)
    ident = "ZY" + "12345678"
    label = "床位" + ident
    pipe_record(project, REC_A, {f"{REC_A}/r003-date-0": "2026-09-18", f"{REC_A}/r004-number-1": "115-150"},
                date="2026-09-18")
    X.put_labels(project, A1, {f"{REC_A}/r004-number-1": label})
    plain = R.export(project)
    assert any(label in t for t in plain["files"].values())
    outs = [R.export(project, deident=m) for m in ("pseudonym", "pseudonym", "dateshift")]
    for res in outs:
        assert not any(ident in t for t in res["files"].values())
        assert ident not in json.dumps([{k: v for k, v in r.items() if k != "transforms"} for r in res["export_map"]],
                                       ensure_ascii=False)
        (row,) = [r for r in res["export_map"] if "115-150" in r["text"]]
        (lay,) = [t for t in row["transforms"] if t["kind"] == "layout"]
        assert lay["from"] == label and "field_id" not in lay
        assert row["text"].startswith(lay["to"] + " 115-150 ")
        assert TOKEN.search(lay["to"]) and lay["to"].startswith("床位")
    assert outs[0]["files"] == outs[1]["files"]


@pytest.mark.parametrize("mode", D.MODES)
def test_identifier_in_field_excludes_record(tmp_path, mode):
    """(S3) an identifier inside an item field or a value field cannot be replaced (no transform may touch a
    value; an item only takes an alias). Spec change D-39 (WI-33h): an identifier in an unknown item field drops
    that row (the record, left with no row, is ``empty_record``); an identifier in the value of a lab item stays
    in a kept field, so the record is ``deident_unsafe``; nothing of either is exported; a clean record beside
    them is exported."""
    project = proj(tmp_path)
    mobile = "".join(["1", "38", "0013", "8000"])
    chain_record(project, REC_A, "2026-09-18", [("MZ" + "12345678", "128", "g/L")])
    chain_record(project, REC_B, "2026-09-18", [("血红蛋白", mobile, "g/L")])
    chain_record(project, REC_C, "2026-09-18", [("血红蛋白", "130", "g/L")])
    assert {r["rec_id"] for r in R.export(project)["export_map"]} == {REC_A, REC_B, REC_C}
    res = R.export(project, deident=mode)
    assert {r["rec_id"] for r in res["export_map"]} == {REC_C}
    assert [(e["rec_id"], e["reason"]) for e in res["excluded"]] == [(REC_A, "empty_record"),    # spec change D-39
                                                                      (REC_B, "deident_unsafe")]
    blob = "\n".join(res["files"].values())
    assert REC_A not in blob and REC_B not in blob and mobile not in blob


def test_date_outside_date_field_excluded_only_under_dateshift(tmp_path):
    """(S3) a date printed from a non-date field would keep the real date under ``dateshift`` (no transform may
    move it): the record is ``deident_unsafe`` there, and exported unchanged under ``pseudonym``."""
    project = proj(tmp_path)
    pipe_record(project, REC_A, {f"{REC_A}/r003-date-0": "2026-09-18", f"{REC_A}/r009-number-1": "2026-07-01"},
                date="2026-09-18")
    pseudo = R.export(project, deident="pseudonym")
    assert any("2026-07-01" in t for t in pseudo["files"].values())
    shifted = R.export(project, deident="dateshift")
    assert shifted["files"] == {} and [(e["rec_id"], e["reason"]) for e in shifted["excluded"]] == [
        (REC_A, "deident_unsafe")]


def test_unparseable_date_field_excluded_under_dateshift(tmp_path):
    """(S3) a record date that is not a calendar date cannot be moved: ``deident_unsafe`` under ``dateshift``,
    exported as written under ``pseudonym``."""
    project = proj(tmp_path)
    chain_record(project, REC_A, "2026-02-30", [("血红蛋白", "128", "g/L")])
    assert any("2026-02-30" in t for t in R.export(project, deident="pseudonym")["files"].values())
    res = R.export(project, deident="dateshift")
    assert res["files"] == {} and [e["reason"] for e in res["excluded"]] == ["deident_unsafe"]


def test_body_date_without_field_shifted(tmp_path):
    """(S1) a record whose date comes only from its body (no date field): the observation line and the items index
    show the moved date, and the line's ``dateshift`` transform has no field_id (line level)."""
    project = proj(tmp_path)
    pipe_record(project, REC_A, {f"{REC_A}/r005-number-0": "128", f"{REC_A}/r005-unit-0": "g/L"},
                date="2026-09-18")
    X.put_labels(project, A1, {f"{REC_A}/r005-number-0": "血红蛋白"})
    res = R.export(project, deident="dateshift")
    off = D.load_key(project, create=False).offset_days
    moved = (dt.date(2026, 9, 18) + dt.timedelta(days=off)).isoformat()
    assert res["files"]["library/items/hemoglobin.txt"] == f"血红蛋白 128 g/L {moved} ⟨{REC_A}⟩\n"
    assert res["items_index"]["hemoglobin"] == [{"rec_id": REC_A, "rev": 1, "date": moved}]
    (row,) = [r for r in res["export_map"] if r["source_file"] == "library/items/hemoglobin.txt"]
    assert row["transforms"] == [{"kind": "dateshift", "from": "2026-09-18", "to": moved}]
    assert PV.check_transforms(row["transforms"], deident_mode="dateshift") == []


# --------------------------------------------------------------------------- key


def test_key_created_once_and_reused(tmp_path):
    """(S5) the first de-identified export creates the key (owner-only, ``_private/.gitignore`` ignoring all);
    a second export reuses it: the key file and the exported bytes are identical."""
    project = proj(tmp_path)
    chain_record(project, REC_A, "2026-09-18", [("血红蛋白", "128", "g/L")])
    first = R.export(project, tmp_path / "o1", deident="dateshift")
    key_bytes = (project / "_private" / "deident_key.json").read_bytes()
    second = R.export(project, tmp_path / "o2", deident="dateshift")
    assert (project / "_private" / "deident_key.json").read_bytes() == key_bytes
    assert first["files"] == second["files"]
    assert X.tree(tmp_path / "o1") == X.tree(tmp_path / "o2")
    assert (project / "_private" / ".gitignore").read_text(encoding="utf-8").split() == ["*"]
    assert ((project / "_private").stat().st_mode & 0o077) == 0
    key = D.load_key(project)
    assert len(key.salt) >= 16 and 365 <= abs(key.offset_days) <= 3650
    assert key.salt.hex() not in repr(key) and str(key.offset_days) not in repr(key)


def test_new_keys_differ():
    """(S2) two fresh keys share neither salt nor (in practice) offset."""
    keys = [D.new_key() for _ in range(8)]
    assert len({k.salt for k in keys}) == 8
    assert all(365 <= abs(k.offset_days) <= 3650 for k in keys)


@pytest.mark.parametrize("content", ["not json", "[]", '{"salt": "zz", "date_offset_days": 400}',
                                     '{"salt": "' + "ab" * 32 + '", "date_offset_days": 0}',
                                     '{"salt": "' + "ab" * 32 + '", "date_offset_days": "400"}',
                                     '{"salt": "' + "ab" * 32 + '"}'])
def test_malformed_key_refused_without_echo(tmp_path, content):
    """(S3) a malformed key file is a ``DeidentError`` that does not repeat the file's content, and the export
    refuses before writing anything."""
    project = proj(tmp_path)
    chain_record(project, REC_A, "2026-09-18", [("血红蛋白", "128", "g/L")])
    (project / "_private").mkdir()
    (project / "_private" / "deident_key.json").write_text(content, encoding="utf-8")
    with pytest.raises(D.DeidentError) as exc:
        D.load_key(project)
    assert "ab" * 8 not in str(exc.value) and "not json" not in str(exc.value)
    with pytest.raises(D.DeidentError):
        R.export(project, tmp_path / "out", deident="pseudonym")
    assert not (tmp_path / "out").exists()


def test_missing_key_without_create(tmp_path):
    """(S3) ``create=False`` never invents a key."""
    project = proj(tmp_path)
    with pytest.raises(D.DeidentError):
        D.load_key(project, create=False)
    assert not (project / "_private").exists()


def test_unknown_mode_refused(tmp_path):
    """(S3) a mode other than pseudonym / dateshift is refused by ``scrub`` and by the export, which writes
    nothing (and creates no key)."""
    project = proj(tmp_path)
    chain_record(project, REC_A, "2026-09-18", [("血红蛋白", "128", "g/L")])
    with pytest.raises(D.DeidentError):
        D.scrub("x", KEY, "plain")
    with pytest.raises(D.DeidentError):
        R.export(project, tmp_path / "out", deident="plain")
    assert not (tmp_path / "out").exists() and not (project / "_private").exists()


# --------------------------------------------------------------------------- text


FRAG = st.sampled_from(["姓名：虚构甲", "编号：T-0000001", "复查 2026-07-18", "门诊号 MZ12345678", "血红蛋白 128 g/L",
                        "电话 010-12345678", "医师：某某", "备注", "身份证 " + "".join(["1101", "0519", "8001", "0112",
                                                                                     "3X"])])


@settings(max_examples=80, deadline=None)
@given(st.lists(FRAG, min_size=1, max_size=8), st.text(alphabet=" \n，。", max_size=3))
def test_scrub_pseudonym_idempotent_and_clean(frags, sep):
    """(S5) ``scrub`` in ``pseudonym`` mode is idempotent, leaves no privacy-rule hit, and leaves dates alone."""
    text = (sep or "\n").join(frags)
    once = D.scrub(text, KEY, "pseudonym")
    assert D.scrub(once, KEY, "pseudonym") == once
    assert P.scan_text(once, "x") == []
    assert ISO_DATES(once) == ISO_DATES(text)
    assert "T-0000001" not in once and "MZ12345678" not in once and "010-12345678" not in once


def ISO_DATES(text: str) -> list[str]:
    return re.findall(r"\d{4}-\d{2}-\d{2}", text)


def test_scrub_dateshift_moves_free_text_dates():
    """(S1) under ``dateshift`` free-text dates in every supported spelling move by the key offset (spelling
    kept); identifiers become the same pseudonyms as under ``pseudonym``."""
    text = "复查 2026-07-18、2026/7/8、2026.07.08、20260708、2026年7月8日；编号：T-0000001"
    out = D.scrub(text, KEY, "dateshift")
    a = (dt.date(2026, 7, 18) + dt.timedelta(days=500))
    b = (dt.date(2026, 7, 8) + dt.timedelta(days=500))
    assert out.startswith(f"复查 {a.isoformat()}、{b.year}/{b.month}/{b.day}、{b.year}.{b.month:02d}.{b.day:02d}、"
                          f"{b.year}{b.month:02d}{b.day:02d}、{b.year}年{b.month}月{b.day}日；编号：")
    assert out.split("编号：")[1] == D.scrub("编号：T-0000001", KEY, "pseudonym").split("编号：")[1]


@pytest.mark.parametrize("value", ["2026-7-8", "2026/07/08", "2026.07.08", "20260708", "2026年7月8日", "2028-02-29"])
@pytest.mark.parametrize("days", [1, -1, 400, -3650])
def test_shift_round_trip_keeps_spelling(value, days):
    """(S5) moving by ``days`` then ``-days`` gives the value back, and the moved value keeps the spelling."""
    moved = D.shift_date(value, days)
    assert D.shift_date(moved, -days) == value
    assert re.sub(r"\d+", "9", moved) == re.sub(r"\d+", "9", value)
    if value not in ("2026-7-8", "2026年7月8日"):
        assert [len(g) for g in re.findall(r"\d+", moved)] == [len(g) for g in re.findall(r"\d+", value)]


@pytest.mark.parametrize("value", ["", "2026-02-30", "2026-13-01", "not a date", "2026-07-18 ", "七月十八日",
                                   "1800-01-01", "2026-07"])
def test_shift_date_rejects_non_dates(value):
    """(S3) anything that is not exactly one supported calendar date is a ``DeidentError``."""
    with pytest.raises(D.DeidentError):
        D.shift_date(value, 10)


def test_pseudonym_token_shape():
    """(S2) pseudonyms are bracketed letter tokens without digits (they can never look like a date, a phone
    number or an ID) and name vs identifier kinds differ."""
    n, i = D.pseudonym("虚构甲", KEY.salt, "name"), D.pseudonym("虚构甲", KEY.salt, "id")
    assert TOKEN.fullmatch(n) and TOKEN.fullmatch(i) and n != i
    assert not re.search(r"\d", n + i)
    assert D.find_identifiers("姓名：" + n) == [] and D.find_identifiers("血红蛋白 128 g/L") == []
