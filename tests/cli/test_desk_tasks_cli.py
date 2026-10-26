"""WI-13a (cli_integration): the book path's desk syntheses, end to end with the real klode 0.5.0 (paired real path of
``tests/unit/test_desk_tasks.py``; closes the design gap of impl-deviations "设计缺口：书籍路径书桌 worker 合成无 WI
负责").

The way the /kb-build skill walks it:

1. ``build --source ... --desk ...`` without ``--syntheses``: ``desk_apply`` FAILS (exit 20) naming the dimensions; the
   sealed build holds the written cards;
2. ``desk-tasks --build <that build> --out SYN``: one prompt per dimension, ``SYN/repair.json``;
3. the workers write ``SYN/<dimension>.md`` (here: the test writes them, anchors copied from the build's own cards -
   the model call itself is the manual gate G2);
4. ``check-synthesis <that build> --syntheses SYN`` passes;
5. ``build`` again with the same arguments plus ``--syntheses SYN --repair SYN/repair.json``: all 13 steps PASS, the
   cards are the first build's, byte for byte, and no writer is called again.

A missing ``klode`` FAILS (never skips).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from _support import build_fx as B
from _support import desk_fx as DX
from _support.flows import REPO, base_env
from klode_kit.build import run_build as RB
from klode_kit.build.check_synthesis import ANCHOR_RE

pytestmark = pytest.mark.cli_integration


def kit(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "klode_kit", *args], capture_output=True, text=True, timeout=600,
                          env=env or base_env(), cwd=str(REPO))


def test_book_path_with_desk_workers_end_to_end(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    replay = B.writer_replay(tmp_path)
    args = B.book_args(tmp_path)
    i = args.index("--syntheses")
    first_args = args[:i] + args[i + 2:]

    # 1. no syntheses: desk_apply fails, naming every dimension
    res = B.build(project, tmp_path, "--kb", "kb/demo-klode", *first_args, extra_env=replay)
    assert res.returncode == 20, res.stderr.decode()
    (failed,) = B.builds(project)
    rec = B.receipt(failed)
    by = {s["step"]: s for s in rec["steps"]}
    assert by["desk_apply"]["status"] == "FAIL" and by["gate1"]["status"] == "PASS"
    for dim in desk_dims():
        assert dim in by["desk_apply"]["detail"]

    # 2. the task list
    syn = tmp_path / "work" / "syntheses"
    planned = kit("desk-tasks", "--build", str(failed), "--out", str(syn), "--project", str(project))
    assert planned.returncode == 0, planned.stderr
    doc = json.loads(planned.stdout)
    assert [t["dimension"] for t in doc["tasks"]] == desk_dims() and doc["present"] == []
    for t in doc["tasks"]:
        assert Path(t["prompt_file"]).read_text(encoding="utf-8").count(str(failed)) >= 1

    # 3. the workers' files (anchors copied from the cards of the failed build)
    for t in doc["tasks"]:
        anchors = []
        for cid in t["cards"]:
            anchors += list(dict.fromkeys(ANCHOR_RE.findall((failed / "library" / "cards" / f"{cid}.md")
                                                            .read_text(encoding="utf-8"))))
        Path(t["output"]).write_text(DX.synthesis(t["dimension"], t["cards"], anchors[:4]), encoding="utf-8")

    # 4. the gate on the worker output
    gate = kit("check-synthesis", str(failed), "--syntheses", str(syn))
    assert gate.returncode == 0, gate.stdout
    assert gate.stdout.count("PASS") == len(desk_dims())
    again = json.loads(kit("desk-tasks", "--build", str(failed), "--out", str(syn), "--project", str(project)).stdout)
    assert again["tasks"] == [] and again["present"] == desk_dims()

    # 5. the second build carries the cards over and passes every step
    res2 = B.build(project, tmp_path, "--kb", "kb/demo-klode", *first_args, "--syntheses", str(syn),
                   "--repair", str(syn / "repair.json"), extra_env=replay)
    assert res2.returncode == 0, res2.stderr.decode()
    (good,) = [b for b in B.builds(project) if b != failed]
    rec2 = B.receipt(good)
    assert [s["step"] for s in rec2["steps"]] == list(RB.STEPS)
    assert [s["status"] for s in rec2["steps"]] == ["PASS"] * 13 and rec2["status"] == "PASS"
    for cid in ("ch01", "ch02", "ch03"):
        assert (good / "cards_content" / f"{cid}.md").read_bytes() == (failed / "cards_content" / f"{cid}.md").read_bytes()
    assert Path(replay["KLODE_KIT_FAKE_LOG"]).read_text(encoding="utf-8").count('"tool": "claude"') == 3
    for dim in desk_dims():
        assert (good / "library" / "frameworks" / "_syntheses" / f"{dim}.md").read_bytes() == (syn / f"{dim}.md").read_bytes()


def desk_dims() -> list[str]:
    return [d["dimension"] for d in B.DESK_SPEC["dimensions"]]
