"""WI-37 (real-data findings P21, P22): the provenance validator on a route-B fallback batch and on a multi-field
source export line. Synthetic data only: a copy of the ``fx-chain/clean`` variant with one targeted change.

P21 rule under test: the route's run id is authoritative. When the MLX route degenerates and falls back to tesseract
(``engines.ocr_page``), its lines carry ``engine: "tesseract"`` but live in the mlx run directory under the mlx run id;
the audit keys their cells by that line engine (``tesseract``) while ``runs.json`` has the route (``mlx``). That is
legitimate. Everything else stays a ``run_id_mismatch``: a line whose run_id points to a different run, a foreign
engine label in a run directory, a prov run_id that is no run of the revision, a revision that also runs tesseract as a
route of its own, and the separate tesseract geometry-reference run (``ocr/tesseract/<fp>.t<n>/``) is a run of its own.

P22 rule under test: ``value_audited`` of a ``source`` export line is the value of its first printed field
(``records_to_txt``: "the first printed value of a source line"), which for a unit-first line is the unit; of an
``observation`` line it is the value. The validator compared every line with its first ``number`` field.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from klode_kit.contract import provenance as P

pytestmark = pytest.mark.unit

CHAIN = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "fx-chain"


def _rows(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def _root(tmp: Path) -> Path:
    root = tmp / "clean"
    shutil.copytree(CHAIN / "clean", root)
    return root


def _audit(root: Path) -> Path:
    return next((root / "audit").glob("*/a1"))


def _mlx_page(root: Path) -> Path:
    return next((root / "ocr" / "mlx").glob("*/*.jsonl"))


def _fall_back(root: Path) -> None:
    """The mlx page was read by tesseract (route B fallback): lines engine tesseract, cells keyed by that engine."""
    page = _mlx_page(root)
    lines = _rows(page)
    ders = {x["der_sha256"] for x in lines}
    for x in lines:
        x["engine"] = "tesseract"
    _write(page, lines)
    adir = _audit(root)
    cells = _rows(adir / "cells.jsonl")
    renamed = set()
    for c in cells:
        if c["prov"].get("mlx", {}).get("der_sha256") in ders:
            for key in ("candidates", "prov"):
                c[key]["tesseract"] = c[key].pop("mlx")
            renamed.add(c["field_id"])
    assert renamed
    _write(adir / "cells.jsonl", cells)
    fixes = _rows(adir / "fixes.jsonl")
    for d in fixes:
        if d.get("route") == "mlx" and d["field_id"] in renamed:
            d["route"] = "tesseract"
    _write(adir / "fixes.jsonl", fixes)


def _runs(found):
    return [(v.code, v.engine) for v in found if v.code == "run_id_mismatch"]


# --------------------------------------------------------------------------- P21


def test_fallback_batch_validates(tmp_path):
    """(S1) the legitimate route-B fallback: 0 violations, not 'line run_id not under ...' / 'prov run_id != runs.json'."""
    root = _root(tmp_path)
    assert P.validate(root) == []
    _fall_back(root)
    assert P.validate(root) == []


def test_fallback_line_in_another_routes_directory_is_still_a_mismatch(tmp_path):
    """(S3) only the mlx route has a tesseract fallback: a tesseract-labelled line under vision/ stays reported."""
    root = _root(tmp_path)
    page = next((root / "ocr" / "vision").glob("*/*.jsonl"))
    lines = _rows(page)
    lines[0]["engine"] = "tesseract"
    _write(page, lines)
    assert _runs(P.validate(root)) == [("run_id_mismatch", "vision")]


def test_foreign_engine_label_in_mlx_directory_is_still_a_mismatch(tmp_path):
    """(S3) the mlx run holds mlx or tesseract lines only."""
    root = _root(tmp_path)
    page = _mlx_page(root)
    lines = _rows(page)
    lines[0]["engine"] = "vision"
    _write(page, lines)
    assert _runs(P.validate(root)) == [("run_id_mismatch", "mlx")]


def test_fallback_line_whose_run_id_names_another_run_is_a_mismatch(tmp_path):
    """(S3 law) a line whose run_id points to a different run is a violation, fallback or not."""
    root = _root(tmp_path)
    _fall_back(root)
    page = _mlx_page(root)
    lines = _rows(page)
    vision_run = _rows(next((root / "ocr" / "vision").glob("*/*.jsonl")))[0]["run_id"]
    lines[0]["run_id"] = vision_run
    _write(page, lines)
    assert ("run_id_mismatch", "mlx") in _runs(P.validate(root))


def test_fallback_cell_pointing_at_a_run_outside_the_revision_is_a_mismatch(tmp_path):
    """(S3) a tesseract-keyed prov whose run_id is no run of runs.json."""
    root = _root(tmp_path)
    _fall_back(root)
    adir = _audit(root)
    cells = _rows(adir / "cells.jsonl")
    hit = next(c for c in cells if "tesseract" in c["prov"])
    hit["prov"]["tesseract"]["run_id"] = hit["prov"]["tesseract"]["run_id"].replace("/t1", "/t9")
    _write(adir / "cells.jsonl", cells)
    assert ("run_id_mismatch", "tesseract") in _runs(P.validate(root))


def test_tesseract_prov_on_a_vision_run_is_a_mismatch(tmp_path):
    """(S3) the fallback exception is the mlx route's: a tesseract prov may not claim the vision run."""
    root = _root(tmp_path)
    _fall_back(root)
    adir = _audit(root)
    runs = json.loads((adir / "runs.json").read_text(encoding="utf-8"))
    cells = _rows(adir / "cells.jsonl")
    hit = next(c for c in cells if "tesseract" in c["prov"])
    hit["prov"]["tesseract"]["run_id"] = runs["vision"]
    _write(adir / "cells.jsonl", cells)
    assert ("run_id_mismatch", "tesseract") in _runs(P.validate(root))


def test_tesseract_as_a_route_of_its_own_must_match_its_own_run(tmp_path):
    """(S3) when the revision runs tesseract as a route (runs.json has it), a tesseract prov must name that run, not
    the mlx run: the exception is for a tesseract that is not a route of the revision."""
    root = _root(tmp_path)
    _fall_back(root)
    adir = _audit(root)
    runs = json.loads((adir / "runs.json").read_text(encoding="utf-8"))
    runs["tesseract"] = runs["mlx"].replace("/mlx/", "/tesseract/")
    (adir / "runs.json").write_text(json.dumps(runs), encoding="utf-8")
    assert ("run_id_mismatch", "tesseract") in _runs(P.validate(root))


def test_geometry_reference_run_is_a_run_of_its_own(tmp_path):
    """(S2) WI-33e: ``ocr/tesseract/<fp>.t1/`` (engine tesseract, its own run id) coexists with an mlx fallback and
    validates; a non-tesseract line inside it is reported."""
    root = _root(tmp_path)
    _fall_back(root)
    src = _mlx_page(root)
    ref_dir = root / "ocr" / "tesseract" / "0123456789ab.t1"
    ref_dir.mkdir(parents=True)
    ref = _rows(src)
    batch = ref[0]["run_id"].split("/")[0]
    for x in ref:
        x["run_id"] = f"{batch}/tesseract/0123456789ab/t1"
    _write(ref_dir / src.name, ref)
    assert P.validate(root) == []
    ref[0]["engine"] = "mlx"
    _write(ref_dir / src.name, ref)
    assert _runs(P.validate(root)) == [("run_id_mismatch", "tesseract")]


# --------------------------------------------------------------------------- P22


def _as_source_line(root: Path, order: tuple[str, str]) -> tuple[dict, list[dict]]:
    """Rewrite export line 0 as a two-field source line printing ``order`` (``unit`` and ``value`` suffixes)."""
    path = root / "build" / "src" / "export_map.jsonl"
    rows = _rows(path)
    row = rows[0]
    rec = row["rec_id"]
    fids = {k: next(f for f in row["field_ids"] if f.endswith(f".{k}")) for k in ("value", "unit")}
    body = json.loads(next((root / "records" / "_rev").glob(f"{rec}.r1.md")).read_text(encoding="utf-8")[4:].split("\n---\n")[0])
    values = {k: body["fields"][fids[k]] for k in fids}
    first, second = order
    text = f"{values[first]} {values[second]} ⟨{rec}⟩"
    a = (0, len(values[first]))
    b = (a[1] + 1, a[1] + 1 + len(values[second]))
    row.update(line_kind="source", field_ids=[fids[first], fids[second]], text=text, transforms=[],
               positions={fids[first]: list(a), fids[second]: list(b)}, value_audited=values[first])
    _write(path, rows)
    target = root / "build" / row["source_file"]
    lines = target.read_text(encoding="utf-8").splitlines()
    lines[row["line_no"] - 1] = text
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return row, rows


def test_source_line_value_audited_is_its_first_printed_field(tmp_path):
    """(S1) unit-first source line (the real P22 shape): value_audited is the unit, the left-most field."""
    root = _root(tmp_path)
    _as_source_line(root, ("unit", "value"))
    assert P.validate(root) == []


def test_source_line_value_audited_of_a_later_field_is_a_mismatch(tmp_path):
    """(S3) value_audited naming the number that is not printed first is a ``value_audited_mismatch``."""
    root = _root(tmp_path)
    row, rows = _as_source_line(root, ("unit", "value"))
    row["value_audited"] = "128"
    _write(root / "build" / "src" / "export_map.jsonl", rows)
    assert [v.code for v in P.validate(root)] == ["value_audited_mismatch"]


@pytest.mark.parametrize("order", [("value", "unit"), ("unit", "value")], ids=["value-first", "unit-first"])
def test_source_line_value_audited_follows_print_order(tmp_path, order):
    """(S4-ish) for either printed order the first printed field's value validates and any other string does not."""
    root = _root(tmp_path)
    row, rows = _as_source_line(root, order)
    assert P.validate(root) == []
    row["value_audited"] = "not-the-value"
    _write(root / "build" / "src" / "export_map.jsonl", rows)
    assert [v.code for v in P.validate(root)] == ["value_audited_mismatch"]


def test_observation_line_value_audited_is_still_the_value(tmp_path):
    """(S3) an observation line keeps comparing with its value field."""
    root = _root(tmp_path)
    path = root / "build" / "src" / "export_map.jsonl"
    rows = _rows(path)
    rows[0]["line_kind"] = "observation"
    rows[0]["value_audited"] = "g/L"
    _write(path, rows)
    assert [v.code for v in P.validate(root)] == ["value_audited_mismatch"]
