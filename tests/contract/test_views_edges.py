"""WI-08d: view rebuild edges - malformed or incomplete record files, leftovers of a rebuild that died
(matrix 08d-* supplements; module docstring of ``klode_kit.audit.views``)."""

from __future__ import annotations

import json
import os

import pytest

from _support import views_fx as V
from _support import writeback_fx as W

pytestmark = pytest.mark.contract_replay


def u8_picked(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    b, adir = W.audited(project, W.U8)
    assert W.submit(project, W.export(project, b, adir, W.picks(adir)), tmp_path)[0] == 0
    return project, V.recs_by_key(project, W.U8, adir)


def test_malformed_record_file_refused(tmp_path):
    project, _ = u8_picked(tmp_path)
    bad = project / "records" / "_by_id" / "r-000000000000.md"
    bad.write_text("no front matter\n", encoding="utf-8")
    res = V.views_cli(project)
    assert res.returncode == 30
    assert "r-000000000000.md" in res.stderr.decode()
    assert V.links(project) == {}
    bad.write_text("---\n[1, 2]\n---\n", encoding="utf-8")
    assert V.views_cli(project).returncode == 30
    bad.write_text("---\n{not json\n---\n", encoding="utf-8")
    assert V.views_cli(project).returncode == 30


def test_record_without_revisions_has_no_view(tmp_path):
    project, r = u8_picked(tmp_path)
    front = W.front(project, r["p001"])
    empty = dict(front, rec_id="r-000000000000", candidates=[])
    empty.pop("effective")
    (project / "records" / "_by_id" / "r-000000000000.md").write_text(
        "---\n" + json.dumps(empty) + "\n---\n", encoding="utf-8")
    V.views_ok(project)
    assert len(V.links(project)) == 4
    assert not any("r-000000000000" in t for t in V.links(project).values())


def test_missing_body_reported_others_written(tmp_path):
    """A record whose revision body is missing gets no view (never a dangling link): exit 30 naming it; the
    other record's views are written."""
    project, r = u8_picked(tmp_path)
    moved = tmp_path / "moved"
    moved.mkdir()
    os.rename(W.body_path(project, r["p001"], 1), moved / "body.md")
    res = V.views_cli(project)
    assert res.returncode == 30
    assert r["p001"] in res.stderr.decode()
    assert V.links(project) == {"records/2026-10-22-lab.md": V.body_link(r["p002"], 1),
                                "verified/2026-10-22-lab.md": V.verified_link(r["p002"], 1)}
    assert V.dangling(project) == []


def test_leftovers_of_a_dead_rebuild_cleaned(tmp_path):
    """Temporary links left by a rebuild that died (any pid, also this process's own) are removed."""
    project, r = u8_picked(tmp_path)
    (project / "verified").mkdir()
    own = project / "records" / f".2026-10-22-lab.md.{os.getpid()}.tmp"
    own.symlink_to("_rev/whatever.md")
    other = project / "verified" / ".2026-10-22-lab.md.99999.tmp"
    other.symlink_to("../records/_rev/whatever.md")
    assert V.views_inproc(project) == 0
    assert not os.path.lexists(own) and not os.path.lexists(other)
    assert len(V.links(project)) == 4 and V.dangling(project) == []
