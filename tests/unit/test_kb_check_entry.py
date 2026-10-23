"""WI-00d2 (R2-11, 00d2-07 deterministic part): the minimal ``/klode-kit:kb-check`` entry receipt.

``klode-kit kb-check --kb <kb_root> --entry <entry>`` (spike scope: only the plugin block is real; the build
checks of WI-13a are reported as not run):

- reads ``<kb_root>/current`` (symlink to ``builds/<build_id>``) - missing or dangling: exit 30, nothing written;
- writes ``builds/<build_id>/check_receipt.json`` (schema ``check_receipt``) and prints the same JSON;
- ``plugin = {name, version}`` from the plugin root's ``.claude-plugin/plugin.json``, ``entry`` as given,
  ``root_sha`` = R3-08 release sha over the run files (``.claude-plugin/plugin.json``, ``skills/**``,
  ``agents/**``, ``workflows/**``, ``hooks/**``, ``bin/**``, ``src/**``, ``prompts/**``, ``schemas/**``,
  ``config/**``, ``pyproject.toml``, ``uv.lock``): ``sha256`` over ``sorted(relpath)`` of
  ``sha256(relpath + "\\0" + content)``. Byte-code caches (``__pycache__``, ``*.pyc``) and ``.DS_Store`` are not
  run files and are skipped.

Law (root_sha): depends only on the (relpath, content) set of run files - independent of creation order and of
non-run files (S4/S6); moving content between two run files changes it (relpath is bound).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from _support.flows import REPO, base_env, run_module
from klode_kit import kb_check
from klode_kit.contract import schema as contract

pytestmark = pytest.mark.unit

ENTRY = "skill:/klode-kit:kb-check"
BUILD = "b-20261023T000000Z-0123abcd"


def _plugin(root: Path, name: str = "klode-kit", version: str = "0.1.0-spike") -> Path:
    (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": name, "version": version}), encoding="utf-8")
    (root / "skills" / "kb-check").mkdir(parents=True, exist_ok=True)
    (root / "skills" / "kb-check" / "SKILL.md").write_text("---\nname: kb-check\n---\nbody\n", encoding="utf-8")
    (root / "src" / "pkg").mkdir(parents=True, exist_ok=True)
    (root / "src" / "pkg" / "m.py").write_text("x = 1\n", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    return root


def _kb(root: Path, link: bool = True) -> Path:
    (root / "builds" / BUILD).mkdir(parents=True)
    if link:
        os.symlink(Path("builds") / BUILD, root / "current")
    return root


def test_plugin_block_reads_manifest(tmp_path):
    root = _plugin(tmp_path / "plug", name="klode-kit", version="9.8.7")
    block = kb_check.plugin_block(root, ENTRY)
    assert block["name"] == "klode-kit"
    assert block["version"] == "9.8.7"
    assert block["entry"] == ENTRY
    assert len(block["root_sha"]) == 64 and int(block["root_sha"], 16) >= 0


def test_root_sha_ignores_non_run_files(tmp_path):
    root = _plugin(tmp_path / "plug")
    before = kb_check.root_sha(root)
    (root / "README.md").write_text("docs", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "t.py").write_text("assert 1", encoding="utf-8")
    (root / "src" / "pkg" / "__pycache__").mkdir()
    (root / "src" / "pkg" / "__pycache__" / "m.cpython-312.pyc").write_bytes(b"\x00\x01")
    (root / "skills" / ".DS_Store").write_bytes(b"ds")
    assert kb_check.root_sha(root) == before


@pytest.mark.parametrize("rel", ["src/pkg/m.py", ".claude-plugin/plugin.json", "skills/kb-check/SKILL.md", "pyproject.toml"])
def test_root_sha_changes_with_run_file_content(tmp_path, rel):
    root = _plugin(tmp_path / "plug")
    before = kb_check.root_sha(root)
    with open(root / rel, "a", encoding="utf-8") as fh:
        fh.write("\n")
    assert kb_check.root_sha(root) != before


def test_root_sha_binds_relpath(tmp_path):
    """Moving content between two run files changes the sha (relpath is part of each entry)."""
    a = _plugin(tmp_path / "a")
    (a / "bin").mkdir()
    (a / "bin" / "one").write_text("AAA", encoding="utf-8")
    (a / "bin" / "two").write_text("BBB", encoding="utf-8")
    b = _plugin(tmp_path / "b")
    (b / "bin").mkdir()
    (b / "bin" / "one").write_text("BBB", encoding="utf-8")
    (b / "bin" / "two").write_text("AAA", encoding="utf-8")
    assert kb_check.root_sha(a) != kb_check.root_sha(b)


_FILES = st.dictionaries(
    st.sampled_from(["src/a.py", "src/sub/b.py", "agents/w.md", "workflows/p.js", "bin/klode-kit", "config/d.toml",
                     "schemas/s.json", "uv.lock", "hooks/h.json", "prompts/p.txt"]),
    st.binary(max_size=40), min_size=1)


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(files=_FILES, seed=st.randoms(use_true_random=False))
def test_root_sha_order_independent(tmp_path_factory, files, seed):
    """S4: the same (relpath, content) set written in two different orders has the same sha."""
    roots = []
    for _ in range(2):
        root = _plugin(tmp_path_factory.mktemp("p"))
        items = list(files.items())
        seed.shuffle(items)
        for rel, data in items:
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_bytes(data)
        roots.append(root)
    assert kb_check.root_sha(roots[0]) == kb_check.root_sha(roots[1])


def test_cli_writes_schema_valid_receipt(tmp_path):
    kb = _kb(tmp_path / "kb" / "fx-labs-3x")
    proc = run_module("klode_kit", ["kb-check", "--kb", str(kb), "--entry", ENTRY], base_env())
    assert proc.returncode == 0, proc.stderr.decode()
    written = json.loads((kb / "builds" / BUILD / "check_receipt.json").read_text(encoding="utf-8"))
    assert json.loads(proc.stdout) == written
    assert contract.issues("check_receipt", written) == []
    assert written["build_id"] == BUILD
    assert written["kb_id"] == "fx-labs-3x"
    assert written["u5_ready"] is False
    manifest = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert written["plugin"] == {"name": manifest["name"], "version": manifest["version"], "entry": ENTRY,
                                 "root_sha": kb_check.root_sha(REPO)}


@pytest.mark.parametrize("link", [False, True], ids=["no-current", "dangling-current"])
def test_cli_unpublished_kb_exit_30(tmp_path, link):
    kb = tmp_path / "kb"
    kb.mkdir()
    if link:
        os.symlink(Path("builds") / BUILD, kb / "current")
    proc = run_module("klode_kit", ["kb-check", "--kb", str(kb), "--entry", ENTRY], base_env())
    assert proc.returncode == 30
    assert not list(kb.rglob("check_receipt.json"))


def test_cli_usage(tmp_path):
    assert run_module("klode_kit", ["kb-check"], base_env()).returncode == 2


def test_cli_build_id_not_conforming_exit_30(tmp_path):
    """Added after red: a current pointer to a build whose name breaks the build_id pattern is not published."""
    kb = tmp_path / "kb"
    (kb / "builds" / "not-a-build-id").mkdir(parents=True)
    os.symlink(Path("builds") / "not-a-build-id", kb / "current")
    proc = run_module("klode_kit", ["kb-check", "--kb", str(kb), "--entry", ENTRY], base_env())
    assert proc.returncode == 30
    assert b"Traceback" not in proc.stderr
    assert not list(kb.rglob("check_receipt.json"))
