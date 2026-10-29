"""WI-15m: ``publish`` pinned exactly (mutation-gate survivors, score 0.799 -> >= 0.85; critical path S5).

The existing publish tests (``test_publish_logic.py``, ``test_publish_edges.py``, ``test_publish_lease_places.py``,
``tests/contract/test_publish_*.py``, ``tests/cli/test_publish_real_klode.py``) check the protocol against real builds
through the CLI. They do not pin: the exact bytes of a log row and its file mode, the error texts of an untrusted log,
the exact problem texts of a refused target, the shape and every key of each summary body (publish / replay / rollback /
refuse / lock busy / prune), the ``already current`` / ``recorded after the fact`` pointer rows, what ``_run_action``
does with a marker newer or older than the build, the name of every path (a Mac volume is case-insensitive, so a
mutant spelling ``CURRENT`` / ``BUILDS`` / ``_TRASH`` is invisible to a plain run), the ``.N`` suffix of the trash, and
the argparse wiring / usage texts / output lines of ``main``. This file does (S1/S2/S3 pins plus two round trips).

How it is driven, in process, with real files:

- a *bare verified build* (``mk_build``): a small tree whose ``build_receipt.json`` is PASS, ``MANIFEST.sha256`` is the
  real manifest of the tree (``run_build.write_manifest``), ``check_receipt.json`` is a valid contract document, and the
  whole tree is read-only - exactly what ``_verify`` demands, without running the 13 steps;
- the four registrations go through the ``ACTION_IMPL`` seam: a recording stand-in (``Impl``) replaces the dict for the
  test (the real registrar has its own tests, ``test_register*.py`` and the ``tests/contract/test_publish_*.py``);
- ``casefs`` makes the process case-sensitive about the names under the test directory (``os.stat`` / ``readlink`` /
  ``rename`` / ``replace`` / ``symlink`` / ``unlink`` / ``chmod`` / ``mkdir`` / ``lexists`` and ``io.open`` refuse a
  name that only matches a different spelling), so ``CURRENT`` / ``BUILDS`` / ``BUILD_RECEIPT.JSON`` mutants cannot
  hide on a case-insensitive volume;
- ``main`` is driven in process with ``capsys``; ``publish`` / ``replay`` / ``rollback`` / ``prune`` are replaced by a
  recorder only where the test is about what ``main`` passes on (types and keywords of the arguments).

The part that needs a child process of its own (the C locale) is in ``tests/contract/test_publish_wi15m.py``.

Law (S4/S5): ``append_row`` / ``read_log`` - whatever the detail is, the row read back is the row appended, and ``seq``
is strictly increasing (``test_append_then_read_round_trips_for_any_detail``); the publish protocol - a build published
twice in a row changes nothing the second time (``test_publish_twice_is_idempotent``). The rest has no law (texts, key
names, defaults): S1-S3.

Mutants left alone as equivalent, with the reason:
``read_text(encoding="UTF-8")`` / ``.encode("UTF-8")`` are the same codecs as ``"utf-8"``; ``ensure_ascii=None`` is
falsy like ``False`` (``append_row``, ``main``); ``Path(...) if (name) or True else Path(dirpath)`` in ``_writable`` - the
root has ``name == ""`` and ``Path(dirpath, "")`` is ``Path(dirpath)``; ``as_posix() or "XX.XX"`` - a relative path is
never the empty string (``Path('.').as_posix()`` is ``"."``), so the fallback is unreachable; ``status, detail = "done",
"XXXX"`` in ``_run_action`` - both branches and the ``except`` assign ``detail`` before it is used; ``seq_of(rows, marker)
if (marker) or True else None`` - ``seq_of(rows, None)`` is ``None`` anyway; ``append_row(..., prev=None)`` without the
keyword - ``None`` is the default; ``record_missing=None`` in ``replay`` - falsy like ``False``; ``(own is not None) or
True`` in ``_complete`` - the branch is only reached with a pointer row of the build (replay checks ``seq_of`` first,
a publish whose pointer row names another build records the switch); ``type=None`` for ``--project`` / ``--receipt`` -
``_project_of`` and ``_write_receipt`` wrap the value in ``Path`` anyway; ``BUILD_RE.fullmatch(wanted or "XXXX")`` -
``"XXXX"`` and ``""`` both fail the match; ``fullmatch(wanted or "")`` for a prune never reaches the check.
"""

from __future__ import annotations

import io
import json
import os
import re
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from _support import publish_fx as F
from klode_kit import __version__
from klode_kit import publish as P
from klode_kit import receipts
from klode_kit.build import run_build as RB
from klode_kit.pipeline import EXIT_CODE, Reason

pytestmark = pytest.mark.unit

A, B, C, D = (F.build_id(i) for i in range(4))
OTHER = "b-20260101T000000Z-deadbeef"


# --------------------------------------------------------------------------- helpers: builds, worlds, seams


def check_doc(bid: str, status: str = "PASS") -> dict:
    return {"schema": "klode-kit.check_receipt/1", "build_id": bid, "kb_id": "demo", "status": status,
            "checks": [{"name": "cards", "status": status}],
            "mcp": {"mcp_config_written": True, "mcp_direct_smoke": True, "mcp_host_visible": "connected"},
            "u5_ready": False}


def mk_build(kb: Path, bid: str, *, receipt="ok", check="ok", manifest: bool = True, tamper=None,
             seal: bool = True) -> Path:
    """``kb/builds/<bid>``: a verified build (``_verify`` finds nothing) unless the keywords say otherwise.

    ``receipt`` / ``check``: ``"ok"`` (the good document), ``None`` (no file), a dict (that document), a str (raw text).
    ``tamper(build)`` runs after the manifest is written and before the tree is sealed."""
    build = kb / "builds" / bid
    (build / "data").mkdir(parents=True)
    (build / "data" / "a.txt").write_text("a\n", encoding="utf-8")

    def put(name: str, what, good) -> None:
        if what == "ok":
            what = good
        if what is None:
            return
        text = what if isinstance(what, str) else json.dumps(what)
        (build / name).write_text(text, encoding="utf-8")

    put("build_receipt.json", receipt, {"status": "PASS", "build_id": bid})
    if manifest:
        RB.write_manifest(build)
    put("check_receipt.json", check, check_doc(bid))
    if tamper is not None:
        tamper(build)
    if seal:
        RB.make_readonly(build)
    return build


def world(tmp: Path, n: int = 2, *, name: str = "demo") -> tuple[Path, Path, list[str]]:
    """``(project, kb, ids)``: ``project/kb/<name>/builds/`` with ``n`` verified builds (ids ordered by age)."""
    project = tmp / "proj"
    kb = project / "kb" / name
    ids = [F.build_id(i) for i in range(n)]
    for bid in ids:
        mk_build(kb, bid)
    return project, kb, ids


class Impl:
    """A recording stand-in for the registrar: ``ACTION_IMPL`` as a dict of ``(marker reader, applier)``.

    ``markers``: action -> the marker the registration "names" (default none); ``details``: action -> what the
    applier returns; ``fail``: action -> the exception it raises."""

    def __init__(self, *, markers=None, details=None, fail=None, no_marker=("approve",)):
        self.calls: list[tuple[str, str, object]] = []
        self.markers, self.details, self.fail = markers or {}, details or {}, fail or {}
        self.table = {}
        for name in P.ACTIONS:
            self.table[name] = (None if name in no_marker else self._marker(name), self._apply(name))

    def _marker(self, name):
        def marker(t):
            self.calls.append(("marker", name, t))
            exc = self.fail.get(("marker", name))
            if exc is not None:
                raise exc
            return self.markers.get(name)

        return marker

    def _apply(self, name):
        def apply(t):
            self.calls.append(("apply", name, t))
            exc = self.fail.get(name)
            if exc is not None:
                raise exc
            return self.details.get(name, f"{name} done")

        return apply

    def applied(self) -> list[str]:
        return [n for kind, n, _ in self.calls if kind == "apply"]

    def target(self):
        return self.calls[0][2]


@pytest.fixture
def impl(monkeypatch):
    fake = Impl()
    monkeypatch.setattr(P, "ACTION_IMPL", fake.table)
    return fake


def install(monkeypatch, fake: Impl) -> Impl:
    monkeypatch.setattr(P, "ACTION_IMPL", fake.table)
    return fake


def rows_of(kb: Path) -> list[dict]:
    return F.rows(kb)


def link_to(kb: Path, bid: str) -> None:
    link = kb / "current"
    if os.path.lexists(link):
        os.unlink(link)
    os.symlink(f"builds/{bid}", link)


ENV = {"HOME": "/nonexistent-home", "KLODE_KIT_BIN": "/launcher"}


def publish_ok(kb: Path, bid: str, **kw):
    return P.publish(kb, bid, env=kw.pop("env", ENV), **kw)


@pytest.fixture
def casefs(tmp_path, monkeypatch):
    """Case-sensitive names below ``tmp_path`` whatever the volume is; returns the (resolved) root to build in."""
    root = tmp_path.resolve()
    real_listdir, real_lstat, real_lexists = os.listdir, os.lstat, os.path.lexists

    def present(path) -> bool:  # lexists on the unpatched lstat (the patched ones call ``bad``)
        try:
            real_lstat(path)
        except OSError:
            return False
        return True

    def bad(path) -> bool:
        try:
            p = os.path.abspath(os.fspath(path))
        except TypeError:
            return False
        if p != str(root) and not p.startswith(str(root) + os.sep):
            return False
        cur = str(root)
        for part in Path(p).relative_to(root).parts:
            if present(os.path.join(cur, part)) and part not in real_listdir(cur):
                return True
            cur = os.path.join(cur, part)
        return False

    def guard(real, *positions):
        def wrapper(*args, **kwargs):
            for i in positions:
                if i < len(args) and bad(args[i]):
                    raise FileNotFoundError(2, "no such name (case)", str(args[i]))
            return real(*args, **kwargs)

        return wrapper

    for fn in ("stat", "lstat", "readlink", "unlink", "chmod", "mkdir"):
        monkeypatch.setattr(os, fn, guard(getattr(os, fn), 0))
    monkeypatch.setattr(os, "rename", guard(os.rename, 0, 1))
    monkeypatch.setattr(os, "replace", guard(os.replace, 0, 1))
    monkeypatch.setattr(os, "symlink", guard(os.symlink, 1))
    monkeypatch.setattr(io, "open", guard(io.open, 0))
    monkeypatch.setattr(os.path, "lexists", lambda p: False if bad(p) else real_lexists(p))
    return root


# --------------------------------------------------------------------------- the clock and the log: bytes and texts


def test_now_is_utc_whatever_the_local_zone(monkeypatch):
    """``at`` is UTC with a ``Z``: with the process in a zone far from UTC the stamp still equals UTC now."""
    import time as _time

    monkeypatch.setenv("TZ", "Pacific/Auckland")
    _time.tzset()
    try:
        stamp = P._now()
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", stamp)
        then = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        assert abs((datetime.now(timezone.utc) - then).total_seconds()) < 30
    finally:
        monkeypatch.undo()
        _time.tzset()


def test_append_row_writes_exactly_one_sorted_utf8_json_line(tmp_path, monkeypatch):
    kb, _ = F.fake_kb(tmp_path, 1)
    monkeypatch.setattr(P, "_now", lambda: "2026-10-30T01:02:03Z")
    row = P.append_row(kb, A, "switch", "done", prev=B, detail="caf\u00e9 \u77e5\u8bc6\u5e93")
    expected = {"seq": 1, "target_build_id": A, "prev_build_id": B, "action": "switch", "status": "done",
                "at": "2026-10-30T01:02:03Z", "by": f"klode-kit/{__version__}", "detail": "caf\u00e9 \u77e5\u8bc6\u5e93"}
    assert row == expected
    raw = (kb / "PUBLISH.log").read_bytes()
    assert raw == (json.dumps(expected, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    assert raw.startswith(b'{"action": "switch", "at": ')  # keys sorted
    assert "caf\u00e9 \u77e5\u8bc6\u5e93".encode("utf-8") in raw  # not \u-escaped
    assert b"\\u" not in raw
    assert raw.count(b"\n") == 1


def test_append_row_without_detail_has_no_detail_key(tmp_path):
    kb, _ = F.fake_kb(tmp_path, 1)
    assert "detail" not in P.append_row(kb, A, "switch", "done")
    assert "detail" not in P.append_row(kb, A, "mcp_json", "done", detail="")
    assert [r["seq"] for r in rows_of(kb)] == [1, 2]
    assert "detail" not in json.loads((kb / "PUBLISH.log").read_text(encoding="utf-8").splitlines()[1])


def test_append_row_creates_the_log_with_mode_0644(tmp_path):
    kb, _ = F.fake_kb(tmp_path, 1)
    old = os.umask(0)
    try:
        P.append_row(kb, A, "switch", "done")
    finally:
        os.umask(old)
    assert stat.S_IMODE(os.stat(kb / "PUBLISH.log").st_mode) == 0o644


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any directory")
def test_append_row_needs_no_read_access_to_the_directory(tmp_path):
    """The directory fsync after the write is best effort: a directory that can be written but not opened for
    reading (mode 0300) still takes the row."""
    kb, _ = F.fake_kb(tmp_path, 1)
    os.chmod(kb, 0o300)
    try:
        P.append_row(kb, A, "switch", "done")
        P.append_row(kb, A, "mcp_json", "done")
    finally:
        os.chmod(kb, 0o700)
    assert [r["seq"] for r in rows_of(kb)] == [1, 2]


def test_append_row_refusal_text(tmp_path):
    kb, _ = F.fake_kb(tmp_path, 1)
    with pytest.raises(ValueError) as exc:
        P.append_row(kb, A, "bogus", "done")
    assert str(exc.value).startswith("publish log row breaks its contract: action: ")
    with pytest.raises(ValueError, match="publish log row breaks its contract: target_build_id: "):
        P.append_row(kb, "not-a-build", "switch", "done")


def test_append_row_keeps_the_first_500_characters_of_a_detail(tmp_path):
    kb, _ = F.fake_kb(tmp_path, 1)
    row = P.append_row(kb, A, "switch", "done", detail="x" * 499 + "YZ")
    assert row["detail"] == "x" * 499 + "Y"
    assert rows_of(kb)[0]["detail"] == "x" * 499 + "Y"


def test_append_row_calls_the_crash_point_after_each_row(tmp_path, monkeypatch):
    kb, _ = F.fake_kb(tmp_path, 1)
    seen = []
    monkeypatch.setattr(P, "crashpoint", seen.append)
    P.append_row(kb, A, "switch", "done")
    assert seen == ["publish.logged"]
    assert len(rows_of(kb)) == 1  # the row was already on disk when the point was reached


def test_read_log_without_a_file_is_empty(tmp_path):
    kb, _ = F.fake_kb(tmp_path, 1)
    assert P.read_log(kb) == []
    assert P.read_log(str(kb)) == []


def test_read_log_error_texts_name_file_and_line(tmp_path):
    kb, _ = F.fake_kb(tmp_path, 1)
    log = kb / "PUBLISH.log"
    good = json.dumps(F.row(1, A, "switch"), sort_keys=True)

    log.write_text(good + "\n" + "not json\n", encoding="utf-8")
    with pytest.raises(P.LogError) as exc:
        P.read_log(kb)
    assert str(exc.value).startswith(f"{log}:2: not JSON: ")

    log.write_text("not json\n", encoding="utf-8")
    with pytest.raises(P.LogError, match=re.escape(f"{log}:1: not JSON: ")):
        P.read_log(kb)

    log.write_text(good, encoding="utf-8")  # no newline at the end
    with pytest.raises(P.LogError) as exc:
        P.read_log(kb)
    assert str(exc.value) == f"{log}: the last line is torn (no newline)"

    bad = F.row(2, A, "bogus")
    log.write_text(good + "\n" + json.dumps(bad) + "\n", encoding="utf-8")
    with pytest.raises(P.LogError) as exc:
        P.read_log(kb)
    assert str(exc.value).startswith(f"{log}:2: action: ")

    log.write_text(good + "\n" + json.dumps(F.row(1, A, "mcp_json")) + "\n", encoding="utf-8")
    with pytest.raises(P.LogError) as exc:
        P.read_log(kb)
    assert str(exc.value) == f"{log}:2: seq 1 does not follow 1"

    log.write_text(good + "\n" + json.dumps(F.row(3, A, "mcp_json")) + "\n" + json.dumps(F.row(2, A, "registry"))
                   + "\n", encoding="utf-8")
    with pytest.raises(P.LogError) as exc:
        P.read_log(kb)
    assert str(exc.value) == f"{log}:3: seq 2 does not follow 3"


def test_read_log_row_that_is_not_an_object_is_named_row(tmp_path):
    """A violation at the root of the row has no path: the text says ``<row>``."""
    kb, _ = F.fake_kb(tmp_path, 1)
    log = kb / "PUBLISH.log"
    log.write_text("[1, 2]\n", encoding="utf-8")
    with pytest.raises(P.LogError) as exc:
        P.read_log(kb)
    assert str(exc.value).startswith(f"{log}:1: <row>: ")
    assert "<ROW>" not in str(exc.value) and "XX" not in str(exc.value)


def test_read_log_empty_file_is_no_rows(tmp_path):
    kb, _ = F.fake_kb(tmp_path, 1)
    (kb / "PUBLISH.log").write_text("", encoding="utf-8")
    assert P.read_log(kb) == []


def _detail_text():
    # U+0085 / U+2028 / U+2029 are line breaks to ``str.splitlines`` but not escaped by ``json`` (see the module's
    # reported finding); the strategy leaves them out, everything else must round-trip.
    return st.text(alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x85\u2028\u2029"),
                   max_size=700)


@settings(max_examples=40, deadline=None)
@given(details=st.lists(st.one_of(st.none(), _detail_text()), min_size=1, max_size=6))
def test_append_then_read_round_trips_for_any_detail(details):
    """Law (round trip / monotonic): every appended row is read back as appended and ``seq`` counts 1, 2, 3 ..."""
    with tempfile.TemporaryDirectory() as raw:
        kb = Path(raw)
        appended = [P.append_row(kb, A, "switch" if i == 0 else "mcp_json", "done", detail=d)
                    for i, d in enumerate(details)]
        assert P.read_log(kb) == appended
        assert [r["seq"] for r in appended] == list(range(1, len(details) + 1))
        for row, detail in zip(appended, details):
            assert row.get("detail") == (detail[:500] if detail else None)


# --------------------------------------------------------------------------- the bookkeeping (pure)


def test_pending_actions_a_row_at_the_pointer_seq_is_not_after_it():
    """Only rows *after* the pointer row belong to the publication: an action row with the very seq of the pointer
    row does not settle anything (``>``, not ``>=``)."""
    rows = [F.row(1, A, "switch"), F.row(1, A, "mcp_json"), F.row(2, A, "registry"), F.row(3, A, "agent"),
            F.row(4, A, "approve")]
    assert P.pending_actions(rows, A) == ["mcp_json"]


def test_pending_actions_the_first_of_two_rows_with_one_seq_stays():
    """Two rows of one action with the same seq: the first read stays the latest (``>``, not ``>=``)."""
    rows = [F.row(1, A, "switch"), F.row(2, A, "mcp_json", "failed"), F.row(2, A, "mcp_json", "done"),
            F.row(3, A, "registry"), F.row(4, A, "agent"), F.row(5, A, "approve")]
    assert P.pending_actions(rows, A) == ["mcp_json"]


def test_pending_actions_of_a_build_without_a_publication_and_order():
    assert P.pending_actions([], A) == []
    assert P.pending_actions([F.row(1, B, "switch")], A) == []
    rows = [F.row(1, A, "switch"), F.row(2, A, "approve"), F.row(3, A, "registry")]
    assert P.pending_actions(rows, A) == ["mcp_json", "agent"]  # in the order they run


def test_seq_of_is_the_latest_done_pointer_row_only():
    rows = [F.row(1, A, "switch"), F.row(2, A, "rollback"), F.row(3, A, "switch", "failed"),
            F.row(4, A, "mcp_json"), F.row(5, B, "prune")]
    assert P.seq_of(rows, A) == 2
    assert P.seq_of(rows, B) is None
    assert P.seq_of([], A) is None


def test_protected_builds_current_without_pointer_rows():
    """``current`` with no row in the log at all (a pointer set by hand): protected, no rollback target, no crash."""
    assert P.protected_builds([], [A, B], A, [], 0) == {A: "current"}


def test_protected_builds_reasons_and_order():
    ids = [F.build_id(i) for i in range(6)]
    rows = [F.row(1, ids[0], "switch"), F.row(2, ids[1], "switch", prev=ids[0]),
            F.row(3, ids[2], "switch", prev=ids[1])] + [F.row(3 + i, ids[2], n) for i, n in enumerate(P.ACTIONS, 1)]
    # ids[0], ids[1] have no registrations: pending.  current = ids[2], rollback target = ids[1]
    got = P.protected_builds(rows, ids, ids[2], [ids[4], ids[2]], 1)
    assert got == {ids[2]: "current", ids[1]: "rollback_target", ids[0]: "pending", ids[4]: "reader",
                   ids[5]: "recent"}
    assert list(got) == [ids[2], ids[1], ids[0], ids[4], ids[5]]  # first reason wins, in this order


def test_protected_builds_keep_zero_and_absent_builds():
    ids = [A, B, C]
    assert P.protected_builds([], ids, None, [], 0) == {}
    assert P.protected_builds([], ids, None, [], 2) == {B: "recent", C: "recent"}
    assert P.protected_builds([], ids, OTHER, [OTHER], 5) == {A: "recent", B: "recent", C: "recent"}  # not present


def test_current_build_reads_the_link_and_ignores_a_trailing_slash(tmp_path):
    kb, ids = F.fake_kb(tmp_path, 1)
    assert P.current_build(kb) is None  # no link
    os.symlink(f"builds/{ids[0]}/", kb / "current")
    assert P.current_build(kb) == ids[0]
    os.unlink(kb / "current")
    os.symlink(f"builds/{ids[0]}//", kb / "current")
    assert P.current_build(kb) == ids[0]
    os.unlink(kb / "current")
    os.symlink("/somewhere/else/" + ids[0], kb / "current")
    assert P.current_build(kb) == ids[0]
    os.unlink(kb / "current")
    os.symlink("builds/not-ours", kb / "current")
    assert P.current_build(kb) is None


def test_current_build_reads_the_link_with_exactly_this_name(casefs):
    kb, ids = F.fake_kb(casefs, 1, current_to=0)
    assert P.current_build(kb) == ids[0]
    assert P.current_build(str(kb)) == ids[0]


def test_switch_pointer_makes_a_relative_link_and_leaves_no_tmp(casefs):
    kb, ids = F.fake_kb(casefs, 2, current_to=0)
    (kb / "current.tmp").symlink_to("builds/stale")  # what a crash between the symlink and the replace leaves
    P.switch_pointer(kb, ids[1])
    assert os.readlink(kb / "current") == f"builds/{ids[1]}"
    assert sorted(os.listdir(kb)) == ["builds", "current"]
    P.switch_pointer(kb, ids[0])
    assert os.readlink(kb / "current") == f"builds/{ids[0]}"
    assert sorted(os.listdir(kb)) == ["builds", "current"]


def test_switch_pointer_to_a_missing_build_raises_and_changes_nothing(casefs):
    kb, ids = F.fake_kb(casefs, 1, current_to=0)
    with pytest.raises(FileNotFoundError) as exc:
        P.switch_pointer(kb, A.replace("0000", "1111"))
    assert str(exc.value) == f"no such build: builds/{A.replace('0000', '1111')}"
    assert os.readlink(kb / "current") == f"builds/{ids[0]}"
    assert sorted(os.listdir(kb)) == ["builds", "current"]


def test_switch_pointer_crash_points_in_order(tmp_path, monkeypatch):
    kb, ids = F.fake_kb(tmp_path, 1)
    seen = []

    def point(name):
        seen.append((name, os.path.lexists(kb / "current"), os.path.lexists(kb / "current.tmp")))

    monkeypatch.setattr(P, "crashpoint", point)
    P.switch_pointer(kb, ids[0])
    assert seen == [("publish.tmp_link", False, True), ("publish.switched", True, False)]


def test_list_builds_lists_by_the_name_builds(casefs):
    kb, ids = F.fake_kb(casefs, 2)
    assert P.list_builds(kb) == ids
    assert P.list_builds(casefs / "kb") == []  # no builds/ below


# --------------------------------------------------------------------------- leases


def test_clean_dead_leases_counts_removes_and_tidies(casefs):
    kb, ids = F.fake_kb(casefs, 2)
    dead = F.dead_pid()
    inside = F.lease(kb / "builds" / ids[0], dead)  # the old place
    live_inside = F.lease(kb / "builds" / ids[1], os.getpid())
    outside_dir = kb / ".readers" / ids[0]
    outside_dir.mkdir(parents=True)
    outside = outside_dir / f"{dead}-x"
    outside.write_text("t\n", encoding="utf-8")
    (kb / ".readers" / ids[1]).mkdir()
    (kb / ".readers" / ids[1] / f"{os.getpid()}-y").write_text("t\n", encoding="utf-8")
    (kb / ".readers" / "stray-file").write_text("not a directory\n", encoding="utf-8")
    assert P._clean_dead_leases(kb, ids) == 2
    assert not inside.exists() and not outside.exists()
    assert live_inside.exists() and (kb / ".readers" / ids[1] / f"{os.getpid()}-y").exists()
    assert not outside_dir.exists()  # emptied, so it goes
    assert (kb / ".readers" / ids[1]).is_dir()  # a live reader keeps its directory
    assert (kb / ".readers" / "stray-file").exists()
    assert P._clean_dead_leases(kb, ids) == 0


def test_clean_dead_leases_survives_a_lease_that_vanished(tmp_path, monkeypatch):
    """The reader removed its own lease between the scan and the unlink: not an error, and not counted."""
    kb, ids = F.fake_kb(tmp_path, 1)
    dead = F.dead_pid()
    first = F.lease(kb / "builds" / ids[0], dead, "a")
    second = F.lease(kb / "builds" / ids[0], dead, "b")
    real = os.unlink
    gone = []

    def unlink(path, *a, **k):
        if not gone and Path(path) == first:
            gone.append(path)
            real(path)
            raise FileNotFoundError(2, "gone", str(path))
        return real(path, *a, **k)

    monkeypatch.setattr(os, "unlink", unlink)
    assert P._clean_dead_leases(kb, ids) == 1
    assert gone and not first.exists() and not second.exists()


def test_leases_and_build_leases_places_and_liveness(tmp_path):
    kb, ids = F.fake_kb(tmp_path, 1)
    build = kb / "builds" / ids[0]
    assert P.leases(build) == [] and P.build_leases(kb, ids[0]) == []
    dead = F.dead_pid()
    inside_dead = F.lease(build, dead)
    inside_nopid = build / ".readers" / "no-pid-here"
    inside_nopid.write_text("t\n", encoding="utf-8")
    d = P.reader_lease_dir(kb, ids[0])
    assert d == kb / ".readers" / ids[0]
    d.mkdir(parents=True)
    outside = d / f"{os.getpid()}-z"
    outside.write_text("t\n", encoding="utf-8")
    got = P.build_leases(kb, ids[0])
    assert [(l.path, l.pid, l.alive) for l in got] == [(outside, os.getpid(), True),
                                                       (inside_dead, dead, False),
                                                       (inside_nopid, None, True)]
    moved = tmp_path / "elsewhere"
    build.rename(moved)
    assert [l.path for l in P.build_leases(kb, ids[0], moved)] == [outside, moved / ".readers" / inside_dead.name,
                                                                   moved / ".readers" / "no-pid-here"]


def test_move_to_trash_names_collisions_with_a_counter(casefs, monkeypatch):
    kb, ids = F.fake_kb(casefs, 1)
    inner, asked = os.path.lexists, []

    def bounded(path):  # a counter that does not advance would ask for the same name forever
        asked.append(path)
        if len(asked) > 60:
            raise AssertionError("the collision loop does not advance")
        return inner(path)

    monkeypatch.setattr(os.path, "lexists", bounded)
    trash = kb / "builds" / "_trash"
    dest = P.move_to_trash(kb, ids[0])
    assert dest == trash / ids[0] and dest.is_dir()
    assert sorted(os.listdir(kb / "builds")) == ["_trash"]
    for expected in (f"{ids[0]}.1", f"{ids[0]}.2", f"{ids[0]}.3"):
        (kb / "builds" / ids[0]).mkdir()
        assert P.move_to_trash(kb, ids[0]) == trash / expected
    assert sorted(os.listdir(trash)) == [ids[0], f"{ids[0]}.1", f"{ids[0]}.2", f"{ids[0]}.3"]


def test_move_to_trash_counter_does_not_loop(tmp_path, monkeypatch):
    """Bounded: the collision loop asks for each free name once (a counter that does not advance would ask forever)."""
    kb, ids = F.fake_kb(tmp_path, 1)
    trash = kb / "builds" / "_trash"
    (trash / ids[0]).mkdir(parents=True)
    (trash / f"{ids[0]}.1").mkdir()
    asked = []
    real = os.path.lexists

    def lexists(path):
        asked.append(str(path))
        if len(asked) > 10:
            raise AssertionError("the collision loop does not advance")
        return real(path)

    monkeypatch.setattr(os.path, "lexists", lexists)
    assert P.move_to_trash(kb, ids[0]) == trash / f"{ids[0]}.2"


def test_move_to_trash_creates_trash_once_and_accepts_an_existing_one(casefs):
    kb, ids = F.fake_kb(casefs, 2)
    (kb / "builds" / "_trash").mkdir()
    assert P.move_to_trash(kb, ids[0]) == kb / "builds" / "_trash" / ids[0]
    assert P.move_to_trash(kb, ids[1]) == kb / "builds" / "_trash" / ids[1]
    assert sorted(os.listdir(kb / "builds")) == ["_trash"]


def test_move_to_trash_of_a_sealed_build_keeps_the_seal(tmp_path):
    kb, ids = F.fake_kb(tmp_path, 0)
    build = mk_build(kb, A)
    mode = stat.S_IMODE(os.stat(build).st_mode)
    assert mode & 0o222 == 0
    dest = P.move_to_trash(kb, A)
    assert not build.exists() and dest.is_dir()
    assert stat.S_IMODE(os.stat(dest).st_mode) == mode


# --------------------------------------------------------------------------- verifying a target


def test_verify_of_a_good_build_is_empty_and_only_check_flag_false(casefs):
    kb = casefs / "kb" / "demo"
    mk_build(kb, A)
    assert P._verify(kb, A) == ([], False)
    assert P.verify_target(kb, A) == []
    assert P.verify_target(str(kb), A) == []


def test_verify_not_a_build_directory(casefs):
    kb = casefs / "kb" / "demo"
    mk_build(kb, A)
    want = ([f"no such build: builds/{B}"], False)
    assert P._verify(kb, B) == want
    assert P._verify(kb, "bogus") == ([f"no such build: builds/bogus"], False)
    # a symlink named like a build, to a real verified build: not a build
    os.symlink(A, kb / "builds" / B)
    assert P._verify(kb, B) == want
    # a real directory with a name that is not a build id
    (kb / "builds" / "scratch").mkdir()
    assert P._verify(kb, "scratch") == (["no such build: builds/scratch"], False)
    # a file named like a build
    (kb / "builds" / C).write_text("x", encoding="utf-8")
    assert P._verify(kb, C) == ([f"no such build: builds/{C}"], False)


def test_verify_build_receipt_texts(casefs):
    kb = casefs / "kb" / "demo"
    mk_build(kb, A, receipt=None)
    assert P.verify_target(kb, A) == ["build_receipt.json: missing or unreadable (not a build of the 13 steps)"]
    mk_build(kb, B, receipt="[1, 2]")
    assert P.verify_target(kb, B) == ["build_receipt.json: missing or unreadable (not a build of the 13 steps)"]
    mk_build(kb, C, receipt="not json")
    assert P.verify_target(kb, C) == ["build_receipt.json: missing or unreadable (not a build of the 13 steps)"]


def test_verify_build_receipt_status_and_id_each_decide(casefs):
    kb = casefs / "kb" / "demo"
    mk_build(kb, A, receipt={"status": "FAIL", "build_id": A})
    assert P.verify_target(kb, A) == [f"build_receipt.json: status 'FAIL' for {A!r}, not a PASS build of {A}"]
    mk_build(kb, B, receipt={"status": "PASS", "build_id": OTHER})
    assert P.verify_target(kb, B) == [f"build_receipt.json: status 'PASS' for {OTHER!r}, not a PASS build of {B}"]
    mk_build(kb, C, receipt={})
    assert P.verify_target(kb, C) == [f"build_receipt.json: status None for None, not a PASS build of {C}"]
    mk_build(kb, D, receipt={"status": "WARN", "build_id": OTHER})
    assert P.verify_target(kb, D) == [f"build_receipt.json: status 'WARN' for {OTHER!r}, not a PASS build of {D}"]


def test_verify_manifest_problems_are_added_not_replacing(casefs):
    kb = casefs / "kb" / "demo"
    mk_build(kb, A, receipt={"status": "FAIL", "build_id": A}, manifest=False)
    assert P.verify_target(kb, A) == [f"build_receipt.json: status 'FAIL' for {A!r}, not a PASS build of {A}",
                                      "MANIFEST.sha256: missing"]

    def add(build: Path) -> None:
        (build / "extra.txt").write_text("e", encoding="utf-8")

    mk_build(kb, B, receipt={"status": "FAIL", "build_id": B}, tamper=add)
    assert P.verify_target(kb, B) == [f"build_receipt.json: status 'FAIL' for {B!r}, not a PASS build of {B}",
                                      "MANIFEST.sha256: unlisted: extra.txt"]


def test_verify_shows_at_most_five_manifest_problems(casefs):
    kb = casefs / "kb" / "demo"

    def add(build: Path) -> None:
        for i in range(8):
            (build / f"extra{i}.txt").write_text("e", encoding="utf-8")

    mk_build(kb, A, tamper=add)
    problems = P.verify_target(kb, A)
    assert problems == [f"MANIFEST.sha256: unlisted: extra{i}.txt" for i in range(5)]


def test_verify_check_receipt_texts(casefs):
    kb = casefs / "kb" / "demo"
    mk_build(kb, A, check=None)
    assert P._verify(kb, A) == (["check_receipt.json: missing or unreadable (the build was not checked)"], False)
    mk_build(kb, B, check="{bad json")
    assert P._verify(kb, B) == (["check_receipt.json: missing or unreadable (the build was not checked)"], False)
    mk_build(kb, C, check={"status": "PASS"})
    assert P._verify(kb, C) == (["check_receipt.json: not a valid check receipt"], False)
    mk_build(kb, D, check=check_doc(OTHER))
    assert P._verify(kb, D) == ([f"check_receipt.json: names {OTHER}, not {D}"], False)


def test_verify_check_status_fail_is_the_only_problem_or_not(casefs):
    kb = casefs / "kb" / "demo"
    mk_build(kb, A, check=check_doc(A, "FAIL"))
    problems, only = P._verify(kb, A)
    assert problems == ["check_receipt.json: status FAIL"] and only is True
    # FAIL and something else wrong: not "only the check"
    mk_build(kb, B, check=check_doc(B, "FAIL"), receipt={"status": "FAIL", "build_id": B})
    problems, only = P._verify(kb, B)
    assert len(problems) == 2 and "check_receipt.json: status FAIL" in problems and only is False
    # WARN is not PASS either, and it is not the FAIL exit
    mk_build(kb, C, check=check_doc(C, "WARN"))
    assert P._verify(kb, C) == (["check_receipt.json: status WARN, not PASS"], False)
    # a FAIL receipt that names another build: two problems
    mk_build(kb, D, check=check_doc(OTHER, "FAIL"))
    problems, only = P._verify(kb, D)
    assert problems == [f"check_receipt.json: names {OTHER}, not {D}", "check_receipt.json: status FAIL"]
    assert only is False


def test_writable_lists_user_group_and_other_write_bits(tmp_path):
    kb = tmp_path / "kb" / "demo"
    build = mk_build(kb, A)
    assert P._writable(build) == []
    for mode, label in ((0o644, "user"), (0o464, "group"), (0o446, "other")):
        os.chmod(build / "data" / "a.txt", mode)
        assert P._writable(build) == ["data/a.txt"], label
    os.chmod(build / "data" / "a.txt", 0o444)
    os.chmod(build / "data", 0o755)
    assert set(P._writable(build)) == {"data"}
    os.chmod(build / "data", 0o555)
    os.chmod(build, 0o755)
    assert P._writable(build) == ["."]
    os.chmod(build, 0o555)
    assert P._writable(build) == []


def test_writable_skips_readers_at_the_top_only_and_links(tmp_path):
    kb = tmp_path / "kb" / "demo"
    build = mk_build(kb, A, seal=False)
    (build / ".readers").mkdir()
    (build / ".readers" / "1-x").write_text("t", encoding="utf-8")
    (build / "data" / ".readers").mkdir()
    (build / "data" / ".readers" / "1-y").write_text("t", encoding="utf-8")
    os.symlink("data/a.txt", build / "link")
    RB.make_readonly(build)
    os.chmod(build / ".readers", 0o755)  # a live reader's lease directory stays writable
    os.chmod(build / ".readers" / "1-x", 0o644)
    # the top-level .readers is not part of the sealed tree; one further down is, and links are never looked at
    assert set(P._writable(build)) == {"data/.readers", "data/.readers/1-y"}


def test_verify_writable_message_for_one_to_six_files(tmp_path):
    kb = tmp_path / "kb" / "demo"
    build = mk_build(kb, A)
    names = []

    def last_problem() -> str:
        return P.verify_target(kb, A)[-1]

    for i in range(6):
        p = build / "data" / f"w{i}.txt"
        os.chmod(build / "data", 0o755)
        p.write_text("w", encoding="utf-8")
        names.append(p)
        os.chmod(build / "data", 0o555)
    # the manifest does not list the new files: use the writable problem only
    problems = [p for p in P.verify_target(kb, A) if p.startswith("writable:")]
    assert len(problems) == 1
    shown = P._writable(build)
    assert len(shown) == 6
    suffix = " (a published build is read-only all the way down)"
    assert problems[0] == "writable: " + ", ".join(shown[:3]) + " and 3 more" + suffix
    # five, four, three, two, one
    for i, expect in ((5, " and 2 more"), (4, " and 1 more"), (3, ""), (2, ""), (1, "")):
        os.chmod(build / "data", 0o755)
        os.chmod(names[i], 0o444)
        os.chmod(build / "data", 0o555)
        shown = P._writable(build)
        assert len(shown) == i
        problems = [p for p in P.verify_target(kb, A) if p.startswith("writable:")]
        assert problems == ["writable: " + ", ".join(shown[:3]) + expect + suffix], i


# --------------------------------------------------------------------------- _run_action


def target(kb: Path, bid: str, project: Path | None = None, env=None):
    return P.Target(kb, project or kb.parent.parent, bid, env if env is not None else ENV)


def test_run_action_done_row_and_result(tmp_path, monkeypatch):
    kb, _ = F.fake_kb(tmp_path, 1)
    fake = install(monkeypatch, Impl(details={"mcp_json": "wrote it"}))
    rows: list[dict] = []
    got = P._run_action("mcp_json", target(kb, A), rows)
    assert got == {"action": "mcp_json", "status": "done", "detail": "wrote it"}
    assert list(got) == ["action", "status", "detail"]
    assert len(rows) == 1 and rows[0] == rows_of(kb)[0]
    assert (rows[0]["target_build_id"], rows[0]["action"], rows[0]["status"], rows[0]["prev_build_id"],
            rows[0]["detail"]) == (A, "mcp_json", "done", None, "wrote it")
    assert fake.applied() == ["mcp_json"]


def test_run_action_without_detail_has_an_empty_detail_and_no_key(tmp_path, monkeypatch):
    kb, _ = F.fake_kb(tmp_path, 1)
    fake = Impl()
    fake.table["agent"] = (None, lambda t: None)
    install(monkeypatch, fake)
    rows: list[dict] = []
    got = P._run_action("agent", target(kb, A), rows)
    assert got == {"action": "agent", "status": "done", "detail": ""}
    assert "detail" not in rows[0]


@pytest.mark.parametrize("exc,text", [(P.ActionError("cannot read .mcp.json"), "cannot read .mcp.json"),
                                      (PermissionError(13, "Permission denied"), "[Errno 13] Permission denied"),
                                      (FileNotFoundError("gone"), "gone")])
def test_run_action_failure_is_a_failed_row_with_the_message(tmp_path, monkeypatch, exc, text):
    kb, _ = F.fake_kb(tmp_path, 1)
    install(monkeypatch, Impl(fail={"registry": exc}))
    rows: list[dict] = []
    got = P._run_action("registry", target(kb, A), rows)
    assert got == {"action": "registry", "status": "failed", "detail": text}
    assert rows[0]["status"] == "failed" and rows[0]["detail"] == text


def test_run_action_a_failing_marker_read_fails_the_action_and_applies_nothing(tmp_path, monkeypatch):
    kb, _ = F.fake_kb(tmp_path, 1)
    fake = install(monkeypatch, Impl(fail={("marker", "mcp_json"): P.ActionError("bad json")}))
    got = P._run_action("mcp_json", target(kb, A), [])
    assert got == {"action": "mcp_json", "status": "failed", "detail": "bad json"}
    assert fake.applied() == []


def test_run_action_other_exceptions_are_not_swallowed(tmp_path, monkeypatch):
    kb, _ = F.fake_kb(tmp_path, 1)
    install(monkeypatch, Impl(fail={"registry": RuntimeError("bug")}))
    with pytest.raises(RuntimeError):
        P._run_action("registry", target(kb, A), [])
    assert not (kb / "PUBLISH.log").exists()


def test_run_action_skips_a_registration_naming_a_build_published_later(tmp_path, monkeypatch):
    kb, _ = F.fake_kb(tmp_path, 2)
    fake = install(monkeypatch, Impl(markers={"registry": B}))
    rows = [F.row(1, A, "switch"), F.row(6, B, "switch")]
    got = P._run_action("registry", target(kb, A), rows)
    assert got == {"action": "registry", "status": "skipped_newer",
                   "detail": f"the registration names build {B}, published later"}
    assert fake.applied() == []
    assert rows[-1]["status"] == "skipped_newer" and rows[-1]["detail"] == got["detail"]
    assert rows_of(kb)[-1]["status"] == "skipped_newer"


@pytest.mark.parametrize("rows,marker", [
    ([F.row(1, A, "switch"), F.row(5, B, "switch")], A),  # names itself
    ([F.row(6, A, "switch"), F.row(1, B, "switch")], B),  # names an older build
    ([F.row(5, A, "switch"), F.row(5, B, "switch")], B),  # the same seq is not later
    ([F.row(1, A, "switch")], B),  # names a build with no publication
    ([F.row(1, B, "switch")], B),  # this build has no publication (yet)
    ([F.row(1, A, "switch"), F.row(2, B, "switch")], None),  # names nothing
])
def test_run_action_applies_unless_the_marker_is_strictly_later(tmp_path, monkeypatch, rows, marker):
    kb, _ = F.fake_kb(tmp_path, 2)
    fake = install(monkeypatch, Impl(markers={"mcp_json": marker}))
    got = P._run_action("mcp_json", target(kb, A), rows)
    assert got["status"] == "done" and fake.applied() == ["mcp_json"]


def test_run_action_without_a_marker_reader_applies(tmp_path, monkeypatch):
    kb, _ = F.fake_kb(tmp_path, 1)
    fake = install(monkeypatch, Impl())
    got = P._run_action("approve", target(kb, A), [F.row(1, A, "switch")])
    assert got["status"] == "done"
    assert [k for k, _, _ in fake.calls] == ["apply"]  # no marker reader for approve


def test_run_action_hands_the_target_to_the_seam(tmp_path, monkeypatch):
    kb, _ = F.fake_kb(tmp_path, 1)
    fake = install(monkeypatch, Impl())
    t = target(kb, A)
    P._run_action("mcp_json", t, [])
    assert [(k, n, tt) for k, n, tt in fake.calls] == [("marker", "mcp_json", t), ("apply", "mcp_json", t)]


# --------------------------------------------------------------------------- publish: the pointer and its rows


def test_publish_first_build_body_log_and_pointer(casefs, impl):
    project, kb, ids = world(casefs, 2)
    code, body = publish_ok(kb, ids[0])
    assert code == 0
    assert body == {"schema": "klode-kit.publish/1", "mode": "publish", "kb_id": "demo", "build_id": ids[0],
                    "exit_code": 0, "reason": "ok", "prev_build_id": None,
                    "actions": [{"action": "switch", "status": "done", "detail": ""},
                                {"action": "mcp_json", "status": "done", "detail": "mcp_json done"},
                                {"action": "registry", "status": "done", "detail": "registry done"},
                                {"action": "agent", "status": "done", "detail": "agent done"},
                                {"action": "approve", "status": "done", "detail": "approve done"}]}
    assert os.readlink(kb / "current") == f"builds/{ids[0]}"
    rows = rows_of(kb)
    assert [(r["seq"], r["action"], r["status"], r["target_build_id"], r["prev_build_id"]) for r in rows] == [
        (1, "switch", "done", ids[0], None), (2, "mcp_json", "done", ids[0], None),
        (3, "registry", "done", ids[0], None), (4, "agent", "done", ids[0], None),
        (5, "approve", "done", ids[0], None)]
    assert all(r["by"] == f"klode-kit/{__version__}" for r in rows)
    assert "detail" not in rows[0] and rows[1]["detail"] == "mcp_json done"
    assert impl.applied() == list(P.ACTIONS)
    assert P.pending_builds(rows) == set()


def test_publish_second_build_records_the_previous_one(casefs, impl):
    project, kb, ids = world(casefs, 2)
    publish_ok(kb, ids[0])
    code, body = publish_ok(kb, ids[1])
    assert code == 0 and body["prev_build_id"] == ids[0] and body["build_id"] == ids[1]
    assert [r["prev_build_id"] for r in rows_of(kb) if r["action"] == "switch"] == [None, ids[0]]
    assert P.current_build(kb) == ids[1]


def test_publish_twice_is_idempotent(casefs, impl):
    """Law (idempotence): publishing the build that is already published, with nothing open, changes nothing."""
    project, kb, ids = world(casefs, 1)
    publish_ok(kb, ids[0])
    log_before, link_before = F.log_bytes(kb), os.readlink(kb / "current")
    impl.calls.clear()
    code, body = publish_ok(kb, ids[0])
    assert code == 0 and body["actions"] == [] and body["reason"] == "ok"
    assert F.log_bytes(kb) == log_before and os.readlink(kb / "current") == link_before
    assert impl.calls == []
    assert body["prev_build_id"] is None  # the prev of the publication that is current


def test_publish_again_keeps_the_prev_of_its_own_pointer_row(casefs, impl):
    project, kb, ids = world(casefs, 2)
    publish_ok(kb, ids[0])
    publish_ok(kb, ids[1])
    code, body = publish_ok(kb, ids[1])
    assert code == 0 and body["actions"] == [] and body["prev_build_id"] == ids[0]
    # a later pointer row of another build must not be taken for this build's own
    P.rollback(kb, ids[0], env=ENV)
    link_to(kb, ids[1])  # hand-made: current back on ids[1] without a row; the latest pointer row names ids[0]
    code, body = publish_ok(kb, ids[1])
    assert code == 0
    assert body["actions"] == [{"action": "switch", "status": "done", "detail": "recorded after the fact"}] + [
        {"action": n, "status": "done", "detail": f"{n} done"} for n in P.ACTIONS]
    last = rows_of(kb)[-5]
    assert (last["action"], last["target_build_id"], last["prev_build_id"],
            last["detail"]) == ("switch", ids[1], ids[0],
                                "the pointer was already on the build; recorded after the fact")


def test_publish_records_a_switch_that_died_before_its_row(casefs, impl):
    project, kb, ids = world(casefs, 1)
    link_to(kb, ids[0])  # the crash between the switch and the row
    code, body = publish_ok(kb, ids[0])
    assert code == 0 and body["prev_build_id"] is None
    assert body["actions"][0] == {"action": "switch", "status": "done", "detail": "recorded after the fact"}
    rows = rows_of(kb)
    assert rows[0]["action"] == "switch" and rows[0]["prev_build_id"] is None
    assert rows[0]["detail"] == "the pointer was already on the build; recorded after the fact"
    assert [r["action"] for r in rows[1:]] == list(P.ACTIONS)


def test_publish_replays_open_actions_only_for_the_same_build(casefs, monkeypatch):
    project, kb, ids = world(casefs, 1)
    bad = install(monkeypatch, Impl(fail={"registry": P.ActionError("registry file is not TOML")}))
    code, body = publish_ok(kb, ids[0])
    assert code == 21 and body["reason"] == "post_publish_failed" and body["exit_code"] == 21
    assert [(a["action"], a["status"]) for a in body["actions"]] == [
        ("switch", "done"), ("mcp_json", "done"), ("registry", "failed"), ("agent", "done"), ("approve", "done")]
    assert body["actions"][2]["detail"] == "registry file is not TOML"
    assert P.current_build(kb) == ids[0]  # the pointer stays
    assert P.pending_builds(rows_of(kb)) == {ids[0]}
    good = install(monkeypatch, Impl())
    code, body = publish_ok(kb, ids[0])
    assert code == 0 and [a["action"] for a in body["actions"]] == ["registry"]
    assert good.applied() == ["registry"]
    assert P.pending_builds(rows_of(kb)) == set()


# --------------------------------------------------------------------------- publish: refusals


def test_publish_refuses_an_unchecked_build_with_exit_30(casefs, impl):
    project, kb, ids = world(casefs, 0)
    mk_build(kb, A, check=None)
    code, body = publish_ok(kb, A)
    assert code == 30
    assert body == {"schema": "klode-kit.publish/1", "mode": "publish", "kb_id": "demo", "build_id": A,
                    "exit_code": 30, "reason": "contract_violation",
                    "problems": ["check_receipt.json: missing or unreadable (the build was not checked)"]}
    assert not (kb / "current").exists() and not (kb / "PUBLISH.log").exists() and impl.calls == []


def test_publish_refuses_a_failed_check_with_exit_20(casefs, impl):
    project, kb, ids = world(casefs, 0)
    mk_build(kb, A, check=check_doc(A, "FAIL"))
    code, body = publish_ok(kb, A)
    assert (code, body["exit_code"], body["reason"]) == (20, 20, "content_gate")
    assert body["mode"] == "publish" and body["build_id"] == A and body["kb_id"] == "demo"
    assert body["problems"] == ["check_receipt.json: status FAIL"]
    assert not (kb / "current").exists() and impl.calls == []


def test_publish_a_failed_check_plus_another_problem_is_exit_30(casefs, impl):
    project, kb, ids = world(casefs, 0)
    mk_build(kb, A, check=check_doc(A, "FAIL"), receipt=None)
    code, body = publish_ok(kb, A)
    assert (code, body["reason"]) == (30, "contract_violation") and len(body["problems"]) == 2


def test_publish_refuses_a_missing_build_and_leaves_an_existing_pointer(casefs, impl):
    project, kb, ids = world(casefs, 1)
    publish_ok(kb, ids[0])
    log = F.log_bytes(kb)
    code, body = publish_ok(kb, B)
    assert code == 30 and body["problems"] == [f"no such build: builds/{B}"] and body["build_id"] == B
    assert F.log_bytes(kb) == log and P.current_build(kb) == ids[0]


def test_publish_refuses_a_writable_tree(casefs, impl):
    project, kb, ids = world(casefs, 1)
    os.chmod(kb / "builds" / ids[0] / "data" / "a.txt", 0o644)
    code, body = publish_ok(kb, ids[0])
    assert code == 30
    assert body["problems"] == ["writable: data/a.txt (a published build is read-only all the way down)"]


def test_publish_with_an_untrusted_log_is_exit_30_with_the_reason(casefs, impl):
    project, kb, ids = world(casefs, 1)
    (kb / "PUBLISH.log").write_text("{torn", encoding="utf-8")
    code, body = publish_ok(kb, ids[0])
    log = kb / "PUBLISH.log"
    assert code == 30
    assert body == {"schema": "klode-kit.publish/1", "mode": "publish", "kb_id": "demo", "build_id": ids[0],
                    "exit_code": 30, "reason": "contract_violation",
                    "problems": [f"{log}: the last line is torn (no newline)"]}
    assert not (kb / "current").exists()


@pytest.mark.parametrize("mode", ["publish", "replay", "rollback", "prune"])
def test_a_held_lock_is_exit_40_for_every_writing_command(casefs, impl, mode):
    project, kb, ids = world(casefs, 1)
    fd = os.open(kb, os.O_RDONLY)
    import fcntl

    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        if mode == "prune":
            code, body = P.prune(kb, 3, env=ENV)
        else:
            code, body = getattr(P, mode)(kb, ids[0], env=ENV)
    finally:
        os.close(fd)
    assert code == 40
    assert body == {"schema": "klode-kit.publish/1", "mode": mode, "kb_id": "demo",
                    "build_id": None if mode == "prune" else ids[0], "exit_code": 40, "reason": "lock_busy",
                    "detail": f"another publish, replay, rollback or prune holds the lock of {kb}"}
    assert impl.calls == [] and not (kb / "PUBLISH.log").exists() and not (kb / "current").exists()


def test_the_lock_is_released_after_a_run_and_after_a_refusal(casefs, impl):
    project, kb, ids = world(casefs, 1)
    assert publish_ok(kb, B)[0] == 30
    assert publish_ok(kb, ids[0])[0] == 0
    assert P.prune(kb, 3, env=ENV)[0] == 0


def test_the_lock_is_taken_before_the_crash_point_and_the_work(casefs, impl, monkeypatch):
    project, kb, ids = world(casefs, 1)
    seen = []
    monkeypatch.setattr(P, "crashpoint", lambda name: seen.append(name))
    publish_ok(kb, ids[0])
    assert seen == ["publish.lock", "publish.tmp_link", "publish.switched", "publish.logged"] + ["publish.logged"] * 4


# --------------------------------------------------------------------------- publish: project, env, receipt


def test_publish_project_defaults_to_the_parent_of_kb(casefs, impl):
    project, kb, ids = world(casefs, 1)
    publish_ok(kb, ids[0])
    t = impl.target()
    assert t.project == project and t.kb == kb and t.build_id == ids[0]


def test_publish_explicit_project_wins(casefs, impl):
    project, kb, ids = world(casefs, 1)
    other = casefs / "elsewhere"
    publish_ok(kb, ids[0], project=other)
    assert impl.target().project == other
    impl.calls.clear()
    publish_ok(kb, ids[0], project=str(other))
    assert impl.calls == []  # nothing open: but the type was accepted


def test_publish_project_of_a_kb_not_below_kb_falls_back_to_two_levels_up(casefs, impl):
    kb = casefs / "x" / "y" / "lib"
    mk_build(kb, A)
    publish_ok(kb, A)
    assert impl.target().project == casefs / "x"


def test_target_env_is_a_copy_of_the_env_given_or_of_the_process(casefs, impl, monkeypatch):
    project, kb, ids = world(casefs, 1)
    env = {"KLODE_KIT_BIN": "/b", "X": "1"}
    publish_ok(kb, ids[0], env=env)
    got = impl.target().env
    assert got == env and got is not env
    # without an env the process's environment is used
    project2, kb2, ids2 = world(casefs / "two", 1)
    monkeypatch.setenv("WI15M_MARKER", "from-process")
    P.publish(kb2, ids2[0])
    assert impl.calls[-1][2].env["WI15M_MARKER"] == "from-process"
    assert impl.calls[-1][2].env == dict(os.environ)


def test_publish_receipt_is_the_body_in_canonical_form(casefs, impl):
    project, kb, ids = world(casefs, 1)
    receipt = casefs / "out" / "receipt.json"
    code, body = publish_ok(kb, ids[0], receipt=receipt)
    assert receipt.read_bytes() == receipts.dumps(body)
    assert json.loads(receipt.read_text(encoding="utf-8")) == body
    publish_ok(kb, ids[0], receipt=str(receipt))
    assert json.loads(receipt.read_text(encoding="utf-8"))["actions"] == []


def test_receipt_goes_through_the_two_named_points(casefs, impl, monkeypatch):
    project, kb, ids = world(casefs, 1)
    seen = []
    real = P._atomic
    monkeypatch.setattr(P, "_atomic", lambda path, data, tmp_point, done_point: (
        seen.append((Path(path).name, tmp_point, done_point)), real(path, data, tmp_point, done_point))[1])
    publish_ok(kb, ids[0], receipt=casefs / "r.json")
    assert seen == [("r.json", "publish.receipt_tmp", "publish.receipt_written")]
    seen.clear()
    publish_ok(kb, ids[0])
    assert seen == []


# --------------------------------------------------------------------------- rollback


def test_rollback_to_an_older_build_writes_a_rollback_row_and_all_registrations(casefs, impl):
    project, kb, ids = world(casefs, 2)
    publish_ok(kb, ids[0])
    publish_ok(kb, ids[1])
    impl.calls.clear()
    code, body = P.rollback(kb, ids[0], env=ENV)
    assert code == 0
    assert body == {"schema": "klode-kit.publish/1", "mode": "rollback", "kb_id": "demo", "build_id": ids[0],
                    "exit_code": 0, "reason": "ok", "prev_build_id": ids[1],
                    "actions": [{"action": "rollback", "status": "done", "detail": ""}] + [
                        {"action": n, "status": "done", "detail": f"{n} done"} for n in P.ACTIONS]}
    assert P.current_build(kb) == ids[0]
    row = [r for r in rows_of(kb) if r["action"] == "rollback"][0]
    assert (row["target_build_id"], row["prev_build_id"], row["status"]) == (ids[0], ids[1], "done")
    assert impl.applied() == list(P.ACTIONS)


def test_rollback_to_the_current_build_is_recorded_as_already_current(casefs, impl):
    project, kb, ids = world(casefs, 2)
    publish_ok(kb, ids[0])
    publish_ok(kb, ids[1])
    code, body = P.rollback(kb, ids[1], env=ENV)
    assert code == 0 and body["prev_build_id"] == ids[0]
    assert body["actions"][0] == {"action": "rollback", "status": "done", "detail": "already current"}
    row = [r for r in rows_of(kb) if r["action"] == "rollback"][0]
    assert (row["target_build_id"], row["prev_build_id"], row["detail"]) == (ids[1], ids[0], "already current")
    assert [a["action"] for a in body["actions"][1:]] == list(P.ACTIONS)  # a rollback registers everything again


def test_rollback_to_the_current_build_of_an_empty_log(casefs, impl):
    project, kb, ids = world(casefs, 1)
    link_to(kb, ids[0])
    code, body = P.rollback(kb, ids[0], env=ENV)
    assert code == 0 and body["prev_build_id"] is None
    assert body["actions"][0] == {"action": "rollback", "status": "done", "detail": "already current"}
    assert rows_of(kb)[0]["prev_build_id"] is None


def test_rollback_refusals_name_the_mode(casefs, impl):
    project, kb, ids = world(casefs, 0)
    mk_build(kb, A, check=check_doc(A, "FAIL"))
    mk_build(kb, B, receipt=None)
    code, body = P.rollback(kb, A, env=ENV)
    assert (code, body["mode"], body["build_id"], body["reason"], body["problems"]) == (
        20, "rollback", A, "content_gate", ["check_receipt.json: status FAIL"])
    code, body = P.rollback(kb, B, env=ENV)
    assert (code, body["mode"], body["build_id"], body["reason"]) == (30, "rollback", B, "contract_violation")
    assert not (kb / "current").exists() and impl.calls == []


def test_rollback_project_env_and_receipt(casefs, impl):
    project, kb, ids = world(casefs, 1)
    other = casefs / "elsewhere"
    receipt = casefs / "rb.json"
    code, body = P.rollback(kb, ids[0], project=other, env={"K": "v"}, receipt=receipt)
    assert impl.target().project == other and impl.target().env == {"K": "v"}
    assert receipt.read_bytes() == receipts.dumps(body)
    impl.calls.clear()
    project2, kb2, ids2 = world(casefs / "two", 1)
    P.rollback(kb2, ids2[0], env={})
    assert impl.target().project == project2 and impl.target().env == {}


def test_rollback_with_an_untrusted_log_is_exit_30(casefs, impl):
    project, kb, ids = world(casefs, 1)
    (kb / "PUBLISH.log").write_text("junk\n", encoding="utf-8")
    code, body = P.rollback(kb, ids[0], env=ENV)
    assert (code, body["mode"], body["build_id"], body["reason"]) == (30, "rollback", ids[0], "contract_violation")
    assert body["problems"][0].startswith(f"{kb / 'PUBLISH.log'}:1: not JSON: ")


# --------------------------------------------------------------------------- replay


def test_replay_of_a_build_that_is_not_current_is_stale_and_writes_nothing(casefs, impl):
    project, kb, ids = world(casefs, 2)
    publish_ok(kb, ids[0])
    log = F.log_bytes(kb)
    impl.calls.clear()
    code, body = P.replay(kb, ids[1], env=ENV)
    assert code == 22
    assert body == {"schema": "klode-kit.publish/1", "mode": "replay", "kb_id": "demo", "build_id": ids[1],
                    "exit_code": 22, "reason": "stale_replay",
                    "detail": f"current is {ids[0]}, not {ids[1]}"}
    assert F.log_bytes(kb) == log and impl.calls == [] and P.current_build(kb) == ids[0]


def test_replay_without_a_pointer_says_not_set(casefs, impl):
    project, kb, ids = world(casefs, 1)
    code, body = P.replay(kb, ids[0], env=ENV)
    assert code == 22 and body["detail"] == f"current is not set, not {ids[0]}"
    assert body["mode"] == "replay" and body["build_id"] == ids[0]
    assert not (kb / "PUBLISH.log").exists()


def test_replay_refuses_a_current_build_that_no_longer_verifies(casefs, impl):
    project, kb, ids = world(casefs, 1)
    publish_ok(kb, ids[0])
    os.chmod(kb / "builds" / ids[0] / "data" / "a.txt", 0o644)
    code, body = P.replay(kb, ids[0], env=ENV)
    assert (code, body["mode"], body["build_id"], body["reason"]) == (30, "replay", ids[0], "contract_violation")
    assert body["problems"] == ["writable: data/a.txt (a published build is read-only all the way down)"]


def test_replay_refuses_a_failed_check_as_exit_20(casefs, impl):
    project, kb, ids = world(casefs, 0)
    mk_build(kb, A, check=check_doc(A, "FAIL"))
    link_to(kb, A)
    code, body = P.replay(kb, A, env=ENV)
    assert (code, body["reason"], body["mode"]) == (20, "content_gate", "replay")


def test_replay_without_a_recorded_publication_is_refused(casefs, impl):
    project, kb, ids = world(casefs, 1)
    link_to(kb, ids[0])
    code, body = P.replay(kb, ids[0], env=ENV)
    assert code == 30
    assert body == {"schema": "klode-kit.publish/1", "mode": "replay", "kb_id": "demo", "build_id": ids[0],
                    "exit_code": 30, "reason": "contract_violation",
                    "problems": [f"PUBLISH.log: no publication of {ids[0]} is recorded; publish it with --build"]}
    assert not (kb / "PUBLISH.log").exists() and impl.calls == []


def test_replay_never_writes_a_pointer_row_and_does_only_the_open_actions(casefs, monkeypatch):
    project, kb, ids = world(casefs, 1)
    install(monkeypatch, Impl(fail={"agent": OSError("disk full")}))
    code, _ = publish_ok(kb, ids[0])
    assert code == 21
    good = install(monkeypatch, Impl())
    n_rows = len(rows_of(kb))
    code, body = P.replay(kb, ids[0], env=ENV)
    assert code == 0
    assert body == {"schema": "klode-kit.publish/1", "mode": "replay", "kb_id": "demo", "build_id": ids[0],
                    "exit_code": 0, "reason": "ok", "prev_build_id": None,
                    "actions": [{"action": "agent", "status": "done", "detail": "agent done"}]}
    assert len(rows_of(kb)) == n_rows + 1 and good.applied() == ["agent"]
    # a second replay has nothing to do and writes nothing
    log = F.log_bytes(kb)
    code, body = P.replay(kb, ids[0], env=ENV)
    assert code == 0 and body["actions"] == [] and F.log_bytes(kb) == log


def test_replay_does_not_record_a_missing_switch_even_if_the_log_names_another_build(casefs, impl):
    """``publish`` repairs a switch that died before its row; a replay only needs the publication to exist."""
    project, kb, ids = world(casefs, 2)
    publish_ok(kb, ids[0])
    publish_ok(kb, ids[1])
    link_to(kb, ids[0])  # by hand: current is ids[0] again, the latest pointer row names ids[1]
    log = F.log_bytes(kb)
    code, body = P.replay(kb, ids[0], env=ENV)
    assert code == 0 and body["actions"] == [] and F.log_bytes(kb) == log
    assert body["prev_build_id"] is None  # the prev of ids[0]'s own pointer row, not of the latest one


def test_replay_prev_is_the_prev_of_the_builds_own_latest_pointer_row(casefs, impl):
    project, kb, ids = world(casefs, 2)
    publish_ok(kb, ids[0])
    publish_ok(kb, ids[1])
    code, body = P.replay(kb, ids[1], env=ENV)
    assert code == 0 and body["prev_build_id"] == ids[0]


def test_replay_skips_a_registration_that_names_a_later_build(casefs, monkeypatch):
    """End to end: ids[0] was published first (registrations open), ids[1] after it; the pointer is put back on ids[0]
    by hand; a registration that already names ids[1] is not overwritten (``skipped_newer``) and counts as settled."""
    project, kb, ids = world(casefs, 2)
    F.write_rows(kb, [F.row(1, ids[0], "switch"), F.row(2, ids[1], "switch", prev=ids[0])]
                 + [F.row(2 + i, ids[1], n) for i, n in enumerate(P.ACTIONS, 1)])
    link_to(kb, ids[0])
    fake = install(monkeypatch, Impl(markers={"registry": ids[1]}))
    code, body = P.replay(kb, ids[0], env=ENV)
    assert code == 0
    assert [(a["action"], a["status"]) for a in body["actions"]] == [
        ("mcp_json", "done"), ("registry", "skipped_newer"), ("agent", "done"), ("approve", "done")]
    assert body["actions"][1]["detail"] == f"the registration names build {ids[1]}, published later"
    assert "registry" not in fake.applied()
    assert P.pending_actions(rows_of(kb), ids[0]) == []


def test_replay_project_env_and_receipt(casefs, impl):
    project, kb, ids = world(casefs, 1)
    publish_ok(kb, ids[0])
    # open one action so that the target reaches the seam
    rows = rows_of(kb)
    log = kb / "PUBLISH.log"
    log.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows if r["action"] != "approve"),
                   encoding="utf-8")
    other = casefs / "elsewhere"
    receipt = casefs / "rp.json"
    impl.calls.clear()
    code, body = P.replay(kb, ids[0], project=other, env={"K": "v"}, receipt=receipt)
    assert impl.target().project == other and impl.target().env == {"K": "v"}
    assert receipt.read_bytes() == receipts.dumps(body)
    log.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows if r["action"] != "approve"),
                   encoding="utf-8")
    impl.calls.clear()
    P.replay(kb, ids[0], env={})
    assert impl.target().project == project and impl.target().env == {}


def test_replay_with_an_untrusted_log_is_exit_30(casefs, impl):
    project, kb, ids = world(casefs, 1)
    link_to(kb, ids[0])
    (kb / "PUBLISH.log").write_text("junk\n", encoding="utf-8")
    code, body = P.replay(kb, ids[0], env=ENV)
    assert (code, body["mode"], body["build_id"]) == (30, "replay", ids[0])


# --------------------------------------------------------------------------- prune


def lease_in(kb: Path, bid: str, pid: int, *, outside: bool = False, nonce: str = "n") -> Path:
    if outside:
        d = P.reader_lease_dir(kb, bid)
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{pid}-{nonce}"
        path.write_text("t\n", encoding="utf-8")
        return path
    return F.lease(kb / "builds" / bid, pid, nonce)


def test_prune_moves_the_unprotected_and_reports_everything(casefs):
    kb, ids = F.fake_kb(casefs, 6, current_to=3)
    F.write_rows(kb, F.published_rows(1, ids[2]) + F.published_rows(6, ids[3], prev=ids[2]))
    dead = F.dead_pid()
    lease_in(kb, ids[0], dead)
    code, body = P.prune(kb, 1, env=ENV)
    assert code == 0
    assert body == {"schema": "klode-kit.publish/1", "mode": "prune", "kb_id": "demo", "build_id": None,
                    "exit_code": 0, "reason": "ok", "keep": 1, "moved": [ids[0], ids[1], ids[4]], "returned": [],
                    "protected": {ids[3]: "current", ids[2]: "rollback_target", ids[5]: "recent"},
                    "cleaned_leases": 1}
    assert F.live_builds(kb) == [ids[2], ids[3], ids[5]]
    assert F.trash(kb) == [ids[0], ids[1], ids[4]]
    assert os.readlink(kb / "current") == f"builds/{ids[3]}"
    prune_rows = [r for r in rows_of(kb) if r["action"] == "prune"]
    assert [(r["target_build_id"], r["status"], r["prev_build_id"], r["detail"]) for r in prune_rows] == [
        (b, "done", None, f"moved to builds/_trash/{b}") for b in (ids[0], ids[1], ids[4])]
    assert "_trash" in os.listdir(kb / "builds")


def test_prune_default_keep_is_what_the_caller_gives(casefs):
    kb, ids = F.fake_kb(casefs, 4)
    code, body = P.prune(kb, 0, env=ENV)
    assert body["keep"] == 0 and body["moved"] == ids and body["protected"] == {}
    assert body["cleaned_leases"] == 0 and body["returned"] == []


def test_prune_a_trash_name_in_use_gets_a_counter_and_the_row_says_so(casefs):
    kb, ids = F.fake_kb(casefs, 2)
    (kb / "builds" / "_trash" / ids[0]).mkdir(parents=True)
    code, body = P.prune(kb, 1, env=ENV)
    assert body["moved"] == [ids[0]]
    row = [r for r in rows_of(kb) if r["action"] == "prune"][0]
    assert row["detail"] == f"moved to builds/_trash/{ids[0]}.1"
    assert F.trash(kb) == [ids[0], f"{ids[0]}.1"]


def test_prune_gives_a_build_back_that_got_a_reader_while_it_moved(casefs, monkeypatch):
    kb, ids = F.fake_kb(casefs, 3)
    real = P.move_to_trash

    def move(kb_, bid):
        dest = real(kb_, bid)
        if bid == ids[0]:  # a reader writes its lease into the build between the check and the move
            F.lease(dest, os.getpid())
        return dest

    monkeypatch.setattr(P, "move_to_trash", move)
    code, body = P.prune(kb, 1, env=ENV)
    assert code == 0 and body["returned"] == [ids[0]]
    assert body["moved"] == [ids[1]]  # the build after the returned one is still looked at (continue, not break)
    assert F.live_builds(kb) == [ids[0], ids[2]]
    assert F.trash(kb) == [ids[1]]
    assert [r["target_build_id"] for r in rows_of(kb) if r["action"] == "prune"] == [ids[1]]


def test_prune_gives_back_to_the_exact_name(casefs, monkeypatch):
    kb, ids = F.fake_kb(casefs, 2)
    real = P.move_to_trash

    def move(kb_, bid):
        dest = real(kb_, bid)
        lease_in(kb_, bid, os.getpid(), outside=True)
        return dest

    monkeypatch.setattr(P, "move_to_trash", move)
    code, body = P.prune(kb, 1, env=ENV)
    assert body["returned"] == [ids[0]] and body["moved"] == []
    assert sorted(os.listdir(kb / "builds")) == sorted([ids[0], ids[1], "_trash"])


def test_prune_protects_readers_in_both_places_and_cleans_the_dead(casefs):
    kb, ids = F.fake_kb(casefs, 5)
    dead = F.dead_pid()
    lease_in(kb, ids[0], os.getpid(), outside=True)
    lease_in(kb, ids[1], os.getpid())  # the old place
    lease_in(kb, ids[2], dead, outside=True)
    lease_in(kb, ids[3], dead)
    code, body = P.prune(kb, 1, env=ENV)
    assert body["protected"] == {ids[0]: "reader", ids[1]: "reader", ids[4]: "recent"}
    assert body["moved"] == [ids[2], ids[3]] and body["cleaned_leases"] == 2


def test_prune_receipt_and_lock_and_untrusted_log(casefs):
    kb, ids = F.fake_kb(casefs, 2)
    receipt = casefs / "pr.json"
    code, body = P.prune(kb, 1, receipt=receipt, env=ENV)
    assert receipt.read_bytes() == receipts.dumps(body)
    code, body = P.prune(kb, 1, receipt=str(receipt))
    assert json.loads(receipt.read_text(encoding="utf-8")) == body
    (kb / "PUBLISH.log").write_text("junk\n", encoding="utf-8")
    code, body = P.prune(kb, 1)
    assert (code, body["mode"], body["build_id"], body["reason"]) == (30, "prune", None, "contract_violation")
    assert F.live_builds(kb) == [ids[1]]  # the first prune moved ids[0]; the refused one moved nothing


def test_prune_without_builds_or_log(casefs):
    kb = casefs / "kb" / "empty"
    kb.mkdir(parents=True)
    code, body = P.prune(kb, 3, env=ENV)
    assert code == 0 and body["moved"] == [] and body["protected"] == {} and body["cleaned_leases"] == 0


# --------------------------------------------------------------------------- main: wiring


class Calls:
    def __init__(self, monkeypatch, result=(0, {"schema": "s"})):
        self.calls: list[tuple[str, tuple, dict]] = []
        self.result = result
        for name in ("publish", "replay", "rollback", "prune"):
            monkeypatch.setattr(P, name, self._make(name))

    def _make(self, name):
        def fn(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return self.result

        return fn


def kbdir(tmp: Path, name: str = "demo") -> Path:
    kb = tmp / "proj" / "kb" / name
    kb.mkdir(parents=True)
    return kb


def test_main_passes_the_arguments_of_each_mode_on(tmp_path, monkeypatch, capsys):
    kb = kbdir(tmp_path)
    c = Calls(monkeypatch)
    receipt = tmp_path / "r.json"
    assert P.main(["--kb", str(kb), "--build", A, "--receipt", str(receipt)]) == 0
    assert P.main(["--kb", str(kb), "--replay", B]) == 0
    assert P.main(["--kb", str(kb), "--rollback-to", C, "--project", str(tmp_path / "other")]) == 0
    assert P.main(["--kb", str(kb), "--prune", "--keep", "7", "--receipt", str(receipt)]) == 0
    assert P.main(["--kb", str(kb), "--prune"]) == 0
    (name1, a1, k1), (name2, a2, k2), (name3, a3, k3), (name4, a4, k4), (name5, a5, k5) = c.calls
    assert (name1, a1, k1) == ("publish", (kb, A), {"project": tmp_path / "proj", "receipt": receipt})
    assert (name2, a2, k2) == ("replay", (kb, B), {"project": tmp_path / "proj", "receipt": None})
    assert (name3, a3, k3) == ("rollback", (kb, C), {"project": tmp_path / "other", "receipt": None})
    assert (name4, a4, k4) == ("prune", (kb, 7), {"receipt": receipt})
    assert (name5, a5, k5) == ("prune", (kb, 3), {"receipt": None})
    for _, args, kwargs in c.calls:
        assert isinstance(args[0], Path)
        assert kwargs["receipt"] is None or isinstance(kwargs["receipt"], Path)
    assert isinstance(k1["project"], Path) and isinstance(k3["project"], Path)


def test_main_project_option_wins_over_the_parent_of_kb(tmp_path, monkeypatch):
    kb = kbdir(tmp_path)
    c = Calls(monkeypatch)
    other = tmp_path / "chosen"
    for flag, value in (("--build", A), ("--replay", A), ("--rollback-to", A)):
        assert P.main(["--kb", str(kb), flag, value, "--project", str(other)]) == 0
    assert [kw["project"] for _, _, kw in c.calls] == [other, other, other]


def test_main_prune_needs_no_project_and_publish_outside_kb_does(tmp_path, monkeypatch, capsys):
    lib = tmp_path / "libs" / "demo"
    lib.mkdir(parents=True)
    c = Calls(monkeypatch)
    assert P.main(["--kb", str(lib), "--prune"]) == 0
    assert c.calls[0][0] == "prune"
    capsys.readouterr()
    assert P.main(["--kb", str(lib), "--build", A]) == 2
    cap = capsys.readouterr()
    msg = "--project is needed: the library is not below a kb/ directory"
    assert cap.err == f"kb-publish: {msg}\n"
    assert json.loads(cap.out) == {"schema": "klode-kit.publish/1", "exit_code": 2, "reason": "usage", "detail": msg}
    assert len(c.calls) == 1
    assert P.main(["--kb", str(lib), "--build", A, "--project", str(tmp_path)]) == 0


def usage_out(capsys, argv) -> tuple[int, str, str]:
    code = P.main(argv)
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def test_main_usage_bodies(tmp_path, monkeypatch, capsys):
    kb = kbdir(tmp_path)
    c = Calls(monkeypatch)
    cases = [
        ([ "--kb", str(tmp_path / "nope"), "--prune"], f"--kb is not a directory: {tmp_path / 'nope'}"),
        (["--kb", str(kb), "--prune", "--keep", "-1"], "--keep must be 0 or more"),
        (["--kb", str(kb), "--build", "b-bad"], "not a build id: 'b-bad'"),
        (["--kb", str(kb), "--replay", ""], "not a build id: None"),
        (["--kb", str(kb), "--rollback-to", A + "x"], f"not a build id: {A + 'x'!r}"),
    ]
    for argv, message in cases:
        code, out, err = usage_out(capsys, argv)
        assert code == 2, argv
        assert err == f"kb-publish: {message}\n", argv
        assert out == json.dumps({"schema": "klode-kit.publish/1", "exit_code": 2, "reason": "usage",
                                  "detail": message}, sort_keys=True) + "\n", argv
        assert list(json.loads(out)) == ["detail", "exit_code", "reason", "schema"]  # sorted keys on the wire
    assert c.calls == []


def test_main_a_file_is_not_a_kb_directory(tmp_path, monkeypatch, capsys):
    f = tmp_path / "file"
    f.write_text("x", encoding="utf-8")
    Calls(monkeypatch)
    code, out, err = usage_out(capsys, ["--kb", str(f), "--prune"])
    assert code == 2 and err == f"kb-publish: --kb is not a directory: {f}\n"


def test_main_keep_zero_is_allowed(tmp_path, monkeypatch):
    kb = kbdir(tmp_path)
    c = Calls(monkeypatch)
    assert P.main(["--kb", str(kb), "--prune", "--keep", "0"]) == 0
    assert c.calls[0][1] == (kb, 0)


def test_main_argparse_errors_are_exit_2_and_help_is_exit_0(tmp_path, monkeypatch, capsys):
    kb = kbdir(tmp_path)
    c = Calls(monkeypatch)
    code, out, err = usage_out(capsys, ["--build", A])
    assert code == 2 and out == "" and "the following arguments are required: --kb" in err
    code, out, err = usage_out(capsys, ["--kb", str(kb)])
    assert code == 2 and out == "" and "one of the arguments --build --replay --rollback-to --prune is required" in err
    code, out, err = usage_out(capsys, ["--kb", str(kb), "--build", A, "--prune"])
    assert code == 2 and "not allowed with argument" in err
    code, out, err = usage_out(capsys, ["--kb", str(kb), "--prune", "--keep", "many"])
    assert code == 2 and "invalid int value: 'many'" in err
    code, out, err = usage_out(capsys, ["--kb", str(kb), "--prune", "--bogus"])
    assert code == 2
    code, out, err = usage_out(capsys, ["--help"])
    assert code == 0 and err == "" and out.startswith("usage: ")
    assert c.calls == []


def test_main_help_text(capsys):
    code, out, err = usage_out(capsys, ["--help"])
    assert code == 0
    flat = " ".join(out.split())
    assert flat.startswith("usage: klode-kit kb-publish [-h] --kb KB [--project PROJECT]")
    assert "(--build ID | --replay ID | --rollback-to ID | --prune)" in flat
    assert "[--keep KEEP] [--receipt RECEIPT]" in flat
    assert flat.index("[--receipt RECEIPT] publish protocol of a library options:") > 0
    for fragment in (
            "--kb KB library root kb/<id> (holds builds/, current, PUBLISH.log)",
            "--project PROJECT data project (default: the parent of kb/)",
            "--build ID publish this checked build",
            "--replay ID do the open registrations of the current build",
            "--rollback-to ID point back to this older build",
            "--prune move unneeded builds to builds/_trash/",
            "--keep KEEP prune: newest N builds to keep (default 3)",
            "--receipt RECEIPT also write the JSON summary to this file"):
        assert fragment in flat, fragment


def test_main_prints_problems_detail_actions_and_the_body(tmp_path, monkeypatch, capsys):
    kb = kbdir(tmp_path)
    body = {"schema": "s", "problems": ["first problem", "second problem"], "detail": "caf\u00e9 \u77e5",
            "actions": [{"action": "mcp_json", "status": "done", "detail": ""},
                        {"action": "registry", "status": "failed", "detail": "boom"},
                        {"action": "skipped", "status": "skipped_newer", "detail": "later"}],
            "zeta": 1, "alpha": "\u00fc"}
    Calls(monkeypatch, (21, body))
    code, out, err = usage_out(capsys, ["--kb", str(kb), "--build", A])
    assert code == 21
    assert err == ("kb-publish: refused: first problem\nkb-publish: refused: second problem\n"
                   "kb-publish: caf\u00e9 \u77e5\n"
                   "kb-publish: done          mcp_json\n"
                   "kb-publish: failed        registry  boom\n"
                   "kb-publish: skipped_newer skipped  later\n")
    assert out == json.dumps(body, sort_keys=True, ensure_ascii=False) + "\n"
    assert list(json.loads(out)) == sorted(body)  # keys in sorted order on the wire
    assert "\u00fc" in out and "\\u00fc" not in out


def test_main_prints_nothing_extra_for_a_plain_body(tmp_path, monkeypatch, capsys):
    kb = kbdir(tmp_path)
    Calls(monkeypatch, (0, {"detail": "", "schema": "s", "actions": []}))
    code, out, err = usage_out(capsys, ["--kb", str(kb), "--replay", A])
    assert code == 0 and err == "" and out == '{"actions": [], "detail": "", "schema": "s"}\n'
    Calls(monkeypatch, (0, {"schema": "s"}))
    code, out, err = usage_out(capsys, ["--kb", str(kb), "--prune"])
    assert err == "" and out == '{"schema": "s"}\n'


def test_main_returns_the_code_of_the_command(tmp_path, monkeypatch):
    kb = kbdir(tmp_path)
    for code in (0, 20, 21, 22, 30, 40):
        Calls(monkeypatch, (code, {"schema": "s"}))
        assert P.main(["--kb", str(kb), "--build", A]) == code


def test_main_end_to_end_with_the_real_commands(casefs, impl, capsys):
    project, kb, ids = world(casefs, 2)
    receipt = casefs / "e2e.json"
    code, out, err = usage_out(capsys, ["--kb", str(kb), "--build", ids[0], "--receipt", str(receipt)])
    assert code == 0
    body = json.loads(out)
    assert body["mode"] == "publish" and body["build_id"] == ids[0] and receipt.read_bytes() == receipts.dumps(body)
    assert err.splitlines()[0] == "kb-publish: done          switch"
    assert err.splitlines()[1] == "kb-publish: done          mcp_json  mcp_json done"
    code, out, err = usage_out(capsys, ["--kb", str(kb), "--build", ids[1]])
    code, out, err = usage_out(capsys, ["--kb", str(kb), "--build", C])
    assert code == 30 and "kb-publish: refused: no such build: builds/" + C in err
    code, out, err = usage_out(capsys, ["--kb", str(kb), "--replay", ids[0]])
    assert code == 22 and f"kb-publish: current is {ids[1]}, not {ids[0]}\n" == err
    code, out, err = usage_out(capsys, ["--kb", str(kb), "--rollback-to", ids[0]])
    assert code == 0 and P.current_build(kb) == ids[0]
    code, out, err = usage_out(capsys, ["--kb", str(kb), "--prune", "--keep", "1"])
    assert code == 0 and json.loads(out)["protected"] == {ids[0]: "current", ids[1]: "rollback_target"}


def test_the_module_constants_are_what_the_documentation_says():
    assert P.SCHEMA == "klode-kit.publish/1" and P.LOG_NAME == "PUBLISH.log"
    assert P.ACTIONS == ("mcp_json", "registry", "agent", "approve")
    assert P.POINTER_ACTIONS == ("switch", "rollback") and P.SETTLED == ("done", "skipped_newer")
    assert P.USAGE_EXIT == EXIT_CODE[Reason.usage] == 2 and P.DEFAULT_KEEP == 3
    assert P.ACTION_IMPL is __import__("klode_kit.register", fromlist=["x"]).ACTION_IMPL
    assert set(P.ACTION_IMPL) == set(P.ACTIONS)
