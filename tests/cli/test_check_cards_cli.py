"""WI-02a: gate 1 through the real launcher on a real klode library (matrix 02a-08, L3 S1; §0.2 klode pairing).

fx-book-aurora goes the book path of 03 §2 with the real tools: ``klode init`` -> ``klode ingest`` (txt) ->
``bin/klode-kit split-chapters --apply`` (4 pieces) -> ``klode build --stamp`` (4 card scaffolds). Three authored
cards are written into ``cards_content/`` (ch01, ch02 good; ch03 with a too-short alias list and an anchor that
is not in the chapter). Then:

- ``bin/klode-kit check-cards --all --json`` passes the published output schema (``check_cards.JSON_SCHEMA``)
  and says ch01/ch02 PASS, ch03 FAIL with F1 and A2;
- the text mode gives the same verdicts and the same problems, with the same exit code (1);
- every anchor gate 1 accepted is ``FOUND`` by the real ``klode verify`` (exit 0) and the rejected one is not
  (exit 1) - gate 1 agrees with klode on what is in the source.

A missing ``klode`` FAILS (never skips).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from _support.flows import REPO
from klode_kit.build import check_cards as C

pytestmark = pytest.mark.cli_integration

AURORA = REPO / "tests" / "fixtures" / "fx-book-aurora" / "aurora-kettle.txt"
LAUNCHER = REPO / "bin" / "klode-kit"
ALIASES = "aliases: [a1, a2, a3, a4, a5, a6, a7, a8, a9, a10]\n"
CARDS = {
    "ch01": ["copper shell that is hammered thin and then annealed twice",
             "Annealing relaxes the metal so that the shell can expand",
             "A brass rim is folded over the upper edge of the shell",
             "A domed base flexes outward when heated",
             "Makers tin the inside of the shell with a thin food-safe layer"],
    "ch02": ["opens only when the steam pressure passes a small threshold",
             "a reed of spring steel vibrates against two slots",
             "the reed is usually coated with scale and needs a vinegar soak",
             "A lever on the handle lifts the valve cap",
             "Spare reeds are cut from the same strip as the original"],
    "ch03": ["kept on a low ember bed and topped up from a covered jug",
             "Vendors wipe the shell with a dry wool cloth after each refill",
             "restored with a paste of salt and lemon juice",
             "The handle is wrapped in waxed cord",
             "the kettle is boiled empty of coffee twice"],  # not in the chapter (tea)
}


def _klode(kb: Path, *args: str) -> subprocess.CompletedProcess:
    exe = shutil.which("klode")
    assert exe, "klode 0.5.0 is required on PATH for the cli_integration lane"
    return subprocess.run([exe, "-c", str(kb / "library.toml"), *args], capture_output=True, text=True, timeout=120)


def _launch(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("KLODE_KIT_PYTHON", None)
    return subprocess.run([str(LAUNCHER), *args], capture_output=True, text=True, env=env, cwd=cwd, timeout=120)


def _card(cid: str, anchors: list[str]) -> str:
    aliases = "aliases: [x, y, z]\n" if cid == "ch03" else ALIASES
    full = "".join(f"- point {k} (grep: `{a}`)\n" for k, a in enumerate(anchors))
    return f"{aliases}\n# {cid}\n\n## Thin\nSummary (grep: `{anchors[0]}`).\n\n## Full\n### Points\n{full}"


@pytest.fixture
def kb(tmp_path) -> Path:
    exe = shutil.which("klode")
    assert exe, "klode 0.5.0 is required on PATH for the cli_integration lane"
    kb = tmp_path / "demo-klode"
    res = subprocess.run([exe, "init", str(kb), "--shelf", "book"], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    res = _klode(kb, "ingest", str(AURORA), "--shelf", "book", "--id", "_whole-book", "--format", "txt")
    assert res.returncode == 0, res.stderr
    (kb / "src").mkdir()
    manifest = {"shelf": "book", "whole": "library/book/_whole-book.txt", "citation": "Aurora Kettle (synthetic)",
                "pieces": [{"id": "front", "title": "Front", "start_line": 1},
                           {"id": "ch01", "title": "Chapter 1", "start_line": 3},
                           {"id": "ch02", "title": "Chapter 2", "start_line": 17},
                           {"id": "ch03", "title": "Chapter 3", "start_line": 31}]}
    (kb / "src" / "chapters-book.json").write_text(json.dumps(manifest), encoding="utf-8")
    res = _launch("split-chapters", str(kb), "--manifest", "src/chapters-book.json", "--apply", cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    assert "4 片拼回 == 原文" in res.stdout
    res = _klode(kb, "build", "--stamp")
    assert res.returncode == 0, res.stderr
    assert "cards: 4 created" in res.stdout
    (kb / "cards_content").mkdir()
    for cid, anchors in CARDS.items():
        (kb / "cards_content" / f"{cid}.md").write_text(_card(cid, anchors), encoding="utf-8")
    return kb


def test_check_cards_json_output(kb, tmp_path):
    """02a-08: JSON passes the schema; JSON and text verdicts, problems and exit codes agree; klode agrees."""
    js = _launch("check-cards", "--all", "--kb", str(kb), "--json", cwd=tmp_path)
    tx = _launch("check-cards", "--all", "--kb", str(kb), cwd=tmp_path)
    assert js.returncode == tx.returncode == 1, (js.stderr, tx.stderr)

    doc = json.loads(js.stdout)
    assert list(Draft202012Validator(C.JSON_SCHEMA).iter_errors(doc)) == []
    assert doc["pass"] is False
    verdict = {c["id"]: c["pass"] for c in doc["cards"]}
    assert verdict == {"ch01": True, "ch02": True, "ch03": False}
    ch03 = next(c for c in doc["cards"] if c["id"] == "ch03")
    assert {p["code"] for p in ch03["problems"]} == {"F1", "A2"}
    assert all(c["policy"] == "authored" and c["source"] == f"library/book/{c['id']}.txt" for c in doc["cards"])

    text_verdict = {m.group(2): m.group(1) == "PASS"
                    for m in re.finditer(r"^(PASS|FAIL)  (\S+)", tx.stdout, flags=re.M)}
    assert text_verdict == verdict
    text_problems = sorted(ln.strip() for ln in tx.stdout.splitlines() if ln.startswith("   "))
    json_problems = sorted(f"{c['id']}: {p['code']} {p['message']}" for c in doc["cards"] for p in c["problems"])
    assert text_problems == json_problems

    for cid, anchors in CARDS.items():
        for a in anchors:
            res = _klode(kb, "verify", cid, a)
            accepted = not (cid == "ch03" and "coffee" in a)
            assert (res.returncode == 0) is accepted, (cid, a, res.stdout, res.stderr)
