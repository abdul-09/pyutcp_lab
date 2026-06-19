"""Tests for the client layer: Budget and UtcpClient."""

from __future__ import annotations

import pytest

from pyutcp_lab.core.errors import (
    TimeoutError,
    TransportError,
)
from pyutcp_lab.core.models import Manual, Provider, Tool, ToolCall, TransportType
from pyutcp_lab.client.budget import Budget
from pyutcp_lab.client.client import UtcpClient
from tests.fakes import FakeClock, FakeTransport


def manual(provider_name: str, *tool_names: str) -> Manual:
    return Manual(
        provider=Provider(name=provider_name, transport=TransportType.HTTP, url="http://x"),
        tools=tuple(Tool(name=t) for t in tool_names),
    )


class TestBudget:
    def test_rejects_nonpositive(self) -> None:
        with pytest.raises(ValueError):
            Budget(0.0)

    def test_remaining_counts_down(self) -> None:
        clock = FakeClock(start=0.0)
        b = Budget(10.0, clock=clock.time)
        assert b.remaining() == pytest.approx(10.0)
        clock.advance(4.0)
        assert b.remaining() == pytest.approx(6.0)

    def test_expired_and_check(self) -> None:
        clock = FakeClock(start=0.0)
        b = Budget(2.0, clock=clock.time)
        clock.advance(3.0)
        assert b.expired()
        with pytest.raises(TimeoutError):
            b.check()

    def test_remaining_deadline_reflects_spent_time(self) -> None:
        clock = FakeClock(start=0.0)
        b = Budget(10.0, clock=clock.time)
        clock.advance(7.0)
        d = b.remaining_deadline()
        assert d.remaining() == pytest.approx(3.0)


class TestClientSingleCall:
    def test_call_routes_to_owner_transport(self) -> None:
        clock = FakeClock()
        transport = FakeTransport(clock, result={"v": 1})
        client = UtcpClient(resolve_transport=lambda name: transport)
        client.register(manual("p", "p.a"))
        assert client.call(ToolCall(tool_name="p.a")) == {"v": 1}
        assert transport.call_count == 1

    def test_call_unknown_tool_raises(self) -> None:
        client = UtcpClient(resolve_transport=lambda name: None)  # type: ignore[arg-type,return-value]
        with pytest.raises(TransportError):
            client.call(ToolCall(tool_name="ghost"))

    def test_uses_supplied_repository(self) -> None:
        from pyutcp_lab.registry.repository import ToolRepository

        repo = ToolRepository()
        repo.register(manual("p", "p.a"))
        clock = FakeClock()
        transport = FakeTransport(clock)
        client = UtcpClient(resolve_transport=lambda name: transport, repository=repo)
        assert client.repository is repo
        assert client.call(ToolCall(tool_name="p.a")) == "ok"


class TestClientValidationAndMetrics:
    def _manual_with_schema(self) -> Manual:
        schema = {
            "type": "object",
            "required": ["city"],
            "properties": {"city": {"type": "string"}},
        }
        return Manual(
            provider=Provider(name="w", transport=TransportType.HTTP, url="http://x"),
            tools=(Tool(name="w.forecast", inputs=schema),),
        )

    def test_validation_rejects_bad_arguments(self) -> None:
        from pyutcp_lab.core.schema import ArgumentValidationError

        clock = FakeClock()
        transport = FakeTransport(clock)
        client = UtcpClient(
            resolve_transport=lambda name: transport, validate_arguments=True
        )
        client.register(self._manual_with_schema())
        with pytest.raises(ArgumentValidationError):
            client.call(ToolCall(tool_name="w.forecast", arguments={}))

    def test_validation_accepts_good_arguments(self) -> None:
        clock = FakeClock()
        transport = FakeTransport(clock, result="sunny")
        client = UtcpClient(
            resolve_transport=lambda name: transport, validate_arguments=True
        )
        client.register(self._manual_with_schema())
        out = client.call(
            ToolCall(tool_name="w.forecast", arguments={"city": "Nairobi"})
        )
        assert out == "sunny"

    def test_validation_passes_through_tool_without_schema(self) -> None:
        clock = FakeClock()
        transport = FakeTransport(clock, result="ok")
        client = UtcpClient(
            resolve_transport=lambda name: transport, validate_arguments=True
        )
        client.register(manual("p", "p.noschema"))  # tool has empty inputs
        assert client.call(ToolCall(tool_name="p.noschema", arguments={"x": 1})) == "ok"

    def test_metrics_recorded(self) -> None:
        from pyutcp_lab.client.metrics import MetricsCollector

        clock = FakeClock(start=0.0)
        transport = FakeTransport(clock, call_cost=1.5)
        metrics = MetricsCollector(clock=clock.time)
        client = UtcpClient(resolve_transport=lambda name: transport, metrics=metrics)
        client.register(manual("p", "p.a"))
        client.call(ToolCall(tool_name="p.a"))
        stats = metrics.stats_for("p.a")
        assert stats.calls == 1
        assert stats.failures == 0
        assert client.metrics is metrics


class TestCallChainBudget:
    """A chain of calls must not outlast its shared budget.

    All the calls in a chain share one budget. Each one needs to run under a
    deadline computed from whatever time is left, so a slow early call eats into
    what the later ones get. If the client checks the budget but forgets to pass
    a deadline down, every call falls back to the transport's own generous
    timeout and the whole chain blows past the budget.
    """

    def _client(self, clock: FakeClock, transport: FakeTransport) -> UtcpClient:
        client = UtcpClient(resolve_transport=lambda name: transport)
        client.register(manual("p", "p.a", "p.b", "p.c"))
        return client

    def test_each_call_receives_a_deadline(self) -> None:
        clock = FakeClock(start=0.0)
        transport = FakeTransport(clock, call_cost=0.0)
        client = self._client(clock, transport)
        budget = Budget(10.0, clock=clock.time)
        client.call_chain(
            [ToolCall(tool_name="p.a"), ToolCall(tool_name="p.b")], budget=budget
        )
        # Every call must have been handed a (non-None) deadline.
        assert len(transport.received_deadlines) == 2
        assert all(d is not None for d in transport.received_deadlines)

    def test_chain_aborts_when_budget_exhausted(self) -> None:
        clock = FakeClock(start=0.0)
        # Each call costs 4s; the budget is 10s. Two calls (8s) succeed; the
        # third is attempted with only 2s left and is refused by its deadline.
        transport = FakeTransport(clock, call_cost=4.0)
        client = self._client(clock, transport)
        budget = Budget(10.0, clock=clock.time)
        with pytest.raises(TimeoutError):
            client.call_chain(
                [
                    ToolCall(tool_name="p.a"),
                    ToolCall(tool_name="p.b"),
                    ToolCall(tool_name="p.c"),
                ],
                budget=budget,
            )
        # Two calls completed (8s); the third was refused before consuming time.
        assert transport.remaining_at_call == [
            pytest.approx(10.0),
            pytest.approx(6.0),
            pytest.approx(2.0),
        ]
        # Clock did not advance past the budget: third call never ran its cost.
        assert clock.time() == pytest.approx(8.0)

    def test_deadline_shrinks_across_steps(self) -> None:
        clock = FakeClock(start=0.0)
        transport = FakeTransport(clock, call_cost=3.0)
        client = self._client(clock, transport)
        budget = Budget(10.0, clock=clock.time)
        client.call_chain(
            [ToolCall(tool_name="p.a"), ToolCall(tool_name="p.b")], budget=budget
        )
        # Captured at call time: first call saw ~10s, second saw ~7s remaining.
        first, second = transport.remaining_at_call
        assert first == pytest.approx(10.0)
        assert second == pytest.approx(7.0)

    def test_fast_chain_completes(self) -> None:
        clock = FakeClock(start=0.0)
        transport = FakeTransport(clock, call_cost=0.5, result="done")
        client = self._client(clock, transport)
        budget = Budget(10.0, clock=clock.time)
        out = client.call_chain(
            [ToolCall(tool_name="p.a"), ToolCall(tool_name="p.b")], budget=budget
        )
        assert out == ["done", "done"]
