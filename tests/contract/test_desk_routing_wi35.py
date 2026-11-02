"""WI-35 (contract_replay): desk acceptance by model routing on the real build chain (real ``klode`` 0.5.0 of PATH;
the worker that routes the holdout is played by the test - the model call itself is the manual gate G2).

The way the /kb-build skill walks it, on the adopt path and on the book path:

1. the first build (no routing file) ends at ``desk_verify`` (exit 20): the holdout is made of new phrasings that no
   cue of the desk reaches, so keyword ``diagnose`` hits 0/5; the detail names ``desk-route-tasks``;
2. ``desk-route-tasks --build <that build> --out DIR`` writes the prompt (the holdout questions only) and says where
   the answer goes;
3. the worker's ``routing.json`` (here: the test writes it from the gold labels it alone knows);
4. the build again with ``--desk-routing`` finishes all 13 steps; the desk receipt carries the routing hit rate, and
   ``build_inputs.json`` the sha of the routing file; keyword results are still in the receipt, not gating;
5. a routing that misses too many questions still fails ``desk_verify``.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from _support import adopt_fx as AF
from _support import build_fx as B
from _support.flows import REPO, base_env
from klode_kit.build import run_build as RB

pytestmark = pytest.mark.contract_replay

NEW_PHRASINGS = [
    {"q": "What should I do about leaks near the rim?", "gold": "construction"},
    {"q": "Why does the note go flat after a few weeks?", "gold": "whistle-valve"},
    {"q": "How do I keep my fingers from burning?", "gold": "safety"},
    {"q": "Is the base thick enough for an induction hob?", "gold": "construction"},
    {"q": "The sound became a hiss, why?", "gold": "whistle-valve"},
]


def kit(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "klode_kit", *args], capture_output=True, text=True, timeout=600,
                          env=base_env(), cwd=str(REPO))


def write_holdout(path: Path) -> None:
    path.write_text("".join(json.dumps(h, ensure_ascii=False) + "\n" for h in NEW_PHRASINGS), encoding="utf-8")


def worker(doc: dict, *, wrong: int = 0) -> Path:
    """The routing worker: reads only the prompt; the gold labels stand for its (perfect) judgement."""
    prompt = Path(doc["prompt_file"]).read_text(encoding="utf-8")
    asked = re.findall(r"^(q\d{2,}): (.+)$", prompt, re.M)
    gold = {h["q"]: h["gold"] for h in NEW_PHRASINGS}
    answers = [{"qid": qid, "dimension": gold[text]} for qid, text in asked]
    for a in answers[:wrong]:
        a["dimension"] = None
    out = Path(doc["output"])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"schema": "klode-kit.desk_routing/1", "prompt_sha256": doc["prompt_sha256"],
                               "answers": answers}), encoding="utf-8")
    return out


def steps(build: Path) -> dict:
    return {s["step"]: s for s in B.receipt(build)["steps"]}


def desk_receipt(build: Path) -> dict:
    return json.loads((build / "desk_receipt.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- adopt path


@pytest.fixture()
def adopt_world(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    old = AF.old_kb(tmp_path / "old", desk=True)
    write_holdout(old / "src" / "holdout.jsonl")
    return project, old


def test_adopt_path_keyword_gate_fails_names_the_next_command_then_routing_passes(tmp_path, adopt_world):
    project, old = adopt_world
    first = AF.adopt(project, tmp_path, old, extra_env=AF.fake_env(tmp_path))
    assert first.returncode == 20, first.stderr.decode()
    (failed,) = B.builds(project)
    by = steps(failed)
    assert by["desk_apply"]["status"] == "PASS" and by["desk_verify"]["status"] == "FAIL"
    detail = by["desk_verify"]["detail"]
    assert "desk-route-tasks" in detail and "--desk-routing" in detail
    assert desk_receipt(failed)["holdout"]["hit"] == 0 and "routing" not in desk_receipt(failed)

    route = tmp_path / "work" / "route"
    planned = kit("desk-route-tasks", "--build", str(failed), "--out", str(route), "--project", str(project))
    assert planned.returncode == 0, planned.stderr
    doc = json.loads(planned.stdout)
    prompt = Path(doc["prompt_file"]).read_text(encoding="utf-8")
    for h in NEW_PHRASINGS:
        assert h["q"] in prompt
    assert '"gold"' not in prompt
    routing = worker(doc)

    second = AF.adopt(project, tmp_path, old, "--desk-routing", str(routing), extra_env=AF.fake_env(tmp_path))
    assert second.returncode == 0, second.stderr.decode()
    (good,) = [b for b in B.builds(project) if b != failed]
    assert [s["status"] for s in B.receipt(good)["steps"]] == ["PASS"] * 13
    r = desk_receipt(good)
    assert r["status"] == "pass" and r["routing"]["hit"] == 5 and r["routing"]["total"] == 5
    assert r["routing"]["sha256"] == hashlib.sha256(routing.read_bytes()).hexdigest()
    assert r["holdout"]["hit"] == 0 and r["holdout"]["pass"] is False  # keyword: reported, not gating
    ins = json.loads((good / "build_inputs.json").read_text(encoding="utf-8"))
    assert ins["desk_routing"] == hashlib.sha256(routing.read_bytes()).hexdigest()
    assert "desk_routing" not in json.loads((failed / "build_inputs.json").read_text(encoding="utf-8"))
    assert AF.claude_calls(AF.fake_env(tmp_path)) == 0


def test_adopt_path_a_routing_below_the_gate_still_fails_desk_verify(tmp_path, adopt_world):
    project, old = adopt_world
    assert AF.adopt(project, tmp_path, old).returncode == 20
    (failed,) = B.builds(project)
    doc = json.loads(kit("desk-route-tasks", "--build", str(failed), "--out", str(tmp_path / "r"),
                         "--project", str(project)).stdout)
    routing = worker(doc, wrong=2)
    res = AF.adopt(project, tmp_path, old, "--desk-routing", str(routing))
    assert res.returncode == 20
    (second,) = [b for b in B.builds(project) if b != failed]
    assert steps(second)["desk_verify"]["status"] == "FAIL"
    assert desk_receipt(second)["routing"]["hit"] == 3


def test_adopt_path_a_routing_for_another_holdout_is_refused(tmp_path, adopt_world):
    project, old = adopt_world
    assert AF.adopt(project, tmp_path, old).returncode == 20
    (failed,) = B.builds(project)
    doc = json.loads(kit("desk-route-tasks", "--build", str(failed), "--out", str(tmp_path / "r"),
                         "--project", str(project)).stdout)
    routing = worker(doc)
    body = json.loads(routing.read_text(encoding="utf-8"))
    body["prompt_sha256"] = "1" * 64
    routing.write_text(json.dumps(body), encoding="utf-8")
    res = AF.adopt(project, tmp_path, old, "--desk-routing", str(routing))
    assert res.returncode == 30  # contract violation, not a quiet pass
    (second,) = [b for b in B.builds(project) if b != failed]
    assert steps(second)["desk_verify"]["status"] == "FAIL"


# --------------------------------------------------------------------------- book path


def test_book_path_routing_with_cards_carried_over(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    replay = B.writer_replay(tmp_path)
    args = B.book_args(tmp_path)
    write_holdout(tmp_path / "desk" / "holdout.jsonl")
    first = B.build(project, tmp_path, "--kb", "kb/demo-klode", *args, extra_env=replay)
    assert first.returncode == 20, first.stderr.decode()
    (failed,) = B.builds(project)
    assert steps(failed)["desk_verify"]["status"] == "FAIL" and "desk-route-tasks" in steps(failed)["desk_verify"]["detail"]

    syn = tmp_path / "syntheses"  # the syntheses the build already used; desk-tasks also writes the repair.json
    tasks = kit("desk-tasks", "--build", str(failed), "--out", str(syn), "--project", str(project))
    assert tasks.returncode == 0 and json.loads(tasks.stdout)["tasks"] == []
    doc = json.loads(kit("desk-route-tasks", "--build", str(failed), "--out", str(tmp_path / "route"),
                         "--project", str(project)).stdout)
    routing = worker(doc)
    second = B.build(project, tmp_path, "--kb", "kb/demo-klode", *args, "--repair", str(syn / "repair.json"),
                     "--desk-routing", str(routing), extra_env=replay)
    assert second.returncode == 0, second.stderr.decode()
    (good,) = [b for b in B.builds(project) if b != failed]
    assert B.receipt(good)["status"] == "PASS" and desk_receipt(good)["routing"]["pass"] is True
    for cid in ("ch01", "ch02", "ch03"):
        assert (good / "cards_content" / f"{cid}.md").read_bytes() == (failed / "cards_content" / f"{cid}.md").read_bytes()
    assert Path(replay["KLODE_KIT_FAKE_LOG"]).read_text(encoding="utf-8").count('"tool": "claude"') == 3
    assert "desk_routing" in json.loads((good / "build_inputs.json").read_text(encoding="utf-8"))
    assert RB.STEPS[-1] == "self_check" and steps(good)["self_check"]["status"] == "PASS"
