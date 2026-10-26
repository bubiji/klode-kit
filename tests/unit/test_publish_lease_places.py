"""WI-11a: where a reader lease lives (impl-deviations "WI-09d -> 11b/31": ``.readers/`` cannot be created by a running
wrapper inside a sealed build). Unit lane: the pure side of ``klode_kit.publish`` - the place, and reading both places.

Design 02 put the lease at ``builds/<id>/.readers/``; a published build is read-only all the way down (WI-09d, K5), so a
running wrapper has no right to create anything there. The lease now lives at ``kb/<id>/.readers/<build_id>/<pid>-<nonce>``
(outside the sealed tree and outside ``MANIFEST.sha256``); the old place is still read, so nothing written there is lost.

Law (S4/S5): ``build_leases(kb, id)`` is exactly the union of the two places, each lease alive exactly when its pid is alive
(a name without a pid counts as alive), and it never depends on another build's leases.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from _support import publish_fx as F
from klode_kit import publish as P

pytestmark = pytest.mark.unit

DEAD = F.dead_pid()


def kb_lease(kb: Path, build_id: str, pid: int, nonce: str = "n") -> Path:
    d = P.reader_lease_dir(kb, build_id)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{pid}-{nonce}"
    path.write_text("2026-10-25T00:00:00Z\n", encoding="utf-8")
    return path


def test_the_place_is_beside_the_builds_not_inside_one(tmp_path):
    kb, ids = F.fake_kb(tmp_path, 2)
    assert P.reader_lease_dir(kb, ids[1]) == kb / ".readers" / ids[1]
    assert P.reader_lease_dir(str(kb), ids[1]) == kb / ".readers" / ids[1]  # a plain path works as well


def test_a_sealed_build_cannot_hold_a_lease_but_the_new_place_can(tmp_path):
    """The reason for the move: with the tree sealed (no write bit, the top directory included) creating ``.readers`` in it
    is refused; the place beside the builds is writable."""
    kb, ids = F.fake_kb(tmp_path, 1)
    build = kb / "builds" / ids[0]
    (build / "library.toml").write_text("x", encoding="utf-8")
    os.chmod(build / "library.toml", 0o444)
    os.chmod(build, 0o555)
    try:
        with pytest.raises(PermissionError):
            (build / ".readers").mkdir()
        lease = kb_lease(kb, ids[0], os.getpid())
        assert lease.is_file() and not (build / ".readers").exists()
        assert [l.path for l in P.build_leases(kb, ids[0])] == [lease]
        assert not [m for m in (build.stat().st_mode,) if m & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)]  # still sealed
    finally:
        os.chmod(build, 0o755)


def test_both_places_are_read(tmp_path):
    kb, ids = F.fake_kb(tmp_path, 2)
    new_alive = kb_lease(kb, ids[0], os.getpid(), "a")
    new_dead = kb_lease(kb, ids[0], DEAD, "b")
    odd = P.reader_lease_dir(kb, ids[0]) / "no-pid-here"
    odd.write_text("", encoding="utf-8")
    old_alive = F.lease(kb / "builds" / ids[0], os.getpid(), "c")
    other = kb_lease(kb, ids[1], os.getpid(), "z")
    got = {l.path.name: (l.alive, l.pid) for l in P.build_leases(kb, ids[0])}
    assert got == {new_alive.name: (True, os.getpid()), new_dead.name: (False, DEAD), odd.name: (True, None),
                   old_alive.name: (True, os.getpid())}
    assert [l.path for l in P.build_leases(kb, ids[1])] == [other]
    assert P.build_leases(kb, "b-20990101T000000Z-00000000") == []


def test_a_moved_build_is_looked_at_where_it_is_now(tmp_path):
    """``build_dir`` names the build's present place (prune looks again right after the move into ``builds/_trash``)."""
    kb, ids = F.fake_kb(tmp_path, 1)
    moved = kb / "builds" / "_trash" / ids[0]
    moved.mkdir(parents=True)
    old = F.lease(moved, os.getpid(), "late")
    new = kb_lease(kb, ids[0], DEAD, "k")
    assert {l.path for l in P.build_leases(kb, ids[0], moved)} == {old, new}
    assert {l.path for l in P.build_leases(kb, ids[0])} == {new}  # not under builds/ any more


@settings(max_examples=80, deadline=None)
@given(kinds=st.lists(st.sampled_from(["none", "new_live", "new_dead", "old_live", "old_dead", "both"]), min_size=1, max_size=5))
def test_leases_of_a_build_are_exactly_its_own(kinds, tmp_path_factory):
    """Law (S4): the leases ``build_leases`` returns for each build are the ones written for it, in either place."""
    tmp = tmp_path_factory.mktemp("leases")
    kb, ids = F.fake_kb(tmp, len(kinds))
    expected = {}
    for bid, kind in zip(ids, kinds):
        mine = set()
        if kind in ("new_live", "both"):
            mine.add(kb_lease(kb, bid, os.getpid(), "nl"))
        if kind == "new_dead":
            mine.add(kb_lease(kb, bid, DEAD, "nd"))
        if kind in ("old_live", "both"):
            mine.add(F.lease(kb / "builds" / bid, os.getpid(), "ol"))
        if kind == "old_dead":
            mine.add(F.lease(kb / "builds" / bid, DEAD, "od"))
        expected[bid] = mine
    for bid in ids:
        got = P.build_leases(kb, bid)
        assert {l.path for l in got} == expected[bid]
        assert all(l.alive == (l.pid == os.getpid()) for l in got)
