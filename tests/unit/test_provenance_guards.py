"""WI-00b3: validator guard and edge cases added during implementation (not in the recorded red).

Each case states an observable outcome of ``provenance.validate`` on a tmp copy of an fx-chain
variant with one targeted change: which codes appear, where, and that nothing raises.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from klode_kit.contract import provenance as P

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]
CHAIN = REPO / "tests" / "fixtures" / "fx-chain"


def _copy(variant: str, tmp: Path) -> Path:
    root = tmp / variant
    shutil.copytree(CHAIN / variant, root)
    return root


def _meta(root: Path) -> dict:
    return json.loads((root / "chain.json").read_text(encoding="utf-8"))


def _audit(root: Path) -> Path:
    return root / "audit" / _meta(root)["batch_id"] / "a1"


def _rec(root: Path) -> str:
    return _meta(root)["rec_ids"][0]


def _rows(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _write_rows(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def _fm(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    return json.loads(text[4:text.index("\n---\n", 4)])


def _write_fm(path: Path, meta: dict) -> None:
    path.write_text("---\n" + json.dumps(meta, ensure_ascii=False, indent=1) + "\n---\n", encoding="utf-8")


def _receipt(root: Path) -> Path:
    return root / "build" / "build_receipt.json"


def _codes(root: Path, layout: P.Layout | None = None) -> list[str]:
    return [v.code for v in P.validate(root, layout)]


# --------------------------------------------------------------------------- malformed inputs


def test_blank_lines_ignored_and_non_object_row_reported(tmp_path):
    root = _copy("clean", tmp_path)
    fixes = _audit(root) / "fixes.jsonl"
    fixes.write_text(fixes.read_text(encoding="utf-8") + "\n[1, 2]\n", encoding="utf-8")
    found = P.validate(root)
    assert [(v.code, v.location.rsplit("/", 1)[-1]) for v in found] == [("malformed_artifact", "fixes.jsonl:15")]


def test_malformed_runs_json_and_receipt(tmp_path):
    root = _copy("clean", tmp_path)
    (_audit(root) / "runs.json").write_text("{oops", encoding="utf-8")
    found = P.validate(root)
    assert "malformed_artifact" in {v.code for v in found}
    # with runs.json unreadable every prov run_id is unbound
    assert {v.code for v in found} == {"malformed_artifact", "run_id_mismatch"}
    root2 = _copy("broken-1", tmp_path)
    _receipt(root2).write_text("[]", encoding="utf-8")
    assert {v.code for v in P.validate(root2)} == {"malformed_artifact", "span_mismatch"}


def test_receipt_without_bindings_is_malformed(tmp_path):
    root = _copy("clean", tmp_path)
    r = json.loads(_receipt(root).read_text(encoding="utf-8"))
    del r["audit_bindings"]
    _receipt(root).write_text(json.dumps(r), encoding="utf-8")
    assert _codes(root) == ["malformed_artifact"]


@pytest.mark.parametrize("content", ["no front matter here\n", "---\n[1]\n---\n"])
def test_malformed_record_front_matter(tmp_path, content):
    root = _copy("clean", tmp_path)
    (root / "records" / "_by_id" / f"{_rec(root)}.md").write_text(content, encoding="utf-8")
    codes = set(_codes(root))
    assert "malformed_artifact" in codes
    # the bound record is still checked through the build binding
    assert codes <= {"malformed_artifact", "record_missing"}


def test_row_of_wrong_shape_is_reported_not_raised(tmp_path):
    root = _copy("clean", tmp_path)
    cells = _rows(_audit(root) / "cells.jsonl")
    del cells[0]["prov"]
    _write_rows(_audit(root) / "cells.jsonl", cells)
    found = P.validate(root)
    assert [v.code for v in found] == ["malformed_artifact"]
    assert "KeyError" in found[0].detail


# --------------------------------------------------------------------------- inputs


def test_manifest_and_derived_without_path_skip_byte_check(tmp_path):
    root = _copy("clean", tmp_path)
    for name in ("manifest.jsonl", "derived.jsonl"):
        rows = _rows(root / name)
        for r in rows:
            r.pop("path")
        _write_rows(root / name, rows)
    (root / "images" / "p001.png").write_bytes(b"x")  # no longer referenced
    assert P.validate(root) == []


def test_manifest_path_to_missing_file(tmp_path):
    root = _copy("clean", tmp_path)
    rows = _rows(root / "manifest.jsonl")
    rows[0]["path"] = "images/absent.png"
    _write_rows(root / "manifest.jsonl", rows)
    found = P.validate(root)
    assert [(v.code, v.location) for v in found] == [("artifact_missing", "images/absent.png")]


def test_derived_page_of_unknown_document(tmp_path):
    root = _copy("clean", tmp_path)
    rows = _rows(root / "derived.jsonl")
    rows[0]["page_id"] = "d-000000000000/p001"
    _write_rows(root / "derived.jsonl", rows)
    assert _codes(root) == ["der_parent_mismatch"]


# --------------------------------------------------------------------------- segment 1


def test_ocr_line_engine_not_matching_its_directory(tmp_path):
    root = _copy("clean", tmp_path)
    page = next((root / "ocr" / "mlx").glob("*/*.jsonl"))
    lines = _rows(page)
    lines[0]["engine"] = "vision"
    _write_rows(page, lines)
    found = P.validate(root)
    assert [(v.code, v.engine) for v in found] == [("run_id_mismatch", "mlx")]


def test_pdftext_without_page_map_dpi(tmp_path):
    root = _copy("legal-pdftext_dpi", tmp_path)
    pm = _rows(root / "page_map.jsonl")
    pm[0]["page_id"] = "d-000000000000/p001"
    _write_rows(root / "page_map.jsonl", pm)
    found = P.validate(root)
    assert {v.code for v in found} == {"pdftext_dpi_mismatch"}
    assert all(v.engine == "pdftext" and "no page_map" in v.detail for v in found)


def test_pdftext_token_without_any_box_is_not_a_dpi_violation(tmp_path):
    root = _copy("legal-pdftext_dpi", tmp_path)
    page = next((root / "ocr" / "pdftext").glob("*/*.jsonl"))
    lines = _rows(page)
    lines[0]["tokens"].append({"text": "", "span": [0, 0], "geometry": "exact", "bbox_px": None, "bbox_pt": None})
    _write_rows(page, lines)
    assert P.validate(root) == []
    lines[0]["tokens"][-1]["bbox_px"] = [1, 1, 2, 2]  # px without pt cannot be checked -> violation
    _write_rows(page, lines)
    assert _codes(root) == ["pdftext_dpi_mismatch"]


def test_candidate_without_prov(tmp_path):
    root = _copy("clean", tmp_path)
    cells = _rows(_audit(root) / "cells.jsonl")
    del cells[0]["prov"]["claude"]
    _write_rows(_audit(root) / "cells.jsonl", cells)
    found = P.validate(root)
    assert [(v.code, v.engine, v.field_id) for v in found] == [("span_mismatch", "claude", cells[0]["field_id"])]


def test_prov_run_not_in_runs_json(tmp_path):
    root = _copy("clean", tmp_path)
    runs = json.loads((_audit(root) / "runs.json").read_text(encoding="utf-8"))
    runs["codex"] = runs["codex"].replace("/t1", "/t2")
    (_audit(root) / "runs.json").write_text(json.dumps(runs), encoding="utf-8")
    found = P.validate(root)
    assert {(v.code, v.engine) for v in found} == {("run_id_mismatch", "codex")}


def test_cloud_route_with_bbox(tmp_path):
    root = _copy("clean", tmp_path)
    cells = _rows(_audit(root) / "cells.jsonl")
    cells[2]["prov"]["claude"]["bbox_px"] = cells[2]["prov"]["vision"]["bbox_px"]
    _write_rows(_audit(root) / "cells.jsonl", cells)
    found = P.validate(root)
    assert [(v.code, v.engine) for v in found] == [("cloud_bbox_not_null", "claude")]


def test_local_route_bbox_outside_its_line(tmp_path):
    root = _copy("clean", tmp_path)
    cells = _rows(_audit(root) / "cells.jsonl")
    cells[2]["prov"]["vision"]["bbox_px"] = [0, 0, 5000, 5000]
    _write_rows(_audit(root) / "cells.jsonl", cells)
    assert [(v.code, v.engine) for v in P.validate(root)] == [("bbox_outside_line", "vision")]


# --------------------------------------------------------------------------- segment 2


def test_none_decision_for_field_absent_from_revision_is_legal(tmp_path):
    root = _copy("clean", tmp_path)
    rec = _rec(root)
    fixes = _rows(_audit(root) / "fixes.jsonl")
    extra = dict(fixes[0], decision_id=f"{_meta(root)['batch_id']}/a1/d0099", field_id=f"{rec}/labs[9].flag",
                 choice="none", route=None, value=None)
    _write_rows(_audit(root) / "fixes.jsonl", fixes + [extra])
    assert P.validate(root) == []
    extra.update(choice="manual", value="H", evidence={
        "region": {"der_sha256": _rows(root / "derived.jsonl")[0]["der_sha256"], "bbox_px": [1, 2, 3, 4]},
        "seen": {}})
    _write_rows(_audit(root) / "fixes.jsonl", fixes + [extra])
    assert [(v.code, v.field_id) for v in P.validate(root)] == [("record_value_mismatch", f"{rec}/labs[9].flag")]


def test_future_decision_on_field_absent_from_old_revision_is_ignored(tmp_path):
    root = _copy("clean", tmp_path)
    rec = _rec(root)
    fixes = _rows(_audit(root) / "fixes.jsonl")
    later = dict(fixes[0], decision_id=f"{_meta(root)['batch_id']}/a1/d0099", field_id=f"{rec}/labs[9].value",
                 rev_base=4, rev_new=5, choice="none", route=None, value=None)
    _write_rows(_audit(root) / "fixes.jsonl", fixes + [later])
    assert P.validate(root) == []


def test_duplicate_effective_decision_reported_once_across_revisions(tmp_path):
    root = _copy("legal-oldbuild", tmp_path)
    rec = _rec(root)
    fixes = _rows(_audit(root) / "fixes.jsonl")
    dup = dict(next(d for d in fixes if d["field_id"] == f"{rec}/labs[0].value"),
               decision_id=f"{_meta(root)['batch_id']}/a1/d0099")
    _write_rows(_audit(root) / "fixes.jsonl", fixes + [dup])
    found = P.validate(root)
    assert [(v.code, v.field_id) for v in found] == [("multiple_effective_decisions", f"{rec}/labs[0].value")]


def test_unverified_record_revisions_are_not_checked(tmp_path):
    root = _copy("clean", tmp_path)
    rec = _rec(root)
    path = root / "records" / "_by_id" / f"{rec}.md"
    meta = _fm(path)
    meta["effective"] = {"rev": 1, "audit_rev": meta["effective"]["audit_rev"], "status": "disputed", "verified_by": None}
    meta["candidates"][0]["status"] = "disputed"
    _write_fm(path, meta)
    r = json.loads(_receipt(root).read_text(encoding="utf-8"))
    r["audit_bindings"] = {"r-000000000000": {"rev": 1, "audit_rev": meta["effective"]["audit_rev"]}}
    _receipt(root).write_text(json.dumps(r), encoding="utf-8")
    # rev 1 is neither verified nor bound -> its missing decisions do not matter
    fixes = [d for d in _rows(_audit(root) / "fixes.jsonl") if d["field_id"] != f"{rec}/date"]
    _write_rows(_audit(root) / "fixes.jsonl", fixes)
    codes = set(_codes(root))
    assert "missing_effective_decision" not in codes
    # the binding names an unknown record and the exported rows are not bound
    assert {"record_missing", "rev_binding_mismatch"} <= codes


def test_missing_revision_body(tmp_path):
    root = _copy("legal-oldbuild", tmp_path)
    rec = _rec(root)
    body = root / "records" / "_rev" / f"{rec}.r2.md"
    body.rename(tmp_path / "moved.r2.md")
    assert set(_codes(root)) == {"record_missing"}


# --------------------------------------------------------------------------- segment 3 + binding


def test_bound_audit_rev_must_match_candidate(tmp_path):
    root = _copy("clean", tmp_path)
    r = json.loads(_receipt(root).read_text(encoding="utf-8"))
    r["audit_bindings"][_rec(root)]["audit_rev"] = _meta(root)["batch_id"] + "/a7"
    _receipt(root).write_text(json.dumps(r), encoding="utf-8")
    found = P.validate(root)
    assert [(v.code, v.location) for v in found] == [("rev_binding_mismatch", "build/build_receipt.json")]


def test_transform_that_does_not_apply(tmp_path):
    root = _copy("legal-alias", tmp_path)
    path = root / "build" / "src" / "export_map.jsonl"
    rows = _rows(path)
    rows[0]["transforms"][0]["from"] = "Hb"
    _write_rows(path, rows)
    assert _codes(root) == ["export_text_mismatch"]


def test_exported_library_file_missing_or_different(tmp_path):
    root = _copy("clean", tmp_path)
    lib = root / "build" / "library" / "items"
    (lib / "wbc.txt").rename(tmp_path / "wbc.txt")
    (lib / "hemoglobin.txt").write_text("something else\n", encoding="utf-8")
    found = P.validate(root)
    assert sorted((v.code, v.location) for v in found) == [
        ("artifact_missing", "build/library/items/wbc.txt"),
        ("export_file_mismatch", "build/src/export_map.jsonl:1"),
    ]


def test_value_audited_must_be_bound_revision_value(tmp_path):
    root = _copy("clean", tmp_path)
    path = root / "build" / "src" / "export_map.jsonl"
    rows = _rows(path)
    rows[0]["value_audited"] = "999"
    _write_rows(path, rows)
    found = P.validate(root)
    assert [(v.code, v.field_id) for v in found] == [("value_audited_mismatch", rows[0]["field_ids"][1])]


def test_build_outside_root_reports_absolute_location(tmp_path):
    root = _copy("broken-6", tmp_path)
    elsewhere = tmp_path / "elsewhere"
    shutil.copytree(root / "build", elsewhere)
    layout = P.Layout.for_products(root)
    layout.build_dir = elsewhere
    found = P.validate(root, layout)
    assert [v.code for v in found] == ["rev_binding_mismatch"]
    assert found[0].location.startswith(str(elsewhere))
