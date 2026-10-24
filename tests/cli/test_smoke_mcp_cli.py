"""WI-02d: smoke_mcp and gen_agent against the real klode / klode-mcp 0.5.0 (matrix 02d-06, L3 S1; real-path pair
of ``tests/unit/test_smoke_mcp.py`` and of the tool names in ``tests/unit/test_gen_agent.py``).

The build of fx-labs-3x is made from public stages only, the way the pipeline will chain them: the real kb-ocr /
kb-audit / writeback (``export_fx.pipeline``) -> ``records_to_txt.export`` (WI-01a, items + records shelves) ->
``klode init`` + ``klode build --stamp`` -> ``[library] id/name/description`` filled in (``init_kb``'s job, WI-03a,
not built yet) -> one item card written through ``bin/klode-kit write-card`` (gate 1 first). Then, through
``bin/klode-kit``:

1. ``smoke-mcp`` with no ``--query``: six PASS lines, exit 0, and the search label carries the card's first alias
   (03 A5: the original default query, the first two words of ``--real``, is replaced);
2. ``smoke-mcp`` with a phrase that is not in the source: the real verify check FAILS (exit 1) - the judgement is
   the real run's, not a path check;
3. ``gen-agent`` on the same build: line 1 ``---``, build comment after the frontmatter, and every
   ``mcp__<server>__<tool>`` it grants is a tool the real ``klode-mcp`` lists.

A missing ``klode`` / ``klode-mcp`` FAILS (never skips).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from _support import export_fx as X
from _support.flows import REPO
from klode_kit.build import records_to_txt as R

pytestmark = pytest.mark.cli_integration

LAUNCHER = REPO / "bin" / "klode-kit"
REAL = "血红蛋白 135 g/L 2026-09-18"
CONTENT = """aliases: [血红蛋白, hemoglobin, Hb, HGB, 血色素, 血常规, 化验单, 贫血, 红细胞, g/L]

## Thin
血红蛋白三次化验结果，最近一次为 135 g/L (grep: `血红蛋白 135 g/L 2026-09-18`)。

## Full

### 观测
- 第一次 (grep: `血红蛋白 128 g/L 2026-07-18`)
- 第二次 (grep: `血红蛋白 128 g/L 2026-08-18`)
- 第三次 (grep: `血红蛋白 135 g/L 2026-09-18`)
"""


def _exe(name: str) -> str:
    exe = shutil.which(name)
    assert exe, f"{name} 0.5.0 is required on PATH for the cli_integration lane"
    return exe


def _launch(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("KLODE_KIT_PYTHON", None)
    return subprocess.run([str(LAUNCHER), *args], capture_output=True, text=True, env=env, cwd=cwd, timeout=300)


@pytest.fixture(scope="module")
def labs_build(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("labs3x")
    project = root / "proj"
    project.mkdir()
    X.pipeline(project, X.FIXTURES / "fx-labs-3x")
    build = root / "build"
    R.export(project, build, kb_id="fx-labs-3x")  # user-approved 2026-10-24: library id; server name appends -klode
    klode = _exe("klode")
    init = subprocess.run([klode, "init", "--shelf", "items", "--shelf", "records", str(build)],
                          capture_output=True, text=True, timeout=120)
    assert init.returncode == 0, init.stderr
    toml = build / "library.toml"
    text = toml.read_text(encoding="utf-8")
    assert "\nid = " not in text  # klode init leaves the identity to init_kb (03 §2 step 1)
    toml.write_text(text.replace("[library]\n", '[library]\nid = "fx-labs-3x"\nname = "合成化验单库"\n'
                                 'description = "三次合成血常规化验单"\n', 1), encoding="utf-8")
    built = subprocess.run([klode, "-c", str(toml), "build", "--stamp"], capture_output=True, text=True, timeout=120)
    assert built.returncode == 0, built.stderr
    (build / "cards_content").mkdir()
    (build / "cards_content" / "hemoglobin.md").write_text(CONTENT, encoding="utf-8")
    wrote = _launch("write-card", str(build), "hemoglobin", cwd=root)
    assert wrote.returncode == 0, wrote.stdout + wrote.stderr
    (build / "src" / "desk.json").write_text(json.dumps({"threshold": 0.8, "dimensions": [
        {"dimension": "labs", "title": "化验", "question": "q", "cards": ["hemoglobin"], "cues": ["血红蛋白"],
         "tests": ["血红蛋白最近一次是多少"]}]}, ensure_ascii=False), encoding="utf-8")
    return build


def test_smoke_6_pass(labs_build):
    """02d-06 (S1): the real klode-mcp 0.5.0 on the fx-labs-3x build - six PASS lines, exit 0; the search used the
    card's first alias."""
    _exe("klode-mcp")
    res = _launch("smoke-mcp", str(labs_build), "--card", "hemoglobin", "--real", REAL, cwd=labs_build)
    assert res.returncode == 0, res.stdout + res.stderr
    checks = [ln for ln in res.stdout.splitlines() if ln.startswith(("PASS", "FAIL"))]
    assert len(checks) == 6 and all(ln.startswith("PASS") for ln in checks), res.stdout
    assert "klode 0.5.0" in checks[0]
    assert "search_sources('血红蛋白', full)" in checks[2]


def test_smoke_real_verify_fails_on_absent_phrase(labs_build):
    """Real-path failure (S3): a ``--real`` phrase the source does not hold (the four routes' misreading 10.5 kind
    of value) makes the real verify check FAIL and the run exit 1."""
    res = _launch("smoke-mcp", str(labs_build), "--card", "hemoglobin", "--real", "血红蛋白 153 g/L 2026-09-18",
                  cwd=labs_build)
    assert res.returncode == 1, res.stdout + res.stderr
    fails = [ln for ln in res.stdout.splitlines() if ln.startswith("FAIL")]
    assert len(fails) == 1 and "真句" in fails[0]


def _mcp_tools(build: Path) -> set[str]:
    proc = subprocess.run(
        [_exe("klode-mcp"), "-c", str(build / "library.toml")],
        input="\n".join(json.dumps(m) for m in (
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})) + "\n",
        capture_output=True, text=True, timeout=60)
    replies = [json.loads(ln) for ln in proc.stdout.splitlines() if ln.strip()]
    (listing,) = [r for r in replies if r.get("id") == 2]
    return {t["name"] for t in listing["result"]["tools"]}


def test_agent_tools_exist_in_klode_mcp(labs_build, tmp_path):
    """Real-path pair of the generated tool list: every ``mcp__fx-labs-3x-klode__<tool>`` the expert is granted is
    listed by the real klode-mcp; the file opens with ``---`` and carries the build comment after it; the example
    is the desk's question."""
    out = tmp_path / ".claude" / "agents" / "fx-labs-3x-klode-expert.md"
    res = _launch("gen-agent", str(labs_build), "--out", str(out), "--build-id", "b-labs-1", cwd=tmp_path)
    assert res.returncode == 0, res.stdout + res.stderr
    text = out.read_text(encoding="utf-8")
    lines = text.split("\n")
    close = lines.index("---", 1)
    assert lines[0] == "---" and lines[close + 1] == "<!-- klode-kit build b-labs-1 -->"
    (tools_line,) = [ln for ln in lines[1:close] if ln.startswith("tools:")]
    prefix = "mcp__fx-labs-3x-klode__"
    granted = {t.strip()[len(prefix):] for t in tools_line[len("tools:"):].split(",") if t.strip().startswith(prefix)}
    assert {"search_sources", "zoom_card", "verify_quote", "list_kbs"} <= granted
    assert granted <= _mcp_tools(labs_build)
    assert "血红蛋白最近一次是多少" in text
    assert "原文是英文" not in text
