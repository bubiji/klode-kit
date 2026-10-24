"""WI-09b: the pure parts and the argument checks of ``klode_kit.build.run_build``.

Laws: MANIFEST.sha256 is a complete, order-independent witness of a build tree (written -> verifies; any change,
addition or removal is reported); ``make_readonly`` leaves no write bit anywhere. The refusals before a build
directory exists (usage 2, environment 40) create nothing.
"""

from __future__ import annotations

import io
import json
import os
import stat
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from klode_kit.build import run_build as RB
from klode_kit.pipeline import Reason

pytestmark = pytest.mark.unit

NAME = st.text(alphabet="abcdefgh", min_size=1, max_size=4)
TREE = st.dictionaries(st.builds(lambda d, n: f"{d}/{n}.txt" if d else f"{n}.txt", st.one_of(st.just(""), NAME), NAME),
                       st.binary(max_size=40), min_size=1, max_size=6)


def _write_tree(root: Path, tree: dict[str, bytes]) -> None:
    for rel, data in tree.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


def _unlock(root: Path) -> None:
    for dirpath, _, files in os.walk(root):
        os.chmod(dirpath, 0o755)
        for f in files:
            os.chmod(Path(dirpath, f), 0o644)


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(TREE, st.data())
def test_manifest_is_a_complete_witness(tmp_path_factory, tree, data):
    root = tmp_path_factory.mktemp("tree")
    _write_tree(root, tree)
    RB.write_manifest(root)
    assert RB.verify_manifest(root) == []
    victim = data.draw(st.sampled_from(sorted(tree)))
    (root / victim).write_bytes(tree[victim] + b"!")
    assert RB.verify_manifest(root) == [f"changed: {victim}"]
    (root / victim).write_bytes(tree[victim])
    (root / "zz-extra.txt").write_text("x", encoding="utf-8")
    assert RB.verify_manifest(root) == ["unlisted: zz-extra.txt"]


def test_manifest_reports_a_missing_file_and_a_missing_manifest(tmp_path):
    _write_tree(tmp_path, {"a.txt": b"1", "d/b.txt": b"2"})
    assert RB.verify_manifest(tmp_path)[0].startswith("MANIFEST.sha256: cannot read")
    RB.write_manifest(tmp_path)
    (tmp_path / "d" / "b.txt").rename(tmp_path / "d" / "c.txt")
    assert RB.verify_manifest(tmp_path) == ["missing: d/b.txt", "unlisted: d/c.txt"]


def test_manifest_leaves_the_reader_leases_out(tmp_path):
    _write_tree(tmp_path, {"a.txt": b"1", ".readers/1-abc": b"t"})
    RB.write_manifest(tmp_path)
    assert ".readers" not in (tmp_path / RB.MANIFEST_NAME).read_text(encoding="utf-8")
    (tmp_path / ".readers" / "2-def").write_bytes(b"u")
    assert RB.verify_manifest(tmp_path) == []


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(TREE)
def test_make_readonly_leaves_no_write_bit(tmp_path_factory, tree):
    root = tmp_path_factory.mktemp("ro")
    _write_tree(root, tree)
    RB.make_readonly(root)
    try:
        bits = [p for p in [root, *root.rglob("*")] if p.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)]
        assert bits == []
    finally:
        _unlock(root)


def test_readers_directory_stays_writable(tmp_path):
    _write_tree(tmp_path, {"a.txt": b"1", ".readers/1-abc": b"t"})
    RB.make_readonly(tmp_path)
    try:
        (tmp_path / ".readers" / "2-def").write_bytes(b"u")  # a reader may still write its lease
        assert not os.access(tmp_path / "a.txt", os.W_OK)
    finally:
        _unlock(tmp_path)


@pytest.mark.parametrize("rc,one,expect", [
    (0, Reason.content_gate, Reason.ok), (1, Reason.content_gate, Reason.content_gate),
    (1, Reason.contract_violation, Reason.contract_violation), (20, Reason.environment, Reason.content_gate),
    (61, Reason.content_gate, Reason.isolation), (40, Reason.content_gate, Reason.environment),
    (127, Reason.content_gate, Reason.environment), (-9, Reason.content_gate, Reason.environment),
    (7, Reason.content_gate, Reason.environment)])
def test_rc_reason_table(rc, one, expect):
    assert RB.rc_reason(rc, one) is expect


def test_auth_failure_is_read_from_the_bridge_receipt(tmp_path):
    path = tmp_path / "run.json"
    path.write_text(json.dumps({"tasks": [{"error": "exit 1", "attempts_log": [{"error": "exit 1",
                                "stderr_tail": "Error: Not logged in · Please run /login"}]}]}), encoding="utf-8")
    assert RB._auth_failure(path) is True
    path.write_text(json.dumps({"tasks": [{"error": "card_missing", "attempts_log": [{"stderr_tail": "boom"}]}]}),
                    encoding="utf-8")
    assert RB._auth_failure(path) is False
    path.write_text("{", encoding="utf-8")
    assert RB._auth_failure(path) is False
    assert RB._auth_failure(tmp_path / "absent.json") is False


def test_default_kb_is_the_only_existing_one_else_the_project_name(tmp_path):
    project = tmp_path / "My Project_1"
    project.mkdir()
    assert RB.default_kb(project) == project / "kb" / "my-project-1"
    (project / "kb" / "one").mkdir(parents=True)
    assert RB.default_kb(project) == project / "kb" / "one"
    (project / "kb" / "two").mkdir()
    assert RB.default_kb(project) == project / "kb" / "my-project-1"


def _run(project, **kw):
    out, so = io.StringIO(), io.StringIO()
    code = RB.run(project, out=out, stdout=so, **kw)
    return code, json.loads(so.getvalue().splitlines()[-1]), out.getvalue()


def test_usage_errors_create_nothing(tmp_path):
    project = tmp_path / "p"
    project.mkdir()
    src = tmp_path / "s.txt"
    src.write_text("x", encoding="utf-8")
    cases = [
        dict(project=tmp_path / "nope"),
        dict(project=project, source=tmp_path / "missing.txt"),
        dict(project=project, manifest=src),
        dict(project=project, source=src, shelf="bad shelf"),
        dict(project=project, kb=tmp_path / "elsewhere"),
        dict(project=project, kb="kb/Not_A_Slug"),
        dict(project=project, syntheses=src),
    ]
    for kw in cases:
        code, doc, _ = _run(**kw)
        assert (code, doc["reason"]) == (2, "usage"), kw
    assert list(project.iterdir()) == []


def test_environment_refusals_create_nothing(tmp_path):
    project = tmp_path / "p"
    project.mkdir()
    code, doc, _ = _run(project, launcher=tmp_path / "no-launcher")
    assert (code, doc["detail"]) == (40, "launcher missing")
    code, doc, _ = _run(project, env={"PATH": str(tmp_path)})
    assert (code, doc["detail"]) == (40, "klode missing")
    (project / "klode-kit.toml").write_text("bogus = 1\n", encoding="utf-8")
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "klode").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (fake / "klode").chmod(0o755)
    code, doc, _ = _run(project, env={"PATH": str(fake)})
    assert (code, doc["reason"]) == (40, "config")
    assert sorted(p.name for p in project.iterdir()) == ["klode-kit.toml"]
