"""WI-08d: compatibility views (matrix 08d-01..05, 08d-07, 08d-08; design 02 R2-10, r4.1, R4-01; 04 U3 / U8;
plan risk R-14).

``klode-kit views --project P`` rebuilds, from ``records/_by_id/*.md`` (written by the writeback alone):

- ``records/<date>-<type>.md`` -> ``_rev/<rec_id>.r<N>.md`` with N = ``effective.rev``, or the newest candidate's
  rev while the record has no effective revision (R4-01: the view shows that candidate and its status);
- ``verified/<date>-<type>.md`` -> ``../records/_rev/<rec_id>.r<effective.rev>.md`` only when
  ``effective.status == verified`` (same name as the record's ``records/`` view);
- records of one date and type are named ``<date>-<type>.md``, ``-2``, ``-3`` in the order (batch, page key,
  record_idx, rec_id) - never the order records were written.

A revision body carries ``status`` and ``uncertain`` rendered as an id list ``<page key>-l<NN>`` (R-14), so a view
shows what 04 U3 / U8 promise. Views never touch ``records/_by_id`` or ``records/_rev``.

Engine boundary: recorded engine outputs of the fixtures (pairing 06-11/12), as in WI-08a..08c.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from _support import confirm_fx as C
from _support import views_fx as V
from _support import writeback_fx as W
from _support.flows import base_env

pytestmark = pytest.mark.contract_replay

U3 = W.FIXTURES / "fx-u3-mixed"
MANUALFIX = W.FIXTURES / "fx-manualfix"


def proj(tmp_path: Path) -> Path:
    p = tmp_path / "proj"
    p.mkdir()
    return p


def frozen(project: Path) -> dict[str, bytes]:
    """``records/_by_id`` and ``records/_rev`` bytes (what views must never change)."""
    out = {}
    for sub in ("_by_id", "_rev"):
        for p in sorted((project / "records" / sub).glob("*")):
            out[f"{sub}/{p.name}"] = p.read_bytes()
    return out


def u8_all_picked(tmp_path):
    project = proj(tmp_path)
    b, adir = W.audited(project, W.U8)
    assert W.submit(project, W.export(project, b, adir, W.picks(adir)), tmp_path)[0] == 0
    return project, b, adir


# --------------------------------------------------------------------------- 08d-01 (U8-01)


def test_view_symlink_points_effective(tmp_path):
    """08d-01 (S1): after the fx-u8 writeback (both records verified by a human), ``records/2026-10-22-lab.md``
    links to the effective body of the p001 record and ``verified/2026-10-22-lab.md`` to the same body; the view
    reads ``status: verified`` (04 U8). The p002 record is ``-2``. Nothing else is linked."""
    project, b, adir = u8_all_picked(tmp_path)
    V.views_ok(project)
    recs = V.recs_by_key(project, W.U8, adir)
    x, y = recs["p001"], recs["p002"]
    ex, ey = W.front(project, x)["effective"]["rev"], W.front(project, y)["effective"]["rev"]
    assert V.links(project) == {
        "records/2026-10-22-lab.md": V.body_link(x, ex),
        "records/2026-10-22-lab-2.md": V.body_link(y, ey),
        "verified/2026-10-22-lab.md": V.verified_link(x, ex),
        "verified/2026-10-22-lab-2.md": V.verified_link(y, ey),
    }
    assert (project / "verified" / "2026-10-22-lab.md").resolve() == W.body_path(project, x, ex).resolve()
    shown = V.view(project, "verified/2026-10-22-lab.md")
    assert (shown["rec_id"], shown["status"], shown["uncertain"]) == (x, "verified", [])


# --------------------------------------------------------------------------- 08d-02


def test_same_day_suffix(tmp_path):
    """08d-02 (S2): three labs of one day (fx-u1-3labs, ingest): ``…-lab.md``, ``-2``, ``-3`` in page order
    (record_idx 0 on each page); only the auto-verified p003 record has a ``verified/`` view, under its name."""
    project = proj(tmp_path)
    b, adir = W.audited(project, W.LABS3)
    assert W.submit(project, None, tmp_path, batch_id=b)[0] == 10
    V.views_ok(project)
    r = V.recs_by_key(project, W.LABS3, adir)
    assert V.links(project) == {
        "records/2026-10-22-lab.md": V.body_link(r["p001"], 1),
        "records/2026-10-22-lab-2.md": V.body_link(r["p002"], 1),
        "records/2026-10-22-lab-3.md": V.body_link(r["p003"], 1),
        "verified/2026-10-22-lab-3.md": V.verified_link(r["p003"], 1),
    }


# --------------------------------------------------------------------------- 08d-03 (U3)


def test_first_ingest_pending_view_r4_01(tmp_path):
    """08d-03 (S2, R4-01, 04 U3): fx-u3-mixed after the first stage (ingest): the p002 record has no effective
    revision; its ``records/`` view links to its newest candidate and reads ``status: pending_confirm``,
    ``uncertain: []``; ``verified/`` holds only the auto-verified p001 record."""
    project = proj(tmp_path)
    b, adir = W.audited(project, U3)
    assert W.submit(project, None, tmp_path, batch_id=b)[0] == 10
    V.views_ok(project)
    r = V.recs_by_key(project, U3, adir)
    assert "effective" not in W.front(project, r["p002"])
    got = V.links(project)
    assert got["records/2026-10-22-lab-2.md"] == V.body_link(r["p002"], 1)
    shown = V.view(project, "records/2026-10-22-lab-2.md")
    assert (shown["rec_id"], shown["status"], shown["uncertain"]) == (r["p002"], "pending_confirm", [])
    assert {k: v for k, v in got.items() if k.startswith("verified/")} == {
        "verified/2026-10-22-lab.md": V.verified_link(r["p001"], 1)}
    assert V.view(project, "verified/2026-10-22-lab.md")["status"] == "verified"


# --------------------------------------------------------------------------- 08d-04


@pytest.mark.parametrize("state", ["u8-ingest", "u8-picked", "labs3-ingest"])
def test_view_rebuild_idempotent(tmp_path, state):
    """08d-04 (S5): rebuilding twice gives the same links (names and targets) and the second rebuild rewrites
    nothing (same link inodes); moving every view link away and rebuilding gives the same set again; record
    files are never changed."""
    project = proj(tmp_path)
    fx = W.LABS3 if state.startswith("labs3") else W.U8
    b, adir = W.audited(project, fx)
    if state == "u8-picked":
        assert W.submit(project, W.export(project, b, adir, W.picks(adir)), tmp_path)[0] == 0
    else:
        assert W.submit(project, None, tmp_path, batch_id=b)[0] == 10
    before = frozen(project)
    assert V.views_inproc(project) == 0
    first = V.links(project)
    assert first
    inodes = {k: os.lstat(project / k).st_ino for k in first}
    assert V.views_inproc(project) == 0
    assert V.links(project) == first
    assert {k: os.lstat(project / k).st_ino for k in first} == inodes
    moved = tmp_path / "moved"
    moved.mkdir()
    for i, k in enumerate(first):
        os.rename(project / k, moved / f"{i}.lnk")
    assert V.links(project) == {}
    V.views_ok(project)
    assert V.links(project) == first
    assert frozen(project) == before


# --------------------------------------------------------------------------- 08d-05 (U8-03 view half)


def test_disputed_not_in_verified(tmp_path):
    """08d-05 (S3, 04 U8, R-14): fx-u8 with "none of them" (no typed value) on p002-l07: that record is disputed,
    has no ``verified/`` view; its ``records/`` view reads ``status: disputed`` and ``uncertain: [p002-l07]``."""
    project = proj(tmp_path)
    b, adir = W.audited(project, W.U8)
    r = V.recs_by_key(project, W.U8, adir)
    todo = W.to_confirm(adir)
    tie = next(f for f in todo[r["p002"]] if f.split("/")[1].startswith("r007-"))
    sel = [s if s["field_id"] != tie else {"field_id": tie, "kind": "none"} for s in W.picks(adir)]
    assert W.submit(project, W.export(project, b, adir, sel), tmp_path)[0] == 10
    assert W.front(project, r["p002"])["status"] == "disputed"
    V.views_ok(project)
    got = V.links(project)
    assert {k: v for k, v in got.items() if k.startswith("verified/")} == {
        "verified/2026-10-22-lab.md": V.verified_link(r["p001"], 1)}
    assert got["records/2026-10-22-lab-2.md"] == V.body_link(r["p002"], 1)
    shown = V.view(project, "records/2026-10-22-lab-2.md")
    assert (shown["status"], shown["uncertain"]) == ("disputed", ["p002-l07"])


# --------------------------------------------------------------------------- 08d-07


def _labs3_by_order(tmp_path: Path, order: tuple[str, str]) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    project = proj(tmp_path)
    b, adir = W.audited(project, W.LABS3)
    r = V.recs_by_key(project, W.LABS3, adir)
    for key in order:  # one writeback per record, in the given order; then the ingest of p003
        choices = W.export(project, b, adir, W.picks(adir, [r[key]]))
        assert W.submit(project, choices, tmp_path)[0] in (0, 10)
    assert W.submit(project, None, tmp_path, batch_id=b)[0] == 0
    return project


def test_view_rebuild_commit_order_independent(tmp_path):
    """08d-07 (S6), commit order: records committed p001 then p002, or p002 then p001, give the same links."""
    a = _labs3_by_order(tmp_path / "ab", ("p001", "p002"))
    b = _labs3_by_order(tmp_path / "ba", ("p002", "p001"))
    V.views_ok(a)
    V.views_ok(b)
    assert V.links(a) == V.links(b)
    assert len(V.links(a)) == 6


@pytest.fixture(scope="module")
def labs3_reference(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("labs3ref")
    project = _labs3_by_order(tmp, ("p001", "p002"))
    V.views_ok(project)
    files = [p for p in (project / "records").rglob("*.md") if not p.is_symlink()]
    files += list((project / "work").glob("*/receipt.json"))
    return project, sorted(p.relative_to(project).as_posix() for p in files), V.links(project)


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(st.randoms(use_true_random=False))
def test_view_rebuild_order_independent(labs3_reference, tmp_path, rnd):
    """08d-07 (S6), write order: the record files copied into a fresh project in any order rebuild to the same
    links (names and targets)."""
    src, files, want = labs3_reference
    order = list(files)
    rnd.shuffle(order)
    with tempfile.TemporaryDirectory(dir=tmp_path) as d:
        dst = Path(d)
        for rel in order:
            (dst / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src / rel, dst / rel)
        assert V.views_inproc(dst) == 0
        assert V.links(dst) == want


# --------------------------------------------------------------------------- 08d-08


def test_concurrent_view_rebuild(tmp_path):
    """08d-08 (S3): several ``views`` processes at once (three rounds, a stale link seeded first) end with the
    links of one rebuild of a copy; no link dangles; no temporary entry is left in the view directories."""
    project = proj(tmp_path)
    b, adir = W.audited(project, W.LABS3)
    assert W.submit(project, None, tmp_path, batch_id=b)[0] == 10
    ref = tmp_path / "ref"
    shutil.copytree(project, ref, symlinks=True)
    V.views_ok(ref)
    want = V.links(ref)
    r = V.recs_by_key(project, W.LABS3, adir)
    for _ in range(3):
        (project / "records" / "2026-01-01-lab.md").symlink_to(V.body_link(r["p001"], 1))
        argv = [sys.executable, "-m", "klode_kit", "views", "--project", str(project)]
        procs = [subprocess.Popen(argv, env=base_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                 for _ in range(4)]
        for p in procs:
            _, err = p.communicate(timeout=120)
            assert p.returncode == 0, err.decode()
        assert V.links(project) == want
        assert V.dangling(project) == []
        leftovers = [p.name for d in ("records", "verified") for p in (project / d).iterdir()
                     if p.name.endswith(".tmp")]
        assert leftovers == []


# --------------------------------------------------------------------------- guards and edges


def test_stale_view_links_removed_foreign_kept(tmp_path):
    """A view link no record names any more is removed; a symbolic link that is not a view (it does not point at a
    revision body) and a regular file are left alone."""
    project, b, adir = u8_all_picked(tmp_path)
    V.views_ok(project)
    want = V.links(project)
    x = V.recs_by_key(project, W.U8, adir)["p001"]
    (project / "records" / "2026-01-01-lab.md").symlink_to(V.body_link(x, 1))
    (project / "verified" / "2026-01-01-lab.md").symlink_to(V.verified_link(x, 1))
    (project / "records" / "notes.md").symlink_to("../elsewhere.txt")
    (project / "records" / "README.md").write_text("mine\n", encoding="utf-8")
    V.views_ok(project)
    assert V.links(project) == {**want, "records/notes.md": "../elsewhere.txt"}
    assert (project / "records" / "README.md").read_text(encoding="utf-8") == "mine\n"


def test_regular_file_at_view_name_refused(tmp_path):
    """A regular file where a view link goes is never replaced: exit 30 naming it, no link written."""
    project, b, adir = u8_all_picked(tmp_path)
    mine = project / "records" / "2026-10-22-lab.md"
    mine.write_text("mine\n", encoding="utf-8")
    res = V.views_cli(project)
    assert res.returncode == 30
    assert "2026-10-22-lab.md" in res.stderr.decode()
    assert mine.read_text(encoding="utf-8") == "mine\n"
    assert V.links(project) == {}


def test_undated_record_view(tmp_path):
    """No receipt, so neither type nor date is known (WI-08c defaults ``doc`` / null): the view is
    ``undated-doc.md``."""
    project = proj(tmp_path)
    b, adir = W.audited(project, W.U8)
    receipt = project / "work" / b / "receipt.json"
    os.rename(receipt, receipt.with_name("receipt.moved.json"))
    assert W.submit(project, None, tmp_path, batch_id=b)[0] == 10
    V.views_ok(project)
    assert sorted(V.links(project)) == ["records/undated-doc-2.md", "records/undated-doc.md"]


def test_views_without_records(tmp_path):
    """A project without records: exit 0 and nothing created."""
    project = proj(tmp_path)
    V.views_ok(project)
    assert sorted(p.name for p in project.iterdir()) == []


def test_views_usage(tmp_path):
    res = V.views_cli(tmp_path, "--bogus")
    assert res.returncode == 2
    res = V.views_cli(tmp_path / "missing")
    assert res.returncode == 2


# --------------------------------------------------------------------------- type / date source (WI-08c remainder 1)


@pytest.mark.parametrize(("fx", "want"), [(W.REDO, ("lab", "2026-10-21")), (MANUALFIX, ("rx", "2026-09-18"))])
def test_type_and_date_derived_from_page(tmp_path, fx, want):
    """A kb-ocr receipt without ``record_type`` / ``date`` (as kb-ocr writes it): the writeback takes them from
    the page (title keyword, labelled date); the view is named after them."""
    project = proj(tmp_path)
    b, adir = C.audited(project, fx)
    W.submit(project, None, tmp_path, batch_id=b)
    (cand,) = W.candidates(adir)
    front = W.front(project, cand["rec_id"])
    assert (front["type"], front["date"]) == want
    assert (W.body(project, cand["rec_id"], 1)["type"], W.body(project, cand["rec_id"], 1)["date"]) == want
    V.views_ok(project)
    assert V.links(project)[f"records/{want[1]}-{want[0]}.md"] == V.body_link(cand["rec_id"], 1)


def test_known_type_and_date_kept_on_redo(tmp_path):
    """Once a record has a type and a date, a later audit revision whose page reads another date keeps them, so a
    redo never renames the record's views."""
    project = proj(tmp_path)
    b, a1 = C.audited(project, W.REDO)
    W.submit(project, None, tmp_path, batch_id=b)
    _, bdir = C.make_batch(project, W.REDO, engine="engine_t3", seq=2)
    for f in bdir.glob("ocr/*/*.t2/*.jsonl"):
        f.write_text(f.read_text(encoding="utf-8").replace("2026-10-21", "2026-10-25"), encoding="utf-8")
    C.audit(project, b)
    W.submit(project, None, tmp_path, batch_id=b)
    (cand,) = W.candidates(a1)
    front = W.front(project, cand["rec_id"])
    assert [c["rev"] for c in front["candidates"]] == [1, 2]
    assert (front["type"], front["date"]) == ("lab", "2026-10-21")
