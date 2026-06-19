"""Latency budget for multi-step operations.

A single logical operation (discovering a provider, then calling two tools in
sequence, say) should respect *one* end-to-end time budget rather than letting
each step spend the full per-call timeout independently. A :class:`Budget`
captures that total deadline and hands out per-step :class:`Deadline` objects
derived from the time still remaining.

The contract that makes this work: every step must obtain its deadline from
:meth:`remaining_deadline` immediately before it runs, and that deadline must be
threaded into the transport call. A slow early step then shrinks the deadline
available to later steps, and once the total is exhausted the next
:meth:`remaining_deadline` call yields an already-expired deadline (or
:meth:`check` raises). A step that ignores the budget and runs under its own
default timeout breaks the guarantee for the whole chain.
"""

from __future__ import annotations

import time
from typing import Callable

from ..core.errors import TimeoutError
from ..transports.base import Deadline


class Budget:
    """An end-to-end time budget shared across the steps of one operation."""

    def __init__(
        self, total_seconds: float, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        if total_seconds <= 0:
            raise ValueError("total_seconds must be positive")
        self._clock = clock
        self._deadline_at = clock() + total_seconds

    def remaining(self) -> float:
        """Seconds left in the budget, clamped at zero."""
        return max(0.0, self._deadline_at - self._clock())

    def expired(self) -> bool:
        return self.remaining() <= 0.0

    def check(self, *, what: str = "operation") -> None:
        """Raise :class:`TimeoutError` if the budget is already exhausted."""
        if self.expired():
            raise TimeoutError(f"{what} exceeded its latency budget")

    def remaining_deadline(self) -> Deadline:
        """A :class:`Deadline` for the next step, sharing this budget's clock.

        Because the returned deadline reads the same clock, it reflects time
        consumed by earlier steps: the more the budget has been spent, the less
        time the next step is allowed.
        """
        return Deadline(monotonic_at=self._deadline_at, clock=self._clock)
