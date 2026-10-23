"""WI-07b: 07b-12 in its user-approved form (2026-10-23), more examples than the recorded test.

Row positions of deletions are ambiguous: base ``A A U A`` with cloud ``A A`` comes from deleting {1, 2} or {2, 3};
base ``A M A M A`` with cloud ``A M A`` from deleting {1, 2} or {3, 4} - one input, two truths. So ``unaligned`` is
judged by value equivalence, like the pairs: the texts of the unaligned rows equal the texts of the deleted rows as a
multiset.
"""

from __future__ import annotations

from collections import Counter

import pytest
from hypothesis import HealthCheck, example, given, settings

from klode_kit.audit import align as A
from unit.test_align import cloud, codes, edited_pages, geo_line

pytestmark = pytest.mark.unit

A_, U, M = "血红蛋白 128 g/L", "尿素 7.3 mmol/L", "肌酐 64 pg"


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(edited_pages())
@example(([A_, A_, A_, A_, A_, U, A_], [A_, A_, f"{A_} {A_}", A_], [("row", 0), ("row", 1), ("merge", 3), ("row", 6)],
          [2, 5]))
@example(([A_, A_, U, A_], [A_, A_], [("row", 0), ("row", 1)], [2, 3]))
@example(([A_, A_, U, A_], [A_, A_], [("row", 0), ("row", 3)], [1, 2]))
@example(([A_, M, A_, M, A_], [A_, M, A_], [("row", 0), ("row", 3), ("row", 4)], [1, 2]))
@example(([A_, M, A_, M, A_], [A_, M, A_], [("row", 0), ("row", 1), ("row", 2)], [3, 4]))
def test_unaligned_text_multiset_equals_deleted(case):
    """07b-12 corrected (L1-S4, user-approved 2026-10-23): pairs are value-equivalent to the truth, every surviving
    line is paired, and the texts of the ``unaligned`` rows equal the texts of the deleted rows as a multiset (row
    positions are ambiguous: base A M A M A with cloud A M A comes from deleting {1, 2} or {3, 4})."""
    texts, lines, where, deleted = case
    al = A.align({"vision": [geo_line("vision", i, t, i) for i, t in enumerate(texts)], "claude": cloud("claude", lines)})
    for p in al.pairs["claude"]:
        w = where[p.seq]
        want = (texts[w[1]],) if w[0] == "row" else (texts[w[1]], texts[w[1] + 1])
        assert tuple(texts[r] for r in p.rows) == want, (p, w)
    assert {p.seq for p in al.pairs["claude"]} == {j for j, w in enumerate(where) if w[0] != "insert"}
    assert Counter(texts[r] for r in codes(al, "unaligned:claude")) == Counter(texts[d] for d in deleted)
