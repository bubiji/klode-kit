"""WI-06a: the per-batch cloud budget ledger (``klode_kit.ocr.cloud_budget``; design 02 r2.6 exit 50).

Law (matrix WI-06 law, S4): spend <= limit + one reservation; after a refusal nothing is admitted again.
Here: the sequential boundaries and guards; the concurrent law is 06-10 (tests/contract/test_adapter_claude.py).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from klode_kit.ocr.cloud_budget import BUDGET_EXIT, CALL_RESERVE_USD, BudgetExceeded, Ledger

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("limit", [0, -1, float("nan"), float("inf"), "5", None])
def test_limit_must_be_positive_finite(limit):
    with pytest.raises(ValueError):
        Ledger(limit)


@pytest.mark.parametrize("reserve", [0, -0.5, float("nan")])
def test_reserve_must_be_positive(reserve):
    with pytest.raises(ValueError):
        Ledger(1.0, reserve_usd=reserve)


def test_negative_spent_rejected():
    with pytest.raises(ValueError):
        Ledger(1.0, spent_usd=-0.01)


def test_reservation_is_min_of_reserve_and_limit():
    assert Ledger(0.3, reserve_usd=1).reserve().amount == Decimal("0.3")
    assert Ledger(5.0).reserve().amount == CALL_RESERVE_USD == Decimal("1.0")


def test_exact_limit_is_not_exceeded_but_admits_nothing_more():
    """S2: spend == limit is not 'exceeded' (exit 50 is for more than the limit) - but no headroom is left, so the
    next reservation is refused, sticky."""
    ledger = Ledger(0.10)
    for _ in range(2):
        ledger.settle(ledger.reserve(), 0.05)
    assert ledger.spent_usd == Decimal("0.10")
    assert not ledger.exceeded
    with pytest.raises(BudgetExceeded) as info:
        ledger.reserve()
    assert info.value.exit_code == BUDGET_EXIT == 50
    assert ledger.stopped
    with pytest.raises(BudgetExceeded):
        ledger.reserve()


def test_unknown_or_invalid_cost_charges_the_reservation():
    """S3: a call whose cost is unknown (timeout, no JSON) or nonsensical is charged at its reservation."""
    for bad in (None, -0.2, float("nan"), "0.1", True):
        ledger = Ledger(1.0, reserve_usd=Decimal("0.25"))
        assert ledger.settle(ledger.reserve(), bad) == Decimal("0.25")
        assert ledger.spent_usd == Decimal("0.25")


def test_release_refunds_without_a_call():
    ledger = Ledger(1.0)
    ledger.release(ledger.reserve())
    assert (ledger.spent_usd, ledger.calls, ledger.outstanding_usd) == (Decimal("0"), 0, Decimal("0"))


def test_settle_twice_rejected():
    ledger = Ledger(1.0)
    r = ledger.reserve()
    ledger.settle(r, 0.1)
    with pytest.raises(ValueError):
        ledger.settle(r, 0.1)
    with pytest.raises(ValueError):
        ledger.release(r)


def test_resumed_ledger_counts_earlier_spend():
    """A batch rerun starts from the persisted spend: already at the limit -> refused before any call."""
    ledger = Ledger(0.10, spent_usd=0.12, calls=3)
    assert ledger.exceeded
    with pytest.raises(BudgetExceeded):
        ledger.reserve()
    assert ledger.calls == 3


def test_on_change_receives_snapshots():
    seen = []
    ledger = Ledger(1.0, on_change=seen.append)
    ledger.settle(ledger.reserve(), 0.2)
    assert seen[-1] == {"limit_usd": 1.0, "spent_usd": 0.2, "calls": 1, "stopped": False, "exceeded": False}


@settings(max_examples=200, deadline=None)
@given(limit=st.decimals(min_value=Decimal("0.01"), max_value=Decimal("3"), places=2),
       reserve=st.decimals(min_value=Decimal("0.01"), max_value=Decimal("1"), places=2),
       costs=st.lists(st.decimals(min_value=Decimal("0"), max_value=Decimal("1"), places=3), max_size=40))
def test_sequential_law(limit, reserve, costs):
    """S4: calls cost at most their reservation -> total spend <= limit + reservation, never more than one call
    past the point where spend reached the limit, and a refusal is final."""
    ledger = Ledger(limit, reserve_usd=reserve)
    cap = min(reserve, limit)
    refused = False
    for cost in costs:
        try:
            r = ledger.reserve()
        except BudgetExceeded:
            refused = True
            continue
        assert not refused
        assert ledger.spent_usd < limit  # admitted only with headroom
        ledger.settle(r, float(min(cost, cap)))
    assert ledger.spent_usd <= limit + cap
