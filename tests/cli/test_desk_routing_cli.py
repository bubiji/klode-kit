"""WI-35 (cli_integration): ``desk-route-tasks`` and ``desk-build --verify --routing`` through the real launcher
``bin/klode-kit`` with the real ``klode`` 0.5.0, on a sealed (read-only) failed build - paired real path of
``tests/unit/test_desk_routing_wi35.py``. A missing ``klode`` FAILS (never skips)."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from _support import adopt_fx as AF
from _support import build_fx as B
from _support.flows import REPO

pytestmark = pytest.mark.cli_integration

LAUNCHER = REPO / "bin" / "klode-kit"
PHRASINGS = [
    ("What should I do about leaks near the rim?", "construction"),
    ("Why does the note go flat after a few weeks?", "whistle-valve"),
    ("How do I keep my fingers from burning?", "safety"),
    ("Is the base thick enough for an induction hob?", "construction"),
    ("The sound became a hiss, why?", "whistle-valve"),
]


def launch(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("KLODE_KIT_PYTHON", None)
    return subprocess.run([str(LAUNCHER), *args], capture_output=True, text=True, env=env, cwd=cwd, timeout=300)


def test_launcher_routes_a_sealed_failed_build(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    old = AF.old_kb(tmp_path / "old", desk=True)
    (old / "src" / "holdout.jsonl").write_text(
        "".join(json.dumps({"q": q, "gold": g}) + "\n" for q, g in PHRASINGS), encoding="utf-8")
    assert AF.adopt(project, tmp_path, old).returncode == 20
    (failed,) = B.builds(project)

    planned = launch("desk-route-tasks", "--build", str(failed), "--out", str(tmp_path / "route"),
                     "--project", str(project), cwd=tmp_path)
    assert planned.returncode == 0, planned.stderr
    doc = json.loads(planned.stdout)
    asked = re.findall(r"^(q\d{2,}): (.+)$", Path(doc["prompt_file"]).read_text(encoding="utf-8"), re.M)
    assert [t for _, t in asked] == [q for q, _ in PHRASINGS]
    gold = dict(PHRASINGS)
    Path(doc["output"]).write_text(json.dumps({
        "schema": "klode-kit.desk_routing/1", "prompt_sha256": doc["prompt_sha256"],
        "answers": [{"qid": qid, "dimension": gold[t]} for qid, t in asked]}), encoding="utf-8")

    receipt = tmp_path / "desk_receipt.json"
    res = launch("desk-build", str(failed), "--verify", "--routing", doc["output"], "--receipt", str(receipt),
                 cwd=tmp_path)
    assert res.returncode == 0, res.stdout + res.stderr
    r = json.loads(receipt.read_text(encoding="utf-8"))
    assert r["status"] == "pass" and r["routing"]["hit"] == 5 and r["holdout"]["hit"] == 0
