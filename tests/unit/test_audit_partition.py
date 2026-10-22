"""WI-00s: durable-write partition judgment used by 00s-16 (pure function over an audit log).

Law (matrix WI-00s): cutting a run at its start, its end and every crashpoint, each segment
holds at most one durable write; durable = mutation whose target is under a durable root,
excluding os.devnull and any __pycache__ path.
"""

from __future__ import annotations

import json
import os

from hypothesis import given, settings
from hypothesis import strategies as st

from klode_kit.devtools.audit import partition, read_log
import pytest

pytestmark = pytest.mark.unit

ROOT = "/kk-root"
OTHER = "/kk-other"
CP = "klode_kit.crashpoint"


def w(path: str, ev: str = "open") -> dict:
    return {"ev": ev, "mut": True, "path": path, "pid": 1}


def r(path: str) -> dict:
    return {"ev": "open", "mut": False, "path": path, "pid": 1}


def cp(name: str) -> dict:
    return {"ev": CP, "name": name, "pid": 1}


def counts(p) -> list[int]:
    return [len(s.writes) for s in p.segments]


def test_empty_run_is_one_clean_segment():
    p = partition([], [ROOT])
    assert counts(p) == [0]
    assert p.violations == []
    assert p.crashpoint_names == frozenset()


def test_one_write_per_segment_is_clean():
    p = partition([w(f"{ROOT}/a"), cp("x"), w(f"{ROOT}/b", "os.replace"), cp("y"), w(f"{ROOT}/c", "os.mkdir")], [ROOT])
    assert counts(p) == [1, 1, 1]
    assert p.violations == []
    assert [(s.start, s.end) for s in p.segments] == [(None, "x"), ("x", "y"), ("y", None)]


def test_two_writes_in_head_segment_violate():
    p = partition([w(f"{ROOT}/a"), w(f"{ROOT}/b"), cp("x"), w(f"{ROOT}/c")], [ROOT])
    assert [(s.start, s.end, len(s.writes)) for s in p.violations] == [(None, "x", 2)]


def test_two_writes_in_tail_segment_violate():
    p = partition([cp("x"), w(f"{ROOT}/a"), w(f"{ROOT}/b", "os.rename")], [ROOT])
    assert [(s.start, s.end, len(s.writes)) for s in p.violations] == [("x", None, 2)]


def test_atomic_write_without_crashpoint_between_violates():
    """mkstemp + os.replace in one segment is two durable writes (matrix 0.3 v7 note 3)."""
    p = partition([cp("a"), w(f"{ROOT}/work/.tmp123"), w(f"{ROOT}/work/state.json", "os.rename"), cp("b")], [ROOT])
    assert [(s.start, s.end) for s in p.violations] == [("a", "b")]


def test_exclusions_and_root_boundary():
    events = [
        w(f"{ROOT}/__pycache__/m.cpython-312.pyc"),
        w(f"{ROOT}/pkg/__pycache__"),
        w(os.devnull),
        w(f"{ROOT}2/x"),  # sibling sharing a prefix is NOT under the root
        w(f"{OTHER}/x"),
        r(f"{ROOT}/read-only"),
        w(ROOT, "os.chmod"),  # the root itself counts
    ]
    p = partition(events, [ROOT])
    assert counts(p) == [1]
    assert p.segments[0].writes[0]["path"] == ROOT


def test_loop_crashpoint_counts_as_one_name():
    p = partition([cp("loop"), w(f"{ROOT}/1"), cp("loop"), w(f"{ROOT}/2"), cp("end")], [ROOT])
    assert p.crashpoint_names == frozenset({"loop", "end"})
    assert p.crashpoints == ["loop", "loop", "end"]
    assert p.violations == []


def test_multiple_roots():
    p = partition([w(f"{ROOT}/a"), w(f"{OTHER}/b")], [ROOT, OTHER])
    assert counts(p) == [2]
    assert len(p.violations) == 1


durable_ev = st.builds(
    lambda name, ev: w(f"{ROOT}/{name}", ev),
    st.sampled_from(["a", "b", "work/c", "builds/d"]),
    st.sampled_from(["open", "os.rename", "os.mkdir", "os.remove", "os.symlink", "os.chmod"]),
)
noise_ev = st.one_of(
    st.builds(lambda n: w(f"{OTHER}/{n}"), st.sampled_from(["x", "y"])),
    st.builds(lambda n: r(f"{ROOT}/{n}"), st.sampled_from(["x", "y"])),
    st.just(w(f"{ROOT}/__pycache__/z.pyc")),
    st.just(w(os.devnull)),
    st.just({"ev": "subprocess.Popen", "argv": ["x"], "pid": 1}),
)
cp_ev = st.builds(cp, st.sampled_from(["p", "q", "r"]))
event_logs = st.lists(st.one_of(durable_ev, noise_ev, cp_ev), max_size=30)


def is_durable(e: dict) -> bool:
    return (
        e.get("mut") is True
        and str(e.get("path", "")).startswith(ROOT + "/")
        and "__pycache__" not in str(e["path"])
    )


@settings(max_examples=300)
@given(events=event_logs)
def test_partition_conserves_writes(events):
    """S4: segments = crashpoints + 1, and the segments' writes are exactly the durable events, in order."""
    p = partition(events, [ROOT])
    assert len(p.segments) == sum(1 for e in events if e["ev"] == CP) + 1
    assert [x for s in p.segments for x in s.writes] == [e for e in events if is_durable(e)]
    assert p.violations == [s for s in p.segments if len(s.writes) > 1]


@settings(max_examples=300)
@given(events=event_logs)
def test_partition_ignores_non_durable_noise(events):
    """S6 (metamorphic): deleting every non-durable event changes nothing."""
    signal = [e for e in events if e["ev"] == CP or is_durable(e)]
    a, b = partition(events, [ROOT]), partition(signal, [ROOT])
    assert counts(a) == counts(b)
    assert [(s.start, s.end) for s in a.violations] == [(s.start, s.end) for s in b.violations]


@settings(max_examples=300)
@given(events=event_logs)
def test_crashpoint_after_every_write_clears_violations(events):
    """S6: inserting a crashpoint after every durable write yields a clean partition."""
    out = []
    for e in events:
        out.append(e)
        if is_durable(e):
            out.append(cp("added"))
    assert partition(out, [ROOT]).violations == []


def test_read_log_roundtrip(tmp_path):
    events = [cp("a"), w(f"{ROOT}/x"), {"ev": "subprocess.Popen", "argv": ["a", "b"], "pid": 2}]
    log = tmp_path / "audit.jsonl"
    log.write_text("".join(json.dumps(e) + "\n" for e in events))
    assert read_log(log) == events


def test_read_log_missing_file_is_empty(tmp_path):
    assert read_log(tmp_path / "absent.jsonl") == []


def test_unresolved_mutation_target_counts_as_durable():
    """A mutation the hook could not attribute to a path must not vanish (no false green)."""
    unknown = {"ev": "os.truncate", "mut": True, "path": None, "unresolved": True, "pid": 1}
    p = partition([w(f"{ROOT}/a"), unknown], [ROOT])
    assert counts(p) == [2]
    assert len(p.violations) == 1
