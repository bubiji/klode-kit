"""WI-00c3: character error rate, ported from 189 ``src/ocr/cer.py`` (matrix 00c3-06/07/08).

Contract of ``klode_kit.cer``:

- ``normalize(text)``: drop ``=== page NNNN ===`` marker lines, Unicode NFKC, drop all whitespace.
  Nothing else - in particular digits, decimal points and signs are only NFKC'd, never folded
  (no typographic dash/minus folding, no dot-leader removal: 02 r2.7 "各路 CER（E35 口径）").
- ``levenshtein(a, b)``: edit distance (insert / delete / substitute, each cost 1).
- ``cer(gold, hyp)`` -> ``{"gold_chars", "hyp_chars", "distance", "cer"}`` over the normalized views;
  ``cer = distance / gold_chars``; empty gold: ``0.0`` when hyp is empty too, else ``inf`` (never raises).
- ``selftest_cases()``: the three 189 self-test vectors ``(name, gold, hyp, want)``.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from klode_kit import cer as C

# The 189 self-test vectors, copied verbatim from 189 src/ocr/cer.py::_selftest (read-only source).
SELFTEST_189 = [
    ("identical", "特許請求の範囲", "特許請求の範囲", 0.0),
    ("one-sub", "見解書を提出", "見解半を提出", 1 / 6),
    ("normalize", "=== page 0012 ===\n国際出願  ＰＣＴ\n", "国際出願 PCT", 0.0),
]


@pytest.mark.parametrize("name,gold,hyp,want", SELFTEST_189, ids=[c[0] for c in SELFTEST_189])
def test_cer_selftest_port(name, gold, hyp, want):
    """00c3-06 (L1/S1): the ported function reproduces 189's expected values."""
    got = C.cer(gold, hyp)
    assert got["cer"] == pytest.approx(want, abs=1e-12)
    assert got["gold_chars"] == len(C.normalize(gold))
    assert got["hyp_chars"] == len(C.normalize(hyp))


def test_cer_selftest_vectors_shipped():
    """The module carries the same three vectors (the CLI --selftest runs them)."""
    assert [tuple(c) for c in C.selftest_cases()] == [tuple(c) for c in SELFTEST_189]
    assert C.main(["--selftest"]) == 0


def test_cer_numeric_not_folded():
    """00c3-07 (L1/S6): width forms of digits are the same number (NFKC); different numbers never are."""
    assert C.cer("10.5", "1.05")["cer"] > 0
    assert C.cer("１０.５", "10.5")["cer"] == 0
    assert C.cer("10.5", "１０．５")["cer"] == 0
    # beyond NFKC nothing is folded: a minus sign is not a hyphen, a leader run is kept
    assert C.cer("−5", "-5")["cer"] > 0
    assert C.cer("1...5", "15")["cer"] > 0
    assert C.normalize("4.35 – 5.1") == "4.35–5.1"


DIGITS_FULL = str.maketrans("0123456789.", "０１２３４５６７８９．")


@settings(max_examples=200, deadline=None)
@given(st.from_regex(r"[0-9]{1,4}(\.[0-9]{1,3})?", fullmatch=True),
       st.from_regex(r"[0-9]{1,4}(\.[0-9]{1,3})?", fullmatch=True))
@example("10.5", "1.05")
@example("128", "126")
def test_cer_numeric_metamorphic(a, b):
    """00c3-07 (S6): cer(a, b) is unchanged when either side is written in full-width forms, and is 0 iff a == b."""
    base = C.cer(a, b)["cer"]
    assert C.cer(a.translate(DIGITS_FULL), b)["cer"] == base
    assert C.cer(a, b.translate(DIGITS_FULL))["cer"] == base
    assert (base == 0) == (a == b)


@settings(max_examples=300, deadline=None)
@given(st.text(max_size=40), st.text(max_size=40))
@example("", "")
@example("", "x")
@example(" \n", "a")
def test_cer_identity_and_nonneg(x, y):
    """00c3-08 (L1/S4): cer(x, x) == 0, cer >= 0, empty gold is defined (0.0 or inf), never raises."""
    assert C.cer(x, x)["cer"] == 0.0
    r = C.cer(x, y)
    assert r["cer"] >= 0
    assert r["distance"] == C.levenshtein(C.normalize(x), C.normalize(y))
    if r["gold_chars"] == 0:
        assert r["cer"] == (0.0 if r["hyp_chars"] == 0 else math.inf)
    else:
        assert r["cer"] == r["distance"] / r["gold_chars"]


@settings(max_examples=200, deadline=None)
@given(st.text(max_size=25), st.text(max_size=25))
def test_levenshtein_metric_laws(a, b):
    """Edit distance is symmetric, bounded by the longer string, and zero exactly on equality."""
    d = C.levenshtein(a, b)
    assert d == C.levenshtein(b, a)
    assert abs(len(a) - len(b)) <= d <= max(len(a), len(b))
    assert (d == 0) == (a == b)


def test_normalize_drops_page_marks_and_whitespace_only():
    assert C.normalize("=== page 0001 ===\n血红蛋白  128\tg/L\n") == "血红蛋白128g/L"
    assert C.normalize("ＡＢＣ　１２３") == "ABC123"


def test_cli_files_and_json(tmp_path, capsys):
    gold, hyp = tmp_path / "g.txt", tmp_path / "h.txt"
    gold.write_text("白细胞 6.2\n", encoding="utf-8")
    hyp.write_text("白细胞 6.8\n", encoding="utf-8")
    assert C.main(["--json", str(gold), str(hyp)]) == 0
    out = capsys.readouterr().out
    assert '"distance": 1' in out and '"gold_chars": 6' in out
    assert C.main([str(gold), str(hyp)]) == 0
    assert "CER=0.1667" in capsys.readouterr().out
    assert C.main([]) == 2
