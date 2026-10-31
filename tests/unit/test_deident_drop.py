"""WI-33h (real-data pilot P13, decision D-39): identity header rows are DROPPED from a de-identified export instead of
excluding the whole record.

Law of the unit: (a) no identifier reaches any ``library/`` file (nor a card built from it) of an exported record;
(b) dropping never removes a row whose label resolves to a lab item or that holds a lab value without identifier;
(c) the exported set only grows against the old rule "any identifier -> the record is ``deident_unsafe``".

Synthetic identifiers only (测试患者甲, T-0000001 style, a generated 18-digit ID-like number). Records are written by
hand in the writeback's format (``export_fx.put_record`` / ``put_labels``). No mock.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from _support import export_fx as X
from klode_kit.build import deident as D
from klode_kit.build import records_to_txt as R

pytestmark = pytest.mark.unit

A1 = "aaaaaaaaaaaa/a1"
REC_A, REC_B = (f"r-{c * 12}" for c in "ab")
FAKE_ID = "110105" + "19800101" + "123" + "4"       # 18 digits, ID-card shaped, generated
FAKE_ADM = "T-" + "0000001"
FAKE_NAME = "测试患者甲"
DATE = "2026-09-18"


def header_record(project: Path, rec: str, header: list[tuple[str, str]], labs: list[tuple[str, str]],
                  *, order: list[int] | None = None) -> dict[str, str]:
    """A verified pipe record: header rows ``(label, value)`` and lab rows ``(item label, value)`` (unit g/L and a
    reference range in ``number-1``), laid out in ``order`` (default header first); the item label of each row
    comes from the audit revision's ``candidates.jsonl``."""
    rows = [("h", lab, val) for lab, val in header] + [("l", lab, val) for lab, val in labs]
    if order is not None:
        rows = [rows[i] for i in order]
    fields = {f"{rec}/r001-date-0": DATE}
    labels = {}
    for n, (kind, lab, val) in enumerate(rows, start=2):
        fields[f"{rec}/r{n:03d}-number-0"] = val
        labels[f"{rec}/r{n:03d}-number-0"] = lab
        if kind == "l":
            fields[f"{rec}/r{n:03d}-unit-0"] = "g/L"
            fields[f"{rec}/r{n:03d}-number-1"] = "115-150"
    X.put_record(project, rec, {1: {"status": "verified", "audit_rev": A1, "fields": fields}}, effective=1, date=DATE)
    X.put_labels(project, A1, labels)
    return fields


def blob(res: dict) -> str:
    return "\n".join(res["files"].values())


HEADER = [("姓名", FAKE_NAME), ("身份证号", FAKE_ID), ("住院号", FAKE_ADM), ("报告者", "测试医生乙"),
          ("临床诊断", "贫血待查")]
LABS = [("血红蛋白", "128"), ("白细胞", "6.2")]


@pytest.mark.parametrize("mode", D.MODES)
def test_header_rows_dropped_and_record_exported(tmp_path, mode):
    """(S1) a lab page with a header block is exported with its lab rows; the identity rows are not in library/,
    and ``dropped`` names every dropped field with its reason; the plain export is unchanged (nothing dropped)."""
    p = tmp_path / "proj"
    fields = header_record(p, REC_A, HEADER, LABS)
    res = R.export(p, deident=mode)
    assert [e for e in res["excluded"]] == []
    text = blob(res)
    for secret in (FAKE_NAME, FAKE_ID, FAKE_ADM, "测试医生乙"):
        assert secret not in text
    assert "血红蛋白 128 g/L" in text and "白细胞 6.2 g/L" in text
    assert "贫血待查" in text                       # a non-identity, identifier-free header row is kept as before
    by_label = {v: k for k, v in fields.items()}
    got = {(d["field_id"], d["reason"]) for d in res["dropped"]}
    assert got == {(by_label[FAKE_NAME], "identity_label"), (by_label[FAKE_ID], "identity_label"),
                   (by_label[FAKE_ADM], "identity_label"), (by_label["测试医生乙"], "identity_label")}
    assert all(d["rec_id"] == REC_A for d in res["dropped"])
    assert R.export(p)["dropped"] == [] and FAKE_ID in blob(R.export(p))   # no deident: nothing is dropped


def test_dropped_identity_rows_are_not_in_export_map(tmp_path):
    """(S1) the dropped fields appear in no export_map row (field_ids, text, value_audited)."""
    p = tmp_path / "proj"
    fields = header_record(p, REC_A, HEADER, LABS)
    res = R.export(p, deident="pseudonym")
    shown = json.dumps(res["export_map"], ensure_ascii=False)
    for secret in (FAKE_NAME, FAKE_ID, FAKE_ADM):
        assert secret not in shown
    dropped = {d["field_id"] for d in res["dropped"]}
    assert dropped and not any(set(r["field_ids"]) & dropped for r in res["export_map"])
    assert dropped <= set(fields)


def test_identifier_in_unlabelled_or_unknown_row_is_dropped(tmp_path):
    """(S2) a row whose label is not an identity label and not a lab item, but whose value holds an identifier,
    is dropped with reason ``identifier``; so is a row with no label at all."""
    p = tmp_path / "proj"
    fields = {f"{REC_A}/r001-date-0": DATE, f"{REC_A}/r002-number-0": "9" * 10 + "5",
              f"{REC_A}/r003-number-1": FAKE_ADM, f"{REC_A}/r004-number-0": "128", f"{REC_A}/r004-unit-0": "g/L"}
    X.put_record(p, REC_A, {1: {"status": "verified", "audit_rev": A1, "fields": fields}}, effective=1, date=DATE)
    X.put_labels(p, A1, {f"{REC_A}/r002-number-0": "备注编码", f"{REC_A}/r004-number-0": "血红蛋白"})
    res = R.export(p, deident="pseudonym")
    assert res["excluded"] == []
    assert {(d["field_id"], d["reason"]) for d in res["dropped"]} == {
        (f"{REC_A}/r002-number-0", "identifier"), (f"{REC_A}/r003-number-1", "identifier")}
    assert "血红蛋白 128 g/L" in blob(res) and "9" * 10 not in blob(res) and FAKE_ADM not in blob(res)


def test_identifier_inside_lab_item_row_still_excludes_record(tmp_path):
    """(S3) the safety net: a lab item whose value holds an identifier is not dropped (its label is a lab item),
    so the identifier would remain in a kept field and the whole record is ``deident_unsafe``; another record is
    exported."""
    p = tmp_path / "proj"
    header_record(p, REC_A, HEADER, [("血红蛋白", FAKE_ADM)])
    header_record(p, REC_B, HEADER, LABS)
    res = R.export(p, deident="pseudonym")
    assert [(e["rec_id"], e["reason"]) for e in res["excluded"]] == [(REC_A, "deident_unsafe")]
    assert {r["rec_id"] for r in res["export_map"]} == {REC_B}
    assert FAKE_ADM not in blob(res) and all(d["rec_id"] == REC_B for d in res["dropped"])


def test_record_with_only_dropped_rows_is_empty_record(tmp_path):
    """(S3) nothing left to show after dropping: excluded as ``empty_record``, no file."""
    p = tmp_path / "proj"
    header_record(p, REC_A, HEADER[:3], [])
    res = R.export(p, deident="pseudonym")
    assert res["files"] == {} and [(e["rec_id"], e["reason"]) for e in res["excluded"]] == [(REC_A, "empty_record")]
    assert res["export_map"] == [] and res["dropped"] == []   # dropped lists exported records only


def test_identity_labels_are_configurable_and_lab_items_never_dropped(tmp_path):
    """(S2) a project ``identity_labels.toml`` replaces the list: ``临床诊断`` is then dropped and ``科室`` kept; a
    lab item named in the list is still kept (law b)."""
    p = tmp_path / "proj"
    p.mkdir()
    (p / "identity_labels.toml").write_text('labels = ["临床诊断", "血红蛋白"]\n', encoding="utf-8")
    header_record(p, REC_A, [("科室", "内科"), ("临床诊断", "贫血待查")], LABS)
    res = R.export(p, deident="pseudonym")
    text = blob(res)
    assert "贫血待查" not in text and "科室 内科" in text
    assert "血红蛋白 128 g/L" in text
    assert {d["reason"] for d in res["dropped"]} == {"identity_label"} and len(res["dropped"]) == 1


def test_default_identity_labels_cover_the_documented_set():
    """(S1) the packaged default list."""
    assert {"姓名", "证件号", "身份证号", "住院号", "门诊号", "就诊流水号", "病案号", "床号", "报告者", "申请医生",
            "检验者", "审核者"} <= R.identity_labels()
    assert R.identity_labels(Path("/nonexistent-project-dir")) == R.identity_labels()


def test_malformed_identity_labels_file_is_an_export_error(tmp_path):
    """(S3) an unusable override is refused, not silently ignored."""
    p = tmp_path / "proj"
    p.mkdir()
    (p / "identity_labels.toml").write_text('labels = "姓名"\n', encoding="utf-8")
    with pytest.raises(R.ExportError):
        R.identity_labels(p)


# --------------------------------------------------------------------------- laws (property)

ident_values = st.one_of(
    st.just(FAKE_ID), st.just(FAKE_ADM),
    st.integers(min_value=10**8, max_value=10**11).map(str),                          # a long digit run
    st.integers(min_value=100000, max_value=9999999).map(lambda n: f"MZ{n}"),          # a letter code
    st.integers(min_value=0, max_value=99999999).map(lambda n: "1380013" + f"{n % 10000:04d}"))  # mobile-like
id_labels = st.sampled_from(["姓名", "证件号", "身份证号", "住院号", "门诊号", "就诊流水号", "病案号", "床号", "报告者",
                             "申请医生", "检验者", "审核者", "备注编码", "条码"])
plain_values = st.sampled_from(["测试患者甲", "测试医生乙", "内科", "12", "贫血待查"])


@st.composite
def pages(draw):
    labs = draw(st.lists(st.tuples(st.sampled_from(["血红蛋白", "白细胞", "血小板", "肌酐"]),
                                   st.integers(min_value=1, max_value=400).map(str)), min_size=1, max_size=4))
    header = draw(st.lists(st.tuples(id_labels, st.one_of(ident_values, plain_values)), max_size=6))
    order = draw(st.permutations(range(len(header) + len(labs))))
    return header, labs, order


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(pages())
def test_laws_no_identifier_lab_rows_kept_and_monotone(page):
    """(S4/S6) over random identifier placement: (a) find_identifiers finds nothing in any exported library file;
    (b) every lab row (known item, identifier-free value) is in the export; (c) the record is exported whenever
    the old rule (no identifier anywhere in its fields) would have exported it."""
    header, labs, order = page
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "proj"
        fields = header_record(p, REC_A, header, labs, order=list(order))
        res = R.export(p, deident="pseudonym")
        for rel, text in res["files"].items():
            assert D.find_identifiers(text) == [], rel
        old_exported = not any(D.find_identifiers(v) for v in fields.values())
        if old_exported:
            assert REC_A in {r["rec_id"] for r in res["export_map"]}
        if res["export_map"]:
            text = blob(res)
            for item, value in labs:
                display = {"血红蛋白": "血红蛋白", "白细胞": "白细胞", "血小板": "血小板", "肌酐": "肌酐"}[item]
                assert f"{display} {value} g/L {DATE}" in text
            dropped = {d["field_id"] for d in res["dropped"]}
            assert not any(set(r["field_ids"]) & dropped for r in res["export_map"])
        else:
            assert res["excluded"][0]["reason"] in {"empty_record", "deident_unsafe"}
