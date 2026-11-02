"""WI-37 (P21): scoped validation of a build and the route-B fallback on real pipeline products.

``provenance.validate_build(project, build_dir)`` validates what the build binds: the batches of the bound records'
audit revisions (every candidate of a bound record, so a reopened record keeps its older batch) and those records;
a defect in a batch or a record that is not part of the build is not this build's violation. ``validate`` over
``Layout.for_project`` stays the whole-project scan. Every violation names its batch in its location
(``work/<batch>/...``), so an unscoped scan can show the user that a defect is unrelated.

Law (S5): over the pipeline's own products the scoped and the whole-project validation agree (both clean), and a
defect inside a bound batch is found by both.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from _support import export_fx as X
from klode_kit.build import records_to_txt as R
from klode_kit.contract import provenance as P

pytestmark = pytest.mark.contract_replay

OTHER = "aaaaaaaaaaaa"


def _jl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _jl_write(path: Path, rows: list[dict]) -> None:
    if path.exists():
        path.chmod(0o644)  # audit files are write-once (read-only)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


@pytest.fixture(scope="module")
def product(tmp_path_factory) -> Path:
    project = tmp_path_factory.mktemp("scope") / "proj"
    project.mkdir()
    X.pipeline(project, X.FIXTURES / "fx-labs-3x")
    res = R.export(project, project / "build", kb_id="k")
    (project / "build" / "build_receipt.json").write_text(
        json.dumps({"audit_bindings": res["audit_bindings"], "deident_mode": None, "kb_id": "k"}), encoding="utf-8")
    return project


@pytest.fixture
def project(product, tmp_path) -> Path:
    shutil.copytree(product, tmp_path / "proj", symlinks=True)
    return tmp_path / "proj"


def _whole(project: Path):
    return P.validate(project, P.Layout.for_project(project, project / "build"))


def _batch(project: Path) -> Path:
    return next(p for p in (project / "work").iterdir() if p.is_dir() and not p.is_symlink())


def _foreign_batch(project: Path) -> None:
    """A batch the build does not bind, with a line labelled with an engine its directory does not name."""
    d = project / "work" / OTHER / "ocr" / "mlx" / "0123456789ab.t1"
    d.mkdir(parents=True)
    _jl_write(d / "p001.jsonl", [{"engine": "vision", "der_sha256": "0" * 64, "line_idx": 0, "page_id": "d-000000000000/p001",
                                  "text": "x", "bbox_px": None, "tokens": None}])


def test_clean_products_agree(project):
    """(S5) the pipeline's own products: scoped and whole-project validation are both clean."""
    assert _whole(project) == []
    assert P.validate_build(project, project / "build") == []


def test_unrelated_batch_defect_blocks_only_the_whole_scan(project):
    """(S1) a defective batch the build does not bind: the whole scan reports it - naming the batch in the location -
    and the scoped build validation does not."""
    _foreign_batch(project)
    whole = _whole(project)
    assert whole and all(OTHER in v.location for v in whole)
    assert P.validate_build(project, project / "build") == []


def test_unrelated_record_defect_blocks_only_the_whole_scan(project):
    """(S1) a malformed record that no binding names."""
    (project / "records" / "_by_id" / "r-zzzzzzzzzzzz.md").write_text("not front matter", encoding="utf-8")
    assert {v.code for v in _whole(project)} == {"malformed_artifact"}
    assert P.validate_build(project, project / "build") == []


def test_defect_in_a_bound_batch_is_found_by_both(project):
    """(S4) scoping never hides the build's own batch: a mismatching engine label under the bound batch."""
    page = next((_batch(project) / "ocr" / "mlx").glob("*/*.jsonl"))
    rows = _jl(page)
    rows[0]["engine"] = "vision"
    _jl_write(page, rows)
    batch = _batch(project).name
    for found in (_whole(project), P.validate_build(project, project / "build")):
        hit = [v for v in found if v.code == "run_id_mismatch"]
        assert hit and all(batch in v.location for v in hit)


def test_scoped_validation_without_a_receipt_falls_back_to_the_whole_scan(project):
    """(S3) no readable binding, no scope: the missing receipt is reported, never a silent clean."""
    (project / "build" / "build_receipt.json").unlink()
    found = P.validate_build(project, project / "build")
    assert any(v.code == "artifact_missing" and "build_receipt.json" in v.location for v in found)


def test_fallback_batch_with_unstamped_pipeline_files_validates(project):
    """(S1) P21 on real pipeline products: kb-ocr page files carry no run_id; page p001 of the mlx route fell back to
    tesseract. The route's run id (from its directory) is authoritative; 0 violations whole-project and scoped."""
    batch = _batch(project)
    for page in batch.glob("ocr/*/*/*.jsonl"):
        rows = _jl(page)
        for r in rows:
            r.pop("run_id", None)
        _jl_write(page, rows)
    page = next((batch / "ocr" / "mlx").glob("*/p001.jsonl"))
    rows = _jl(page)
    ders = {r["der_sha256"] for r in rows}
    for r in rows:
        r["engine"] = "tesseract"
    _jl_write(page, rows)
    adir = next(batch.glob("audit/a1"))
    cells = _jl(adir / "cells.jsonl")
    moved = set()
    for c in cells:
        if c["prov"].get("mlx", {}).get("der_sha256") in ders:
            for key in ("candidates", "prov"):
                c[key]["tesseract"] = c[key].pop("mlx")
            moved.add(c["field_id"])
    assert moved
    _jl_write(adir / "cells.jsonl", cells)
    fixes = _jl(adir / "fixes.jsonl")
    for d in fixes:
        if d.get("route") == "mlx" and d["field_id"] in moved:
            d["route"] = "tesseract"
    _jl_write(adir / "fixes.jsonl", fixes)
    assert _whole(project) == []
    assert P.validate_build(project, project / "build") == []
