"""Tests for the asynchronous transports and async retry."""

from __future__ import annotations

import json

import pytest

from pyutcp_lab.core.errors import TimeoutError, TransportError, ValidationError
from pyutcp_lab.core.models import Provider, ToolCall, TransportType
from pyutcp_lab.transports.async_http import AsyncHttpTransport
from pyutcp_lab.transports.async_retry import call_with_retry_async
from pyutcp_lab.transports.async_sse import AsyncSSETransport
from pyutcp_lab.transports.base import Deadline
from pyutcp_lab.transports.retry import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    RetryExhaustedError,
    RetryPolicy,
)
from tests.fakes import AsyncFakeConnection, FakeClock


def http_provider() -> Provider:
    return Provider(name="api", transport=TransportType.HTTP, url="http://x")


def sse_provider() -> Provider:
    return Provider(name="s", transport=TransportType.SSE, url="http://x")


def _manual_bytes(*names: str) -> bytes:
    return json.dumps({"tools": [{"name": n} for n in names]}).encode()


# --------------------------------------------------------------------------
# Async HTTP transport
# --------------------------------------------------------------------------


class TestAsyncHttpTransport:
    def test_rejects_wrong_transport(self) -> None:
        cli = Provider(name="c", transport=TransportType.CLI, command=("x",))
        with pytest.raises(TransportError):
            AsyncHttpTransport(cli, connect=lambda p: None)  # type: ignore[arg-type,return-value]

    async def test_discover(self) -> None:
        conn = AsyncFakeConnection([_manual_bytes("a", "b")], FakeClock())
        t = AsyncHttpTransport(http_provider(), connect=lambda p: conn)
        manual = await t.discover()
        assert manual.tool_names() == ("a", "b")
        assert conn.closed

    async def test_discover_bad_payload(self) -> None:
        conn = AsyncFakeConnection([b"[1,2]"], FakeClock())
        t = AsyncHttpTransport(http_provider(), connect=lambda p: conn)
        with pytest.raises(TransportError):
            await t.discover()

    async def test_discover_non_array_tools(self) -> None:
        conn = AsyncFakeConnection([b'{"tools": {}}'], FakeClock())
        t = AsyncHttpTransport(http_provider(), connect=lambda p: conn)
        with pytest.raises(TransportError):
            await t.discover()

    async def test_call(self) -> None:
        conn = AsyncFakeConnection([b'{"sum": 3}'], FakeClock())
        t = AsyncHttpTransport(http_provider(), connect=lambda p: conn)
        result = await t.call(ToolCall(tool_name="math.add", arguments={"a": 1}))
        assert result == {"sum": 3}
        method, path, body = conn.requests[0]
        assert method == "POST"
        assert path == "/tools/math.add"

    async def test_call_empty_is_none(self) -> None:
        conn = AsyncFakeConnection([], FakeClock())
        t = AsyncHttpTransport(http_provider(), connect=lambda p: conn)
        assert await t.call(ToolCall(tool_name="noop")) is None

    async def test_call_bad_json(self) -> None:
        conn = AsyncFakeConnection([b"{bad"], FakeClock())
        t = AsyncHttpTransport(http_provider(), connect=lambda p: conn)
        with pytest.raises(TransportError):
            await t.call(ToolCall(tool_name="x"))

    async def test_stream(self) -> None:
        conn = AsyncFakeConnection([b"1", b"2", b"3"], FakeClock())
        t = AsyncHttpTransport(http_provider(), connect=lambda p: conn)
        out = [item async for item in t.stream(ToolCall(tool_name="gen"))]
        assert out == [1, 2, 3]

    async def test_read_deadline_enforced(self) -> None:
        clock = FakeClock(start=0.0)
        conn = AsyncFakeConnection([b"a", b"b"], clock, read_cost=2.0)
        t = AsyncHttpTransport(
            http_provider(), connect=lambda p: conn, default_timeout=10.0
        )
        d = Deadline.after(3.0, clock=clock.time)
        with pytest.raises(TransportError):
            await t.call(ToolCall(tool_name="slow"), deadline=d)

    async def test_context_manager(self) -> None:
        conn = AsyncFakeConnection([b'{"tools": []}'], FakeClock())
        async with AsyncHttpTransport(http_provider(), connect=lambda p: conn) as t:
            assert (await t.discover()).tool_names() == ()

    async def test_discover_bad_json_manual(self) -> None:
        conn = AsyncFakeConnection([b"not json"], FakeClock())
        t = AsyncHttpTransport(http_provider(), connect=lambda p: conn)
        with pytest.raises(TransportError):
            await t.discover()

    async def test_stream_deadline_enforced(self) -> None:
        clock = FakeClock(start=0.0)
        conn = AsyncFakeConnection([b"1", b"2"], clock, read_cost=1.0)
        t = AsyncHttpTransport(
            http_provider(), connect=lambda p: conn, default_timeout=10.0
        )
        d = Deadline.after(1.0, clock=clock.time)
        with pytest.raises(TimeoutError):
            [x async for x in t.stream(ToolCall(tool_name="g"), deadline=d)]


# --------------------------------------------------------------------------
# Async SSE transport
# --------------------------------------------------------------------------


class TestAsyncSSETransport:
    def test_rejects_wrong_transport(self) -> None:
        http = http_provider()
        with pytest.raises(TransportError):
            AsyncSSETransport(http, connect=lambda p: None)  # type: ignore[arg-type,return-value]

    async def test_stream_across_chunks(self) -> None:
        conn = AsyncFakeConnection(
            [b'data: {"n": ', b'1}\n\n', b'data: {"n": 2}\n\n'], FakeClock()
        )
        t = AsyncSSETransport(sse_provider(), connect=lambda p: conn)
        out = [item async for item in t.stream(ToolCall(tool_name="gen"))]
        assert out == [{"n": 1}, {"n": 2}]

    async def test_call_first_event(self) -> None:
        conn = AsyncFakeConnection([b'data: {"v": 7}\n\n'], FakeClock())
        t = AsyncSSETransport(sse_provider(), connect=lambda p: conn)
        assert await t.call(ToolCall(tool_name="one")) == {"v": 7}

    async def test_call_no_events_none(self) -> None:
        conn = AsyncFakeConnection([], FakeClock())
        t = AsyncSSETransport(sse_provider(), connect=lambda p: conn)
        assert await t.call(ToolCall(tool_name="empty")) is None

    async def test_discover(self) -> None:
        manual = json.dumps({"tools": [{"name": "s.a"}]})
        conn = AsyncFakeConnection([f"data: {manual}\n\n".encode()], FakeClock())
        t = AsyncSSETransport(sse_provider(), connect=lambda p: conn)
        assert (await t.discover()).tool_names() == ("s.a",)

    async def test_discover_no_event(self) -> None:
        conn = AsyncFakeConnection([b": comment\n\n"], FakeClock())
        t = AsyncSSETransport(sse_provider(), connect=lambda p: conn)
        with pytest.raises(TransportError):
            await t.discover()

    async def test_discover_unterminated_via_flush(self) -> None:
        manual = json.dumps({"tools": [{"name": "s.a"}]})
        conn = AsyncFakeConnection([f"data: {manual}".encode()], FakeClock())
        t = AsyncSSETransport(sse_provider(), connect=lambda p: conn)
        assert (await t.discover()).tool_names() == ("s.a",)

    async def test_stream_deadline(self) -> None:
        clock = FakeClock(start=0.0)
        conn = AsyncFakeConnection([b"1", b"2"], clock, read_cost=1.0)
        t = AsyncSSETransport(sse_provider(), connect=lambda p: conn)
        d = Deadline.after(1.0, clock=clock.time)
        with pytest.raises(TimeoutError):
            [x async for x in t.stream(ToolCall(tool_name="g"), deadline=d)]

    async def test_discover_with_deadline(self) -> None:
        manual = json.dumps({"tools": [{"name": "s.a"}]})
        clock = FakeClock(start=0.0)
        conn = AsyncFakeConnection([f"data: {manual}\n\n".encode()], clock)
        t = AsyncSSETransport(sse_provider(), connect=lambda p: conn)
        d = Deadline.after(30.0, clock=clock.time)
        assert (await t.discover(deadline=d)).tool_names() == ("s.a",)

    async def test_stream_unterminated_final_event(self) -> None:
        conn = AsyncFakeConnection([b'data: {"n": 9}'], FakeClock())
        t = AsyncSSETransport(sse_provider(), connect=lambda p: conn)
        out = [x async for x in t.stream(ToolCall(tool_name="g"))]
        assert out == [{"n": 9}]


# --------------------------------------------------------------------------
# Async retry
# --------------------------------------------------------------------------


async def _noop_sleep(_s: float) -> None:
    return None


class TestAsyncRetry:
    async def test_succeeds_first_try(self) -> None:
        async def fn() -> str:
            return "ok"

        result = await call_with_retry_async(
            fn, RetryPolicy(max_attempts=3), sleep=_noop_sleep
        )
        assert result == "ok"

    async def test_retries_then_succeeds(self) -> None:
        state = {"n": 0}

        async def fn() -> str:
            state["n"] += 1
            if state["n"] < 3:
                raise TransportError("transient")
            return "ok"

        result = await call_with_retry_async(
            fn, RetryPolicy(max_attempts=5, jitter=0.0), sleep=_noop_sleep
        )
        assert result == "ok"
        assert state["n"] == 3

    async def test_exhausts(self) -> None:
        async def fn() -> str:
            raise TransportError("always")

        with pytest.raises(RetryExhaustedError):
            await call_with_retry_async(
                fn, RetryPolicy(max_attempts=2), sleep=_noop_sleep
            )

    async def test_non_retryable_propagates(self) -> None:
        async def fn() -> str:
            raise ValidationError("bad")

        with pytest.raises(ValidationError):
            await call_with_retry_async(
                fn,
                RetryPolicy(max_attempts=3),
                sleep=_noop_sleep,
                retry_on=(TransportError,),
            )

    async def test_breaker_short_circuits(self) -> None:
        cb = CircuitBreaker(failure_threshold=1, clock=FakeClock().time)
        cb.record_failure()

        async def fn() -> str:
            return "unreached"

        with pytest.raises(CircuitBreakerOpenError):
            await call_with_retry_async(
                fn, RetryPolicy(max_attempts=2), sleep=_noop_sleep, breaker=cb
            )

    async def test_breaker_records_success(self) -> None:
        from pyutcp_lab.transports.retry import BreakerState

        cb = CircuitBreaker(failure_threshold=2, clock=FakeClock().time)
        cb.record_failure()

        async def fn() -> str:
            return "ok"

        await call_with_retry_async(
            fn, RetryPolicy(max_attempts=2), sleep=_noop_sleep, breaker=cb
        )
        assert cb.state is BreakerState.CLOSED

    async def test_breaker_records_failures_on_exhaustion(self) -> None:
        cb = CircuitBreaker(failure_threshold=10, clock=FakeClock().time)

        async def fn() -> str:
            raise TransportError("always")

        with pytest.raises(RetryExhaustedError):
            await call_with_retry_async(
                fn, RetryPolicy(max_attempts=3), sleep=_noop_sleep, breaker=cb
            )

    async def test_breaker_records_non_retryable(self) -> None:
        from pyutcp_lab.transports.retry import BreakerState

        cb = CircuitBreaker(failure_threshold=1, clock=FakeClock().time)

        async def fn() -> str:
            raise ValidationError("bad")

        with pytest.raises(ValidationError):
            await call_with_retry_async(
                fn,
                RetryPolicy(max_attempts=3),
                sleep=_noop_sleep,
                breaker=cb,
                retry_on=(TransportError,),
            )
        assert cb.state is BreakerState.OPEN


# --------------------------------------------------------------------------
# Integration: async transports drive the AsyncUtcpClient
# --------------------------------------------------------------------------


class TestAsyncClientIntegration:
    async def test_client_calls_async_http_transport(self) -> None:
        from pyutcp_lab.client.async_client import AsyncUtcpClient
        from pyutcp_lab.core.models import Manual, Tool

        conn = AsyncFakeConnection([b'{"echo": 1}'], FakeClock())
        transport = AsyncHttpTransport(http_provider(), connect=lambda p: conn)

        client = AsyncUtcpClient(resolve_transport=lambda name: transport)
        client.register(
            Manual(provider=http_provider(), tools=(Tool(name="api.echo"),))
        )
        result = await client.call(ToolCall(tool_name="api.echo"))
        assert result == {"echo": 1}
