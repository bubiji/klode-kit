"""WI-00b3b: the provenance validator on real pipeline products (impl-deviations WI-01a「待修」, WI-01c「发现」,
WI-08c 遗留③).

Products: the real stages on a fixture (``export_fx.pipeline``: kb-ocr batch -> ``kb-audit`` -> writeback) then the
real WI-01a/01c export ``records_to_txt.export(project, project/build, deident=None|pseudonym|dateshift)`` and the
build receipt the validator reads (``audit_bindings``, ``deident_mode``). Field ids are kb-audit slots
(``<rec_id>/r005-number-0``, ``r003-date-0``, ``r002-unit-0``, ``r001-drug-0``).

Public surface added (``klode_kit.contract.provenance``):

- ``Layout.for_project(project, build_dir)``: the data-project layout - ``work/<batch>/{manifest,derived,
  page_map}.jsonl``, ``work/<batch>/ocr``, ``work/<batch>/text``, audit revisions ``work/<batch>/audit/a<N>/``,
  ``records/``, and the build directory;
- an OCR line without ``run_id`` (real kb-ocr page files carry none; kb-audit stamps the route's run id when it
  reads them) is traced by the run id its attempt directory names, ``<batch>/<engine>/<fp>/t<N>`` for
  ``work/<batch>/ocr/<engine>/<fp>.t<N>/<key>.jsonl`` (the kb-ocr formula); when the directories name no valid run
  id the line is a ``run_id_mismatch``, never a malformed artifact or a silent skip.

Law: the pipeline's own products validate with 0 violations in every deident mode (S5); a single corruption of
segment (3) on a real slot - a transform on a number or unit, a dateshift outside dateshift mode, a changed
``value_audited``, a field of unknown slot format - is always reported with its own code (S4).

Engine boundary: recorded engine outputs of the fixtures (as WI-08a..08d, 01a, 01c).
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from _support import export_fx as X
from klode_kit.build import records_to_txt as R
from klode_kit.contract import provenance as P

pytestmark = pytest.mark.contract_replay

FIXTURES = ("fx-labs-3x", "fx-manualfix", "fx-rx-stamp", "fx-u8")
MODES = (None, "pseudonym", "dateshift")
_CACHE: dict[tuple[str, str | None], Path] = {}


def _jl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _jl_write(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def _build(root: Path, fx: str, mode: str | None) -> Path:
    project = root / "proj"
    project.mkdir(parents=True)
    X.pipeline(project, X.FIXTURES / fx)
    res = R.export(project, project / "build", kb_id="k", deident=mode)
    receipt = {"audit_bindings": res["audit_bindings"], "deident_mode": mode, "kb_id": "k"}
    (project / "build" / "build_receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    return project


@pytest.fixture(scope="module")
def products(tmp_path_factory):
    """``(fixture, mode) -> project``: each pipeline run once per module; tests work on copies."""
    def get(fx: str, mode: str | None) -> Path:
        if (fx, mode) not in _CACHE:
            _CACHE[(fx, mode)] = _build(tmp_path_factory.mktemp(f"{fx}-{mode}"), fx, mode)
        return _CACHE[(fx, mode)]
    return get


def _copy(src: Path, dest: Path) -> Path:
    shutil.copytree(src, dest, symlinks=True)
    return dest


def _validate(project: Path) -> list[P.Violation]:
    return P.validate(project, P.Layout.for_project(project, project / "build"))


def _map(project: Path) -> list[dict]:
    return _jl(project / "build" / "src" / "export_map.jsonl")


# --------------------------------------------------------------------------- zero violations


@pytest.mark.parametrize("mode", MODES, ids=["plain", "pseudonym", "dateshift"])
@pytest.mark.parametrize("fx", FIXTURES)
def test_pipeline_products_zero_violations(products, fx, mode):
    """(S5) real stages + real export: 0 violations in every deident mode; the export uses kb-audit slots, and the
    dateshift export really carries dateshift transforms on date slots (the check is not vacuous)."""
    project = products(fx, mode)
    rows = _map(project)
    assert rows
    fids = [f for r in rows for f in r["field_ids"]]
    assert fids and all(P.slot_kind(f) in {"number", "date", "unit", "flag", "drug"} for f in fids)
    assert any(P.slot_kind(f) == "number" for f in fids)
    shifts = [t for r in rows for t in r["transforms"] if t["kind"] == "dateshift"]
    assert bool(shifts) == (mode == "dateshift")
    assert all("-date-" in t["field_id"] for t in shifts)
    assert _validate(project) == []


def test_for_project_lists_pipeline_artifacts(products):
    project = products("fx-labs-3x", None)
    lay = P.Layout.for_project(project, project / "build")
    batch = next(p for p in (project / "work").iterdir() if p.is_dir())
    assert lay.root == project and lay.build_dir == project / "build"
    assert lay.manifest == [batch / "manifest.jsonl"] and lay.derived == [batch / "derived.jsonl"]
    assert lay.ocr_dirs == [batch / "ocr"] and lay.records_dir == project / "records"
    assert lay.text_dirs == [] and lay.page_map == []


def test_empty_project_is_not_clean(tmp_path):
    """(S3) a project directory without pipeline products never validates as 0 violations."""
    (tmp_path / "proj").mkdir()
    found = P.validate(tmp_path / "proj", P.Layout.for_project(tmp_path / "proj", tmp_path / "proj" / "build"))
    assert found and {v.code for v in found} == {"artifact_missing"}


# --------------------------------------------------------------------------- run_id of real kb-ocr page files


def _strip_run_ids(project: Path) -> int:
    n = 0
    for page in sorted(project.glob("work/*/ocr/*/*/*.jsonl")):
        rows = _jl(page)
        for r in rows:
            n += r.pop("run_id", None) is not None
        _jl_write(page, rows)
    return n


@pytest.mark.parametrize("mode", [None, "dateshift"], ids=["plain", "dateshift"])
def test_unstamped_ocr_lines_trace_by_attempt_dir(products, tmp_path, mode):
    """(S2) real kb-ocr page files carry no ``run_id``: the validator derives it from the attempt directory and
    the products still validate with 0 violations."""
    project = _copy(products("fx-labs-3x", mode), tmp_path / "proj")
    assert _strip_run_ids(project) > 0
    assert _validate(project) == []


def test_unstamped_line_without_derivable_run_id_is_reported(products, tmp_path):
    """(S3) unstamped lines whose directories name no valid run id (the batch directory is not a batch id): every
    such line is a ``run_id_mismatch`` - not a malformed artifact, not skipped in silence."""
    project = _copy(products("fx-labs-3x", None), tmp_path / "proj")
    _strip_run_ids(project)
    lay = P.Layout.for_project(project, project / "build")
    odd = tmp_path / "not-a-batch"
    shutil.copytree(lay.ocr_dirs[0], odd / "ocr")
    lay.ocr_dirs = [odd / "ocr"]
    pages = sorted((odd / "ocr").glob("*/*/*.jsonl"))
    n_lines = sum(len(_jl(p)) for p in pages)
    found = P.validate(project, lay)
    codes = {v.code for v in found}
    assert "malformed_artifact" not in codes
    flagged = {v.location for v in found if v.code == "run_id_mismatch"}
    assert len(flagged) == n_lines


# --------------------------------------------------------------------------- single corruption on real slots


def _fid_of(row: dict, kind: str) -> str | None:
    return next((f for f in row["field_ids"] if f.split("/", 1)[1].split("-")[-2:-1] == [kind]), None)


def _corrupt(cls: str, project: Path, pick: int) -> tuple[str, int] | None:
    """Corrupt one export-map row; ``(expected code, line number)`` or ``None`` when the row has no such slot."""
    path = project / "build" / "src" / "export_map.jsonl"
    rows = _jl(path)
    i = pick % len(rows)
    row = rows[i]
    rec = row["rec_id"]
    meta = json.loads((project / "build" / "build_receipt.json").read_text(encoding="utf-8"))
    rev = meta["audit_bindings"][rec]["rev"]
    text = (project / "records" / "_rev" / f"{rec}.r{rev}.md").read_text(encoding="utf-8")
    body = json.loads(text[4:text.index("\n---", 4)])["fields"]
    if cls in ("alias_on_number", "dateshift_on_number", "alias_on_unit", "dateshift_on_unit"):
        kind = cls.rsplit("_", 1)[1]
        fid = _fid_of(row, kind)
        if fid is None:
            return None
        row["transforms"].append({"kind": cls.split("_")[0], "field_id": fid, "from": body[fid], "to": body[fid] + "0"})
        code = "transform_on_numeric"
    elif cls == "dateshift_wrong_mode":
        if meta["deident_mode"] == "dateshift":
            return None
        fid = _fid_of(row, "date")
        if fid is None:
            return None
        row["transforms"].append({"kind": "dateshift", "field_id": fid, "from": body[fid], "to": body[fid]})
        code = "dateshift_without_mode"
    elif cls == "alias_on_date":
        fid = _fid_of(row, "date")
        if fid is None:
            return None
        row["transforms"].append({"kind": "alias", "field_id": fid, "from": body[fid], "to": body[fid]})
        code = "alias_not_item"
    elif cls == "value_audited":
        if _fid_of(row, "number") is None:
            return None
        row["value_audited"] = row["value_audited"] + "1"
        code = "value_audited_mismatch"
    elif cls == "unknown_slot":
        fid = row["field_ids"][0]
        bad = f"{rec}/r999-weird-0"
        row["field_ids"][0] = bad
        row["positions"][bad] = row["positions"].pop(fid)
        code = "unknown_slot"
    else:  # pragma: no cover - the strategy draws from CLASSES only
        raise AssertionError(cls)
    _jl_write(path, rows)
    return code, i + 1


CLASSES = ("alias_on_number", "dateshift_on_number", "alias_on_unit", "dateshift_on_unit", "dateshift_wrong_mode",
           "alias_on_date", "value_audited", "unknown_slot")


@settings(max_examples=80, deadline=None, suppress_health_check=[HealthCheck.too_slow,
                                                                  HealthCheck.function_scoped_fixture])
@given(fx=st.sampled_from(FIXTURES), mode=st.sampled_from(MODES), cls=st.sampled_from(CLASSES),
       pick=st.integers(min_value=0, max_value=10_000))
def test_single_corruption_on_real_slots_detected(products, fx, mode, cls, pick):
    """(S4) any one corruption of segment (3) on a real kb-audit slot is reported with its own code, located at
    the corrupted export-map row; an uncorrupted copy stays clean."""
    with tempfile.TemporaryDirectory() as d:
        project = _copy(products(fx, mode), Path(d) / "proj")
        hit = _corrupt(cls, project, pick)
        found = _validate(project)
        if hit is None:
            assert found == []
            return
        code, line_no = hit
        where = f"build/src/export_map.jsonl:{line_no}"
        assert any(v.code == code and (v.location == where or v.location.startswith(where + " ")) for v in found), found
