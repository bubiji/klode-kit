"""WI-33b: doctor's project_trust must not block local-only runs and must give a reachable fix (pilot finding P1).

Facts (Claude Code 2.1.285): a plain folder whose parent is trusted shows no trust dialog, so "cd <project> && claude"
can never set its own trust. Law: ``hostenv.trusted`` is unchanged (exact folder / git root only); the skip rule is
monotone - adding a route or a later stage never turns a required trust check into a skipped one.
Synthetic tmp HOME only.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from klode_kit import hostenv, pipeline
from klode_kit.check import doctor

pytestmark = pytest.mark.unit

GITIGNORE = "".join(f"{e}\n" for e in ("_work/", "_raw/", "_masked/", "_mask_review/", "_private/", "work/"))


def _project(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    return root


def _home(tmp_path: Path, monkeypatch, projects: dict) -> Path:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    (home / ".claude.json").write_text(json.dumps({"projects": projects}), encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    return home


def _trust(path: Path) -> dict:
    return {os.path.realpath(path): {"hasTrustDialogAccepted": True}}


def _run(project: Path, tmp_path: Path, *extra: str):
    out = tmp_path / "doctor.json"
    rc = doctor.main(["--project", str(project), "--only", "project", "--receipt", str(out), *extra])
    data = json.loads(out.read_text(encoding="utf-8"))
    return rc, data, {c["id"]: c for c in data["checks"]}["project_trust"]


# --------------------------------------------------------------------------- hostenv.trusted_ancestor


def test_trusted_ancestor_nearest_wins(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    a, b = tmp_path / "a", tmp_path / "a" / "b"
    (b / "data").mkdir(parents=True)
    cj = tmp_path / "c.json"
    cj.write_text(json.dumps({"projects": {**_trust(a), **_trust(b)}}), encoding="utf-8")
    assert hostenv.trusted_ancestor(b / "data", claude_json=cj, home=home) == Path(os.path.realpath(b))


def test_trusted_ancestor_excludes_self_and_none_when_absent(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    d = tmp_path / "x" / "data"
    d.mkdir(parents=True)
    cj = tmp_path / "c.json"
    cj.write_text(json.dumps({"projects": _trust(d)}), encoding="utf-8")
    assert hostenv.trusted_ancestor(d, claude_json=cj, home=home) is None


def test_trusted_ancestor_stops_before_home(tmp_path):
    home = tmp_path / "home"
    d = home / "work" / "data"
    d.mkdir(parents=True)
    cj = tmp_path / "c.json"
    cj.write_text(json.dumps({"projects": _trust(home)}), encoding="utf-8")
    assert hostenv.trusted_ancestor(d, claude_json=cj, home=home) is None


def test_trusted_ancestor_ignores_unaccepted_and_unreadable(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    p = tmp_path / "p"
    d = p / "data"
    d.mkdir(parents=True)
    cj = tmp_path / "c.json"
    cj.write_text(json.dumps({"projects": {os.path.realpath(p): {"hasTrustDialogAccepted": False}}}), encoding="utf-8")
    assert hostenv.trusted_ancestor(d, claude_json=cj, home=home) is None
    assert hostenv.trusted_ancestor(d, claude_json=tmp_path / "missing.json", home=home) is None


def test_trusted_semantics_unchanged_parent_does_not_count(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    d = tmp_path / "p" / "data"
    d.mkdir(parents=True)
    cj = tmp_path / "c.json"
    cj.write_text(json.dumps({"projects": _trust(tmp_path / "p")}), encoding="utf-8")
    assert hostenv.trusted(d, claude_json=cj, home=home) is False


# --------------------------------------------------------------------------- doctor skip / require


def test_local_only_routes_skip_trust_with_reason(tmp_path, monkeypatch, capsys):
    project = _project(tmp_path / "data")
    _home(tmp_path, monkeypatch, {})
    rc, data, trust = _run(project, tmp_path, "--routes", "vision,mlx", "--last-stage", "writeback")
    capsys.readouterr()
    assert rc == 0
    assert data["missing"] == 0
    assert trust["ok"] is True
    assert trust["skipped"] is True
    assert "vision,mlx" in trust["detail"] and "writeback" in trust["detail"]


@pytest.mark.parametrize("extra", [
    (),                                                              # bare doctor: as today
    ("--routes", "vision,claude", "--last-stage", "writeback"),     # cloud route
    ("--routes", "vision,mlx", "--last-stage", "build"),            # host/MCP stages
    ("--routes", "vision,mlx", "--last-stage", "publish"),
    ("--routes", "vision,mlx"),                                      # no stage bound -> all stages
    ("--last-stage", "writeback"),                                   # no routes -> all routes
])
def test_trust_still_required(tmp_path, monkeypatch, capsys, extra):
    project = _project(tmp_path / "data")
    _home(tmp_path, monkeypatch, {})
    rc, _, trust = _run(project, tmp_path, *extra)
    capsys.readouterr()
    assert rc == 40
    assert trust["ok"] is False
    assert "skipped" not in trust


def test_required_and_trusted_passes(tmp_path, monkeypatch, capsys):
    project = _project(tmp_path / "data")
    _home(tmp_path, monkeypatch, _trust(project))
    rc, _, trust = _run(project, tmp_path, "--routes", "claude", "--last-stage", "ocr")
    capsys.readouterr()
    assert rc == 0 and trust["ok"] is True and "skipped" not in trust


def test_bad_last_stage_is_usage_error(tmp_path, monkeypatch, capsys):
    project = _project(tmp_path / "data")
    _home(tmp_path, monkeypatch, {})
    rc = doctor.main(["--project", str(project), "--only", "project", "--last-stage", "nope"])
    capsys.readouterr()
    assert rc == 2


# --------------------------------------------------------------------------- reachable fix


def test_fix_names_trusted_parent_and_real_options(tmp_path, monkeypatch, capsys):
    parent = tmp_path / "parent"
    project = _project(parent / "data")
    _home(tmp_path, monkeypatch, _trust(parent))
    rc, _, trust = _run(project, tmp_path)
    err = capsys.readouterr().err
    assert rc == 40
    fix = trust["fix"]
    assert "在数据项目目录交互运行一次 claude" not in fix or "git init" in fix
    assert os.path.realpath(parent) in fix
    assert "git init" in fix
    assert "不会弹出" in fix
    assert "G3" in fix
    assert "git init" in err


def test_fix_without_trusted_ancestor_keeps_old_text(tmp_path, monkeypatch, capsys):
    project = _project(tmp_path / "data")
    _home(tmp_path, monkeypatch, {})
    _, _, trust = _run(project, tmp_path)
    capsys.readouterr()
    assert "在数据项目目录交互运行一次 claude" in trust["fix"]
    assert "不会弹出" not in trust["fix"]


# --------------------------------------------------------------------------- pipeline preflight wiring


def test_preflight_passes_routes_and_last_stage_to_doctor(tmp_path):
    calls = []

    class FakeCtx:
        project = tmp_path
        pdir = tmp_path
        routes = "vision,mlx"
        last_stage = "writeback"

        def child(self, *args):
            calls.append(args)
            return 0, ""

        def rel(self, p):
            return p.name

    pipeline._preflight_run(FakeCtx())
    args = calls[0]
    assert args[args.index("--routes") + 1] == "vision,mlx"
    assert args[args.index("--last-stage") + 1] == "writeback"


def test_preflight_inputs_change_when_trust_need_changes(tmp_path):
    class C:
        project = tmp_path
        launcher = "l"
        env = {}

        def __init__(self, routes, last):
            self.routes, self.last_stage = routes, last

    a = pipeline._digest(pipeline._preflight_inputs(C("vision,mlx", "writeback")))
    b = pipeline._digest(pipeline._preflight_inputs(C("vision,claude", "writeback")))
    assert a != b


# --------------------------------------------------------------------------- law (S4)


STAGES = list(pipeline.STAGE_NAMES)
ROUTES = ["vision", "mlx", "claude", "codex"]


@settings(max_examples=60, deadline=None)
@given(routes=st.lists(st.sampled_from(ROUTES), min_size=1, max_size=4, unique=True), i=st.integers(0, 6),
       extra_route=st.sampled_from(ROUTES), j=st.integers(0, 6))
def test_trust_need_is_monotone(routes, i, extra_route, j):
    base = doctor.trust_required(",".join(routes), STAGES[i])
    bigger_routes = list(dict.fromkeys([*routes, extra_route]))
    assert doctor.trust_required(",".join(bigger_routes), STAGES[max(i, j)]) >= base
