"""WI-34 (cli_integration): the whole adopt path with the real klode 0.5.0, up to ``klode check --strict`` 0/0 (paired
real path of ``tests/contract/test_adopt_wi34.py``).

An old library (three shelves, cards below the scaffold marker only, a desk with holdout and syntheses) built by the
real ``klode`` is adopted into ``kb/demo-adopt``: 13 steps PASS, strict ``0 errors, 0 warning(s)`` when run again by
hand on the sealed tree, every old anchor FOUND by ``klode verify`` in the new build, ``check-published --all`` and the
manifest verify, the expert agent points at ``kb/demo-adopt/current``, and the old tree is unchanged. A missing
``klode`` FAILS (never skips).
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from _support import adopt_fx as AF
from _support import build_fx as B
from _support import desk_fx as DX
from _support.flows import REPO
from klode_kit.build import run_build as RB

pytestmark = pytest.mark.cli_integration


def _klode(build, *args, json_mode=False):
    exe = shutil.which("klode")
    assert exe, "klode 0.5.0 is required on PATH for the cli_integration lane"
    pre = ["--json"] if json_mode else []
    return subprocess.run([exe, *pre, "-c", str(build / "library.toml"), *args], capture_output=True, text=True,
                          timeout=300)


def test_adopt_full_path_to_strict_zero_zero(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    old = AF.old_kb(tmp_path / "old", cards="scaffold", desk=True)
    old_strict = _klode(old, "check", "--strict")  # the old library itself is a clean klode library
    assert old_strict.returncode == 0, old_strict.stdout
    before_old = AF.tree_hash(old)
    env = AF.fake_env(tmp_path)
    res = AF.adopt(project, tmp_path, old, extra_env=env)
    assert res.returncode == 0, res.stderr.decode()
    (bd,) = B.builds(project)
    rec = B.receipt(bd)
    assert [s["step"] for s in rec["steps"]] == list(RB.STEPS)
    assert [s["status"] for s in rec["steps"]] == ["PASS"] * 13 and rec["status"] == "PASS"
    strict = _klode(bd, "check", "--strict")
    assert strict.returncode == 0 and "0 errors, 0 warning(s)" in strict.stdout, strict.stdout
    for cid in AF.CARDS:
        verified = _klode(bd, "--json", "verify", cid, DX.ANCHORS[cid][0])
        assert verified.returncode == 0 and json.loads(verified.stdout)["value"]["resolution"] == "found"
    published = subprocess.run([str(REPO / "bin" / "klode-kit"), "check-published", "--all", "--kb", str(bd), "--json"],
                               capture_output=True, text=True, cwd=project, timeout=300)
    assert published.returncode == 0 and json.loads(published.stdout)["pass"] is True
    assert RB.verify_manifest(bd) == []
    agent = bd / "agent" / "demo-adopt-klode-expert.md"
    assert agent.is_file() and "kb/demo-adopt/current" in agent.read_text(encoding="utf-8")
    assert AF.claude_calls(env) == 0
    assert AF.tree_hash(old) == before_old


def test_adopt_one_shelf_of_a_multi_shelf_library_to_strict_zero_zero(tmp_path):
    """D-45: ``--adopt-shelves alpha``: only that shelf's source and card; the others are absent; the desk is written
    again (the old one names cards of other shelves) - first build exit 20, desk-tasks, workers, second build with
    ``--syntheses`` and ``--repair`` - and the sealed tree passes ``klode check --strict`` 0 errors, 0 warnings."""
    from pathlib import Path

    from klode_kit.build.check_synthesis import ANCHOR_RE

    project = tmp_path / "proj"
    project.mkdir()
    old = AF.old_kb(tmp_path / "old", cards="scaffold", desk=True)
    before_old = AF.tree_hash(old)
    env = AF.fake_env(tmp_path)
    first = AF.adopt(project, tmp_path, old, "--adopt-shelves", "alpha", extra_env=env)
    assert first.returncode == 20, first.stderr.decode()
    (failed,) = B.builds(project)
    syn = tmp_path / "work" / "syntheses"
    planned = subprocess.run([str(REPO / "bin" / "klode-kit"), "desk-tasks", "--build", str(failed), "--out", str(syn),
                              "--project", str(project)], capture_output=True, text=True, timeout=300)
    assert planned.returncode == 0, planned.stderr
    for t in json.loads(planned.stdout)["tasks"]:
        anchors: list[str] = []
        for cid in t["cards"]:
            anchors += list(dict.fromkeys(ANCHOR_RE.findall((failed / "library" / "cards" / f"{cid}.md").read_text(encoding="utf-8"))))
        Path(t["output"]).write_text(DX.synthesis(t["dimension"], t["cards"], anchors[:4]), encoding="utf-8")
    second = AF.adopt(project, tmp_path, old, "--adopt-shelves", "alpha", "--syntheses", str(syn), "--repair",
                      str(syn / "repair.json"), extra_env=env)
    assert second.returncode == 0, second.stderr.decode()
    (bd,) = [b for b in B.builds(project) if b != failed]
    assert [s["status"] for s in B.receipt(bd)["steps"]] == ["PASS"] * 13
    strict = _klode(bd, "check", "--strict")
    assert strict.returncode == 0 and "0 errors, 0 warning(s)" in strict.stdout, strict.stdout
    assert not (bd / "library" / "beta").exists() and not (bd / "library" / "gamma").exists()
    assert sorted(p.name for p in (bd / "library").glob("*.txt")) == [] and (bd / "library" / "alpha" / "ch01.txt").is_file()
    assert sorted(p.stem for p in (bd / "library" / "cards").glob("*.md") if p.stem != "INDEX") == ["ch01"]
    found = _klode(bd, "--json", "verify", "ch01", DX.ANCHORS["ch01"][0])
    assert found.returncode == 0 and json.loads(found.stdout)["value"]["resolution"] == "found"
    assert AF.claude_calls(env) == 0 and AF.tree_hash(old) == before_old
