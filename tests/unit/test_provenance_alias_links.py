"""WI-13b: a date alias ``work/<yyyymmdd>`` (symlink to a batch) is not a second batch for the provenance layout."""

from __future__ import annotations

from pathlib import Path

import pytest

from klode_kit.contract import provenance

pytestmark = pytest.mark.unit


def test_for_project_counts_a_batch_once_whatever_links_point_at_it(tmp_path: Path) -> None:
    batch = tmp_path / "work" / "a1b2c3d4e5f6"
    (batch / "ocr").mkdir(parents=True)
    (batch / "manifest.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "work" / "20261022").symlink_to("a1b2c3d4e5f6")
    (tmp_path / "work" / "20261022-2").symlink_to("a1b2c3d4e5f6")
    lay = provenance.Layout.for_project(tmp_path, tmp_path / "build")
    assert lay.manifest == [batch / "manifest.jsonl"]
    assert lay.ocr_dirs == [batch / "ocr"]
