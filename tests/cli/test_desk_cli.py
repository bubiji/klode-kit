"""WI-02c: the desk against the real klode 0.5.0 (matrix 02c-01, L3 S3; paired real path of the unit-lane klode
stand-in in ``tests/_support/desk_fx.py``).

klode-kb SKILL.md desk steps ②-⑤ with the real tools: ``klode init`` -> ``klode ingest`` (the three aurora
chapters) -> ``klode build --stamp`` -> ``write-card`` (gate 1) -> ``desk-build --apply`` -> three syntheses whose
anchors are copied from the cards -> ``check-synthesis`` -> ``desk-build --verify``. One dimension is ``safety``,
without a hyphen, and every core question carries hyphenated English words.

**B3**: the real prose output of ``klode diagnose`` for the safety question names ``safety`` but the original
regex (``\\b([a-z]+(?:-[a-z]+)+)\\b``, first match) picks a hyphenated word instead; ``desk-build --verify`` must
judge the question right (``→ safety``), agreeing with ``klode --json diagnose`` ``value[0][0]``. Same for
``eval-compare --desk-gold``. A missing ``klode`` FAILS (never skips).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from _support.desk_fx import ALIASES, ANCHORS, CH, synthesis
from _support.flows import REPO

pytestmark = pytest.mark.cli_integration

LAUNCHER = REPO / "bin" / "klode-kit"
OLD_RE = re.compile(r"\b([a-z]+(?:-[a-z]+)+)\b")
SPEC = {"threshold": 0.8, "dimensions": [
    {"dimension": "construction", "title": "Construction",
     "question": "How is the hand-made kettle body built to be long-lasting?",
     "cards": ["ch01"], "cues": ["copper shell", "annealing", "domed base", "tinning"],
     "tests": ["Why is the copper shell annealed twice?"]},
    {"dimension": "whistle-valve", "title": "Whistle valve",
     "question": "How does the two-tone whistle stay in tune?",
     "cards": ["ch02"], "cues": ["whistle", "reed", "valve cap"],
     "tests": ["What makes the whistle produce two tones?"]},
    {"dimension": "safety", "title": "Safety at the night market",
     "question": "What keeps a night-market kettle safe from the open flame and well-kept?",
     "cards": ["ch03"], "cues": ["ember bed", "waxed cord", "flame flares", "unattended"],
     "tests": ["Can the kettle be left unattended on the ember bed?"]},
]}
HOLDOUT = [
    {"q": "How often is tinning renewed inside the kettle?", "gold": "construction"},
    {"q": "Does the domed base matter on a flat stove?", "gold": "construction"},
    {"q": "Why is the valve cap threaded the other way?", "gold": "whistle-valve"},
    {"q": "The reed sounds dull - what now?", "gold": "whistle-valve"},
    {"q": "Why is the handle wrapped in waxed cord?", "gold": "safety"},
]
SAFETY_Q = SPEC["dimensions"][2]["tests"][0]


def _klode(kb: Path, *args: str, json_mode: bool = False) -> subprocess.CompletedProcess:
    exe = shutil.which("klode")
    assert exe, "klode 0.5.0 is required on PATH for the cli_integration lane"
    pre = ["--json"] if json_mode else []
    return subprocess.run([exe, *pre, "-c", str(kb / "library.toml"), *args], capture_output=True, text=True,
                          timeout=120)


def _launch(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("KLODE_KIT_PYTHON", None)
    return subprocess.run([str(LAUNCHER), *args], capture_output=True, text=True, env=env, cwd=cwd, timeout=300)


@pytest.fixture
def kb(tmp_path) -> Path:
    exe = shutil.which("klode")
    assert exe, "klode 0.5.0 is required on PATH for the cli_integration lane"
    kb = tmp_path / "demo-klode"
    res = subprocess.run([exe, "init", str(kb), "--shelf", "book"], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    for cid, text in CH.items():
        src = tmp_path / f"{cid}.txt"
        src.write_text(text, encoding="utf-8")
        res = _klode(kb, "ingest", str(src), "--shelf", "book", "--id", cid, "--format", "txt")
        assert res.returncode == 0, res.stderr
    res = _klode(kb, "build", "--stamp")
    assert res.returncode == 0 and "cards: 3 created" in res.stdout, res.stdout + res.stderr
    (kb / "cards_content").mkdir()
    for cid, anchors in ANCHORS.items():
        full = "".join(f"- point {k} (grep: `{a}`)\n" for k, a in enumerate(anchors))
        (kb / "cards_content" / f"{cid}.md").write_text(
            f"aliases: [{', '.join(ALIASES[cid])}]\n\n# {cid}\n\n## Thin\nSummary (grep: `{anchors[0]}`).\n\n"
            f"## Full\n### Points\n{full}", encoding="utf-8")
    res = _launch("write-card", str(kb), cwd=tmp_path)
    assert res.returncode == 0, res.stdout + res.stderr
    (kb / "src").mkdir()
    (kb / "src" / "desk.json").write_text(json.dumps(SPEC, ensure_ascii=False, indent=1), encoding="utf-8")
    (kb / "src" / "holdout.jsonl").write_text("".join(json.dumps(h) + "\n" for h in HOLDOUT), encoding="utf-8")
    return kb


def test_b3_json_parse(kb, tmp_path):
    res = _launch("desk-build", str(kb), "--apply", cwd=tmp_path)
    assert res.returncode == 0, res.stdout + res.stderr
    syn = kb / "library" / "frameworks" / "_syntheses"
    assert (syn / "_diagnostics.md").is_file()
    for d in SPEC["dimensions"]:
        (syn / f"{d['dimension']}.md").write_text(
            synthesis(d["dimension"], d["cards"], ANCHORS[d["cards"][0]][:4], title=d["title"]).replace(
                f"**Core question:** {d['dimension']} 怎么做。", f"**Core question:** {d['question']}"),
            encoding="utf-8")
    res = _launch("check-synthesis", str(kb), cwd=tmp_path)
    assert res.returncode == 0, res.stdout + res.stderr

    # the reproduction: real prose names `safety`, the original regex does not
    prose = _klode(kb, "diagnose", SAFETY_Q)
    assert "• safety" in prose.stdout, prose.stdout
    old = [g for g in OLD_RE.findall(prose.stdout) if g != "most-relevant"]
    assert (old[0] if old else None) != "safety"
    oracle = json.loads(_klode(kb, "diagnose", SAFETY_Q, json_mode=True).stdout)["value"][0][0]
    assert oracle == "safety"

    res = _launch("desk-build", str(kb), "--verify", cwd=tmp_path)
    out = res.stdout
    assert res.returncode == 0, out + res.stderr
    assert f"PASS  [调优] diagnose「{SAFETY_Q}」→ safety（期望 safety）" in out
    assert "klode check --strict: OK: 0 errors" in out
    receipt = json.loads((kb / "desk_receipt.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "pass" and receipt["strict"]["pass"] is True
    assert (receipt["tune"]["hit"], receipt["tune"]["total"]) == (3, 3)
    assert (receipt["holdout"]["hit"], receipt["holdout"]["total"]) == (5, 5)
    for r in receipt["tune"]["results"] + receipt["holdout"]["results"]:
        real = json.loads(_klode(kb, "diagnose", r["q"], json_mode=True).stdout)["value"]
        assert r["got"] == (real[0][0] if real else None)

    # eval-compare --desk-gold: same B3 fix
    gold = tmp_path / "gold.jsonl"
    gold.write_text(json.dumps({"q": "copper shell annealing", "gold": ["ch01"], "kind": "精确"}) + "\n",
                    encoding="utf-8")
    desk_gold = tmp_path / "desk_gold.jsonl"
    desk_gold.write_text(json.dumps({"q": SAFETY_Q, "gold": "safety"}) + "\n"
                         + json.dumps({"q": "Why is the copper shell annealed twice?", "gold": "construction"}) + "\n",
                         encoding="utf-8")
    res = _launch("eval-compare", "--gold", str(gold), "--kb", f"demo={kb / 'library.toml'}",
                  "--desk-gold", str(desk_gold), cwd=tmp_path)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "| demo | 2/2 | 1.00 |" in res.stdout
    assert f"| {SAFETY_Q} | safety | ✅ safety |" in res.stdout


def test_strict_failure_fails_verify(kb, tmp_path):
    """A synthesis anchor that is not in the source makes the real ``klode check --strict`` report errors."""
    _launch("desk-build", str(kb), "--apply", cwd=tmp_path)
    syn = kb / "library" / "frameworks" / "_syntheses"
    for d in SPEC["dimensions"]:
        (syn / f"{d['dimension']}.md").write_text(
            synthesis(d["dimension"], d["cards"], ANCHORS[d["cards"][0]][:4]), encoding="utf-8")
    strict = _klode(kb, "check", "--strict")
    assert "0 errors" in strict.stdout
    card = kb / "library" / "cards" / "ch01.md"
    card.write_text(card.read_text(encoding="utf-8").replace(ANCHORS["ch01"][3], "a sentence that is nowhere"),
                    encoding="utf-8")
    strict = _klode(kb, "check", "--strict")
    assert "0 errors" not in strict.stdout.strip().splitlines()[-1], strict.stdout
    res = _launch("desk-build", str(kb), "--verify", cwd=tmp_path)
    assert res.returncode == 1
    assert (kb / "desk_receipt.json").is_file(), "expected the verify receipt"
    receipt = json.loads((kb / "desk_receipt.json").read_text(encoding="utf-8"))
    assert receipt["strict"]["pass"] is False
