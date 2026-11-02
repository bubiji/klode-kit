"""WI-33m (migration finding P25): the self-check smoke test must pick an anchor that ``verify_quote`` can VERIFY -
one that occurs exactly once in its card's source and carries no ``#N`` pin. The first anchor of the first card can
be a phrase repeated in the source (a flowchart label drawn twice), which made self_check fail on a good build."""

from __future__ import annotations

from pathlib import Path

import pytest

from klode_kit.build import run_build as R

pytestmark = pytest.mark.unit


def _build(tmp_path: Path, cards: dict[str, tuple[str, str]]) -> R.Build:
    d = tmp_path / "b"
    (d / "library" / "cards").mkdir(parents=True)
    (d / "library" / "shelf").mkdir(parents=True)
    for cid, (card_text, source) in cards.items():
        (d / "library" / "cards" / f"{cid}.md").write_text(card_text, encoding="utf-8")
        (d / "library" / "shelf" / f"{cid}.txt").write_text(source, encoding="utf-8")
    b = R.Build.__new__(R.Build)
    b.dir = d
    return b


def test_repeated_first_anchor_is_skipped(tmp_path):
    b = _build(tmp_path, {"a-card": ("- x (grep: `a repeated label text`)\n- y (grep: `a unique sentence here`)\n",
                                     "a repeated label text\nfoo\na repeated label text\na unique sentence here\n")})
    assert b._smoke_target() == ("a-card", "a unique sentence here")


def test_pinned_anchor_is_skipped(tmp_path):
    b = _build(tmp_path, {"a-card": ("- x (grep: `pinned phrase text#2`)\n- y (grep: `another fine sentence`)\n",
                                     "pinned phrase text\npinned phrase text\nanother fine sentence\n")})
    assert b._smoke_target() == ("a-card", "another fine sentence")


def test_falls_through_to_a_later_card(tmp_path):
    b = _build(tmp_path, {"a-card": ("- x (grep: `twice twice twice`)\n", "twice twice twice\ntwice twice twice\n"),
                          "b-card": ("- y (grep: `only once in b`)\n", "only once in b\n")})
    assert b._smoke_target() == ("b-card", "only once in b")


def test_no_unique_anchor_falls_back_to_the_first(tmp_path):
    b = _build(tmp_path, {"a-card": ("- x (grep: `twice twice twice`)\n", "twice twice twice\ntwice twice twice\n")})
    assert b._smoke_target() == ("a-card", "twice twice twice")
