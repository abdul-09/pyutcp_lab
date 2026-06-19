"""Tests for pyutcp_lab.client.async_client."""

from __future__ import annotations

import asyncio

import pytest

from pyutcp_lab.core.errors import TransportError
from pyutcp_lab.core.models import Manual, Provider, Tool, ToolCall, TransportType
from pyutcp_lab.client.async_client import AsyncUtcpClient
from pyutcp_lab.client.budget import Budget


def manual(provider_name: str, *tool_names: str) -> Manual:
    return Manual(
        provider=Provider(name=provider_name, transport=TransportType.HTTP, url="http://x"),
        tools=tuple(Tool(name=t) for t in tool_names),
    )


class AsyncFakeTransport:
    """Async transport recording concurrency and optionally failing."""

    def __init__(self, *, delay: float = 0.0, fail_on: set[str] | None = None) -> None:
        self.delay = delay
        self.fail_on = fail_on or set()
        self.active = 0
        self.peak = 0
        self.calls: list[str] = []

    async def call(self, call: ToolCall) -> object:
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            self.calls.append(call.tool_name)
            if call.tool_name in self.fail_on:
                raise TransportError(f"{call.tool_name} failed")
            return {"tool": call.tool_name}
        finally:
            self.active -= 1


class TestConstruction:
    def test_rejects_zero_concurrency(self) -> None:
        with pytest.raises(ValueError):
            AsyncUtcpClient(resolve_transport=lambda n: None, max_concurrency=0)  # type: ignore[arg-type,return-value]

    def test_exposes_repository_and_concurrency(self) -> None:
        client = AsyncUtcpClient(resolve_transport=lambda n: None, max_concurrency=5)  # type: ignore[arg-type,return-value]
        assert client.max_concurrency == 5
        assert client.repository is not None


class TestSingleCall:
    async def test_call_routes(self) -> None:
        transport = AsyncFakeTransport()
        client = AsyncUtcpClient(resolve_transport=lambda n: transport)
        client.register(manual("p", "p.a"))
        result = await client.call(ToolCall(tool_name="p.a"))
        assert result == {"tool": "p.a"}

    async def test_unknown_tool_raises(self) -> None:
        client = AsyncUtcpClient(resolve_transport=lambda n: None)  # type: ignore[arg-type,return-value]
        with pytest.raises(TransportError):
            await client.call(ToolCall(tool_name="ghost"))


class TestGather:
    async def test_gather_returns_in_order(self) -> None:
        transport = AsyncFakeTransport()
        client = AsyncUtcpClient(resolve_transport=lambda n: transport)
        client.register(manual("p", "p.a", "p.b", "p.c"))
        calls = [ToolCall(tool_name=n) for n in ("p.a", "p.b", "p.c")]
        results = await client.gather(calls)
        assert [r["tool"] for r in results] == ["p.a", "p.b", "p.c"]

    async def test_concurrency_is_bounded(self) -> None:
        transport = AsyncFakeTransport(delay=0.01)
        client = AsyncUtcpClient(
            resolve_transport=lambda n: transport, max_concurrency=3
        )
        client.register(manual("p", *[f"p.t{i}" for i in range(10)]))
        calls = [ToolCall(tool_name=f"p.t{i}") for i in range(10)]
        await client.gather(calls)
        # Peak simultaneous in-flight calls must not exceed the limit.
        assert transport.peak <= 3
        assert len(transport.calls) == 10

    async def test_return_exceptions(self) -> None:
        transport = AsyncFakeTransport(fail_on={"p.b"})
        client = AsyncUtcpClient(resolve_transport=lambda n: transport)
        client.register(manual("p", "p.a", "p.b", "p.c"))
        calls = [ToolCall(tool_name=n) for n in ("p.a", "p.b", "p.c")]
        results = await client.gather(calls, return_exceptions=True)
        assert results[0] == {"tool": "p.a"}
        assert isinstance(results[1], TransportError)
        assert results[2] == {"tool": "p.c"}

    async def test_failure_propagates_without_return_exceptions(self) -> None:
        transport = AsyncFakeTransport(fail_on={"p.a"})
        client = AsyncUtcpClient(resolve_transport=lambda n: transport)
        client.register(manual("p", "p.a"))
        with pytest.raises(TransportError):
            await client.gather([ToolCall(tool_name="p.a")])


class TestGatherWithBudget:
    async def test_within_budget_completes(self) -> None:
        transport = AsyncFakeTransport(delay=0.0)
        client = AsyncUtcpClient(resolve_transport=lambda n: transport)
        client.register(manual("p", "p.a", "p.b"))
        budget = Budget(5.0)
        results = await client.gather(
            [ToolCall(tool_name="p.a"), ToolCall(tool_name="p.b")], budget=budget
        )
        assert len(results) == 2

    async def test_exhausted_budget_raises_before_start(self) -> None:
        from tests.fakes import FakeClock

        clock = FakeClock(start=0.0)
        transport = AsyncFakeTransport()
        client = AsyncUtcpClient(resolve_transport=lambda n: transport)
        client.register(manual("p", "p.a"))
        budget = Budget(1.0, clock=clock.time)
        clock.advance(2.0)  # budget already spent
        with pytest.raises(asyncio.TimeoutError):
            await client.gather([ToolCall(tool_name="p.a")], budget=budget)

    async def test_slow_gather_times_out(self) -> None:
        transport = AsyncFakeTransport(delay=0.5)
        client = AsyncUtcpClient(resolve_transport=lambda n: transport)
        client.register(manual("p", "p.a"))
        budget = Budget(0.05)  # far less than the 0.5s call
        with pytest.raises(asyncio.TimeoutError):
            await client.gather([ToolCall(tool_name="p.a")], budget=budget)


class TestMapTool:
    async def test_map_tool(self) -> None:
        transport = AsyncFakeTransport()
        client = AsyncUtcpClient(resolve_transport=lambda n: transport)
        client.register(manual("p", "p.fetch"))
        results = await client.map_tool(
            "p.fetch", [{"id": 1}, {"id": 2}, {"id": 3}]
        )
        assert len(results) == 3
        assert all(r["tool"] == "p.fetch" for r in results)
