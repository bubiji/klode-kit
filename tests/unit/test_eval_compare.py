"""WI-02c: ``build/eval_compare`` - same gold questions over several klode libraries (B3 in ``--desk-gold``).

Specification (03 B3; vendored from the user's klode-build ``eval_compare.py``):

- Kept: ``--gold`` lines ``{"q", "gold": [card id, ...], "kind"}``; each library is searched with
  ``klode --json -c <toml> search --full <q words>``; card-level Hit@1 / Hit@k / MRR per library, a per-kind table
  and a per-question rank table (``—`` = not in the top k); ``--out`` also writes the report.
- **B3**: ``--desk-gold`` routing (lines ``{"q", "gold": <dimension>}``) is read from ``klode --json diagnose``
  (shared with desk_build), so a dimension without a hyphen is scored right.
- New: no ``klode`` on PATH is exit 40 (the original ran a hard-coded ``~/.local/bin/klode``).

No law: a report over recorded answers.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from _support.desk_fx import FakeKlode
from klode_kit.build import eval_compare as E

pytestmark = pytest.mark.unit


@pytest.fixture
def setup(tmp_path, monkeypatch):
    fk = FakeKlode(tmp_path / "fakebin")
    fk.install(monkeypatch)
    gold = tmp_path / "gold.jsonl"
    gold.write_text("".join(json.dumps(x) + "\n" for x in [
        {"q": "copper shell annealing", "gold": ["ch01"], "kind": "精确"},
        {"q": "two tone whistle", "gold": ["ch02"], "kind": "意译"},
        {"q": "winter storage", "gold": ["ch03"], "kind": "意译"},
    ]), encoding="utf-8")
    desk = tmp_path / "desk_gold.jsonl"
    desk.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in [
        {"q": "is it safe to leave it on the embers", "gold": "safety"},
        {"q": "how does the whistle work", "gold": "whistle-valve"},
    ]), encoding="utf-8")
    fk.set(search={"copper shell annealing": ["ch01", "ch02"], "two tone whistle": ["ch03", "ch02"],
                   "winter storage": []})
    fk.route({"is it safe to leave it on the embers": ["safety"], "how does the whistle work": ["whistle-valve"]})
    return tmp_path, gold, desk


def test_search_metrics_and_desk_routing(setup, capsys):
    tmp, gold, desk = setup
    out_md = tmp / "out" / "latest.md"
    rc = E.main(["--gold", str(gold), "--kb", f"demo={tmp / 'library.toml'}", "--k", "5",
                 "--desk-gold", str(desk), "--out", str(out_md)])
    assert rc == 0
    assert out_md.is_file(), "expected the --out report"
    text = out_md.read_text(encoding="utf-8")
    assert "| demo | 0.33 | 0.67 | 0.50 |" in text          # ranks 1, 2, none
    assert "| copper shell annealing | 1 |" in text and "| winter storage | — |" in text
    assert "| demo | 2/2 | 1.00 |" in text                  # B3: `safety` scored right
    assert "| is it safe to leave it on the embers | safety | ✅ safety |" in text
    assert text in capsys.readouterr().out


def test_klode_missing_exit40(setup, monkeypatch, capsys):
    tmp, gold, _ = setup
    monkeypatch.setenv("PATH", str(tmp / "empty-bin"))
    assert E.main(["--gold", str(gold), "--kb", f"demo={tmp / 'library.toml'}"]) == 40
    assert "klode" in capsys.readouterr().err


def test_unparseable_search_counts_as_miss(setup, capsys):
    tmp, gold, _ = setup
    (tmp / "fakebin" / "scenario.json").write_text(json.dumps({"search": None}), encoding="utf-8")
    rc = E.main(["--gold", str(gold), "--kb", f"demo={tmp / 'library.toml'}"])
    assert rc == 0
    assert "| demo | 0.00 | 0.00 | 0.00 |" in capsys.readouterr().out
