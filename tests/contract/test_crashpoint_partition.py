"""WI-00s: crashpoints partition durable writes (matrix 00s-16, section 0.3 v6-v8).

Infrastructure under test:
- ``crashpoint()`` always emits ``sys.audit("klode_kit.crashpoint", name)``;
- ``sitecustomize`` (PYTHONPATH-injected) installs the audit hook only in project processes
  (``KLODE_KIT_AUDIT_ROLE=1`` and ``__main__`` is ``klode_kit`` or a submodule), merging events
  of project Python descendants into one log;
- ``klode_kit.devtools.audit.partition`` cuts the log at start, end and every crashpoint.

Synthetic flows (``klode_kit.devtools.synthflow``) exercise the infrastructure now. The four
real flows of 00s-16, each paired 1:1 with an enumeration list, are strict xfails until the
WIs that implement them supply a runner and the list (tests/_support/flows.py REAL_FLOWS).

synthflow ``partition-ok`` (the specification the synthetic flow must follow):
  mkdir work | cp synth.after_mkdir | mkstemp in work | cp synth.before_replace | os.replace
  | 2x (cp synth.loop | write item) | cp synth.before_symlink | symlink work/current
  | cp synth.before_child | project child writes work/child.txt; a foreign ``python -c`` child
  writes work/foreign.txt; noise: private mkdtemp under TMPDIR, os.devnull, root/__pycache__.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from _support.flows import REAL_FLOWS, audit_env, run_module, run_synthflow
from klode_kit.devtools.audit import partition, read_log

pytestmark = pytest.mark.contract_replay

EXPECTED_OK = frozenset(
    {"synth.after_mkdir", "synth.before_replace", "synth.loop", "synth.before_symlink", "synth.before_child"}
)
MUTATION_KINDS = {"open", "os.rename", "os.symlink", "os.link", "os.remove", "os.mkdir", "os.rmdir", "os.truncate", "os.chmod"}


def _real(p: Path) -> str:
    return os.path.realpath(p)


def _run(tmp_path: Path, scenario: str, **extra_env: str):
    root = tmp_path / "proj"
    root.mkdir()
    log = tmp_path / "audit.jsonl"
    res = run_synthflow(scenario, root, audit_env(log, extra_env or None), timeout=60)
    assert res.returncode == 0, res.stderr.decode()
    return root, read_log(log), res


def _durable_count(p) -> int:
    return sum(len(s.writes) for s in p.segments)


def test_synthetic_flow_partition_holds(tmp_path):
    root, events, _ = _run(tmp_path, "partition-ok")
    p = partition(events, [root])

    assert p.violations == []
    assert p.crashpoint_names == EXPECTED_OK
    assert p.crashpoints.count("synth.loop") == 2
    assert _durable_count(p) == 7  # mkdir, mkstemp, replace, 2 items, symlink, child.txt

    rroot = _real(root)
    paths = {e.get("path") for e in events}
    # the project child's write is merged into the same log
    child = [e for e in events if e.get("path") == f"{rroot}/work/child.txt" and e.get("mut")]
    assert len(child) == 1
    assert child[0]["pid"] != p.segments[0].writes[0]["pid"]
    # the foreign (non-klode_kit) child's write happened but is a blind spot
    assert (root / "work" / "foreign.txt").read_text() == "foreign"
    assert f"{rroot}/work/foreign.txt" not in paths
    # noise is observed, yet excluded from durable writes
    tmpdirs = [e["path"] for e in events if e["ev"] == "tempfile.mkdtemp"]
    assert tmpdirs and all(not t.startswith(rroot + os.sep) for t in tmpdirs)
    assert any("__pycache__" in str(e.get("path")) and e.get("mut") for e in events)
    assert any(e.get("path") == os.devnull and e.get("mut") for e in events)


@pytest.mark.parametrize(
    ("scenario", "where"),
    [
        ("partition-bad-head", [(None, "synth.first")]),
        ("partition-bad-mid", [("synth.a", "synth.b")]),
        ("partition-bad-tail", [("synth.last", None)]),
        ("partition-bad-child", [("synth.before_child", None)]),
    ],
)
def test_synthetic_violations_are_located(tmp_path, scenario, where):
    root, events, _ = _run(tmp_path, scenario)
    p = partition(events, [root])
    assert [(s.start, s.end) for s in p.violations] == where
    assert all(len(s.writes) == 2 for s in p.violations)


def test_bad_child_writes_come_from_descendant(tmp_path):
    root, events, _ = _run(tmp_path, "partition-bad-child")
    p = partition(events, [root])
    parent_pid = next(e["pid"] for e in events if e["ev"] == "klode_kit.crashpoint")
    assert {w["pid"] for w in p.violations[0].writes} - {parent_pid} != set()


def test_hook_records_every_mutation_kind(tmp_path):
    """F19: the hook observes all mutation shapes, not one event form; fd targets are resolved to paths."""
    root, events, _ = _run(tmp_path, "ops")
    p = partition(events, [root])
    kinds = {w["ev"] for s in p.segments for w in s.writes}
    assert MUTATION_KINDS <= kinds
    assert p.violations == []
    rroot = _real(root)
    truncs = [w["path"] for s in p.segments for w in s.writes if w["ev"] == "os.truncate"]
    assert truncs == [f"{rroot}/t.bin", f"{rroot}/t.bin"]  # path form and fd form


def test_crashpoint_audit_event_is_unconditional(tmp_path):
    """Section 0.3 v6: the audit event is emitted in production mode too (no test seams)."""
    root = tmp_path / "proj"
    root.mkdir()
    log = tmp_path / "audit.jsonl"
    env = audit_env(log, {"KLODE_KIT_REAL": "1"}, drop=("KLODE_KIT_TEST_SEAMS",))
    res = run_synthflow("partition-ok", root, env, timeout=60)
    assert res.returncode == 0, res.stderr.decode()
    assert res.stderr == b""
    assert partition(read_log(log), [root]).crashpoint_names == EXPECTED_OK


def test_hook_not_installed_without_role(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    log = tmp_path / "audit.jsonl"
    env = audit_env(log, drop=("KLODE_KIT_AUDIT_ROLE",))
    res = run_synthflow("partition-ok", root, env, timeout=60)
    assert res.returncode == 0, res.stderr.decode()
    assert read_log(log) == []


@pytest.mark.parametrize("argv", [["-c", "import sys; open(sys.argv[1], 'w').write('x')"], ["-m", "klode_kitx"]])
def test_hook_not_installed_outside_project_main(tmp_path, argv):
    """v8 S1: only processes whose __main__ is klode_kit(.*) install the hook (klode CLI is a blind spot)."""
    log = tmp_path / "audit.jsonl"
    target = tmp_path / "x.txt"
    subprocess.run([sys.executable, *argv, str(target)], env=audit_env(log), capture_output=True, timeout=60)
    assert read_log(log) == []


def test_hook_installed_for_package_main(tmp_path):
    log = tmp_path / "audit.jsonl"
    res = run_module("klode_kit", ["--version"], audit_env(log), timeout=60)
    assert res.returncode == 0, res.stderr.decode()
    assert any(e["ev"] == "open" for e in read_log(log))


@pytest.mark.parametrize(
    "flow_name",
    [
        pytest.param(name, marks=pytest.mark.xfail(strict=True, reason=f"依赖 {flow.depends_on}（流程尚未实现，配对 {flow.paired_case}）"))
        for name, flow in REAL_FLOWS.items()
    ],
)
def test_crashpoints_partition_durable_writes(tmp_path, flow_name):
    """00s-16: each real flow, run normally under the audit hook, has <=1 durable write per segment
    (head and tail included) and its observed crashpoint names equal its paired enumeration list."""
    flow = REAL_FLOWS[flow_name]
    assert flow.runner is not None and flow.expected_crashpoints is not None, (
        f"{flow.name}: runner / enumeration list not provided yet ({flow.depends_on}, pairs with {flow.paired_case})"
    )
    log = tmp_path / "audit.jsonl"
    roots = flow.runner(tmp_path, audit_env(log))
    p = partition(read_log(log), roots)
    assert _durable_count(p) >= 1
    assert p.violations == []
    assert p.crashpoint_names == flow.expected_crashpoints
