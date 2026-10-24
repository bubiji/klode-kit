"""WI-02d: ``build/smoke_mcp`` - klode-mcp smoke run (matrix 02d-07; failure paths at unit level).

Specification (03 A5 and §5 次要; 02 §3.1 A5 "采纳：默认 query 改取 alias 首项"; vendored from the user's
klode-build ``smoke_mcp.py``):

- ``klode-kit smoke-mcp KB --card ID --real PHRASE [--query Q] [--desk SYMPTOM=DIM] [--timeout S]`` starts
  ``klode-mcp -c KB/library.toml`` (found on PATH) and runs initialize -> tools/list -> search_sources(full) ->
  zoom_card thin -> verify_quote real -> verify_quote fake; ``--desk`` adds list_lenses / diagnose /
  consult_dimension. One ``PASS|FAIL  <label>`` line per check; exit 0 when all pass, 1 otherwise. Availability is
  judged only by the run (kept from the original).
- **A5**: without ``--query`` the search query is the card's first alias (``aliases: [...]`` in the card's
  front matter); a card without aliases falls back to the original rule (the first two words of ``--real``).
- Hardening: a server that dies, answers an error or stops answering within ``--timeout`` makes the remaining
  checks FAIL (exit 1) instead of crashing or hanging; no ``klode-mcp`` on PATH -> exit 40.

The ``klode-mcp`` child process is a boundary (plan §0.4): here a scripted stand-in written into the test's tmp
directory and put first on PATH. Paired real-path tests: ``tests/cli/test_smoke_mcp_cli.py`` (02d-06).

No law: a fixed sequence of probes against one server (matrix WI-02d header).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from klode_kit.build import smoke_mcp as M

pytestmark = pytest.mark.unit

CARD = """---
id: {cid}
shelf: items
zoom: full
aliases: [{aliases}]
---

# {cid}

<!-- scaffold: managed by klode - edit Thin/Full below the marker by hand -->

## Thin
text
"""
REAL = "血红蛋白 135 g/L 2026-09-18"

FAKE_SERVER = r'''#!{python}
import json, sys, time
sc = json.load(open({scenario!r}, encoding="utf-8"))
log = open({log!r}, "a", encoding="utf-8")
for line in sys.stdin:
    msg = json.loads(line)
    log.write(line)
    log.flush()
    if "id" not in msg:
        continue
    m = msg["method"]
    if m in sc.get("die_on", []):
        sys.exit(3)
    if m in sc.get("hang_on", []):
        time.sleep(60)
    if m == "initialize":
        res = {{"serverInfo": {{"name": sc.get("server", "klode"), "version": "0.5.0"}}}}
    elif m == "tools/list":
        res = {{"tools": [{{"name": n}} for n in sc["tools"]]}}
    else:
        name = msg["params"]["name"]
        if name in sc.get("error_on", []):
            print(json.dumps({{"jsonrpc": "2.0", "id": msg["id"], "error": {{"code": -32000, "message": "boom"}}}}),
                  flush=True)
            continue
        args = msg["params"]["arguments"]
        key = name + ":" + args.get("phrase", args.get("query", args.get("symptom", args.get("dimension", ""))))
        text = sc["answers"].get(key, sc["answers"].get(name, ""))
        res = {{"content": [{{"type": "text", "text": text}}], "isError": False}}
    print(json.dumps({{"jsonrpc": "2.0", "id": msg["id"], "result": res}}, ensure_ascii=False), flush=True)
'''
TOOLS = ["list_lenses", "diagnose", "consult_dimension", "search_sources", "zoom_card", "verify_quote", "list_kbs"]
GOOD = {
    "search_sources": "1 of 1 matching cards (most relevant first):\n  hemoglobin  Hemoglobin",
    "zoom_card": "[kb]\n# Hemoglobin  (thin)\n\n" + "血红蛋白三次化验结果，最近一次为 135 g/L。" * 3,
    f"verify_quote:{REAL}": f"[kb]\nVERIFIED — '{REAL}' occurs in library/items/hemoglobin.txt:",
    "verify_quote": "[kb]\nNOT FOUND — 'x' does not resolve in library/items/hemoglobin.txt.",
    "list_lenses": "dosage-history  剂量变化",
    "diagnose": "  • dosage-history\n    剂量怎么变",
    "consult_dimension": "## Craft\n### 机制",
}


def make_kb(root: Path, aliases: str = "血红蛋白, hemoglobin, Hb") -> Path:
    kb = root / "kb"
    (kb / "library" / "cards").mkdir(parents=True)
    (kb / "library.toml").write_text('[library]\nid = "med"\n', encoding="utf-8")
    (kb / "library" / "cards" / "hemoglobin.md").write_text(CARD.format(cid="hemoglobin", aliases=aliases),
                                                            encoding="utf-8")
    return kb


@pytest.fixture
def fake_mcp(tmp_path, monkeypatch):
    """Write a scripted ``klode-mcp`` first on PATH; returns ``(set_scenario, read_log)``."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    scenario, log = tmp_path / "scenario.json", tmp_path / "mcp.log"
    exe = bindir / "klode-mcp"
    exe.write_text(FAKE_SERVER.format(python=sys.executable, scenario=str(scenario), log=str(log)),
                   encoding="utf-8")
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ["PATH"])

    def set_scenario(**overrides):
        sc = {"tools": TOOLS, "answers": dict(GOOD)}
        sc.update(overrides)
        scenario.write_text(json.dumps(sc, ensure_ascii=False), encoding="utf-8")

    def calls() -> list[dict]:
        return [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []

    return set_scenario, calls


def run(kb: Path, *extra: str) -> int:
    return M.main([str(kb), "--card", "hemoglobin", "--real", REAL, *extra])


def lines(out: str) -> list[str]:
    return [ln for ln in out.splitlines() if ln.startswith(("PASS", "FAIL"))]


# --------------------------------------------------------------------------- 02d-07 default query


def test_smoke_default_query_first_alias(tmp_path):
    """02d-07 (S1): the default query is the card's first alias, verbatim (a multi-word or CJK alias stays
    whole)."""
    assert M.default_query(make_kb(tmp_path / "a"), "hemoglobin", REAL) == "血红蛋白"
    kb = make_kb(tmp_path / "b", aliases="night lamp, wick")
    assert M.default_query(kb, "hemoglobin", "To reset the lamp hold the dial") == "night lamp"


def test_default_query_without_aliases(tmp_path):
    """02d-07 boundary: ``aliases: []`` (or no card file) -> the original rule, the first two words of --real."""
    kb = make_kb(tmp_path, aliases="")
    assert M.default_query(kb, "hemoglobin", "one part white vinegar") == "one part"
    assert M.default_query(kb, "no-such-card", "one part white vinegar") == "one part"


def test_default_query_sent_to_search(tmp_path, fake_mcp, capsys):
    """02d-07 through the command: without --query the search_sources call carries the first alias and full=true;
    an explicit --query wins."""
    set_scenario, calls = fake_mcp
    set_scenario()
    kb = make_kb(tmp_path)
    assert run(kb) == 0
    search = [c for c in calls() if c.get("params", {}).get("name") == "search_sources"]
    assert [c["params"]["arguments"] for c in search] == [{"query": "血红蛋白", "full": True}]
    assert run(kb, "--query", "Hb 135") == 0
    search = [c for c in calls() if c.get("params", {}).get("name") == "search_sources"]
    assert search[-1]["params"]["arguments"]["query"] == "Hb 135"


# --------------------------------------------------------------------------- kept judgement, failure paths


def test_six_checks_pass_and_desk_three(tmp_path, fake_mcp, capsys):
    """Kept: six PASS lines and exit 0; ``--desk`` adds exactly three more."""
    set_scenario, _ = fake_mcp
    set_scenario()
    kb = make_kb(tmp_path)
    assert run(kb) == 0
    out = lines(capsys.readouterr().out)
    assert len(out) == 6 and all(ln.startswith("PASS") for ln in out)
    assert run(kb, "--desk", "剂量怎么变=dosage-history") == 0
    out = lines(capsys.readouterr().out)
    assert len(out) == 9 and all(ln.startswith("PASS") for ln in out)


@pytest.mark.parametrize("change,label", [
    ({"server": "other"}, "initialize"),
    ({"tools": ["search_sources", "zoom_card"]}, "tools/list"),
    ({"answers": {**GOOD, "search_sources": "0 matching cards"}}, "search_sources"),
    ({"answers": {**GOOD, "zoom_card": "short"}}, "zoom_card"),
    ({"answers": {**GOOD, f"verify_quote:{REAL}": "[kb]\nNOT FOUND — nope"}}, "真句"),
    ({"answers": {**GOOD, "verify_quote": "[kb]\nVERIFIED — fake"}}, "伪句"),
])
def test_each_check_can_fail(tmp_path, fake_mcp, capsys, change, label):
    """Kept judgement (S3 per check): breaking one answer makes exactly that check FAIL and the exit 1."""
    set_scenario, _ = fake_mcp
    set_scenario(**change)
    assert run(make_kb(tmp_path)) == 1
    out = lines(capsys.readouterr().out)
    fails = [ln for ln in out if ln.startswith("FAIL")]
    assert len(fails) == 1 and label in fails[0]


def test_server_error_and_death_fail_not_crash(tmp_path, fake_mcp, capsys):
    """Hardening: a JSON-RPC error on zoom_card fails that check only; a server that exits on tools/list fails
    every remaining check - exit 1 in both cases, never a traceback."""
    set_scenario, _ = fake_mcp
    set_scenario(error_on=["zoom_card"])
    kb = make_kb(tmp_path)
    assert run(kb) == 1
    fails = [ln for ln in lines(capsys.readouterr().out) if ln.startswith("FAIL")]
    assert len(fails) == 1 and "zoom_card" in fails[0]
    set_scenario(die_on=["tools/list"])
    assert run(kb) == 1
    out = lines(capsys.readouterr().out)
    assert out[0].startswith("PASS  initialize")
    assert len(out) == 6 and all(ln.startswith("FAIL") for ln in out[1:])


def test_server_hang_times_out(tmp_path, fake_mcp, capsys):
    """Hardening: a server that stops answering fails within ``--timeout`` (no hang)."""
    set_scenario, _ = fake_mcp
    set_scenario(hang_on=["tools/list"])
    assert run(make_kb(tmp_path), "--timeout", "1") == 1
    out = lines(capsys.readouterr().out)
    assert out[0].startswith("PASS") and all(ln.startswith("FAIL") for ln in out[1:])


def test_no_klode_mcp_is_env_error(tmp_path, monkeypatch, capsys):
    """Guard: no klode-mcp on PATH -> exit 40, nothing probed."""
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert run(make_kb(tmp_path)) == 40
    assert "klode-mcp" in capsys.readouterr().err


def test_bad_desk_arg_is_usage(tmp_path, fake_mcp):
    """Guard: ``--desk`` without ``=`` is a usage error (2)."""
    set_scenario, _ = fake_mcp
    set_scenario()
    assert run(make_kb(tmp_path), "--desk", "no-equals") == 2
