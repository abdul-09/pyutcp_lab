"""Tests for the transport layer: Deadline and HttpTransport."""

from __future__ import annotations

import json

import pytest

from pyutcp_lab.core.errors import TimeoutError, TransportError
from pyutcp_lab.core.models import Provider, ToolCall, TransportType
from pyutcp_lab.transports.base import Deadline
from pyutcp_lab.transports.http import HttpTransport
from tests.fakes import FakeClock, FakeConnection


@pytest.fixture
def http_provider() -> Provider:
    return Provider(name="api", transport=TransportType.HTTP, url="http://x")


def _manual_bytes(*tool_names: str) -> bytes:
    return json.dumps(
        {"tools": [{"name": n, "description": ""} for n in tool_names]}
    ).encode("utf-8")


class TestDeadline:
    def test_remaining_counts_down(self) -> None:
        clock = FakeClock(start=100.0)
        d = Deadline.after(10.0, clock=clock.time)
        assert d.remaining() == pytest.approx(10.0)
        clock.advance(5.0)
        assert d.remaining() == pytest.approx(5.0)

    def test_remaining_clamped_at_zero(self) -> None:
        clock = FakeClock(start=100.0)
        d = Deadline.after(1.0, clock=clock.time)
        clock.advance(100.0)
        assert d.remaining() == 0.0

    def test_expired(self) -> None:
        clock = FakeClock(start=100.0)
        d = Deadline.after(1.0, clock=clock.time)
        clock.advance(0.5)
        assert not d.expired()
        clock.advance(1.0)
        assert d.expired()

    def test_check_raises_when_expired(self) -> None:
        clock = FakeClock(start=100.0)
        d = Deadline.after(1.0, clock=clock.time)
        clock.advance(2.0)
        with pytest.raises(TimeoutError):
            d.check(what="thing")

    def test_check_passes_when_time_remains(self) -> None:
        clock = FakeClock(start=100.0)
        d = Deadline.after(5.0, clock=clock.time)
        clock.advance(1.0)
        d.check()  # should not raise

    def test_default_clock_is_monotonic(self) -> None:
        d = Deadline.after(60.0)
        assert d.remaining() > 0.0


class TestHttpTransportConstruction:
    def test_rejects_wrong_transport(self) -> None:
        cli = Provider(name="c", transport=TransportType.CLI, command=("x",))
        with pytest.raises(TransportError):
            HttpTransport(cli, connect=lambda p: None)  # type: ignore[arg-type,return-value]

    def test_accepts_streaming_http(self) -> None:
        p = Provider(name="s", transport=TransportType.STREAMING_HTTP, url="http://x")
        HttpTransport(p, connect=lambda p: None)  # type: ignore[arg-type,return-value]

    def test_usable_as_context_manager(self, http_provider: Provider) -> None:
        conn = FakeConnection([b'{"tools": []}'], FakeClock())
        with HttpTransport(http_provider, connect=lambda p: conn) as t:
            assert t.discover().tool_names() == ()


class TestHttpTransportDiscover:
    def test_discover_parses_tools(self, http_provider: Provider) -> None:
        conn = FakeConnection([_manual_bytes("a", "b")], FakeClock())
        t = HttpTransport(http_provider, connect=lambda p: conn)
        manual = t.discover()
        assert manual.tool_names() == ("a", "b")
        assert conn.closed

    def test_discover_empty_tools(self, http_provider: Provider) -> None:
        conn = FakeConnection([b'{"tools": []}'], FakeClock())
        t = HttpTransport(http_provider, connect=lambda p: conn)
        assert t.discover().tool_names() == ()

    def test_discover_rejects_non_object(self, http_provider: Provider) -> None:
        conn = FakeConnection([b"[1,2,3]"], FakeClock())
        t = HttpTransport(http_provider, connect=lambda p: conn)
        with pytest.raises(TransportError):
            t.discover()

    def test_discover_rejects_bad_json(self, http_provider: Provider) -> None:
        conn = FakeConnection([b"not json"], FakeClock())
        t = HttpTransport(http_provider, connect=lambda p: conn)
        with pytest.raises(TransportError):
            t.discover()

    def test_discover_rejects_non_array_tools(self, http_provider: Provider) -> None:
        conn = FakeConnection([b'{"tools": {}}'], FakeClock())
        t = HttpTransport(http_provider, connect=lambda p: conn)
        with pytest.raises(TransportError):
            t.discover()


class TestHttpTransportCall:
    def test_call_returns_decoded_result(self, http_provider: Provider) -> None:
        conn = FakeConnection([b'{"sum": 3}'], FakeClock())
        t = HttpTransport(http_provider, connect=lambda p: conn)
        result = t.call(ToolCall(tool_name="math.add", arguments={"a": 1, "b": 2}))
        assert result == {"sum": 3}
        method, path, body = conn.requests[0]
        assert method == "POST"
        assert path == "/tools/math.add"
        assert json.loads(body) == {"a": 1, "b": 2}

    def test_call_empty_body_returns_none(self, http_provider: Provider) -> None:
        conn = FakeConnection([], FakeClock())
        t = HttpTransport(http_provider, connect=lambda p: conn)
        assert t.call(ToolCall(tool_name="noop")) is None

    def test_call_bad_result_raises(self, http_provider: Provider) -> None:
        conn = FakeConnection([b"{bad"], FakeClock())
        t = HttpTransport(http_provider, connect=lambda p: conn)
        with pytest.raises(TransportError):
            t.call(ToolCall(tool_name="x"))


class TestHttpTransportStream:
    def test_stream_yields_chunks(self, http_provider: Provider) -> None:
        conn = FakeConnection([b"1", b"2", b"3"], FakeClock())
        t = HttpTransport(http_provider, connect=lambda p: conn)
        assert list(t.stream(ToolCall(tool_name="gen"))) == [1, 2, 3]
        assert conn.closed

    def test_stream_stops_at_deadline(self, http_provider: Provider) -> None:
        clock = FakeClock(start=0.0)
        conn = FakeConnection([b"1", b"2", b"3"], clock, read_cost=1.0)
        t = HttpTransport(
            http_provider, connect=lambda p: conn, default_timeout=10.0
        )
        # Budget 1.0s: read 1 consumes it exactly (clock -> 1.0); before read 2
        # the deadline is exhausted and the pre-read check raises.
        d = Deadline.after(1.0, clock=clock.time)
        with pytest.raises(TimeoutError):
            list(t.stream(ToolCall(tool_name="gen"), deadline=d))


class TestReadTimeoutBudget:
    """A slow trickling response can't outlast the deadline.

    A response that dribbles in slowly shouldn't be able to run past the
    caller's deadline. Every read is charged against the time that's left, and
    the per-read timeout gets smaller as the budget runs down.
    """

    def test_slow_response_aborts_within_budget(
        self, http_provider: Provider
    ) -> None:
        clock = FakeClock(start=0.0)
        conn = FakeConnection([b"a", b"b", b"c"], clock, read_cost=2.0)
        t = HttpTransport(
            http_provider, connect=lambda p: conn, default_timeout=10.0
        )
        d = Deadline.after(3.0, clock=clock.time)
        with pytest.raises(TransportError):
            t.call(ToolCall(tool_name="slow"), deadline=d)

    def test_fast_response_completes_within_budget(
        self, http_provider: Provider
    ) -> None:
        clock = FakeClock(start=0.0)
        conn = FakeConnection([b'{"ok": true}'], clock, read_cost=0.5)
        t = HttpTransport(
            http_provider, connect=lambda p: conn, default_timeout=10.0
        )
        d = Deadline.after(5.0, clock=clock.time)
        assert t.call(ToolCall(tool_name="fast"), deadline=d) == {"ok": True}

    def test_no_deadline_uses_default_timeout(
        self, http_provider: Provider
    ) -> None:
        conn = FakeConnection([b'{"ok": 1}'], FakeClock(), read_cost=1.0)
        t = HttpTransport(
            http_provider, connect=lambda p: conn, default_timeout=5.0
        )
        assert t.call(ToolCall(tool_name="x")) == {"ok": 1}

    def test_per_read_timeout_capped_at_default(
        self, http_provider: Provider
    ) -> None:
        clock = FakeClock(start=0.0)
        conn = FakeConnection([b'{"ok": 1}'], clock, read_cost=3.0)
        t = HttpTransport(
            http_provider, connect=lambda p: conn, default_timeout=2.0
        )
        d = Deadline.after(100.0, clock=clock.time)
        with pytest.raises(TransportError):
            t.call(ToolCall(tool_name="x"), deadline=d)
