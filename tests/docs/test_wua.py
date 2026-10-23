"""WI-00e: the three generic WUA diagrams in ``docs/wua/`` (matrix 00e-01 ... 00e-10).

The diagrams are wfviz workflow documents (``*.wf.json``) plus the SVG wfviz renders from
them. wfviz itself lives in another repository; ``WFVIZ_SRC`` must point at that
repository's ``src`` directory. A missing or wrong ``WFVIZ_SRC`` FAILS the rendering
tests (cli_integration never skips on a missing dependency).

Structural content checks (00e-02 ... 00e-07, 00e-09) only parse JSON/SVG text and carry the
``unit`` marker as well; being under ``tests/docs/`` they also run in cli_integration.

Machine-readable conventions the diagrams follow (wfviz ignores unknown keys):
- scenarios: every step is one scenario and carries ``"scenario": <n>``;
- usecases: every step carries ``"usecase": "U<n>"``;
- pipeline: every step carries ``"stage"``; a human role carries ``"human": true``; every
  loop carries ``"id": "L<n>"``; record-state artifacts carry ``"state"``; a state edge
  A -> B is drawn when one step takes the A artifact in and puts the B artifact out.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from klode_kit.devtools import privacy_scan

REPO = Path(__file__).resolve().parents[2]
WUA = REPO / "docs" / "wua"
NAMES = ("scenarios", "usecases", "pipeline")
FORBIDDEN_KEYS = {"extends", "include", "ref", "inherits"}
STAGES = ["preflight", "ocr", "audit", "writeback", "build", "check", "publish"]
SIX_SCENARIOS = {
    "住院期截图当天入库",
    "门诊纸质单拍照",
    "家人远程放 _inbox",
    "指南 PDF 入库",
    "查询问答",
    "历史资料重做 OCR",
}
STATES = {"extracted", "pending_confirm", "verified", "disputed"}
STATE_EDGES = {
    ("extracted", "pending_confirm"),
    ("pending_confirm", "verified"),
    ("pending_confirm", "disputed"),
}
LOCAL_PATH_RE = re.compile(r"/Users/|/home/|/private/|~/|[A-Za-z]:\\\\")


def load(name: str) -> dict:
    path = WUA / f"{name}.wf.json"
    assert path.is_file(), f"missing diagram source {path.relative_to(REPO)}"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{name}: top level must be an object"
    return data


def wfviz_env() -> dict[str, str]:
    src = os.environ.get("WFVIZ_SRC", "")
    if not src:
        pytest.fail("WFVIZ_SRC is not set: point it at the wfviz repository's src directory")
    if not (Path(src) / "wfviz" / "__main__.py").is_file():
        pytest.fail(f"WFVIZ_SRC does not contain wfviz/__main__.py: {src!r}")
    env = dict(os.environ)
    env["PYTHONPATH"] = src
    return env


def render(name: str, out: Path) -> subprocess.CompletedProcess:
    env = wfviz_env()
    src = WUA / f"{name}.wf.json"
    assert src.is_file(), f"missing diagram source {src.relative_to(REPO)}"
    return subprocess.run(
        [sys.executable, "-m", "wfviz", str(src), str(out), "--mode", "inline"],
        capture_output=True, text=True, timeout=120, env=env, cwd=REPO,
    )


def human_roles(wf: dict) -> set[str]:
    return {r["id"] for r in wf["roles"] if r.get("human") is True}


def step_index(wf: dict) -> dict[str, int]:
    return {s["id"]: i for i, s in enumerate(wf["steps"])}


# ---------------------------------------------------------------- cli_integration


@pytest.mark.cli_integration
@pytest.mark.parametrize("name", NAMES)
def test_wfviz_renders(name, tmp_path):
    """00e-01: wfviz exit 0 for every diagram; SVG non-empty and well-formed XML."""
    out = tmp_path / f"{name}.svg"
    res = render(name, out)
    assert res.returncode == 0, res.stderr
    assert out.is_file() and out.stat().st_size > 0
    root = ET.parse(out).getroot()
    assert root.tag.endswith("svg")


@pytest.mark.cli_integration
@pytest.mark.parametrize("name", NAMES)
def test_committed_svg_matches_source(name, tmp_path):
    """00e-10: the committed docs/wua/<name>.svg is exactly what wfviz renders from the committed JSON."""
    out = tmp_path / f"{name}.svg"
    res = render(name, out)
    assert res.returncode == 0, res.stderr
    committed = WUA / f"{name}.svg"
    assert committed.is_file(), f"missing {committed.relative_to(REPO)}"
    assert committed.read_bytes() == out.read_bytes(), f"{name}.svg is stale: re-render it from {name}.wf.json"


@pytest.mark.cli_integration
@pytest.mark.parametrize("value", [None, "", "/nonexistent/wfviz-src"])
def test_wfviz_src_missing_fails(pytester, monkeypatch, value):
    """00e-08: without a usable WFVIZ_SRC the rendering test FAILS, it is never skipped."""
    if value is None:
        monkeypatch.delenv("WFVIZ_SRC", raising=False)
    else:
        monkeypatch.setenv("WFVIZ_SRC", value)
    res = pytester.runpytest_subprocess(
        str(Path(__file__).resolve()), "-k", "test_wfviz_renders", "-p", "no:cacheprovider",
    )
    outcomes = res.parseoutcomes()
    assert outcomes.get("failed", 0) == len(NAMES)
    assert outcomes.get("skipped", 0) == 0
    assert outcomes.get("passed", 0) == 0
    assert "WFVIZ_SRC" in res.stdout.str()


# ---------------------------------------------------------------- content (unit + cli)


@pytest.mark.unit
def test_scenarios_exactly_6():
    """00e-02: exactly the six confirmed scenarios (decision 2), every step is one scenario."""
    wf = load("scenarios")
    steps = wf["steps"]
    assert len(steps) == 6
    assert all("scenario" in s for s in steps)
    assert sorted(s["scenario"] for s in steps) == [1, 2, 3, 4, 5, 6]
    assert {s["zh"] for s in steps} == SIX_SCENARIOS


@pytest.mark.unit
def test_usecases_cover_u1_u8():
    """00e-03: the use-case set is exactly U1 ... U8, one step each."""
    wf = load("usecases")
    ucs = [s.get("usecase") for s in wf["steps"]]
    assert sorted(ucs, key=lambda u: int(str(u)[1:])) == [f"U{i}" for i in range(1, 9)]
    assert len(ucs) == len(set(ucs)) == 8


@pytest.mark.unit
def test_pipeline_stages_and_single_human_step():
    """00e-04: the seven stages appear in order; the only human step is 「点选」."""
    wf = load("pipeline")
    order: list[str] = []
    for s in wf["steps"]:
        assert s.get("stage") in STAGES, f"step {s['id']}: unknown stage {s.get('stage')!r}"
        if not order or order[-1] != s["stage"]:
            order.append(s["stage"])
    assert order == STAGES, "stages must be complete and contiguous in pipeline order"

    humans = human_roles(wf)
    assert humans, "pipeline must declare the human role"
    human_steps = [s for s in wf["steps"] if s["role"] in humans]
    assert len(human_steps) == 1
    assert "点选" in human_steps[0]["zh"]


@pytest.mark.unit
def test_loops_back_edges():
    """00e-05: L1-L3 exist, each loop's from comes strictly after its to; loops stay in build/check."""
    wf = load("pipeline")
    idx = step_index(wf)
    stage_of = {s["id"]: s["stage"] for s in wf["steps"]}
    loops = {lp.get("id"): lp for lp in wf.get("loops", [])}
    assert set(loops) == {"L1", "L2", "L3"}
    for lid, lp in loops.items():
        assert lp["from"] in idx and lp["to"] in idx, lid
        assert idx[lp["from"]] > idx[lp["to"]], f"{lid}: from must come after to"
        assert stage_of[lp["to"]] == "build", f"{lid}: loops return into build"
        assert stage_of[lp["from"]] in {"build", "check"}, lid


@pytest.mark.unit
def test_state_machine_drawn():
    """00e-06: states extracted/pending_confirm/verified/disputed, edges as specified, effective/candidate note."""
    wf = load("pipeline")
    state_of = {a["id"]: a["state"] for a in wf["artifacts"] if "state" in a}
    assert set(state_of.values()) == STATES
    edges = set()
    for s in wf["steps"]:
        ins = [state_of[a] for a in s.get("in", []) + s.get("refers_to", []) if a in state_of]
        outs = [state_of[a] for a in s.get("out", []) if a in state_of]
        edges |= {(i, o) for i in ins for o in outs}
    assert edges == STATE_EDGES

    nodes = wf["artifacts"] + wf["steps"]
    notes = [n for n in nodes if "effective" in n["zh"] + n["en"] and "candidate" in n["zh"] + n["en"]]
    assert notes, "a node must explain effective vs candidate"


def _keys(node):
    if isinstance(node, dict):
        for k, v in node.items():
            yield k
            yield from _keys(v)
    elif isinstance(node, list):
        for v in node:
            yield from _keys(v)


@pytest.mark.unit
@pytest.mark.parametrize("name", NAMES)
def test_no_forbidden_keys_and_all_artifacts_referenced(name):
    """00e-07: no extends/include/ref/inherits key at any depth; every artifact used by some step."""
    wf = load(name)
    assert not (set(_keys(wf)) & FORBIDDEN_KEYS)
    used = set()
    for s in wf["steps"]:
        used |= set(s.get("in", [])) | set(s.get("out", [])) | set(s.get("refers_to", []))
    ids = [a["id"] for a in wf["artifacts"]]
    assert ids, f"{name}: no artifacts"
    unused = [a for a in ids if a not in used]
    assert unused == []
    assert used <= set(ids), f"{name}: step references unknown artifact"


@pytest.mark.unit
@pytest.mark.parametrize("name", NAMES)
def test_diagrams_generic_no_private_data(name):
    """00e-09: sources and SVGs pass privacy_scan (SVG allowed under docs/wua) and hold no local paths."""
    files = [WUA / f"{name}.wf.json", WUA / f"{name}.svg"]
    for f in files:
        assert f.is_file(), f"missing {f.relative_to(REPO)}"
        assert not LOCAL_PATH_RE.search(f.read_text(encoding="utf-8")), f"local path in {f.name}"
    assert privacy_scan.scan(REPO, files) == []
