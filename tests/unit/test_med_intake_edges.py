"""WI-13b: edges of the pure parts of ``med_intake`` added after the red record (unit lane; additions only)."""

from __future__ import annotations

import io
import os
from pathlib import Path

import pytest

from klode_kit import med_intake as M

pytestmark = pytest.mark.unit


def test_run_refuses_an_unknown_type_before_touching_anything(tmp_path: Path) -> None:
    err = io.StringIO()
    assert M.run(tmp_path / "inbox", project=tmp_path, record_type="scan", out=err) == 2
    assert "unknown document type" in err.getvalue()
    assert list(tmp_path.iterdir()) == []


def test_run_refuses_a_missing_project(tmp_path: Path) -> None:
    err = io.StringIO()
    assert M.run(tmp_path, project=tmp_path / "nope", out=err) == 2
    assert not (tmp_path / "nope").exists()


def test_a_leftover_temporary_link_of_a_killed_run_is_replaced(tmp_path: Path) -> None:
    (tmp_path / "confirm").mkdir()
    (tmp_path / "confirm" / ".20261022.html.tmp").symlink_to("leftover")
    page = M.link_confirm(tmp_path, "20261022", "aaaaaaaaaaaa", 1)
    assert os.readlink(page) == "../work/aaaaaaaaaaaa/audit/a1/confirm.html"
    assert sorted(p.name for p in (tmp_path / "confirm").iterdir(
        ) if p.name.startswith(".")) == []  # nothing left behind


def test_link_confirm_will_not_take_over_another_batchs_link(tmp_path: Path) -> None:
    M.link_confirm(tmp_path, "20261022", "aaaaaaaaaaaa", 1)
    with pytest.raises(M.AliasConflict):
        M.link_confirm(tmp_path, "20261022", "bbbbbbbbbbbb", 1)
    assert os.readlink(tmp_path / "confirm" / "20261022.html") == "../work/aaaaaaaaaaaa/audit/a1/confirm.html"


def test_routes_of_a_receipt_without_attempts_come_from_its_pages() -> None:
    receipt = {"pages": [{"key": "p001", "routes": {"codex": {"status": "done"}, "vision": {"status": "done"}}}],
               "attempts": {}}
    assert M.ocr_line(receipt) == "ocr: 1 页 × 2 路 OK"


def test_summary_line_of_a_child_that_printed_junk_is_empty() -> None:
    assert M._json_line("") == {} and M._json_line("not json\n") == {} and M._json_line("[1]\n") == {}
    assert M._json_line('noise\n{"batch_id": "x"}\n') == {"batch_id": "x"}


def test_unreadable_receipt_is_none(tmp_path: Path) -> None:
    (tmp_path / "bad.json").write_text("{", encoding="utf-8")
    assert M._read_json(tmp_path / "bad.json") is None and M._read_json(tmp_path / "gone.json") is None
    (tmp_path / "ok.json").write_text('{"a": 1}', encoding="utf-8")
    assert M._read_json(tmp_path / "ok.json") == {"a": 1}
