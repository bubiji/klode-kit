"""WI-09d: the bookkeeping of the publish protocol, unit lane (matrix 09d-15 and the pure parts of 09d-08 / 09d-14 /
09d-18; design 02 r3.2 "单一生效指针", r4.2 "发布日志" / "replay 规则" / "prune 排除集").

Law (S4/S5): the protected set of a prune is built from ``current``, its rollback target, the builds with a pending
action, the newest ``keep`` builds and the builds with a live reader - and from nothing else - and a build is pending
exactly when an action of its latest publication has not ended ``done`` / ``skipped_newer``. "Newer" is the order of
``PUBLISH.log.seq``, never the order of build ids. The file side (real trees, processes, crashes) is in
``tests/contract/test_publish_*.py``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from _support import publish_fx as F
from klode_kit import publish as P
from klode_kit.contract.schema import issues

pytestmark = pytest.mark.unit

A, B, C = (F.build_id(i) for i in range(3))


# --------------------------------------------------------------------------- 09d-15: the latest status counts


def test_pending_judged_by_latest_status():
    """09d-15 (L1, S2): an action that failed first and ended ``done`` later is not pending; one that ended
    ``failed`` is; so is one that never got a row after the pointer moved."""
    rows = [F.row(1, A, "switch"), F.row(2, A, "mcp_json", "failed"), F.row(3, A, "mcp_json", "done"),
            F.row(4, A, "registry", "done"), F.row(5, A, "agent", "done"), F.row(6, A, "approve", "done")]
    assert P.pending_builds(rows) == set()
    assert P.pending_actions(rows, A) == []

    rows[2] = F.row(3, A, "mcp_json", "failed")
    assert P.pending_builds(rows) == {A}
    assert P.pending_actions(rows, A) == ["mcp_json"]

    rows = [F.row(1, A, "switch"), F.row(2, A, "mcp_json", "failed"), F.row(3, A, "mcp_json", "done"),
            F.row(4, A, "registry", "done")]  # agent and approve never got a row
    assert P.pending_actions(rows, A) == ["agent", "approve"]
    assert P.pending_builds(rows) == {A}


def test_failed_then_done_in_either_order_of_appearance_uses_seq():
    """A later row wins - rows are ordered by ``seq``, not by where they happen to stand in the list."""
    rows = [F.row(3, A, "mcp_json", "done"), F.row(2, A, "mcp_json", "failed"), F.row(1, A, "switch"),
            F.row(4, A, "registry"), F.row(5, A, "agent"), F.row(6, A, "approve")]
    assert P.pending_actions(rows, A) == []


def test_skipped_newer_is_settled_not_pending():
    rows = F.published_rows(1, A, statuses={"registry": "skipped_newer"})
    assert P.pending_builds(rows) == set()
    assert P.pending_actions(rows, A) == []


def test_a_new_publication_starts_a_new_epoch():
    """Publishing A again after B: the old ``done`` rows of A belong to the first publication; the artifacts carry
    B's marker now, so all four actions of the second publication are open until they have a row."""
    rows = F.published_rows(1, A) + F.published_rows(7, B, prev=A) + [F.row(13, A, "switch", prev=B)]
    assert P.pending_actions(rows, A) == list(P.ACTIONS)
    assert P.pending_builds(rows) == {A}
    assert P.pending_actions(rows, B) == []


def test_never_published_build_is_not_pending():
    assert P.pending_builds([]) == set()
    assert P.pending_actions([], A) == []
    assert P.pending_builds([F.row(1, A, "prune")]) == set()  # a prune row is not a publication


def test_failed_pointer_row_is_not_a_publication():
    rows = [F.row(1, A, "switch", "failed")]
    assert P.seq_of(rows, A) is None
    assert P.pending_builds(rows) == set()


# --------------------------------------------------------------------------- 09d-18: newer = seq, not build id


def test_seq_of_is_the_latest_done_pointer_row():
    rows = F.published_rows(1, A) + F.published_rows(7, B, prev=A) + F.published_rows(13, A, prev=B, action="rollback")
    assert P.seq_of(rows, A) == 13  # the rollback is A's latest publication
    assert P.seq_of(rows, B) == 7
    assert P.seq_of(rows, C) is None
    assert B > A  # B's id sorts after A's, yet A is the newer publication
    assert P.seq_of(rows, A) > P.seq_of(rows, B)


@settings(max_examples=100, deadline=None)
@given(order=st.permutations([0, 1, 2]))
def test_seq_of_ignores_the_order_of_the_ids(order):
    """Whatever order the builds were published in, the build with the larger ``seq`` is the newer."""
    ids = [F.build_id(i) for i in order]
    rows = []
    for i, bid in enumerate(ids):
        rows += F.published_rows(1 + 10 * i, bid)
    assert [P.seq_of(rows, bid) for bid in ids] == [1, 11, 21]


# --------------------------------------------------------------------------- the protected set (S4)


def test_protected_reasons_and_the_rest():
    ids = [F.build_id(i) for i in range(6)]  # 0 oldest .. 5 newest
    rows = F.published_rows(1, ids[1]) + F.published_rows(7, ids[2], prev=ids[1]) + F.published_rows(13, ids[3], prev=ids[2],
                                                                               statuses={"agent": "failed"})
    prot = P.protected_builds(rows, ids, ids[3], {ids[0]}, keep=1)
    # ids[3] is current (and pending, too: the first reason names it), ids[2] its rollback target, ids[0] has a
    # reader, ids[5] is the newest; ids[1] and ids[4] are free to go
    assert prot == {ids[3]: "current", ids[2]: "rollback_target", ids[0]: "reader", ids[5]: "recent"}


def test_protected_pending_build_that_is_not_current():
    ids = [F.build_id(i) for i in range(4)]
    rows = F.published_rows(1, ids[0], statuses={"registry": "failed"}) + F.published_rows(7, ids[1], prev=ids[0])
    prot = P.protected_builds(rows, ids, ids[1], set(), keep=0)
    assert prot == {ids[1]: "current", ids[0]: "rollback_target"}  # ids[0] is both; rollback target is named first
    rows = F.published_rows(1, ids[0], statuses={"registry": "failed"}) + F.published_rows(7, ids[1], prev=None)
    prot = P.protected_builds(rows, ids, ids[1], set(), keep=0)
    assert prot == {ids[1]: "current", ids[0]: "pending"}


def test_protected_without_current_and_with_a_missing_rollback_target():
    ids = [F.build_id(i) for i in range(3)]
    assert P.protected_builds([], ids, None, set(), keep=0) == {}
    gone = F.build_id(9)
    rows = F.published_rows(1, ids[1], prev=gone)
    assert P.protected_builds(rows, ids, ids[1], set(), keep=0) == {ids[1]: "current"}  # the target is not a build


def test_keep_boundary_values():
    """09d-14, pure part (L1, S2): keep 0 protects no one by age, keep 1 exactly the newest; ``current`` stays
    protected in both; more than there are builds protects all."""
    ids = [F.build_id(i) for i in range(3)]
    rows = F.published_rows(1, ids[0])
    assert P.protected_builds(rows, ids, ids[0], set(), keep=0) == {ids[0]: "current"}
    assert P.protected_builds(rows, ids, ids[0], set(), keep=1) == {ids[0]: "current", ids[2]: "recent"}
    assert set(P.protected_builds(rows, ids, ids[0], set(), keep=99)) == set(ids)


@settings(max_examples=250, deadline=None)
@given(world=F.worlds(), cur=st.integers(-1, 6), readers=st.sets(st.integers(0, 6)), keep=st.integers(0, 8))
def test_protected_set_is_exactly_the_five_reasons(world, cur, readers, keep):
    """09d-08, pure part (L1, S4): the protected builds are the union of the five reasons of the design - nothing
    protected for another reason, none of the five left out."""
    ids, rows, pending, last_prev = world
    current = ids[cur] if 0 <= cur < len(ids) else None
    reader_ids = {ids[i] for i in readers if i < len(ids)}
    expected = set(reader_ids) | set(ids[max(0, len(ids) - keep):] if keep else [])
    if current is not None:
        expected.add(current)
        # the rollback target is the prev of the current build's latest pointer row, when that build exists
        prev = last_prev.get(current)
        if prev in ids:
            expected.add(prev)
    expected |= pending
    got = P.protected_builds(rows, ids, current, reader_ids, keep)
    assert set(got) == expected
    assert P.pending_builds(rows) == pending
    assert set(got) <= set(ids)
    assert set(got.values()) <= {"current", "rollback_target", "pending", "recent", "reader"}


# --------------------------------------------------------------------------- the pointer and the log


def test_current_build_reads_the_link(tmp_path):
    kb, ids = F.fake_kb(tmp_path, 2)
    assert P.current_build(kb) is None
    os.symlink(f"builds/{ids[1]}", kb / "current")
    assert P.current_build(kb) == ids[1]
    (kb / "current").unlink()
    os.symlink("builds/not-a-build-id", kb / "current")  # a link that is not one of ours
    assert P.current_build(kb) is None


def test_switch_pointer_replaces_atomically_and_leaves_no_temp(tmp_path):
    kb, ids = F.fake_kb(tmp_path, 2, current_to=0)
    P.switch_pointer(kb, ids[1])
    assert os.readlink(kb / "current") == f"builds/{ids[1]}"  # relative: the project can be moved
    assert (kb / "current").resolve() == (kb / "builds" / ids[1]).resolve()
    assert sorted(p.name for p in kb.iterdir()) == ["builds", "current"]


def test_switch_pointer_clears_the_stale_temp_link_of_a_crash(tmp_path):
    kb, ids = F.fake_kb(tmp_path, 2, current_to=0)
    os.symlink("builds/nowhere", kb / "current.tmp")  # what a crash between symlink and replace leaves
    P.switch_pointer(kb, ids[1])
    assert F.current(kb) == ids[1]
    assert not os.path.lexists(kb / "current.tmp")


def test_switch_pointer_refuses_a_missing_build(tmp_path):
    kb, ids = F.fake_kb(tmp_path, 1, current_to=0)
    with pytest.raises(FileNotFoundError):
        P.switch_pointer(kb, F.build_id(5))
    assert F.current(kb) == ids[0] and not os.path.lexists(kb / "current.tmp")


def test_log_is_append_only_with_increasing_seq_and_valid_rows(tmp_path):
    kb, ids = F.fake_kb(tmp_path, 2)
    assert P.read_log(kb) == []
    seen = []
    for i, (target, action, status) in enumerate([(ids[0], "switch", "done"), (ids[0], "mcp_json", "failed"),
                                                  (ids[1], "prune", "done")], start=1):
        before = F.log_bytes(kb)
        r = P.append_row(kb, target, action, status, prev=None, detail="x")
        after = F.log_bytes(kb)
        assert after.startswith(before) and len(after) > len(before)  # only ever longer, the old bytes untouched
        assert r["seq"] == i and not issues("publish_log", r)
        seen.append(r)
    assert P.read_log(kb) == seen
    assert [json.loads(x)["seq"] for x in F.log_bytes(kb).decode().splitlines()] == [1, 2, 3]
    assert all(r["by"].startswith("klode-kit") for r in seen)


def test_read_log_rejects_a_torn_or_foreign_line(tmp_path):
    kb, ids = F.fake_kb(tmp_path, 1)
    F.write_rows(kb, [F.row(1, ids[0], "switch")])
    with open(kb / "PUBLISH.log", "a", encoding="utf-8") as fh:
        fh.write('{"seq": 2, "target_build_id"')  # a half line: the writer died inside it
    with pytest.raises(P.LogError):
        P.read_log(kb)


def test_lease_pid_and_liveness(tmp_path):
    build = tmp_path / "b"
    build.mkdir()
    mine = F.lease(build, os.getpid(), "x")
    dead = F.lease(build, F.dead_pid(), "y")
    odd = build / ".readers" / "not-a-pid"
    odd.write_text("", encoding="utf-8")
    leases = {l.path.name: l.alive for l in P.leases(build)}
    assert leases == {mine.name: True, dead.name: False, odd.name: True}  # unreadable name: cannot be proven dead
    assert P.leases(tmp_path / "no-such-build") == []
