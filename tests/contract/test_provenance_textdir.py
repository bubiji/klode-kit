"""WI-04c pending item (impl-deviations「WI-04c 待定项建议」, done with WI-07b): text-layer lines in ``text/``.

WI-04c writes the pdftext rows of a text-layer page to ``work/<batch>/text/<key>.jsonl`` (no ``run_id``); the
provenance validator only scanned ``ocr/<engine>/<fp>.t<N>/``. Contract added (``klode_kit.contract.provenance``):

- ``Layout.text_dirs``: directories of ``<key>.jsonl`` pdftext rows; ``Layout.for_products(root)`` lists
  ``root/text`` when it exists, and lists ``root/ocr`` unless the products have ``text/`` and no ``ocr/``
  (a text-layer-only batch has no OCR attempts).
- A line read from ``text/<key>.jsonl`` is traced by the key ``run_id = "text/<key>"`` + ``der_sha256`` +
  ``line_idx``; ``cells.jsonl`` prov of a pdftext candidate carries that key as ``run_id`` (``cells.json``
  accepts it). ``runs.json`` freezes OCR attempts only and is not consulted for ``text/`` keys.
- Only ``pdftext`` lines may live in ``text/`` (anything else: ``run_id_mismatch``); the dpi check
  (``pdftext_dpi_mismatch``) applies to them exactly as to ``ocr/pdftext``.
- Transition: both locations are scanned, so fx-chain ``legal-pdftext_dpi`` (still under ``ocr/pdftext``) stays
  clean, and a product carrying both copies validates too.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from klode_kit.contract import provenance as P
from klode_kit.contract import schema as S

pytestmark = pytest.mark.contract_replay

REPO = Path(__file__).resolve().parents[2]
LEGAL = REPO / "tests" / "fixtures" / "fx-chain" / "legal-pdftext_dpi"


def rows(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def write_rows(path: Path, data: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in data), encoding="utf-8")


def cells_path(root: Path) -> Path:
    return next((root / "audit").glob("*/a*/cells.jsonl"))


def migrate(tmp_path: Path, key: str = "p001") -> Path:
    """legal-pdftext_dpi with its pdftext rows moved to text/<key>.jsonl, cells re-keyed, runs.json without
    pdftext, and no ocr/ directory left in the products."""
    root = tmp_path / "products"
    shutil.copytree(LEGAL, root)
    page = next((root / "ocr" / "pdftext").glob("*/*.jsonl"))
    lines = rows(page)
    for line in lines:
        line.pop("run_id")
    write_rows(root / "text" / f"{key}.jsonl", lines)
    shutil.move(str(root / "ocr"), str(tmp_path / "ocr-moved-away"))
    cells = rows(cells_path(root))
    for cell in cells:
        cell["prov"]["pdftext"]["run_id"] = f"text/{key}"
    write_rows(cells_path(root), cells)
    runs = cells_path(root).parent / "runs.json"
    runs.write_text("{}\n", encoding="utf-8")
    return root


def test_legal_fixture_still_clean_and_layout_unchanged():
    lay = P.Layout.for_products(LEGAL)
    assert lay.text_dirs == [] and lay.ocr_dirs == [LEGAL / "ocr"]
    assert P.validate(LEGAL) == []


def test_text_dir_products_validate_clean(tmp_path):
    root = migrate(tmp_path)
    lay = P.Layout.for_products(root)
    assert lay.text_dirs == [root / "text"] and lay.ocr_dirs == []
    assert P.validate(root) == []


def test_both_locations_scanned_during_transition(tmp_path):
    root = tmp_path / "both"
    shutil.copytree(LEGAL, root)
    lines = rows(next((root / "ocr" / "pdftext").glob("*/*.jsonl")))
    for line in lines:
        line.pop("run_id")
    write_rows(root / "text" / "p001.jsonl", lines)
    (root / "text" / "d-50782f11b14c_whole.txt").write_text("not jsonl, ignored\n", encoding="utf-8")
    lay = P.Layout.for_products(root)
    assert lay.text_dirs == [root / "text"] and lay.ocr_dirs == [root / "ocr"]
    assert P.validate(root) == []
    # and a cell may point at either copy
    cells = rows(cells_path(root))
    cells[0]["prov"]["pdftext"]["run_id"] = "text/p001"
    write_rows(cells_path(root), cells)
    assert P.validate(root) == []


def test_wrong_text_key_is_span_mismatch(tmp_path):
    root = migrate(tmp_path)
    cells = rows(cells_path(root))
    cells[0]["prov"]["pdftext"]["run_id"] = "text/p002"
    write_rows(cells_path(root), cells)
    found = P.validate(root)
    assert [(v.code, v.field_id, v.engine) for v in found] == [("span_mismatch", cells[0]["field_id"], "pdftext")]


def test_text_dir_dpi_checked(tmp_path):
    root = migrate(tmp_path)
    path = root / "text" / "p001.jsonl"
    lines = rows(path)
    lines[0]["bbox_px"] = list(lines[0]["bbox_pt"])
    write_rows(path, lines)
    found = P.validate(root)
    assert [(v.code, v.location) for v in found] == [("pdftext_dpi_mismatch", "text/p001.jsonl:1")]


def test_text_dir_holds_pdftext_only(tmp_path):
    root = migrate(tmp_path)
    path = root / "text" / "p001.jsonl"
    lines = rows(path)
    lines[0]["engine"] = "vision"
    write_rows(path, lines)
    found = P.validate(root)
    assert ("run_id_mismatch", "text/p001.jsonl:1", "vision") in [(v.code, v.location, v.engine) for v in found]


def test_text_only_products_without_text_dir_report_ocr_missing(tmp_path):
    root = migrate(tmp_path)
    shutil.move(str(root / "text"), str(tmp_path / "text-moved-away"))
    lay = P.Layout.for_products(root)
    assert lay.ocr_dirs == [root / "ocr"] and lay.text_dirs == []
    assert ("artifact_missing", "ocr") in [(v.code, v.location) for v in P.validate(root)]


def test_cells_schema_accepts_text_key():
    cell = rows(cells_path(LEGAL))[0]
    cell["prov"]["pdftext"]["run_id"] = "text/p001"
    assert S.issues("cells", cell) == []
    cell["prov"]["pdftext"]["run_id"] = "text/"
    assert S.issues("cells", cell) != []


def test_explicit_missing_text_dir_reported(tmp_path):
    """Added after green: an explicit layout naming a text dir that does not exist reports it missing."""
    root = migrate(tmp_path)
    lay = P.Layout.for_products(root)
    lay.text_dirs = [root / "no-such-text"]
    assert ("artifact_missing", "no-such-text") in [(v.code, v.location) for v in P.validate(root, lay)]
