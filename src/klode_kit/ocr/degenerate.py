"""Degenerate-output detection for local OCR routes (E15; config key ``repeat_ratio_max``).

``repeat_ratio`` is the share of non-blank lines that repeat an earlier line:
``(n - distinct) / n`` over stripped, non-blank lines; ``0.0`` when there are none.
A page is degenerate when the ratio is **strictly greater** than ``max_ratio`` (default 0.3,
matrix 00a-09). The integer division form keeps boundary values exact (30/100 == 0.3).

WI-04a may refine how MLX output is split into lines; the threshold rule stays here.
"""

from __future__ import annotations

from collections.abc import Iterable

DEFAULT_MAX_RATIO = 0.3


def repeat_ratio(lines: Iterable[str]) -> float:
    content = [line.strip() for line in lines if line.strip()]
    if not content:
        return 0.0
    return (len(content) - len(set(content))) / len(content)


def is_degenerate(ratio: float, max_ratio: float = DEFAULT_MAX_RATIO) -> bool:
    return ratio > max_ratio


def ngram_repeat_ratio(text: str, n: int = 6) -> float:
    """189 ``engines.repeat_ratio`` (MLX degenerate check, threshold ``> 0.5`` there): the share of repeated
    character ``n``-grams, ``1 - distinct / total``. It catches the single line that loops on a phrase, which the
    line-level ratio above cannot see (WI-04a alignment). The window bound is 189's, kept as is."""
    grams = [text[i:i + n] for i in range(0, max(0, len(text) - n))]
    return 0.0 if not grams else 1 - len(set(grams)) / len(grams)
