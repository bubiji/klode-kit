"""WI-00d2: minimal check_cards-style gate used by the host spike (WI-02a's ``build/check_cards`` is not vendored yet).

``klode-kit check-cards <cards_content/<id>.md> [--kb DIR]`` (cwd = the build directory by default) checks one
authored card against its source ``library/*/<id>.txt``, with the rules of the user's klode-build
``check_cards.py`` (F1 aliases first line >= 10; F2 ``## Thin`` with an anchor; F3 ``## Full`` with a ``###``
section and an anchor on every ``- `` bullet; A1 anchor >= 15 chars; A2 verbatim exactly once, or >= N with
``#N``; A3 no newline / outer whitespace) plus the authored rule ``min_anchors_full`` = 5 anchors in Full
(r2.6, R2-02). Exit 0 PASS, 1 FAIL (problems on stdout), 2 usage.

No law: a fixed rule list over one document; examples per rule (S2/S3).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from _support.flows import REPO, base_env, run_module
from klode_kit.devtools import spike_check_card as scc

pytestmark = pytest.mark.unit

SOURCE = (REPO / "tests" / "fixtures" / "fx-book-aurora" / "aurora-kettle.txt").read_text(encoding="utf-8")
CH1 = SOURCE[SOURCE.index("Chapter 1:"):SOURCE.index("Chapter 2:")]

A = [
    "copper shell that is hammered thin and then annealed twice",
    "Annealing relaxes the metal so that the shell can expand",
    "A brass rim is folded over the upper edge of the shell",
    "A domed base flexes outward when heated",
    "Makers tin the inside of the shell with a thin food-safe layer",
    "Retinning is needed roughly once every ten years of daily use",
]
ALIASES = "aliases: [copper shell, kettle shell, annealing, brass rim, domed base, tinning, retinning, food-safe layer, hammered copper, kettle body]\n"


def card(aliases: str = ALIASES, thin: str | None = None, full: list[str] | None = None) -> str:
    thin = thin if thin is not None else f"The shell is copper (grep: `{A[0]}`).\n"
    full = full if full is not None else [f"- point {i} (grep: `{a}`)" for i, a in enumerate(A)]
    return aliases + "\n# The Copper Shell\n\n## Thin\n" + thin + "\n## Full\n### Shell\n" + "\n".join(full) + "\n"


@pytest.fixture
def kb(tmp_path):
    root = tmp_path / "builds" / "b-1"
    (root / "library" / "book").mkdir(parents=True)
    (root / "library" / "book" / "ch01.txt").write_text(CH1, encoding="utf-8")
    (root / "cards_content").mkdir()
    return root


def _write(kb: Path, text: str) -> str:
    (kb / "cards_content" / "ch01.md").write_text(text, encoding="utf-8")
    return "cards_content/ch01.md"


def test_good_card_passes(kb):
    assert scc.check(kb, _write(kb, card())) == []


@pytest.mark.parametrize("text, rule", [
    pytest.param(card(aliases="aliases: [a, b, c]\n"), "F1", id="F1-too-few-aliases"),
    pytest.param(card(aliases="title: x\n"), "F1", id="F1-no-aliases-line"),
    pytest.param(card(thin="no anchors here\n"), "F2", id="F2-thin-without-anchor"),
    pytest.param(card().replace("## Thin\n", "## Thinner\n"), "F2", id="F2-no-thin"),
    pytest.param(card().replace("### Shell\n", ""), "F3", id="F3-no-subsection"),
    pytest.param(card(full=[f"- p (grep: `{a}`)" for a in A] + ["- bare bullet"]), "F3", id="F3-bullet-without-anchor"),
    pytest.param(card(full=[f"- p (grep: `{a}`)" for a in A] + ["- short (grep: `the shell`)"]), "A1", id="A1-short"),
    pytest.param(card(full=[f"- p (grep: `{a}`)" for a in A] + ["- x (grep: `this sentence is not in the chapter`)"]), "A2", id="A2-not-found"),
    pytest.param(card(full=[f"- p (grep: `{a}`)" for a in A] + ["- x (grep: ` so that the shell can expand`)"]), "A3", id="A3-outer-space"),
    pytest.param(card(full=[f"- p (grep: `{a}`)" for a in A[:4]]), "MIN_FULL", id="min-anchors-full-4"),
])
def test_each_rule_fails(kb, text, rule):
    problems = scc.check(kb, _write(kb, text))
    assert problems and any(p.startswith(rule) for p in problems), problems


def test_ambiguous_anchor_fails_and_hash_n_resolves(kb):
    """A2 boundary: a phrase occurring twice is AMBIGUOUS; ``#2`` accepts it; ``#3`` (more than exist) fails."""
    amb = "the shell can expand without"
    assert CH1.count(amb) == 1
    (kb / "library" / "book" / "ch01.txt").write_text(CH1 + "\n" + amb + " cracking.\n", encoding="utf-8")
    extra = [f"- p (grep: `{a}`)" for a in A]
    assert any(p.startswith("A2") for p in scc.check(kb, _write(kb, card(full=extra + [f"- y (grep: `{amb}`)"]))))
    assert scc.check(kb, _write(kb, card(full=extra + [f"- y (grep: `{amb}` #2)"]))) == []
    assert any(p.startswith("A2") for p in scc.check(kb, _write(kb, card(full=extra + [f"- y (grep: `{amb}` #3)"]))))


def test_missing_card_or_source(kb):
    assert scc.check(kb, "cards_content/ch09.md")[0].startswith("MISSING")
    (kb / "cards_content" / "ch02.md").write_text(card(), encoding="utf-8")
    assert scc.check(kb, "cards_content/ch02.md")[0].startswith("MISSING")


def test_cli_exit_codes(kb):
    good = _write(kb, card())
    env = base_env()
    ok = run_module("klode_kit", ["check-cards", good], env, cwd=kb)
    assert ok.returncode == 0, ok.stderr.decode()
    assert b"PASS" in ok.stdout
    _write(kb, card(aliases="aliases: [a]\n"))
    bad = run_module("klode_kit", ["check-cards", good, "--kb", str(kb)], env)
    assert bad.returncode == 1
    assert b"F1" in bad.stdout
    usage = run_module("klode_kit", ["check-cards"], env, cwd=kb)
    assert usage.returncode == 2
    outside = run_module("klode_kit", ["check-cards", "../x.md"], env, cwd=kb)
    assert outside.returncode == 2
