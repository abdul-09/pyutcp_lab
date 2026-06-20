"""Asynchronous retry with backoff and circuit breaking.

The retry *policy* and the circuit *breaker* are plain logic with no I/O, so this
reuses :class:`~pyutcp_lab.transports.retry.RetryPolicy` and
:class:`~pyutcp_lab.transports.retry.CircuitBreaker` directly. Only the wait
between attempts changes: it awaits ``asyncio.sleep`` instead of blocking, so a
retrying call yields the event loop to other coroutines while it backs off.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Optional

from ..core.errors import UtcpError
from .retry import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    RetryExhaustedError,
    RetryPolicy,
    TransportError,
)

AsyncSleepFn = Callable[[float], Awaitable[None]]


async def call_with_retry_async(
    fn: Callable[[], Awaitable[object]],
    policy: RetryPolicy,
    *,
    sleep: AsyncSleepFn = asyncio.sleep,
    breaker: Optional[CircuitBreaker] = None,
    retry_on: tuple[type[BaseException], ...] = (TransportError,),
) -> object:
    """Await ``fn`` with retry/backoff and an optional circuit breaker.

    Mirrors :func:`~pyutcp_lab.transports.retry.call_with_retry`, but ``fn`` is a
    coroutine factory and the backoff is awaited.
    """
    last_exc: Optional[BaseException] = None
    attempt = 0
    while True:
        if breaker is not None and not breaker.allow():
            raise CircuitBreakerOpenError("circuit breaker is open")
        try:
            result = await fn()
        except retry_on as exc:
            last_exc = exc
            if breaker is not None:
                breaker.record_failure()
            if not policy.should_retry(attempt):
                raise RetryExhaustedError(attempt + 1, exc) from exc
            await sleep(policy.delay_for(attempt + 1))
            attempt += 1
            continue
        except UtcpError:
            if breaker is not None:
                breaker.record_failure()
            raise
        else:
            if breaker is not None:
                breaker.record_success()
            return result
