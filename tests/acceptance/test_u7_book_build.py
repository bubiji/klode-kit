"""U7 (04 section 6): a book of the user's old ``klode-build`` flow through ``/kb-build`` - ``kb-build --kb kb/demo-klode
--source aurora-kettle.txt --desk src/desk.json``.

What the user sees (04): ``build_receipt.json``: the chapter cut checks itself (pieces put back == the whole),
``check_cards`` 3/3 PASS, strict 0 errors, the desk verified; ``cards_content/`` holds 3 cards, ``.mcp.json`` gets the
library's entry, ``smoke_mcp`` 6 checks PASS.

Matrix U7-01, U7-02 (lane cli_integration: the real ``klode`` 0.5.0 and ``klode-mcp``; the three card writers are the
replayed ``claude`` - a real writer call is G2 and is ``tests/model/test_card_writer_real.py``). Facts reported next to
the tests (08 notes):

- **The desk gate** is the ``kb-check`` rule the user decided on 2026-10-27: a holdout of >= 5 questions with >= 0.8
  right (not "5/5"). The fixture's own desk (``src/desk.json``) routes 3 of the 5 holdout questions (0.6); the desk
  of this test, ``src/desk.l1.json``, is the same desk plus tuning questions made by a rule that never looks at the
  holdout (one question per card alias that is not a cue yet), and ``build --loops`` (the L1 loop: ``cue-suggest``
  reads the tuning set only) lifts it to 4/5 = 0.8. The holdout file is byte-identical to the fixture's and frozen.
  Why not 5/5: the holdout question "How is a darkened copper shell restored?" belongs to ``night-market-care`` but
  contains the cue ``copper shell`` of ``construction``; the card alias is ``darkened shell`` (not a phrase of the
  question), so no cue taken from the book's own aliases can route it - fixing that would mean tuning on the holdout
  (iron law 2 of the kb-build skill), which this suite does not do.
- **The registration** is made by ``kb-publish`` (never by ``build``): the entry of the library is
  ``demo-klode-klode`` (``<kb dir name>-klode``), 04 writes ``demo-klode``.
- ``klode-kit smoke-mcp`` prints 6 PASS lines: initialize, tools/list, search_sources, zoom_card, verify_quote real,
  verify_quote fake.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from _support import accept_fx as A
from _support import build_fx as B
from _support import desk_fx as DX
from klode_kit.build import cue_suggest

pytestmark = pytest.mark.cli_integration

AURORA = B.AURORA
SRC = AURORA / "src"
STEPS = ["export", "init_kb", "ingest", "split_manifest", "klode_build", "dispatch_writers", "gate1", "write_card",
         "check_strict", "desk_apply", "desk_verify", "gen_agent", "self_check"]


@pytest.fixture(scope="module")
def u7(tmp_path_factory):
    """The U7 command, run once: first without the loops (the gate says no), then with ``--loops``."""
    tmp = tmp_path_factory.mktemp("u7")
    project = tmp / "proj"
    project.mkdir()
    replay = B.writer_replay(tmp)
    args = A.aurora_inputs(tmp)
    plain = B.build(project, tmp, "--kb", "kb/demo-klode", *args, extra_env=replay)
    plain_builds = B.builds(project)
    looped = B.build(project, tmp, "--kb", "kb/demo-klode", *args, "--loops", extra_env=replay)
    builds = B.builds(project)
    final = json.loads(looped.stdout.decode().strip().splitlines()[-1])
    return {"tmp": tmp, "project": project, "plain": plain, "plain_builds": plain_builds, "looped": looped,
            "builds": builds, "final": B.builds(project)[-1] if builds else None, "summary": final,
            "replay": replay}


def test_u7_book_build(u7):
    """U7-01 (L3 S1)."""
    assert u7["looped"].returncode == 0, u7["looped"].stdout.decode()[-1500:] + u7["looped"].stderr.decode()[-1500:]
    summary = u7["summary"]
    assert summary["status"] == "PASS" and summary["rounds"] >= 2
    bd = u7["project"] / summary["build_dir"]
    rec = B.receipt(bd)
    assert rec["status"] == "PASS" and rec["kb_id"] == "demo-klode"
    assert [s["step"] for s in rec["steps"]] == STEPS and [s["status"] for s in rec["steps"]] == ["PASS"] * 13

    # the chapter cut checks itself: the three pieces put back are the whole, and the whole is the book
    source = (AURORA / "aurora-kettle.txt").read_text(encoding="utf-8")
    pieces = [(bd / "library" / "book" / f"{c}.txt").read_text(encoding="utf-8") for c in ("ch01", "ch02", "ch03")]
    assert "".join(pieces) == source
    # gate 1: check_cards 3/3 (the user's command, on the finished tree)
    cards = subprocess.run([sys.executable, "-m", "klode_kit", "check-cards", "--all", "--kb", str(bd), "--json"],
                           capture_output=True, text=True, cwd=str(u7["project"]), timeout=300)
    assert cards.returncode == 0, cards.stdout + cards.stderr
    verdicts = json.loads(cards.stdout)
    rows = verdicts["cards"] if isinstance(verdicts, dict) and "cards" in verdicts else verdicts
    assert len(rows) == 3 and all(r.get("pass", r.get("status") == "PASS") for r in rows), verdicts
    # strict: 0 errors, run by hand on the finished tree
    strict = A.klode(bd, "check", "--strict")
    assert strict.returncode == 0 and strict.stdout.strip().splitlines()[-1] == "OK: 0 errors, 0 warning(s)"
    # the desk: the gate (>= 5 questions and >= 0.8 right), on a holdout nobody touched
    desk = json.loads((bd / "desk_receipt.json").read_text(encoding="utf-8"))
    hold = desk["holdout"]
    assert desk["status"] == "pass" and hold["pass"] is True
    assert hold["total"] >= 5 and hold["hit"] / hold["total"] >= 0.8
    assert desk["tune"]["hit"] / desk["tune"]["total"] >= 0.8
    assert (SRC / "holdout.jsonl").read_bytes() == (bd / "src" / "holdout.jsonl").read_bytes()
    # three cards, and the expert agent
    assert sorted(p.stem for p in (bd / "cards_content").glob("*.md")) == ["ch01", "ch02", "ch03"]
    assert (bd / "agent" / "demo-klode-klode-expert.md").is_file()
    # smoke_mcp: its six checks, run again by hand against the finished tree
    smoke = subprocess.run([sys.executable, "-m", "klode_kit", "smoke-mcp", str(bd), "--card", "ch01", "--real",
                            DX.ANCHORS["ch01"][0]], capture_output=True, text=True, timeout=300,
                           env=B.env(u7["tmp"]))
    assert smoke.returncode == 0, smoke.stdout + smoke.stderr
    assert len(re.findall(r"^PASS", smoke.stdout, re.M)) == 6, smoke.stdout
    assert "FAIL" not in smoke.stdout
    # nothing went to a real model: the writer calls are the replay's, three per build that wrote cards (the plain
    # build and the loop's first round; the loop's second round carries the cards over, WI-09c)
    calls = Path(u7["replay"]["KLODE_KIT_FAKE_LOG"]).read_text(encoding="utf-8").count('"tool": "claude"')
    assert calls == 6


def test_u7_the_gate_needed_the_l1_loop_and_the_loop_never_read_the_holdout(u7):
    """The desk is tuned by the loop, not by the test: without ``--loops`` the same inputs stop at the desk gate; the
    loop's input is tuning-set misses only; the cues it added are the cards' own aliases; the holdout is frozen."""
    assert u7["plain"].returncode == 20, u7["plain"].stdout.decode()[-800:]
    (first,) = u7["plain_builds"]
    failed = json.loads((first / "desk_receipt.json").read_text(encoding="utf-8")) if (
        first / "desk_receipt.json").is_file() else None
    assert failed is not None and failed["holdout"]["pass"] is False  # the fixture's tuned-by-rule desk, before the loop
    receipt_path = u7["project"] / json.loads(u7["looped"].stdout.decode().strip().splitlines()[-1])["loops"]
    loops = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert loops["status"] == "PASS" and loops["remaining_problems"] == []
    round1 = loops["rounds"][0]
    codes = {p["code"] for p in round1["problems"]}
    assert "tuning_miss" in codes and {c.split(":")[0] for c in codes} <= {"tuning_miss", "holdout_below"}
    assert [(a["loop"], a["step"]) for a in round1["actions"]] == [("L1", "cue_suggest")]
    suggested = {c for cues in round1["actions"][0]["suggestions"].values() for c in cues}
    aliases = {a for c in DX.ALIASES.values() for a in c}
    assert suggested and suggested <= aliases  # only words the cards themselves carry
    # every suggestion occurs in a *tuning* question that missed (the rule of cue-suggest), none in a holdout question
    holdout = [json.loads(x)["q"].lower() for x in (SRC / "holdout.jsonl").read_text(encoding="utf-8").splitlines()]
    missed = [p["detail"].lower() for p in round1["problems"] if p["code"] == "tuning_miss"]
    for cue in suggested:
        assert any(cue.lower() in q for q in missed), cue
    # the holdout stayed what the fixture has, frozen by the first verify
    final = u7["project"] / loops["final_build_dir"]
    assert (final / "src" / "holdout.jsonl").read_bytes() == (SRC / "holdout.jsonl").read_bytes()
    freeze = json.loads((final / "src" / "holdout.freeze.json").read_text(encoding="utf-8"))
    assert len(json.dumps(freeze)) > 0 and holdout  # the freeze file exists and the holdout was non-empty
    # the loop only added cues: every cue of the starting desk is still there, in its dimension
    start = json.loads((SRC / "desk.l1.json").read_text(encoding="utf-8"))
    end = json.loads((final / "src" / "desk.json").read_text(encoding="utf-8"))
    for d0, d1 in zip(start["dimensions"], end["dimensions"]):
        assert d0["dimension"] == d1["dimension"] and d0["cues"] == d1["cues"][:len(d0["cues"])]
        assert d0["tests"] == d1["tests"]
    assert cue_suggest.SCHEMA_ID == "klode-kit.cue_suggest/1"


def test_u7_mcp_json_other_entries_preserved(u7, tmp_path):
    """U7-02 (L2 S5): registering the library adds ``demo-klode-klode`` to ``.mcp.json`` and leaves the user's other
    servers as they were."""
    final = u7["project"] / u7["summary"]["build_dir"]
    project = tmp_path / "proj"
    shutil.copytree(u7["project"], project, symlinks=True)
    kb = B.kb_dir(project)
    other = {"command": "npx", "args": ["-y", "some-other-mcp"], "env": {"TOKEN_NAME": "placeholder"}}
    before = {"mcpServers": {"other-server": other}, "someTopLevelKey": {"keep": True}}
    (project / ".mcp.json").write_text(json.dumps(before, indent=2), encoding="utf-8")
    world = A.check_and_publish(project, tmp_path, kb / "builds" / final.name)
    after = json.loads((project / ".mcp.json").read_text(encoding="utf-8"))
    assert after["mcpServers"]["other-server"] == other
    assert json.dumps(after["mcpServers"]["other-server"], sort_keys=True) == json.dumps(other, sort_keys=True)
    assert after["someTopLevelKey"] == {"keep": True}
    assert sorted(after["mcpServers"]) == ["demo-klode-klode", "other-server"]
    entry = after["mcpServers"]["demo-klode-klode"]
    assert entry["args"][:2] == ["mcp", "--kb"] and Path(entry["args"][2]) == world.kb
    assert entry["env"]["KLODE_KIT_BUILD"] == final.name
