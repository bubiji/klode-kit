"""Character error rate (CER) with one normalized view (WI-00c3; design 02 r2.7 "各路 CER（E35 口径）").

Ported from the 189 project ``src/ocr/cer.py`` (read-only source, 2026-10-23) and changed on purpose:

- **Numbers are only NFKC'd, never folded.** 189 also folded typographic quotes/dashes (``U+2212`` minus ->
  ``-``, en/em dash -> ``-``), expanded ligatures and dropped dot-leader runs (``...``) - fine for book prose,
  wrong for lab values: ``−5`` is not ``-5`` and ``1...5`` is not ``15``. Here the view is exactly:
  drop ``=== page NNNN ===`` marker lines, Unicode NFKC (``１０.５`` == ``10.5``, ``ﬁ`` -> ``fi``), drop all
  whitespace (OCR engines disagree on spacing; spacing is not a recognition error).
- No correction of any kind.

``cer(gold, hyp) = levenshtein(normalize(gold), normalize(hyp)) / len(normalize(gold))``; an empty gold view
gives ``0.0`` when the hypothesis view is empty too, else ``inf`` (defined, never raises).

CLI: ``python -m klode_kit.cer <gold.txt> <hyp.txt> [--json]`` or ``--selftest`` (the three 189 vectors).
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import unicodedata

PAGE_MARK = re.compile(r"^\s*===\s*page\s+\d+\s*===\s*$", re.M)
WS = re.compile(r"\s+")
USAGE_EXIT = 2

# The 189 self-test vectors, verbatim (name, gold, hyp, expected cer).
_SELFTEST = (
    ("identical", "特許請求の範囲", "特許請求の範囲", 0.0),
    ("one-sub", "見解書を提出", "見解半を提出", 1 / 6),
    ("normalize", "=== page 0012 ===\n国際出願  ＰＣＴ\n", "国際出願 PCT", 0.0),
)


def normalize(text: str) -> str:
    text = PAGE_MARK.sub("", text)
    text = unicodedata.normalize("NFKC", text)
    return WS.sub("", text)


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(gold: str, hyp: str) -> dict:
    g, h = normalize(gold), normalize(hyp)
    dist = levenshtein(g, h)
    rate = dist / len(g) if g else (0.0 if not h else math.inf)
    return {"gold_chars": len(g), "hyp_chars": len(h), "distance": dist, "cer": rate}


def selftest_cases() -> list[tuple[str, str, str, float]]:
    return [tuple(c) for c in _SELFTEST]  # type: ignore[misc]


def _selftest() -> int:
    bad = 0
    for name, g, h, want in _SELFTEST:
        got = cer(g, h)["cer"]
        ok = abs(got - want) < 1e-9
        bad += not ok
        print(f"[{'ok' if ok else 'FAIL'}] {name}: cer={got:.4f} want={want:.4f}")
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="klode_kit.cer", description="character error rate (NFKC view)")
    ap.add_argument("gold", nargs="?")
    ap.add_argument("hyp", nargs="?")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    try:
        a = ap.parse_args(argv)
    except SystemExit as exc:  # argparse usage error
        return USAGE_EXIT if exc.code else 0
    if a.selftest:
        return _selftest()
    if not (a.gold and a.hyp):
        print("klode_kit.cer: need <gold.txt> <hyp.txt> or --selftest", file=sys.stderr)
        return USAGE_EXIT
    with open(a.gold, encoding="utf-8") as fh:
        gold = fh.read()
    with open(a.hyp, encoding="utf-8") as fh:
        hyp = fh.read()
    r = cer(gold, hyp)
    if a.json:
        print(json.dumps(r, ensure_ascii=False))
    else:
        print(f"CER={r['cer']:.4f}  dist={r['distance']}  gold={r['gold_chars']}  hyp={r['hyp_chars']}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    sys.exit(main())
