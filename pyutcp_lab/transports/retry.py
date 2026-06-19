"""Resilience helpers: retry with backoff, and a circuit breaker.

Calling a remote tool can fail transiently (a dropped connection, a brief 503).
Retrying with exponential backoff smooths over those blips, but unbounded retries
against a provider that is genuinely down just pile on load and waste the
caller's latency budget. Two pieces work together here:

* :class:`RetryPolicy` decides whether and how long to wait before the next
  attempt, growing the delay geometrically and adding jitter so a fleet of
  clients does not retry in lockstep.
* :class:`CircuitBreaker` tracks consecutive failures per provider and "opens"
  after a threshold, short-circuiting calls for a cool-down window so a sick
  provider is left alone long enough to recover.

Both the clock and the sleep function are injectable, so retry timing and
breaker windows are exercised in tests without real waiting.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

from ..core.errors import TransportError, UtcpError

SleepFn = Callable[[float], None]
ClockFn = Callable[[], float]


class RetryExhaustedError(TransportError):
    """Raised when every retry attempt has been used up."""

    def __init__(self, attempts: int, last: BaseException) -> None:
        self.attempts = attempts
        self.last = last
        super().__init__(f"retries exhausted after {attempts} attempts: {last}")


@dataclass(frozen=True)
class RetryPolicy:
    """Computes backoff delays and decides when to stop retrying.

    The delay for attempt ``n`` (0-based) is
    ``min(max_delay, base_delay * factor**n)``, optionally perturbed by up to
    ``jitter`` fraction in either direction.
    """

    max_attempts: int = 3
    base_delay: float = 0.1
    factor: float = 2.0
    max_delay: float = 5.0
    jitter: float = 0.1
    _rng: random.Random = field(default_factory=random.Random, compare=False)

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.base_delay < 0 or self.max_delay < 0:
            raise ValueError("delays must be non-negative")
        if not 0 <= self.jitter <= 1:
            raise ValueError("jitter must be between 0 and 1")

    def delay_for(self, attempt: int) -> float:
        """Backoff delay before the given 0-based attempt index."""
        if attempt <= 0:
            base = self.base_delay
        else:
            base = self.base_delay * (self.factor**attempt)
        base = min(self.max_delay, base)
        if self.jitter:
            spread = base * self.jitter
            base += self._rng.uniform(-spread, spread)
        return max(0.0, base)

    def should_retry(self, attempt: int) -> bool:
        """True if another attempt (0-based) is still allowed."""
        return attempt + 1 < self.max_attempts


class BreakerState(str, Enum):
    CLOSED = "closed"  # calls flow normally
    OPEN = "open"  # calls are short-circuited
    HALF_OPEN = "half_open"  # one trial call is allowed through


class CircuitBreakerOpenError(TransportError):
    """Raised when a call is short-circuited because the breaker is open."""


class CircuitBreaker:
    """Per-target failure tracker that opens after repeated failures."""

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        reset_timeout: float = 30.0,
        clock: ClockFn = time.monotonic,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        self._threshold = failure_threshold
        self._reset_timeout = reset_timeout
        self._clock = clock
        self._failures = 0
        self._opened_at: Optional[float] = None
        self._state = BreakerState.CLOSED

    @property
    def state(self) -> BreakerState:
        self._maybe_half_open()
        return self._state

    def _maybe_half_open(self) -> None:
        if (
            self._state is BreakerState.OPEN
            and self._opened_at is not None
            and self._clock() - self._opened_at >= self._reset_timeout
        ):
            self._state = BreakerState.HALF_OPEN

    def allow(self) -> bool:
        """Whether a call may proceed right now."""
        self._maybe_half_open()
        return self._state is not BreakerState.OPEN

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None
        self._state = BreakerState.CLOSED

    def record_failure(self) -> None:
        self._failures += 1
        if self._state is BreakerState.HALF_OPEN:
            # The trial call failed; re-open immediately.
            self._open()
        elif self._failures >= self._threshold:
            self._open()

    def _open(self) -> None:
        self._state = BreakerState.OPEN
        self._opened_at = self._clock()


def call_with_retry(
    fn: Callable[[], object],
    policy: RetryPolicy,
    *,
    sleep: SleepFn = time.sleep,
    breaker: Optional[CircuitBreaker] = None,
    retry_on: tuple[type[BaseException], ...] = (TransportError,),
) -> object:
    """Invoke ``fn`` with retry/backoff and an optional circuit breaker.

    Retries only on ``retry_on`` exceptions. A breaker, if supplied, can
    short-circuit the call (raising :class:`CircuitBreakerOpenError`) and is
    updated with the outcome of every attempt that runs.
    """
    last_exc: Optional[BaseException] = None
    attempt = 0
    while True:
        if breaker is not None and not breaker.allow():
            raise CircuitBreakerOpenError("circuit breaker is open")
        try:
            result = fn()
        except retry_on as exc:
            last_exc = exc
            if breaker is not None:
                breaker.record_failure()
            if not policy.should_retry(attempt):
                raise RetryExhaustedError(attempt + 1, exc) from exc
            sleep(policy.delay_for(attempt + 1))
            attempt += 1
            continue
        except UtcpError:
            # Non-retryable library error: record and propagate as-is.
            if breaker is not None:
                breaker.record_failure()
            raise
        else:
            if breaker is not None:
                breaker.record_success()
            return result
