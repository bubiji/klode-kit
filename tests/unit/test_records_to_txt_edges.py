"""WI-01a edges of ``records_to_txt.export``: malformed or missing record files, a build snapshot naming a body that
is not verified, dates and item labels that are absent. Written after the 01a-* specification went green (coverage
of the guard paths), not part of the recorded red.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from _support import export_fx as X
from klode_kit.build import records_to_txt as R

pytestmark = pytest.mark.unit

A1 = "aaaaaaaaaaaa/a1"
REC = "r-" + "a" * 12


def proj(tmp_path: Path) -> Path:
    p = tmp_path / "proj"
    p.mkdir()
    return p


@pytest.mark.parametrize("content", ["no front matter\n", "---\n[1, 2]\n---\n", "---\n{not json\n---\n"],
                         ids=["no-block", "not-object", "bad-json"])
def test_malformed_record_file_refused(tmp_path, content):
    project = proj(tmp_path)
    d = project / "records" / "_by_id"
    d.mkdir(parents=True)
    (d / f"{REC}.md").write_text(content, encoding="utf-8")
    with pytest.raises(R.ExportError, match="malformed record file"):
        R.export(project)


def test_unreadable_record_file_refused(tmp_path):
    project = proj(tmp_path)
    (project / "records" / "_by_id" / f"{REC}.md").mkdir(parents=True)
    with pytest.raises(R.ExportError, match="cannot read"):
        R.export(project)


def test_bound_body_missing_refused(tmp_path):
    project = proj(tmp_path)
    with pytest.raises(R.ExportError, match="bound revision body not found"):
        R.export(project, bindings={REC: {"rev": 3, "audit_rev": A1}})


def test_snapshot_naming_unverified_body_excluded(tmp_path):
    project = proj(tmp_path)
    X.put_record(project, REC, {1: {"status": "disputed", "audit_rev": A1,
                                    "fields": {f"{REC}/date": "2026-09-18", f"{REC}/labs[0].item": "血红蛋白",
                                               f"{REC}/labs[0].value": "128"}}}, effective=None, date="2026-09-18")
    res = R.export(project, bindings={REC: {"rev": 1, "audit_rev": A1}})
    assert res["excluded"] == [{"rec_id": REC, "reason": "not_verified", "status": "disputed"}]
    assert res["files"] == {}


def test_body_without_status_and_record_unverified_by_record_file(tmp_path):
    """A body without ``status`` (written before WI-08c) takes its revision's status from the record file; no
    record file -> no status -> not exported."""
    project = proj(tmp_path)
    rev = project / "records" / "_rev"
    rev.mkdir(parents=True)
    body = {"rec_id": REC, "rev": 1, "audit_rev": A1, "date": "2026-09-18",
            "fields": {f"{REC}/labs[0].item": "血红蛋白", f"{REC}/labs[0].value": "128"}}
    (rev / f"{REC}.r1.md").write_text("---\n" + json.dumps(body, ensure_ascii=False) + "\n---\n", encoding="utf-8")
    res = R.export(project, bindings={REC: {"rev": 1, "audit_rev": A1}})
    assert res["excluded"] == [{"rec_id": REC, "reason": "not_verified", "status": None}]


def test_date_from_field_when_body_has_none_and_no_date_at_all(tmp_path):
    project = proj(tmp_path)
    fields = {f"{REC}/r003-date-0": "2026-09-18", f"{REC}/r005-drug-0": "示例片", f"{REC}/r005-number-0": "1.05"}
    X.put_record(project, REC, {1: {"status": "verified", "audit_rev": A1, "fields": fields}}, effective=1,
                 date=None)
    res = R.export(project)
    assert res["files"]["library/items/sample-tablet.txt"] == f"示例片 1.05 2026-09-18 ⟨{REC}⟩\n"
    other = "r-" + "b" * 12
    X.put_record(project, other, {1: {"status": "verified", "audit_rev": A1,
                                      "fields": {f"{other}/labs[0].item": "白细胞", f"{other}/labs[0].value": "6.2"}}},
                 effective=1, date=None)
    res = R.export(project)
    assert res["excluded"] == [{"rec_id": other, "reason": "no_date", "status": "verified"}]


def test_rows_without_label_and_bare_slots_are_source_lines(tmp_path):
    """No item label anywhere (no audit revision named, a blank line and a non-text label in candidates.jsonl): a
    row holding a value but no item is a source line; a bare non-date slot is a source line too."""
    project = proj(tmp_path)
    X.put_labels(project, A1, {f"{REC}/r006-number-0": "wbc"})
    path = project / "work" / "aaaaaaaaaaaa" / "audit" / "a1" / "candidates.jsonl"
    row = {"rec_id": REC, "fields": [{"field_id": f"{REC}/r005-number-0", "item_label": 7}]}
    path.write_text(path.read_text(encoding="utf-8") + "\n" + json.dumps(row) + "\n", encoding="utf-8")
    fields = {f"{REC}/r005-number-0": "128", f"{REC}/r005-unit-0": "g/L", f"{REC}/r006-number-0": "6.2",
              f"{REC}/note": "复查"}
    X.put_record(project, REC, {1: {"status": "verified", "audit_rev": A1, "fields": fields}}, effective=1,
                 date="2026-09-18")
    res = R.export(project)
    assert res["files"][f"library/records/{REC}.txt"] == (
        f"128 g/L ⟨{REC}⟩\n白细胞 6.2 2026-09-18 ⟨{REC}⟩\n复查 ⟨{REC}⟩\n")
    X.put_record(project, REC, {1: {"status": "verified", "audit_rev": None, "fields": fields}}, effective=1,
                 date="2026-09-18")
    res = R.export(project, bindings={REC: {"rev": 1, "audit_rev": A1}})
    assert res["files"][f"library/records/{REC}.txt"] == (
        f"128 g/L ⟨{REC}⟩\n6.2 ⟨{REC}⟩\n复查 ⟨{REC}⟩\n")
    assert "library/items/wbc.txt" not in res["files"]
