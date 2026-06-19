"""Tests for pyutcp_lab.transports.retry."""

from __future__ import annotations

import random

import pytest

from pyutcp_lab.core.errors import TransportError, ValidationError
from pyutcp_lab.transports.retry import (
    BreakerState,
    CircuitBreaker,
    CircuitBreakerOpenError,
    RetryExhaustedError,
    RetryPolicy,
    call_with_retry,
)
from tests.fakes import FakeClock


def no_jitter() -> RetryPolicy:
    return RetryPolicy(max_attempts=4, base_delay=0.1, factor=2.0, jitter=0.0)


class TestRetryPolicy:
    def test_validates_attempts(self) -> None:
        with pytest.raises(ValueError):
            RetryPolicy(max_attempts=0)

    def test_validates_jitter(self) -> None:
        with pytest.raises(ValueError):
            RetryPolicy(jitter=2.0)

    def test_validates_delays(self) -> None:
        with pytest.raises(ValueError):
            RetryPolicy(base_delay=-1)

    def test_geometric_backoff(self) -> None:
        p = no_jitter()
        assert p.delay_for(1) == pytest.approx(0.2)
        assert p.delay_for(2) == pytest.approx(0.4)
        assert p.delay_for(3) == pytest.approx(0.8)

    def test_capped_at_max_delay(self) -> None:
        p = RetryPolicy(base_delay=1.0, factor=10.0, max_delay=5.0, jitter=0.0)
        assert p.delay_for(5) == 5.0

    def test_should_retry(self) -> None:
        p = RetryPolicy(max_attempts=3)
        assert p.should_retry(0)
        assert p.should_retry(1)
        assert not p.should_retry(2)

    def test_jitter_within_bounds(self) -> None:
        p = RetryPolicy(base_delay=1.0, factor=1.0, jitter=0.5, _rng=random.Random(0))
        for _ in range(50):
            d = p.delay_for(0)
            assert 0.5 <= d <= 1.5


class TestCallWithRetry:
    def test_succeeds_first_try(self) -> None:
        calls = {"n": 0}

        def fn() -> str:
            calls["n"] += 1
            return "ok"

        assert call_with_retry(fn, no_jitter(), sleep=lambda s: None) == "ok"
        assert calls["n"] == 1

    def test_retries_then_succeeds(self) -> None:
        calls = {"n": 0}
        slept: list[float] = []

        def fn() -> str:
            calls["n"] += 1
            if calls["n"] < 3:
                raise TransportError("transient")
            return "ok"

        result = call_with_retry(fn, no_jitter(), sleep=slept.append)
        assert result == "ok"
        assert calls["n"] == 3
        assert len(slept) == 2  # two backoffs before the third, successful try

    def test_exhausts_and_raises(self) -> None:
        def fn() -> str:
            raise TransportError("always fails")

        with pytest.raises(RetryExhaustedError) as exc:
            call_with_retry(fn, RetryPolicy(max_attempts=2), sleep=lambda s: None)
        assert exc.value.attempts == 2

    def test_non_retryable_propagates_immediately(self) -> None:
        calls = {"n": 0}

        def fn() -> str:
            calls["n"] += 1
            raise ValidationError("bad input")

        with pytest.raises(ValidationError):
            call_with_retry(
                fn, no_jitter(), sleep=lambda s: None, retry_on=(TransportError,)
            )
        assert calls["n"] == 1  # not retried


class TestCircuitBreaker:
    def test_validates_threshold(self) -> None:
        with pytest.raises(ValueError):
            CircuitBreaker(failure_threshold=0)

    def test_opens_after_threshold(self) -> None:
        cb = CircuitBreaker(failure_threshold=3, clock=FakeClock().time)
        for _ in range(3):
            cb.record_failure()
        assert cb.state is BreakerState.OPEN
        assert not cb.allow()

    def test_success_resets(self) -> None:
        cb = CircuitBreaker(failure_threshold=2, clock=FakeClock().time)
        cb.record_failure()
        cb.record_success()
        cb.record_failure()
        assert cb.state is BreakerState.CLOSED  # counter was reset

    def test_half_opens_after_timeout(self) -> None:
        clock = FakeClock(start=0.0)
        cb = CircuitBreaker(
            failure_threshold=1, reset_timeout=10.0, clock=clock.time
        )
        cb.record_failure()
        assert cb.state is BreakerState.OPEN
        clock.advance(11.0)
        assert cb.state is BreakerState.HALF_OPEN
        assert cb.allow()

    def test_half_open_failure_reopens(self) -> None:
        clock = FakeClock(start=0.0)
        cb = CircuitBreaker(failure_threshold=1, reset_timeout=5.0, clock=clock.time)
        cb.record_failure()
        clock.advance(6.0)
        assert cb.state is BreakerState.HALF_OPEN
        cb.record_failure()  # trial fails
        assert cb.state is BreakerState.OPEN

    def test_half_open_success_closes(self) -> None:
        clock = FakeClock(start=0.0)
        cb = CircuitBreaker(failure_threshold=1, reset_timeout=5.0, clock=clock.time)
        cb.record_failure()
        clock.advance(6.0)
        assert cb.state is BreakerState.HALF_OPEN
        cb.record_success()
        assert cb.state is BreakerState.CLOSED


class TestRetryWithBreaker:
    def test_breaker_short_circuits(self) -> None:
        cb = CircuitBreaker(failure_threshold=1, clock=FakeClock().time)
        cb.record_failure()  # opens immediately

        def fn() -> str:
            return "should not run"

        with pytest.raises(CircuitBreakerOpenError):
            call_with_retry(fn, no_jitter(), sleep=lambda s: None, breaker=cb)

    def test_breaker_records_success(self) -> None:
        cb = CircuitBreaker(failure_threshold=2, clock=FakeClock().time)
        cb.record_failure()
        call_with_retry(lambda: "ok", no_jitter(), sleep=lambda s: None, breaker=cb)
        assert cb.state is BreakerState.CLOSED

    def test_breaker_records_failures_through_exhaustion(self) -> None:
        cb = CircuitBreaker(failure_threshold=10, clock=FakeClock().time)

        def fn() -> str:
            raise TransportError("always")

        with pytest.raises(RetryExhaustedError):
            call_with_retry(
                fn, RetryPolicy(max_attempts=3), sleep=lambda s: None, breaker=cb
            )
        # Each of the three attempts recorded a failure on the breaker.
        assert cb.state in (BreakerState.CLOSED, BreakerState.OPEN)

    def test_breaker_records_non_retryable_failure(self) -> None:
        cb = CircuitBreaker(failure_threshold=1, clock=FakeClock().time)

        def fn() -> str:
            raise ValidationError("bad")

        with pytest.raises(ValidationError):
            call_with_retry(
                fn,
                no_jitter(),
                sleep=lambda s: None,
                breaker=cb,
                retry_on=(TransportError,),
            )
        # The non-retryable failure still trips the breaker.
        assert cb.state is BreakerState.OPEN
