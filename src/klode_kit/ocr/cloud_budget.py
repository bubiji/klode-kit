"""Per-batch cloud budget ledger (WI-06a; design 02 r2.6 ``cloud.max_cost_usd_per_batch``, exit 50).

Every cloud call is admitted by ``reserve()`` BEFORE it is dispatched and settled by ``settle()`` after it:

- the reservation is ``min(reserve_usd, limit)``; the sender passes it to the CLI as its own spending cap
  (claude ``--max-budget-usd``), so one call may not cost more than it reserved;
- admission: ``spent + outstanding < limit``. When that fails while other calls are still in flight, the caller
  waits for them to settle; once nothing is in flight and ``spent >= limit``, the reservation is refused with
  ``BudgetExceeded`` (exit 50) and the ledger is stopped for good - no later call is admitted;
- ``settle(r, cost)`` charges the cost the CLI reported, or the reservation when the cost is unknown or not a
  finite number >= 0 (timeout, no JSON): spend is never under-counted. ``release(r)`` returns a reservation for a
  call that never started (refused by the sender).

Law (matrix WI-06, 06-10): if every call costs at most its reservation, total spend < limit + one reservation -
at the last admission ``spent + outstanding < limit`` and every call still in flight is covered by its
reservation. Caveat: the CLI's own cap is a client-side estimate and "Spend can pass the cap"
(https://code.claude.com/docs/en/cli-reference, ``--max-budget-usd``); such an overrun is charged as reported, so
the bound then grows by that overrun. ``exceeded`` (spend > limit) is the "more than the budget" of r2.6; spending exactly the limit is
not exceeded, but leaves no headroom, so the next reservation is refused.

Money is ``Decimal`` (from ``str`` of the float), so 0.05 + 0.05 == 0.10 exactly. Thread-safe.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable

BUDGET_EXIT = 50
CALL_RESERVE_USD = Decimal("1.0")


class BudgetExceeded(Exception):
    """No reservation: the batch budget is used up (exit 50); nothing was started for it."""

    exit_code = BUDGET_EXIT

    def __init__(self, spent: Decimal, limit: Decimal):
        super().__init__(f"cloud budget used up: spent {spent} USD of {limit} USD per batch")
        self.spent = spent
        self.limit = limit


@dataclass(eq=False)
class Reservation:
    amount: Decimal
    open: bool = field(default=True, repr=False)


def _money(value: Any, name: str, *, positive: bool) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError(f"{name} must be a number, got {value!r}")
    dec = value if isinstance(value, Decimal) else Decimal(str(value))  # str of an int/float always parses
    if not dec.is_finite() or dec < 0 or (positive and dec == 0):
        raise ValueError(f"{name} must be a finite number {'> 0' if positive else '>= 0'}, got {value!r}")
    return dec


def _cost(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    dec = value if isinstance(value, Decimal) else Decimal(str(value))  # nan/inf -> Decimal NaN/Infinity
    return dec if dec.is_finite() and dec >= 0 else None


class Ledger:
    def __init__(self, limit_usd: Any, *, spent_usd: Any = 0, calls: int = 0,
                 reserve_usd: Any = CALL_RESERVE_USD,
                 on_change: Callable[[dict[str, Any]], None] | None = None):
        self._limit = _money(limit_usd, "limit_usd", positive=True)
        self._reserve = _money(reserve_usd, "reserve_usd", positive=True)
        self._spent = _money(spent_usd, "spent_usd", positive=False)
        if isinstance(calls, bool) or not isinstance(calls, int) or calls < 0:
            raise ValueError(f"calls must be an integer >= 0, got {calls!r}")
        self._calls = calls
        self._outstanding = Decimal(0)
        self._stopped = False
        self._on_change = on_change
        self._cond = threading.Condition()

    # ------------------------------------------------------------------ state

    @property
    def limit_usd(self) -> Decimal:
        return self._limit

    @property
    def spent_usd(self) -> Decimal:
        return self._spent

    @property
    def outstanding_usd(self) -> Decimal:
        return self._outstanding

    @property
    def calls(self) -> int:
        return self._calls

    @property
    def stopped(self) -> bool:
        return self._stopped

    @property
    def exceeded(self) -> bool:
        return self._spent > self._limit

    @property
    def reservation_usd(self) -> Decimal:
        return min(self._reserve, self._limit)

    def snapshot(self) -> dict[str, Any]:
        return {"limit_usd": float(self._limit), "spent_usd": float(self._spent), "calls": self._calls,
                "stopped": self._stopped, "exceeded": self.exceeded}

    # ------------------------------------------------------------------ calls

    def reserve(self) -> Reservation:
        with self._cond:
            while (not self._stopped and self._outstanding > 0
                   and self._spent + self._outstanding >= self._limit):
                self._cond.wait()
            if self._stopped or self._spent >= self._limit:
                self._stopped = True
                self._cond.notify_all()
                raise BudgetExceeded(self._spent, self._limit)
            amount = self.reservation_usd
            self._outstanding += amount
            return Reservation(amount)

    def settle(self, reservation: Reservation, cost_usd: Any) -> Decimal:
        """Charge one finished call; returns the amount charged."""
        cost = _cost(cost_usd)
        with self._cond:
            self._close(reservation)
            charged = reservation.amount if cost is None else cost
            self._spent += charged
            self._calls += 1
            self._cond.notify_all()
            snap = self.snapshot()
        if self._on_change is not None:
            self._on_change(snap)
        return charged

    def release(self, reservation: Reservation) -> None:
        """Return the reservation of a call that never started."""
        with self._cond:
            self._close(reservation)
            self._cond.notify_all()

    def _close(self, reservation: Reservation) -> None:
        if not reservation.open:
            raise ValueError("reservation already settled or released")
        reservation.open = False
        self._outstanding -= reservation.amount
