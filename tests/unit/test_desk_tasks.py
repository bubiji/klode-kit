"""WI-13a (unit): ``desk-tasks`` and ``check-synthesis --syntheses`` - the book path's desk workers (the design gap
recorded in impl-deviations "设计缺口：书籍路径书桌 worker 合成无 WI 负责").

Specification:

- A book build without ``--syntheses`` fails ``desk_apply`` (exit 20) naming the dimensions. The finished (read-only)
  build still holds the cards the workers must cite. ``klode-kit desk-tasks --build BUILD --out SYN_DIR`` reads that
  build and writes, for each dimension of its ``src/desk.json`` that has no ``SYN_DIR/<dimension>.md`` yet, a worker
  prompt ``SYN_DIR/_tasks/<dimension>.txt``; it prints one JSON document (``klode-kit.desk_tasks/1``) with the tasks and
  the dimensions already present, and writes ``SYN_DIR/repair.json`` (``klode-kit.repair/1``, ``from_build`` relative to
  the project, nothing to redispatch) so the next ``build --repair`` carries the cards over byte for byte: the anchors
  the workers copied stay valid.
- ``check-synthesis KB --syntheses DIR [DIM ...]`` judges ``DIR/<dim>.md`` against the cards of ``KB`` (S1-S6 as
  ever); the build tree is never written, and the syntheses of ``KB`` itself are not looked at.
- Exit codes: 0 planned, 2 usage (no build / out inside the build / build outside the project), 30 contract (no or bad
  ``src/desk.json``).

Laws (matrix 13a adds none for the skills; these belong to the new command): idempotence (S5: the second run prints
and writes the same bytes), partition (S4: tasks and present dimensions split the spec exactly, in spec order),
round trip (S5: a synthesis written to a task's ``output`` passes the gate, and then the dimension is no task any
more).
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from _support import desk_fx as DX
from _support.flows import REPO
from klode_kit.__main__ import COMMANDS
from klode_kit.build import check_synthesis as CS
from klode_kit.build import desk_tasks as DT
from klode_kit.build import run_build as RB

pytestmark = pytest.mark.unit

BUILD_ID = "b-20261023T000000Z-0123abcd"
SPEC = DX.spec_with(5)
DIMS = [d["dimension"] for d in SPEC["dimensions"]]


def world(root: Path, spec: dict | None = None) -> tuple[Path, Path]:
    """``(project, build)``: a project with a finished, sealed book build that lacks its syntheses."""
    project = root / "proj"
    build = project / "kb" / "demo-klode" / "builds" / BUILD_ID
    DX.make_kb(build, spec or SPEC, syntheses=False)
    return project, build


def seal(build: Path) -> None:
    for dirpath, dirs, files in os.walk(build, topdown=False):
        for name in files:
            p = Path(dirpath, name)
            p.chmod(p.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
        Path(dirpath).chmod(Path(dirpath).stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def unseal(build: Path) -> None:
    for dirpath, dirs, files in os.walk(build):
        os.chmod(dirpath, os.stat(dirpath).st_mode | stat.S_IWUSR)
        for f in files:
            p = Path(dirpath, f)
            p.chmod(p.stat().st_mode | stat.S_IWUSR)


def tree(root: Path) -> dict[str, tuple]:
    out = {}
    for dirpath, dirs, files in os.walk(root):
        for name in dirs + files:
            p = Path(dirpath, name)
            rel = p.relative_to(root).as_posix()
            st_ = p.lstat()
            out[rel] = ("dir", st_.st_mtime_ns) if p.is_dir() else (
                "file", hashlib.sha256(p.read_bytes()).hexdigest(), st_.st_mtime_ns)
    return out


def run(capsys, project: Path, build: Path, out: Path, *extra: str) -> tuple[int, dict | None, str]:
    code = DT.main(["--build", str(build), "--out", str(out), "--project", str(project), *extra])
    cap = capsys.readouterr()
    text = cap.out.strip()
    return code, (json.loads(text) if text.startswith("{") else None), cap.err


@pytest.fixture
def sealed(tmp_path):
    project, build = world(tmp_path)
    seal(build)
    yield project, build
    unseal(build)


# --------------------------------------------------------------------------- check-synthesis --syntheses


def write_syn(out: Path, kb: Path, dim: str, *, anchors: list[str] | None = None) -> Path:
    spec = next(d for d in SPEC["dimensions"] if d["dimension"] == dim)
    got = anchors or [a for c in spec["cards"] for a in DX.ANCHORS[c]][:4]
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{dim}.md"
    path.write_text(DX.synthesis(dim, spec["cards"], got), encoding="utf-8")
    return path


def test_syntheses_dir_is_judged_against_the_cards_of_the_kb(tmp_path, capsys):
    kb = DX.make_kb(tmp_path / "kb", SPEC, syntheses=False)
    out = tmp_path / "syn"
    for d in DIMS:
        write_syn(out, kb, d)
    assert CS.main([str(kb), "--syntheses", str(out)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [ln.split()[1] for ln in lines if ln.startswith("PASS")] == sorted(DIMS)
    assert not (kb / "library" / "frameworks").exists()  # nothing was written into the kb


def test_syntheses_dir_anchor_not_in_the_cards_is_s5(tmp_path, capsys):
    kb = DX.make_kb(tmp_path / "kb", SPEC, syntheses=False)
    out = tmp_path / "syn"
    write_syn(out, kb, DIMS[0], anchors=["a sentence the worker recalled instead of copying"] + DX.ANCHORS["ch01"][:3])
    assert CS.main([str(kb), "--syntheses", str(out), DIMS[0]]) == 1
    text = capsys.readouterr().out
    assert text.startswith(f"FAIL  {DIMS[0]}") and "S5" in text and "recalled instead of copying" in text


def test_syntheses_dir_names_the_missing_file(tmp_path, capsys):
    kb = DX.make_kb(tmp_path / "kb", SPEC, syntheses=False)
    out = tmp_path / "syn"
    out.mkdir()
    assert CS.main([str(kb), "--syntheses", str(out)]) == 1  # nothing to check at all is a FAIL
    assert "没有可检查的 synthesis" in capsys.readouterr().out
    assert CS.main([str(kb), "--syntheses", str(out), "construction"]) == 1
    assert "construction: 文件不存在" in capsys.readouterr().out


def test_syntheses_dir_does_not_read_the_syntheses_of_the_kb(tmp_path):
    """The kb's own passing synthesis must not stand in for a failing one in DIR."""
    kb = DX.make_kb(tmp_path / "kb", SPEC, syntheses=True)
    out = tmp_path / "syn"
    out.mkdir()
    (out / f"{DIMS[0]}.md").write_text("no front matter at all\n", encoding="utf-8")
    assert CS.run(kb, [DIMS[0]])[0] == 0
    code, lines = CS.run(kb, [DIMS[0]], out)
    assert code == 1 and "S1 缺 front-matter" in "\n".join(lines)


def test_syntheses_without_a_value_is_usage(tmp_path, capsys):
    kb = DX.make_kb(tmp_path / "kb", SPEC, syntheses=False)
    assert CS.main([str(kb), "--syntheses"]) == 2
    assert CS.main([str(kb), "--syntheses", str(tmp_path / "nowhere")]) == 2


# --------------------------------------------------------------------------- desk-tasks


def test_command_is_registered():
    assert COMMANDS["desk-tasks"][0] == "klode_kit.build.desk_tasks:main"


def test_one_task_per_missing_dimension_in_spec_order(sealed, tmp_path, capsys):
    project, build = sealed
    out = tmp_path / "syn"
    code, doc, _ = run(capsys, project, build, out)
    assert code == 0 and doc["schema"] == "klode-kit.desk_tasks/1"
    assert [t["dimension"] for t in doc["tasks"]] == DIMS and doc["present"] == []
    for t, d in zip(doc["tasks"], SPEC["dimensions"]):
        assert t["title"] == d["title"] and t["cards"] == d["cards"]
        assert Path(t["output"]) == out / f"{d['dimension']}.md"
        assert Path(t["prompt_file"]) == out / "_tasks" / f"{d['dimension']}.txt"
        assert Path(t["prompt_file"]).is_file() and not Path(t["output"]).exists()
    assert Path(doc["syntheses"]) == out and Path(doc["repair"]) == out / "repair.json"


def test_present_dimensions_are_no_task(sealed, tmp_path, capsys):
    project, build = sealed
    out = tmp_path / "syn"
    write_syn(out, build, DIMS[1])
    write_syn(out, build, DIMS[3])
    code, doc, _ = run(capsys, project, build, out)
    assert code == 0
    assert doc["present"] == [DIMS[1], DIMS[3]]
    assert [t["dimension"] for t in doc["tasks"]] == [DIMS[0], DIMS[2], DIMS[4]]
    assert not (out / "_tasks" / f"{DIMS[1]}.txt").exists()


def test_prompt_names_the_cards_the_output_and_the_gate(sealed, tmp_path, capsys):
    project, build = sealed
    out = tmp_path / "syn"
    _, doc, _ = run(capsys, project, build, out)
    task = doc["tasks"][0]
    text = Path(task["prompt_file"]).read_text(encoding="utf-8")
    for cid in task["cards"]:
        assert str(build.resolve() / "library" / "cards" / f"{cid}.md") in text
    assert task["output"] in text
    assert f"check-synthesis {build.resolve()} --syntheses {out.resolve()} {task['dimension']}" in text
    assert str(REPO / "src" / "klode_kit" / "prompts" / "synthesis.md") in text
    assert (REPO / "src" / "klode_kit" / "prompts" / "synthesis.md").is_file()
    assert "--force" not in text and "git" in text  # the rule "never touch git" is in the prompt
    # the sealed build is the cards' place, not the target: the worker is not told to write into it
    assert str(build.resolve() / "library" / "frameworks") not in text
    assert "交回" in text or "handback" in text


def test_the_build_tree_is_only_read(sealed, tmp_path, capsys):
    project, build = sealed
    before = tree(build)
    code, _, _ = run(capsys, project, build, tmp_path / "syn")
    assert code == 0
    assert tree(build) == before


def test_repair_doc_carries_the_cards_over(sealed, tmp_path, capsys):
    project, build = sealed
    out = tmp_path / "syn"
    code, doc, _ = run(capsys, project, build, out)
    spec = json.loads((out / "repair.json").read_text(encoding="utf-8"))
    assert spec["schema"] == RB.REPAIR_SCHEMA == "klode-kit.repair/1"
    assert spec["from_build"] == f"kb/demo-klode/builds/{BUILD_ID}"
    assert (project / spec["from_build"]).is_dir()
    assert spec["redispatch"] == {} and spec["fixed"] == {} and spec["round"] == 1
    assert spec["invalidated_dimensions"] == []


def test_second_run_prints_and_writes_the_same_bytes(sealed, tmp_path, capsys):
    """Idempotence (S5)."""
    project, build = sealed
    out = tmp_path / "syn"
    run(capsys, project, build, out)  # creates the files
    code, doc1, _ = run(capsys, project, build, out)
    snap = tree(out)
    code2, doc2, _ = run(capsys, project, build, out)
    assert code == code2 == 0 and doc1 == doc2
    assert tree(out) == snap  # same bytes and the same mtimes: nothing was rewritten


@given(present=st.sets(st.sampled_from(DIMS)))
@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_tasks_and_present_partition_the_spec(present, capsys):
    """S4: for any set of existing syntheses, tasks + present are the spec's dimensions exactly once, in spec order."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        project, build = world(root)
        out = root / "syn"
        for d in present:
            write_syn(out, build, d)
        code, doc, _ = run(capsys, project, build, out)
        assert code == 0
        tasks = [t["dimension"] for t in doc["tasks"]]
        assert sorted(tasks + doc["present"], key=DIMS.index) == DIMS
        assert not set(tasks) & set(doc["present"]) and set(doc["present"]) == set(present)
        assert tasks == [d for d in DIMS if d not in present] and doc["present"] == [d for d in DIMS if d in present]


def test_round_trip_a_written_synthesis_passes_and_stops_being_a_task(sealed, tmp_path, capsys):
    """S5: what a worker writes at ``output`` is judged against the sealed build's cards; then no task is left."""
    project, build = sealed
    out = tmp_path / "syn"
    _, doc, _ = run(capsys, project, build, out)
    for t in doc["tasks"]:
        write_syn(out, build, t["dimension"])
    assert CS.run(build, None, out)[0] == 0
    code, doc2, _ = run(capsys, project, build, out)
    assert code == 0 and doc2["tasks"] == [] and doc2["present"] == DIMS


# --------------------------------------------------------------------------- guards


def test_out_inside_the_build_is_usage(sealed, capsys):
    project, build = sealed
    code, doc, err = run(capsys, project, build, build / "library" / "frameworks" / "_syntheses")
    assert code == 2 and doc is None and "inside the build" in err


def test_build_outside_the_project_is_usage(tmp_path, capsys):
    _, build = world(tmp_path / "a")
    other = tmp_path / "b"
    other.mkdir()
    code, doc, err = run(capsys, other, build, tmp_path / "syn")
    assert code == 2 and "not inside the project" in err and not (tmp_path / "syn").exists()


@pytest.mark.parametrize("argv", [[], ["--build"], ["--out", "x"], ["--build", "x"]])
def test_missing_arguments_are_usage(argv, capsys):
    assert DT.main(argv) == 2


def test_not_a_build_directory_is_usage(tmp_path, capsys):
    project = tmp_path / "proj"
    (project / "kb").mkdir(parents=True)
    code, doc, err = run(capsys, project, project / "kb" / "nowhere", tmp_path / "syn")
    assert code == 2 and "no such build directory" in err
    assert not (tmp_path / "syn").exists()


def test_missing_or_bad_desk_spec_is_contract(tmp_path, capsys):
    project, build = world(tmp_path)
    (build / "src" / "desk.json").unlink()
    code, _, err = run(capsys, project, build, tmp_path / "syn")
    assert code == 30 and "desk.json" in err and not (tmp_path / "syn" / "_tasks").exists()
    (build / "src" / "desk.json").write_text("{not json", encoding="utf-8")
    assert run(capsys, project, build, tmp_path / "syn")[0] == 30
    bad = json.loads(json.dumps(SPEC))
    bad["dimensions"][0]["cards"] = ["no-such-card"]
    (build / "src" / "desk.json").write_text(json.dumps(bad), encoding="utf-8")
    code, _, err = run(capsys, project, build, tmp_path / "syn")
    assert code == 30 and "no-such-card" in err


def test_a_spec_without_dimensions_is_contract(tmp_path, capsys):
    project, build = world(tmp_path)
    (build / "src" / "desk.json").write_text(json.dumps({"threshold": 0.8, "dimensions": []}), encoding="utf-8")
    assert run(capsys, project, build, tmp_path / "syn")[0] == 30
