"""WI-09b: mechanical desk syntheses of the medical build (``klode_kit.build.gen_synthesis``).

Law (S5, closure under the gate): whatever cards a dimension names, the synthesis written for it passes
``check_synthesis`` S1-S6 - every anchor it holds is copied verbatim from one of the dimension's own cards, nothing
else is invented - and writing is deterministic (same cards, same bytes) and never overwrites.
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from _support import desk_fx as DX
from klode_kit.build import check_synthesis as CS
from klode_kit.build import gen_synthesis as GS

pytestmark = pytest.mark.unit


def _spec_dims(n_cards: int):
    cids = list(DX.CH)[:n_cards]
    return [{"dimension": "dim-one", "title": "Dim one", "question": "什么？", "cards": cids}]


@settings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(st.integers(1, 3))
def test_written_synthesis_passes_the_synthesis_gate(tmp_path_factory, n):
    kb = DX.make_kb(tmp_path_factory.mktemp("kb"), syntheses=False)
    assert GS.write(kb, _spec_dims(n)) == ["dim-one"]
    rc, lines = CS.run(kb)
    assert rc == 0, lines
    text = (CS.syntheses_dir(kb) / "dim-one.md").read_text(encoding="utf-8")
    pool = set().union(*(CS.card_anchors(kb, c) for c in list(DX.CH)[:n]))
    assert set(CS.ANCHOR_RE.findall(text)) <= pool and CS.ANCHOR_RE.findall(text)


def test_same_cards_same_bytes_and_no_overwrite(tmp_path):
    a = DX.make_kb(tmp_path / "a", syntheses=False)
    b = DX.make_kb(tmp_path / "b", syntheses=False)
    GS.write(a, _spec_dims(2))
    GS.write(b, _spec_dims(2))
    assert (CS.syntheses_dir(a) / "dim-one.md").read_bytes() == (CS.syntheses_dir(b) / "dim-one.md").read_bytes()
    with pytest.raises(FileExistsError):
        GS.write(a, _spec_dims(2))


def test_a_dimension_whose_cards_hold_no_anchor_is_refused(tmp_path):
    kb = DX.make_kb(tmp_path / "kb", syntheses=False)
    (kb / "library" / "cards" / "ch01.md").write_text("---\nid: ch01\naliases: [x]\n---\n\n# ch01\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no anchor"):
        GS.write(kb, [{"dimension": "d", "cards": ["ch01"]}])
