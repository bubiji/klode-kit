"""WI-13c unit rows (spec .claude/tdd-guardian/wi-13c-spec.md): 13c-02, 13c-06, 13c-09, 13c-10.

Law: none for the guards and the schema (closed value sets); the confirm directory link follows the same state rules
as the ``.html`` link (13c-10 covers moving with the revision and refusing foreign names).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from klode_kit import pipeline as P
from klode_kit.contract.schema import issues
from klode_kit.med_intake import AliasConflict, allocate_alias, link_confirm
from klode_kit.ocr import run_batch

pytestmark = pytest.mark.unit

BATCH = "abc123def456"
RECEIPT = {
    "schema": "klode-kit.receipt/1", "step": "ocr_vision", "status": "done", "started": "2026-10-26T10:00:00Z",
    "ended": "2026-10-26T10:00:01Z", "inputs_hash": "0" * 64, "config_hash": "1" * 64, "config_version": 1,
    "outputs": [], "exit_code": 0,
}


# ---------------------------------------------------------------- 13c-02
def test_kb_ocr_rejects_unknown_type_and_creates_nothing(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    project = tmp_path / "proj"
    project.mkdir()
    assert run_batch.main(["--batch", str(inbox), "--project", str(project), "--type", "scan"]) == 2
    assert not (project / "work").exists()


def test_pipeline_rejects_unknown_type(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    assert P.main(["--batch", str(inbox), "--project", str(tmp_path), "--type", "scan"]) == 2
    assert not (tmp_path / "work").exists()


# ---------------------------------------------------------------- 13c-06
def _ctx(tmp_path: Path, record_type: str | None) -> P.Ctx:
    return P.Ctx(tmp_path, tmp_path, BATCH, ["b" * 64, "a" * 64], tmp_path / "launcher", "vision", {}, None,
                 open(os.devnull, "w"), None, record_type=record_type)


def test_ocr_inputs_only_change_when_a_type_is_given(tmp_path: Path) -> None:
    plain = P._ocr_inputs(_ctx(tmp_path, None))
    assert plain == {"images": ["a" * 64, "b" * 64], "routes": "vision"}  # the shape the old hashes were made from
    lab, rx = P._ocr_inputs(_ctx(tmp_path, "lab")), P._ocr_inputs(_ctx(tmp_path, "rx"))
    assert len({repr(plain), repr(lab), repr(rx)}) == 3


# ---------------------------------------------------------------- 13c-09
@pytest.mark.parametrize("value", [0, 5, 123456])
def test_elapsed_ms_valid(value: int) -> None:
    assert issues("receipt", {**RECEIPT, "elapsed_ms": value}) == []


@pytest.mark.parametrize("value", [-1, 1.5, "3", None])
def test_elapsed_ms_invalid(value: object) -> None:
    assert issues("receipt", {**RECEIPT, "elapsed_ms": value}) != []


def test_receipt_without_elapsed_ms_still_valid() -> None:
    assert issues("receipt", RECEIPT) == []


# ---------------------------------------------------------------- 13c-10
def _audit_rev(root: Path, rev: int) -> Path:
    adir = root / "work" / BATCH / "audit" / f"a{rev}"
    (adir / "crops").mkdir(parents=True)
    (adir / "crops" / "x.png").write_bytes(f"png{rev}".encode())
    (adir / "confirm.html").write_text("<img src=\"crops/x.png\">", encoding="utf-8")
    return adir


def test_confirm_folder_is_made_and_moves_with_the_revision(tmp_path: Path) -> None:
    _audit_rev(tmp_path, 1)
    _audit_rev(tmp_path, 2)
    folder = tmp_path / "confirm" / "20261022"
    link_confirm(tmp_path, "20261022", BATCH, 1)
    assert folder.is_dir() and not folder.is_symlink()  # a folder of links: the recorded link set stays as it was
    assert os.readlink(folder / "confirm.html") == f"../../work/{BATCH}/audit/a1/confirm.html"
    assert os.readlink(folder / "crops") == f"../../work/{BATCH}/audit/a1/crops"
    assert os.readlink(tmp_path / "confirm" / "20261022.html") == f"../work/{BATCH}/audit/a1/confirm.html"
    assert (folder / "crops" / "x.png").read_bytes() == b"png1"  # what the relative src of the page resolves to
    link_confirm(tmp_path, "20261022", BATCH, 1)  # idempotent
    link_confirm(tmp_path, "20261022", BATCH, 2)
    assert (folder / "crops" / "x.png").read_bytes() == b"png2"
    assert os.readlink(tmp_path / "confirm" / "20261022.html") == f"../work/{BATCH}/audit/a2/confirm.html"
    assert sorted(p.name for p in folder.iterdir()) == ["confirm.html", "crops"]


def test_a_killed_runs_leftover_and_an_empty_folder_are_adopted(tmp_path: Path) -> None:
    _audit_rev(tmp_path, 1)
    folder = tmp_path / "confirm" / "20261022"
    folder.mkdir(parents=True)
    os.symlink("nowhere", folder / ".crops.tmp")
    link_confirm(tmp_path, "20261022", BATCH, 1)
    assert (folder / "crops" / "x.png").read_bytes() == b"png1"


def test_foreign_confirm_folder_or_link_is_never_touched(tmp_path: Path) -> None:
    _audit_rev(tmp_path, 1)
    foreign = tmp_path / "confirm" / "20261022"
    foreign.mkdir(parents=True)
    (foreign / "mine.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(AliasConflict):
        link_confirm(tmp_path, "20261022", BATCH, 1)
    assert (foreign / "mine.txt").read_text(encoding="utf-8") == "keep"
    assert not (tmp_path / "confirm" / "20261022.html").exists()  # nothing half-made
    os.symlink("elsewhere", tmp_path / "confirm" / "20261023")  # a stray link under the folder's name
    with pytest.raises(AliasConflict):
        link_confirm(tmp_path, "20261023", BATCH, 1)
    assert os.readlink(tmp_path / "confirm" / "20261023") == "elsewhere"


def test_allocate_alias_skips_a_name_whose_confirm_folder_exists(tmp_path: Path) -> None:
    (tmp_path / "confirm" / "20261022").mkdir(parents=True)
    assert allocate_alias(tmp_path, "20261022", BATCH) == "20261022-2"
