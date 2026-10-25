"""WI-09d: ``kb-publish --prune --keep N`` on the file system (matrix 09d-08, 09, 10, 11, 14; design 02 r3.2 "旧版本",
r4.2 "读者租约" / "prune 排除集").

Laws: prune never moves a protected build (current, its rollback target, a build with a pending action, the newest N, a
build with a live reader); it only *moves* - what leaves ``builds/`` arrives in ``builds/_trash/`` with its bytes -
so the number of builds is conserved; leases of dead processes are cleaned and never block a move. The builds here are
bare directories (prune does not look inside them) except where the read-only seal matters.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from _support import publish_fx as F
from klode_kit import publish as P

pytestmark = pytest.mark.contract_replay

DEAD = F.dead_pid()


def populate(kb: Path, ids, rows, current, readers, dead_leases=()):
    for i, bid in enumerate(ids):
        (kb / "builds" / bid / "marker.txt").write_text(f"content of {bid}\n", encoding="utf-8")
    F.write_rows(kb, rows)
    if current is not None:
        os.symlink(f"builds/{current}", kb / "current")
    for bid in readers:
        F.lease(kb / "builds" / bid, os.getpid(), "r")
    for bid in dead_leases:
        F.lease(kb / "builds" / bid, DEAD, "d")


def prune(kb: Path, keep: int):
    code, body = P.prune(kb, keep)
    assert code == 0, body
    return body


@settings(max_examples=120, deadline=None)
@given(world=F.worlds(), cur=st.integers(-1, 6), readers=st.sets(st.integers(0, 6)), dead=st.sets(st.integers(0, 6)),
       keep=st.integers(0, 8))
def test_prune_never_moves_protected(world, cur, readers, dead, keep):
    """09d-08 (L2, S4): whatever the log, the leases and N, the five protected kinds of build are still in ``builds/``
    after a prune and every other build is in ``builds/_trash/``."""
    ids, rows, pending, last_prev = world
    current = ids[cur] if 0 <= cur < len(ids) else None
    reader_ids = {ids[i] for i in readers if i < len(ids)}
    dead_ids = {ids[i] for i in dead if i < len(ids)}
    expected = set(reader_ids) | set(ids[max(0, len(ids) - keep):] if keep else []) | pending
    if current is not None:
        expected.add(current)
        if last_prev.get(current) in ids:
            expected.add(last_prev[current])
    with tempfile.TemporaryDirectory() as tmp:
        kb, _ = F.fake_kb(Path(tmp), len(ids))
        populate(kb, ids, rows, current, reader_ids, dead_ids)
        log_before = F.log_bytes(kb)
        prune(kb, keep)
        assert set(F.live_builds(kb)) == expected
        assert set(F.trash(kb)) == set(ids) - expected
        assert F.current(kb) == current
        assert F.log_bytes(kb).startswith(log_before)  # the log only grew


@settings(max_examples=80, deadline=None)
@given(world=F.worlds(), cur=st.integers(-1, 6), keep=st.integers(0, 8))
def test_prune_conserves_builds(world, cur, keep):
    """09d-09 (L2, S5): ``|builds| + |_trash|`` is the same before and after, every build keeps its bytes, and one
    ``prune`` row is written per build that moved (none for the ones that stayed)."""
    ids, rows, _pending, _prev = world
    current = ids[cur] if 0 <= cur < len(ids) else None
    with tempfile.TemporaryDirectory() as tmp:
        kb, _ = F.fake_kb(Path(tmp), len(ids))
        populate(kb, ids, rows, current, ())
        before = len(F.live_builds(kb)) + len(F.trash(kb))
        n_rows = len(P.read_log(kb))
        body = prune(kb, keep)
        assert len(F.live_builds(kb)) + len(F.trash(kb)) == before == len(ids)
        for bid in F.trash(kb):
            assert (kb / "builds" / "_trash" / bid / "marker.txt").read_text(encoding="utf-8") == f"content of {bid}\n"
        new = P.read_log(kb)[n_rows:]
        assert sorted(r["target_build_id"] for r in new) == sorted(F.trash(kb))
        assert {r["action"] for r in new} <= {"prune"} and {r["status"] for r in new} <= {"done"}
        assert sorted(body["moved"]) == sorted(F.trash(kb))
        again = prune(kb, keep)  # a second prune finds nothing more to move
        assert again["moved"] == [] and len(F.live_builds(kb)) + len(F.trash(kb)) == before


def test_prune_cleans_dead_leases(tmp_path):
    """09d-10 (L2, S3): a build whose only lease belongs to a dead pid loses the lease and can be moved; a dead lease
    on a protected build is cleaned, too; a live one stays."""
    kb, ids = F.fake_kb(tmp_path, 4, current_to=0)
    F.write_rows(kb, F.published_rows(1, ids[0]))
    dead_only = F.lease(kb / "builds" / ids[1], DEAD, "x")
    on_current = F.lease(kb / "builds" / ids[0], DEAD, "y")
    alive = F.lease(kb / "builds" / ids[2], os.getpid(), "z")
    body = prune(kb, 0)
    assert not dead_only.exists() and not on_current.exists()
    assert alive.exists()
    assert F.live_builds(kb) == [ids[0], ids[2]]
    assert F.trash(kb) == [ids[1], ids[3]]
    assert not list((kb / "builds" / "_trash" / ids[1] / ".readers").glob("*"))  # the lease did not travel
    assert body["cleaned_leases"] == 2 and body["protected"][ids[2]] == "reader"


def test_prune_keep_boundary_current_stays(tmp_path):
    """09d-14 (L2, S2): N = 0 keeps only what is protected for another reason, N = 1 additionally the newest; the
    current build is kept either way, even when it is the oldest."""
    kb, ids = F.fake_kb(tmp_path / "zero", 4, current_to=0)
    F.write_rows(kb, F.published_rows(1, ids[0]))
    prune(kb, 0)
    assert F.live_builds(kb) == [ids[0]]
    kb, ids = F.fake_kb(tmp_path / "one", 4, current_to=0)
    F.write_rows(kb, F.published_rows(1, ids[0]))
    prune(kb, 1)
    assert F.live_builds(kb) == [ids[0], ids[3]]
    kb, ids = F.fake_kb(tmp_path / "nocurrent", 3)  # nothing published: N = 0 moves everything
    body = prune(kb, 0)
    assert F.live_builds(kb) == [] and sorted(body["moved"]) == ids


def test_prune_moves_a_sealed_build_and_keeps_its_seal(tmp_path):
    kb, ids = F.fake_kb(tmp_path, 2, current_to=1)
    F.write_rows(kb, F.published_rows(1, ids[1]))
    victim = kb / "builds" / ids[0]
    (victim / "library").mkdir()
    (victim / "library" / "card.txt").write_text("x\n", encoding="utf-8")
    from klode_kit.build import run_build as RB

    RB.write_manifest(victim)
    RB.make_readonly(victim)
    prune(kb, 0)
    moved = kb / "builds" / "_trash" / ids[0]
    assert moved.is_dir() and RB.verify_manifest(moved) == []
    assert not os.stat(moved).st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)  # still read-only


def test_prune_gives_a_name_to_a_second_build_of_the_same_id(tmp_path):
    kb, ids = F.fake_kb(tmp_path, 2, current_to=1)
    F.write_rows(kb, F.published_rows(1, ids[1]))
    (kb / "builds" / "_trash" / ids[0]).mkdir(parents=True)  # an earlier prune of the same id (restored since)
    prune(kb, 0)
    assert F.trash(kb) == [ids[0], f"{ids[0]}.1"]


def test_prune_ignores_what_is_not_a_build(tmp_path):
    kb, ids = F.fake_kb(tmp_path, 2, current_to=1)
    F.write_rows(kb, F.published_rows(1, ids[1]))
    (kb / "builds" / "notes.txt").write_text("hi", encoding="utf-8")
    (kb / "builds" / "scratch").mkdir()
    prune(kb, 0)
    assert (kb / "builds" / "notes.txt").is_file() and (kb / "builds" / "scratch").is_dir()


def test_prune_cli_reports_and_logs(tmp_path):
    kb, ids = F.fake_kb(tmp_path, 3, current_to=2)
    F.write_rows(kb, F.published_rows(1, ids[2]))
    proc = F.cli(kb, tmp_path, tmp_path, "--prune", "--keep", "0")
    assert proc.returncode == 0, proc.stderr.decode()
    body = F.summary(proc)
    assert (body["reason"], sorted(body["moved"])) == ("ok", ids[:2])
    pruned = [r for r in F.rows(kb) if r["action"] == "prune"]
    assert sorted(r["target_build_id"] for r in pruned) == ids[:2]
    assert F.current(kb) == ids[2]


# --------------------------------------------------------------------------- 09d-11: a reader in another process

READER = """
import os, sys, time
build = sys.argv[1]
os.makedirs(os.path.join(build, ".readers"), exist_ok=True)
with open(os.path.join(build, ".readers", f"{os.getpid()}-r"), "w") as fh:
    fh.write("now\\n")
print("READY", flush=True)
sys.stdin.read()  # lives until the test closes its stdin
"""


def test_lease_vs_prune_race(tmp_path):
    """09d-11 (L2, S5): a reader process holds a lease on a build while prune runs over and over in other processes -
    the build is never moved; once the reader has gone, the next prune clears the dead lease and moves the build."""
    kb, ids = F.fake_kb(tmp_path, 4, current_to=3)
    F.write_rows(kb, F.published_rows(1, ids[3]))
    held = kb / "builds" / ids[1]
    reader = subprocess.Popen([sys.executable, "-c", READER, str(held)], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, text=True)
    try:
        assert reader.stdout.readline().strip() == "READY"
        for _ in range(8):
            proc = F.cli(kb, tmp_path, tmp_path, "--prune", "--keep", "0")
            assert proc.returncode == 0, proc.stderr.decode()
            assert held.is_dir() and ids[1] not in F.trash(kb)
            assert os.listdir(held / ".readers")
        assert F.live_builds(kb) == [ids[1], ids[3]] and F.trash(kb) == [ids[0], ids[2]]
    finally:
        reader.stdin.close()
        reader.wait(timeout=30)
    time.sleep(0.1)
    assert F.cli(kb, tmp_path, tmp_path, "--prune", "--keep", "0").returncode == 0
    assert F.live_builds(kb) == [ids[3]] and ids[1] in F.trash(kb)


def test_prune_gives_a_build_back_that_got_a_reader_while_it_was_moved(tmp_path, monkeypatch):
    """The window between "no live lease" and the move: if a lease appears in it, the build is moved back."""
    kb, ids = F.fake_kb(tmp_path, 2, current_to=1)
    F.write_rows(kb, F.published_rows(1, ids[1]))
    real_move = P.move_to_trash

    def move_then_lease(kb_, bid):
        dest = real_move(kb_, bid)
        F.lease(dest, os.getpid(), "late")  # the reader wrote its lease just after the check
        return dest

    monkeypatch.setattr(P, "move_to_trash", move_then_lease)
    body = prune(kb, 0)
    assert F.live_builds(kb) == [ids[0], ids[1]] and F.trash(kb) == []
    assert body["moved"] == [] and body["returned"] == [ids[0]]
    assert not [r for r in F.rows(kb) if r["action"] == "prune"]
    assert (kb / "builds" / ids[0] / ".readers").is_dir()


def test_prune_busy_exit40(tmp_path):
    kb, ids = F.fake_kb(tmp_path, 2, current_to=1)
    fd = os.open(kb, os.O_RDONLY)
    import fcntl

    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        proc = F.cli(kb, tmp_path, tmp_path, "--prune", "--keep", "0")
    finally:
        os.close(fd)
    assert proc.returncode == 40 and F.live_builds(kb) == ids
