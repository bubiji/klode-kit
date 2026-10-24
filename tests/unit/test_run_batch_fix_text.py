"""Regression (WI-08a finding, fixed in WI-08b): the kb-ocr receipt's fix actions must not offer the
"continue with the other routes" choice that design r3.3 removed (a page with a missing / failed route is
disputed until the route is repaired; the confirm page shows "缺路，不可点选" and offers nothing).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from klode_kit.ocr import run_batch

pytestmark = pytest.mark.unit

PAGE = SimpleNamespace(page_id="d-000000000000/p001")
REMOVED = ("continue with the other routes", "other routes", "继续", "N-1", "N−1")


@pytest.mark.parametrize("state", sorted(run_batch.FIX))
@pytest.mark.parametrize("error", ["", "budget_exceeded: 2.0 > 1.0", "timeout after 600s"])
def test_fix_actions_offer_no_partial_routes(state, error):
    """S3: for every page state and the budget stop, the fix action names a repair (rerun kb-ocr, or raise the
    budget and rerun) and never the removed 'continue with the other routes' option."""
    entry = run_batch._uncertain(PAGE, "codex", {"status": state, "error": error})
    fix = entry["fix_action"]
    assert "kb-ocr" in fix
    for phrase in REMOVED:
        assert phrase not in fix, (state, error, fix)
    assert entry["reason"] == state and entry["scope"] == "page"
