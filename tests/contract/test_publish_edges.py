"""WI-09d: edges of the publish protocol on real builds - what a replay, a rollback and a prune do with a log or a
build in an odd state (cases added while implementing; the specification files are unchanged by them)."""

from __future__ import annotations

import json
import os

import pytest

from _support import publish_fx as F
from klode_kit import publish as P

pytestmark = pytest.mark.contract_replay


@pytest.fixture()
def ab(tmp_path):
    return F.ab(tmp_path)


def run(ab, *args):
    return F.cli(ab.kb, ab.project, ab.tmp, *args)


def test_a_log_that_cannot_be_trusted_stops_every_command_with_exit30(ab):
    (ab.kb / "PUBLISH.log").write_text('{"seq": 1', encoding="utf-8")  # a torn line
    os.symlink(f"builds/{ab.a}", ab.kb / "current")  # (a replay only gets as far as the log with a pointer)
    for args in (("--build", ab.a), ("--replay", ab.a), ("--rollback-to", ab.a), ("--prune", "--keep", "0")):
        proc = run(ab, *args)
        assert proc.returncode == 30, (args, proc.stderr.decode())
        assert "torn" in proc.stderr.decode()
    assert F.current(ab.kb) == ab.a and (ab.kb / "PUBLISH.log").read_text(encoding="utf-8") == '{"seq": 1'
    assert F.live_builds(ab.kb) == [ab.a, ab.b]


def test_replay_of_a_build_that_was_never_recorded(ab):
    os.symlink(f"builds/{ab.a}", ab.kb / "current")  # the pointer was put there by hand, nothing was published
    proc = run(ab, "--replay", ab.a)
    assert proc.returncode == 30 and "publish it with --build" in proc.stderr.decode()
    assert F.rows(ab.kb) == []
    assert not (ab.project / ".mcp.json").exists()


def test_replay_verifies_the_build_like_a_publish(ab):
    assert run(ab, "--build", ab.a).returncode == 0
    os.chmod(ab.kb / "builds" / ab.a, 0o755)  # the seal was broken after the publication
    log = F.log_bytes(ab.kb)
    proc = run(ab, "--replay", ab.a)
    assert proc.returncode == 30 and "writable" in proc.stderr.decode()
    assert F.log_bytes(ab.kb) == log


def test_publish_records_a_switch_that_died_before_its_row(ab):
    """The pointer was moved, the process died before the row: the next ``--build`` of that build says so in the log
    (after the fact, with the previous build) and finishes the registrations."""
    assert run(ab, "--build", ab.a).returncode == 0
    os.symlink(f"builds/{ab.b}", ab.kb / "current.tmp")
    os.replace(ab.kb / "current.tmp", ab.kb / "current")  # what a kill right after the replace leaves
    n = len(F.rows(ab.kb))
    proc = run(ab, "--build", ab.b)
    assert proc.returncode == 0, proc.stderr.decode()
    new = F.rows(ab.kb)[n:]
    assert [(r["action"], r["prev_build_id"]) for r in new[:1]] == [("switch", ab.a)]
    assert "after the fact" in new[0]["detail"]
    assert [r["action"] for r in new[1:]] == list(P.ACTIONS)
    assert F.sound(ab.kb) == []


def test_rollback_to_the_current_build_registers_everything_again(ab):
    assert run(ab, "--build", ab.a).returncode == 0
    assert run(ab, "--build", ab.b).returncode == 0
    n = len(F.rows(ab.kb))
    proc = run(ab, "--rollback-to", ab.b)
    assert proc.returncode == 0, proc.stderr.decode()
    new = F.rows(ab.kb)[n:]
    assert [(r["action"], r["prev_build_id"]) for r in new[:1]] == [("rollback", ab.a)]  # the rollback target stays A
    assert new[0]["detail"] == "already current"
    assert [r["action"] for r in new[1:]] == list(P.ACTIONS)
    assert F.current(ab.kb) == ab.b


def test_a_lease_directory_inside_a_sealed_build_does_not_block_a_publish(ab):
    """``.readers/`` is outside the MANIFEST and the read-only seal by design (r4.2): a reader may already be there."""
    build = ab.kb / "builds" / ab.a
    os.chmod(build, 0o755)
    (build / ".readers").mkdir()
    F.lease(build, os.getpid(), "r")
    os.chmod(build, 0o555)
    proc = run(ab, "--build", ab.a)
    assert proc.returncode == 0, proc.stderr.decode()
    assert F.sound(ab.kb) == []


def test_receipt_is_written_for_every_mode(ab, tmp_path):
    out = tmp_path / "receipts"
    assert run(ab, "--build", ab.a, "--receipt", str(out / "p.json")).returncode == 0
    assert run(ab, "--replay", ab.a, "--receipt", str(out / "r.json")).returncode == 0
    assert run(ab, "--rollback-to", ab.a, "--receipt", str(out / "b.json")).returncode == 0
    assert run(ab, "--prune", "--keep", "0", "--receipt", str(out / "n.json")).returncode == 0
    modes = {p.stem: json.loads(p.read_text(encoding="utf-8"))["mode"] for p in out.iterdir()}
    assert modes == {"p": "publish", "r": "replay", "b": "rollback", "n": "prune"}


def test_the_project_is_needed_when_the_library_is_not_below_kb(ab, tmp_path):
    loose = tmp_path / "elsewhere" / "demo"
    loose.mkdir(parents=True)
    proc = F.cli(loose, None, tmp_path, "--build", ab.a)
    assert proc.returncode == 2 and "--project" in proc.stderr.decode()
    assert F.cli(loose, None, tmp_path, "--prune", "--keep", "1").returncode == 0  # prune needs no project


def test_prune_after_publishes_protects_the_rollback_target(ab):
    assert run(ab, "--build", ab.a).returncode == 0
    assert run(ab, "--build", ab.b).returncode == 0
    proc = run(ab, "--prune", "--keep", "0")
    assert proc.returncode == 0
    body = F.summary(proc)
    assert body["protected"] == {ab.b: "current", ab.a: "rollback_target"} and body["moved"] == []
    assert F.live_builds(ab.kb) == [ab.a, ab.b]
