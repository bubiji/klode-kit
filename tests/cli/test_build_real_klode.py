"""WI-09b: the 13-step build with the real klode 0.5.0 (matrix 09b-09, L3 S1; paired real path of the contract-lane
build tests in ``tests/contract/test_build_steps.py``).

- U7 shape (``fx-book-aurora``): ``build --source aurora-kettle.txt --manifest <3 chapters> --desk src/desk.json
  --syntheses <desk worker output>``; the card writers are the fake ``claude`` replay (a real model call is gated by
  G2 and is not made here). All 13 steps PASS, the pieces put back equal the source, three cards, strict
  ``0 errors, 0 warning(s)`` when run again by hand on the finished (read-only) tree, desk verify PASS.
- medical shape (``fx-labs-3x``): deterministic cards, strict 0E, each exported observation line FOUND by
  ``klode verify`` on the build directory, ``check-published --all`` PASS.
A missing ``klode`` / ``klode-mcp`` FAILS (never skips).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from _support import build_fx as B
from _support import desk_fx as DX
from _support.flows import REPO
from klode_kit.build import run_build as RB

pytestmark = pytest.mark.cli_integration

LAUNCHER = REPO / "bin" / "klode-kit"


def _klode(build: Path, *args: str, json_mode: bool = False) -> subprocess.CompletedProcess:
    exe = shutil.which("klode")
    assert exe, "klode 0.5.0 is required on PATH for the cli_integration lane"
    pre = ["--json"] if json_mode else []
    return subprocess.run([exe, *pre, "-c", str(build / "library.toml"), *args], capture_output=True, text=True,
                          timeout=300)


def test_build_u7_real_klode(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    replay = B.writer_replay(tmp_path)
    res = B.build(project, tmp_path, "--kb", "kb/demo-klode", *B.book_args(tmp_path), extra_env=replay)
    assert res.returncode == 0, res.stderr.decode()
    (bd,) = B.builds(project)
    rec = B.receipt(bd)
    assert [s["step"] for s in rec["steps"]] == list(RB.STEPS)
    assert [s["status"] for s in rec["steps"]] == ["PASS"] * 13 and rec["status"] == "PASS"
    assert rec["kb_id"] == "demo-klode" and "deident_mode" not in rec and rec["audit_bindings"] == {}
    source = (B.AURORA / "aurora-kettle.txt").read_text(encoding="utf-8")
    pieces = [(bd / "library" / "book" / f"{cid}.txt").read_text(encoding="utf-8") for cid in ("ch01", "ch02", "ch03")]
    assert "".join(pieces) == source
    assert sorted(p.stem for p in (bd / "cards_content").glob("*.md")) == ["ch01", "ch02", "ch03"]
    assert Path(replay["KLODE_KIT_FAKE_LOG"]).read_text(encoding="utf-8").count('"tool": "claude"') == 3
    strict = _klode(bd, "check", "--strict")
    assert strict.returncode == 0 and "0 errors, 0 warning(s)" in strict.stdout, strict.stdout
    desk = json.loads((bd / "desk_receipt.json").read_text(encoding="utf-8"))
    assert desk["status"] == "pass" and desk["holdout"]["pass"] is True
    agent = bd / "agent" / "demo-klode-klode-expert.md"
    assert agent.is_file() and "kb/demo-klode/current" in agent.read_text(encoding="utf-8")
    assert B.writable_bits(bd) == []
    verified = _klode(bd, "--json", "verify", "ch01", DX.ANCHORS["ch01"][0])
    assert verified.returncode == 0 and json.loads(verified.stdout)["value"]["resolution"] == "found"


def test_build_labs_3x_real_klode(tmp_path):
    project = B.project(tmp_path)
    res = B.build(project, tmp_path)
    assert res.returncode == 0, res.stderr.decode()
    bd = B.builds(project)[0]
    strict = _klode(bd, "check", "--strict")
    assert strict.returncode == 0 and "0 errors, 0 warning(s)" in strict.stdout, strict.stdout
    rows = [json.loads(x) for x in (bd / "src" / "export_map.jsonl").read_text(encoding="utf-8").splitlines()]
    obs = [r for r in rows if r["line_kind"] == "observation" and r["source_file"].startswith("library/items/")]
    assert obs
    for r in obs:
        card = Path(r["source_file"]).stem
        found = _klode(bd, "--json", "verify", card, r["text"])
        assert found.returncode == 0, found.stdout + found.stderr
        assert json.loads(found.stdout)["value"]["resolution"] == "found"
    published = subprocess.run([str(LAUNCHER), "check-published", "--all", "--kb", str(bd), "--json"],
                               capture_output=True, text=True, cwd=project, timeout=300)
    assert published.returncode == 0, published.stdout + published.stderr
    assert json.loads(published.stdout)["pass"] is True
