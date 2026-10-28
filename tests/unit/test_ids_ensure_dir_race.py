"""``ids._ensure_dir`` when another process creates the same directory first (deterministic, no timing).

The race branches - ``FileExistsError`` for a directory that now exists (fine) and for a non-directory in the way
(re-raised) - were only reached by chance in concurrent tests, so CI's coverage of ``contract/ids.py`` depended on
timing (2026-10-28, 93.33% branches on macOS CI).
"""

from __future__ import annotations

import os

import pytest

from klode_kit.contract import ids

pytestmark = pytest.mark.unit


def test_directory_created_by_someone_else_in_between_is_accepted(tmp_path, monkeypatch):
    target = tmp_path / "a" / "b"
    real_mkdir = os.mkdir

    def racing_mkdir(path, *args, **kwargs):
        real_mkdir(path, *args, **kwargs)  # the other process wins ...
        raise FileExistsError(path)        # ... and our own mkdir reports it

    monkeypatch.setattr(ids.os, "mkdir", racing_mkdir)
    ids._ensure_dir(target)
    assert target.is_dir()


def test_a_file_in_the_way_is_not_accepted(tmp_path, monkeypatch):
    target = tmp_path / "a"

    def file_in_the_way(path, *args, **kwargs):
        (tmp_path / "a").write_text("not a directory", encoding="utf-8")
        raise FileExistsError(path)

    monkeypatch.setattr(ids.os, "mkdir", file_in_the_way)
    with pytest.raises(FileExistsError):
        ids._ensure_dir(target)
    assert not target.is_dir()
