"""WI-11a: ``kb-publish --prune`` and the reader lease beside the builds (``kb/<id>/.readers/<build_id>/<pid>-<nonce>``;
impl-deviations "WI-09d -> 11b/31"; design 02 r4.2 "读者租约" / "prune 排除集"). The recorded WI-09d rows
(``test_publish_prune.py``) keep the old place and pass unchanged: prune reads both.

Laws: a build with a live lease in either place is never moved; leases of dead pids are removed from either place and a
lease directory left empty goes with them; the number of builds is conserved.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
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


def prune(kb: Path, keep: int = 0) -> dict:
    code, body = P.prune(kb, keep)
    assert code == 0, body
    return body


def published(tmp_path, n=4, current_to=3):
    kb, ids = F.fake_kb(tmp_path, n, current_to=current_to)
    F.write_rows(kb, F.published_rows(1, ids[current_to]))
    return kb, ids


def test_a_lease_beside_the_builds_protects_its_build(tmp_path):
    kb, ids = published(tmp_path)
    kb_lease(kb, ids[1], os.getpid())
    body = prune(kb)
    assert body["protected"][ids[1]] == "reader" and body["moved"] == [ids[0], ids[2]]
    assert F.live_builds(kb) == [ids[1], ids[3]] and F.trash(kb) == [ids[0], ids[2]]
    assert P.reader_lease_dir(kb, ids[1]).is_dir()


def test_a_dead_lease_beside_the_builds_is_cleaned_and_does_not_protect(tmp_path):
    kb, ids = published(tmp_path)
    dead = kb_lease(kb, ids[1], DEAD, "x")
    on_current = kb_lease(kb, ids[3], DEAD, "y")
    alive = kb_lease(kb, ids[2], os.getpid(), "z")
    body = prune(kb)
    assert not dead.exists() and not on_current.exists() and alive.exists()
    assert not P.reader_lease_dir(kb, ids[1]).exists() and not P.reader_lease_dir(kb, ids[3]).exists()  # empty dirs go
    assert F.live_builds(kb) == [ids[2], ids[3]] and F.trash(kb) == [ids[0], ids[1]]
    assert body["cleaned_leases"] == 2


def test_a_dead_lease_of_a_build_that_no_longer_exists_is_cleaned(tmp_path):
    """A wrapper killed while serving a build that was moved away since leaves a lease for an id not in ``builds/``."""
    kb, ids = published(tmp_path)
    gone = F.build_id(9)
    stale = kb_lease(kb, gone, DEAD)
    live = kb_lease(kb, F.build_id(8), os.getpid())
    body = prune(kb)
    assert not stale.exists() and not P.reader_lease_dir(kb, gone).exists()
    assert live.exists() and body["cleaned_leases"] == 1


def test_a_lease_directory_with_a_live_lease_survives_cleaning(tmp_path):
    kb, ids = published(tmp_path)
    live = kb_lease(kb, ids[1], os.getpid(), "live")
    dead = kb_lease(kb, ids[1], DEAD, "dead")
    prune(kb)
    assert live.exists() and not dead.exists() and P.reader_lease_dir(kb, ids[1]).is_dir()


def test_both_places_at_once(tmp_path):
    kb, ids = published(tmp_path)
    F.lease(kb / "builds" / ids[0], os.getpid(), "old")
    kb_lease(kb, ids[1], os.getpid(), "new")
    kb_lease(kb, ids[2], DEAD, "dead-new")
    F.lease(kb / "builds" / ids[2], DEAD, "dead-old")
    body = prune(kb)
    assert F.live_builds(kb) == [ids[0], ids[1], ids[3]] and F.trash(kb) == [ids[2]]
    assert body["cleaned_leases"] == 2 and body["moved"] == [ids[2]]


@settings(max_examples=60, deadline=None)
@given(kinds=st.lists(st.sampled_from(["none", "new_live", "new_dead", "old_live", "old_dead", "both_live", "new_dead_old_live"]),
                      min_size=1, max_size=6), keep=st.integers(0, 3))
def test_prune_moves_exactly_the_unprotected(kinds, keep, tmp_path_factory):
    """Law (S4/S5): with no current and no log, the builds left in ``builds/`` are the newest ``keep`` and those with a live
    lease in either place; nothing is lost; no lease of a dead pid is left anywhere."""
    tmp = tmp_path_factory.mktemp("places")
    kb, ids = F.fake_kb(tmp, len(kinds))
    held, dead_files = set(), []
    for bid, kind in zip(ids, kinds):
        if kind in ("new_live", "both_live"):
            kb_lease(kb, bid, os.getpid()); held.add(bid)
        if kind in ("new_dead", "new_dead_old_live"):
            dead_files.append(kb_lease(kb, bid, DEAD))
        if kind in ("old_live", "both_live", "new_dead_old_live"):
            F.lease(kb / "builds" / bid, os.getpid(), "o"); held.add(bid)
        if kind == "old_dead":
            dead_files.append(F.lease(kb / "builds" / bid, DEAD, "od"))
    recent = set(ids[max(0, len(ids) - keep):]) if keep else set()
    body = prune(kb, keep)
    assert set(F.live_builds(kb)) == held | recent
    assert set(F.trash(kb)) == set(ids) - held - recent
    assert len(F.live_builds(kb)) + len(F.trash(kb)) == len(ids)
    assert not any(p.exists() for p in dead_files) and body["cleaned_leases"] == len(dead_files)


def test_a_reader_that_appears_while_a_build_is_moved_gets_it_back(tmp_path, monkeypatch):
    """The window between "no live lease" and the move, for a lease beside the builds: if one appears, the build is moved
    back and no ``prune`` row is written."""
    kb, ids = published(tmp_path, n=2, current_to=1)
    real_move = P.move_to_trash

    def move_then_lease(kb_, bid):
        dest = real_move(kb_, bid)
        kb_lease(kb_, bid, os.getpid(), "late")  # the wrapper wrote its lease just after prune's check
        return dest

    monkeypatch.setattr(P, "move_to_trash", move_then_lease)
    body = prune(kb)
    assert F.live_builds(kb) == [ids[0], ids[1]] and F.trash(kb) == []
    assert body["moved"] == [] and body["returned"] == [ids[0]]
    assert not [r for r in F.rows(kb) if r["action"] == "prune"]


READER = """
import os, sys
kb, build = sys.argv[1], sys.argv[2]
d = os.path.join(kb, ".readers", build)
os.makedirs(d, exist_ok=True)
with open(os.path.join(d, f"{os.getpid()}-r"), "w") as fh:
    fh.write("now\\n")
print("READY", flush=True)
sys.stdin.read()  # lives until the test closes its stdin
"""


def test_lease_vs_prune_race_beside_the_builds(tmp_path):
    """A reader process holds a lease beside the builds while prune runs over and over in other processes - the build is
    never moved; once the reader has gone, the next prune clears the dead lease (and its directory) and moves it."""
    kb, ids = published(tmp_path)
    reader = subprocess.Popen([sys.executable, "-c", READER, str(kb), ids[1]], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, text=True)
    try:
        assert reader.stdout.readline().strip() == "READY"
        for _ in range(8):
            proc = F.cli(kb, tmp_path, tmp_path, "--prune", "--keep", "0")
            assert proc.returncode == 0, proc.stderr.decode()
            assert (kb / "builds" / ids[1]).is_dir() and ids[1] not in F.trash(kb)
            assert os.listdir(P.reader_lease_dir(kb, ids[1]))
        assert F.live_builds(kb) == [ids[1], ids[3]] and F.trash(kb) == [ids[0], ids[2]]
    finally:
        reader.stdin.close()
        reader.wait(timeout=30)
    time.sleep(0.1)
    assert F.cli(kb, tmp_path, tmp_path, "--prune", "--keep", "0").returncode == 0
    assert F.live_builds(kb) == [ids[3]] and ids[1] in F.trash(kb)
    assert not P.reader_lease_dir(kb, ids[1]).exists()
