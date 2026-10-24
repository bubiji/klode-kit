"""WI-02a: ``build/fix_anchors`` - the vendored anchor repair (matrix 02a-05, 02a-06; 03 A3, B1; 02 §3.3 B1).

Specification:

- ``variants(q)`` yields the equivalent spellings of an anchor: every place where the card wrote ``'`` ``"``
  ``--`` ``-`` ``...`` may be the source's ``’‘`` ``“”`` ``—–`` ``—–‐`` ``…``. B1: ``"--"`` and ``"-"`` start at
  the same place and the original kept the short one, so ``"reset -- this"`` never became ``"reset — this"``;
  the long match must be among the variants. Everything the original produced is still produced (02a-06).
- ``fix_one(kb, cid) -> (fixed, left)``: for each anchor of ``cards_content/<cid>.md`` not found verbatim in
  ``library/*/<cid>.txt``: an anchor with outer whitespace whose stripped form is found once is stripped; else
  the first variant found **exactly once** replaces it (``#N`` kept); else it is left and listed. The card file is
  rewritten only when something was fixed.
- CLI ``klode-kit fix-anchors <kb> [id ...]``: exit 0 nothing left, 1 something left, 2 usage.

Laws: the new variant set is a superset of the original's (02a-06, S6 metamorphic: old implementation as the
relation's other side); for any source anchor written with ASCII stand-ins, fix_one restores it exactly (S4).
"""

from __future__ import annotations

import itertools
import re
import tempfile
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from klode_kit.build import fix_anchors as F

pytestmark = pytest.mark.unit


# The original ``variants`` of the user's klode-build skill (scripts/fix_anchors.py, read 2026-10-24), verbatim,
# as the other side of the 02a-06 relation.
LEGACY_SUBS = [
    ("'", ["’", "‘"]),
    ('"', ["“", "”"]),
    ("--", ["—", "–"]),
    ("-", ["—", "–", "‐"]),
    ("...", ["…"]),
]


def legacy_variants(q: str):
    spots = []
    for src, dsts in LEGACY_SUBS:
        for m in re.finditer(re.escape(src), q):
            spots.append((m.start(), m.end(), [src] + dsts))
    spots.sort()
    pruned, last_end = [], -1
    for s, e, opts in spots:
        if s >= last_end:
            pruned.append((s, e, opts)); last_end = e
    if not pruned or len(pruned) > 12:
        return
    for choice in itertools.product(*[o for _, _, o in pruned]):
        out, pos = [], 0
        for (s, e, _), c in zip(pruned, choice):
            out.append(q[pos:s]); out.append(c); pos = e
        out.append(q[pos:])
        yield "".join(out)


# --------------------------------------------------------------------------- 02a-05 B1


def test_b1_longest_match():
    """02a-05 (L1/S3): ``"reset -- this"`` -> the variants contain ``"reset — this"`` (and the en dash form)."""
    vs = set(F.variants("reset -- this"))
    assert "reset — this" in vs
    assert "reset – this" in vs


def test_b1_fix_one_repairs_double_hyphen(tmp_path):
    """03 step 10: the card's ``--`` against the source's em dash is now repaired (the original left it)."""
    kb = make_kb(tmp_path, "Hold the button to reset — this clears the timer.\n",
                 "aliases: [a]\n## Thin\nx (grep: `to reset -- this clears`)\n")
    assert F.fix_one(kb, "ch01") == (1, [])
    assert "(grep: `to reset — this clears`)" in card(kb)


# --------------------------------------------------------------------------- 02a-06 superset

SUBST_ALPHABET = st.sampled_from(list("ab -'\".…—–’"))


@settings(max_examples=300, deadline=None)
@given(q=st.text(SUBST_ALPHABET, max_size=12).filter(lambda s: sum(s.count(c) for c in "-'\".") <= 6))
def test_fix_anchors_variants_superset(q):
    """02a-06 (L1/S6): for any anchor text, set(new variants) ⊇ set(original variants)."""
    old = set(legacy_variants(q))
    new = set(F.variants(q))
    assert old <= new


def test_variants_bounds():
    assert list(F.variants("no substitutable characters")) == []
    assert list(F.variants("'" * 13)) == []  # more than 12 places: none, as before
    assert set(F.variants("it's")) == {"it's", "it’s", "it‘s"}
    assert set(F.variants("wait...")) == {"wait...", "wait…"}


# --------------------------------------------------------------------------- fix_one / CLI


def make_kb(root: Path, source: str, content: str, cid: str = "ch01") -> Path:
    kb = root / "kb"
    (kb / "library" / "book").mkdir(parents=True, exist_ok=True)
    (kb / "cards_content").mkdir(exist_ok=True)
    (kb / "library" / "book" / f"{cid}.txt").write_text(source, encoding="utf-8")
    (kb / "cards_content" / f"{cid}.md").write_text(content, encoding="utf-8")
    return kb


def card(kb: Path, cid: str = "ch01") -> str:
    return (kb / "cards_content" / f"{cid}.md").read_text(encoding="utf-8")


SRC = "The kettle’s rim is “folded” over the edge… so the seam hides — and the mouth stiffens.\n" \
      "A second line repeats the seam twice: the seam, the seam.\n"


def test_fix_one_cases(tmp_path):
    content = (
        "aliases: [a]\n## Thin\n"
        "ok (grep: `rim is “folded” over`)\n"            # found verbatim: untouched
        "q (grep: `The kettle's rim`)\n"                  # straight quote -> curly
        "d (grep: `\"folded\" over the edge...` #1)\n"    # quotes + ellipsis, #N kept
        "w (grep: ` so the seam hides `)\n"               # outer whitespace, stripped form unique
        "a (grep: `the seam'`)\n"                         # nothing unique: left
    )
    kb = make_kb(tmp_path, SRC, content)
    fixed, left = F.fix_one(kb, "ch01")
    assert fixed == 3
    assert left == ["the seam'"]
    text = card(kb)
    assert "(grep: `rim is “folded” over`)" in text
    assert "(grep: `The kettle’s rim`)" in text
    assert "(grep: `“folded” over the edge…` #1)" in text
    assert "(grep: `so the seam hides`)" in text
    assert "(grep: `the seam'`)" in text


def test_fix_one_whitespace_not_unique_tries_variants(tmp_path):
    """Outer whitespace with a stripped form found twice is not stripped blindly; left when nothing is unique."""
    kb = make_kb(tmp_path, SRC, "aliases: [a]\n(grep: ` the seam `) (grep: `edge... so` #2)\n")
    fixed, left = F.fix_one(kb, "ch01")
    assert fixed == 1
    assert left == [" the seam "]
    assert "(grep: `edge… so` #2)" in card(kb)


def test_fix_one_no_change_leaves_file_untouched(tmp_path):
    content = "aliases: [a]\n(grep: `rim is “folded” over`) (grep: `absent sentence`)\n"
    kb = make_kb(tmp_path, SRC, content)
    path = kb / "cards_content" / "ch01.md"
    before = path.stat().st_mtime_ns
    assert F.fix_one(kb, "ch01") == (0, ["absent sentence"])
    assert path.read_text(encoding="utf-8") == content
    assert path.stat().st_mtime_ns == before


def test_fix_one_missing_source_or_card(tmp_path):
    kb = make_kb(tmp_path, SRC, "aliases: [a]\n")
    (kb / "cards_content" / "ch02.md").write_text("aliases: [a]\n(grep: `x`)\n", encoding="utf-8")
    fixed, left = F.fix_one(kb, "ch02")
    assert fixed == 0 and len(left) == 1 and "ch02" in left[0]
    fixed, left = F.fix_one(kb, "ch09")
    assert fixed == 0 and len(left) == 1 and "ch09" in left[0]


def test_cli(tmp_path, capsys):
    kb = make_kb(tmp_path, SRC, "aliases: [a]\n(grep: `The kettle's rim`)\n")
    (kb / "cards_content" / "ch02.md").write_text("aliases: [a]\n(grep: `nowhere at all here`)\n", encoding="utf-8")
    (kb / "library" / "book" / "ch02.txt").write_text("other text\n", encoding="utf-8")
    assert F.main([str(kb), "ch01"]) == 0
    assert "ch01: 修好 1，仍找不到 0" in capsys.readouterr().out
    assert F.main([str(kb)]) == 1
    out = capsys.readouterr().out
    assert "ch02: 修好 0，仍找不到 1" in out and "nowhere at all here" in out
    assert F.main([]) == 2


# --------------------------------------------------------------------------- S4 restore property

TYPO = {"’": ["'"], "‘": ["'"], "“": ['"'], "”": ['"'], "—": ["--", "-"], "–": ["--", "-"], "‐": ["-"],
        "…": ["..."]}
WORD = st.text(st.sampled_from("abcdefghij"), min_size=1, max_size=5)


@st.composite
def anchor_and_ugly(draw):
    words = draw(st.lists(WORD, min_size=2, max_size=6))
    marks = draw(st.lists(st.sampled_from(sorted(TYPO)), min_size=1, max_size=len(words) - 1))
    spaced = draw(st.lists(st.booleans(), min_size=len(marks), max_size=len(marks)))
    true, ugly = [words[0]], [words[0]]
    for k, (m, sp) in enumerate(zip(marks, spaced)):
        sep = " " if sp else ""
        true += [sep, m, sep, words[k + 1]]
        ugly += [sep, draw(st.sampled_from(TYPO[m])), sep, words[k + 1]]
    for w in words[len(marks) + 1:]:
        true += [" ", w]
        ugly += [" ", w]
    return "".join(true), "".join(ugly)


@settings(max_examples=200, deadline=None)
@given(pair=anchor_and_ugly(), pad=st.text(st.sampled_from("0123456789 "), max_size=20))
def test_fix_restores_source_spelling(pair, pad):
    """S4: a source phrase written with ASCII stand-ins (marks separated by words) is restored exactly."""
    true, ugly = pair
    with tempfile.TemporaryDirectory() as tmp:
        kb = make_kb(Path(tmp), pad + true + pad + "\n", f"aliases: [a]\n(grep: `{ugly}` #1)\n")
        fixed, left = F.fix_one(kb, "ch01")
        assert (fixed, left) == ((1, []) if ugly != true else (0, []))
        assert card(kb) == f"aliases: [a]\n(grep: `{true}` #1)\n"
