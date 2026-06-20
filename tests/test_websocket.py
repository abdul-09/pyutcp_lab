"""Tests for the WebSocket transports and their framing."""

from __future__ import annotations

import json

import pytest

from pyutcp_lab.core.models import Provider, ToolCall, TransportType
from pyutcp_lab.transports.async_websocket import AsyncWebSocketTransport
from pyutcp_lab.transports.websocket import WebSocketTransport
from pyutcp_lab.transports.ws_protocol import (
    FrameError,
    Request,
    RequestIds,
    decode_response,
    matches,
)
from tests.fakes import AsyncFakeWebSocket, FakeWebSocket


def ws_provider() -> Provider:
    return Provider(name="w", transport=TransportType.WEBSOCKET, url="ws://x")


def frame(**kw) -> str:
    return json.dumps(kw)


# --------------------------------------------------------------------------
# Protocol framing
# --------------------------------------------------------------------------


class TestRequest:
    def test_encode_minimal(self) -> None:
        body = json.loads(Request(id=1, method="discover").encode())
        assert body == {"id": 1, "method": "discover"}

    def test_encode_with_tool_and_args(self) -> None:
        body = json.loads(
            Request(id=2, method="call", tool="t", arguments={"a": 1}).encode()
        )
        assert body == {"id": 2, "method": "call", "tool": "t", "arguments": {"a": 1}}


class TestRequestIds:
    def test_monotonic(self) -> None:
        ids = RequestIds()
        assert [ids.next(), ids.next(), ids.next()] == [1, 2, 3]


class TestDecodeResponse:
    def test_basic(self) -> None:
        r = decode_response(frame(id=1, data={"v": 2}))
        assert r.id == 1
        assert r.data == {"v": 2}
        assert r.final is True
        assert not r.is_error

    def test_error_frame(self) -> None:
        r = decode_response(frame(id=1, error="boom"))
        assert r.is_error
        assert r.error == "boom"

    def test_non_final(self) -> None:
        r = decode_response(frame(id=1, data="chunk", final=False))
        assert r.final is False

    def test_bad_json(self) -> None:
        with pytest.raises(FrameError):
            decode_response("{not json")

    def test_non_object(self) -> None:
        with pytest.raises(FrameError):
            decode_response("[1, 2]")


class TestMatches:
    def test_matching_id(self) -> None:
        assert matches(decode_response(frame(id=5, data=1)), 5)

    def test_mismatched_id(self) -> None:
        assert not matches(decode_response(frame(id=4, data=1)), 5)

    def test_no_id_is_broadcast(self) -> None:
        assert matches(decode_response(frame(data=1)), 5)


# --------------------------------------------------------------------------
# Sync WebSocket transport
# --------------------------------------------------------------------------


class TestWebSocketTransport:
    def test_rejects_wrong_transport(self) -> None:
        http = Provider(name="h", transport=TransportType.HTTP, url="http://x")
        with pytest.raises(FrameError):
            WebSocketTransport(http, connect=lambda p: None)  # type: ignore[arg-type,return-value]

    def test_call(self) -> None:
        ws = FakeWebSocket([frame(id=1, data={"sum": 3})])
        t = WebSocketTransport(ws_provider(), connect=lambda p: ws)
        result = t.call(ToolCall(tool_name="math.add", arguments={"a": 1}))
        assert result == {"sum": 3}
        # The request frame carried tool + arguments.
        sent = json.loads(ws.sent[0])
        assert sent["tool"] == "math.add"
        assert sent["arguments"] == {"a": 1}

    def test_call_skips_unrelated_frames(self) -> None:
        # A frame for a different request id is skipped until ours arrives.
        ws = FakeWebSocket(
            [frame(id=999, data="not mine"), frame(id=1, data="mine")]
        )
        t = WebSocketTransport(ws_provider(), connect=lambda p: ws)
        assert t.call(ToolCall(tool_name="x")) == "mine"

    def test_error_frame_raises(self) -> None:
        ws = FakeWebSocket([frame(id=1, error="bad tool")])
        t = WebSocketTransport(ws_provider(), connect=lambda p: ws)
        with pytest.raises(FrameError, match="bad tool"):
            t.call(ToolCall(tool_name="x"))

    def test_discover(self) -> None:
        ws = FakeWebSocket(
            [frame(id=1, data={"tools": [{"name": "w.a"}, {"name": "w.b"}]})]
        )
        t = WebSocketTransport(ws_provider(), connect=lambda p: ws)
        assert t.discover().tool_names() == ("w.a", "w.b")

    def test_discover_bad_payload(self) -> None:
        ws = FakeWebSocket([frame(id=1, data=[1, 2])])
        t = WebSocketTransport(ws_provider(), connect=lambda p: ws)
        with pytest.raises(FrameError):
            t.discover()

    def test_stream_multiple_frames(self) -> None:
        ws = FakeWebSocket(
            [
                frame(id=1, data="a", final=False),
                frame(id=1, data="b", final=False),
                frame(id=1, data="c", final=True),
            ]
        )
        t = WebSocketTransport(ws_provider(), connect=lambda p: ws)
        assert list(t.stream(ToolCall(tool_name="gen"))) == ["a", "b", "c"]

    def test_stream_skips_unrelated_and_stops_at_final(self) -> None:
        ws = FakeWebSocket(
            [
                frame(id=42, data="other"),
                frame(id=1, data="x", final=True),
            ]
        )
        t = WebSocketTransport(ws_provider(), connect=lambda p: ws)
        assert list(t.stream(ToolCall(tool_name="gen"))) == ["x"]

    def test_stream_error_raises(self) -> None:
        ws = FakeWebSocket([frame(id=1, error="stream failed")])
        t = WebSocketTransport(ws_provider(), connect=lambda p: ws)
        with pytest.raises(FrameError):
            list(t.stream(ToolCall(tool_name="gen")))

    def test_connection_reused_across_calls(self) -> None:
        ws = FakeWebSocket([frame(id=1, data=1), frame(id=2, data=2)])
        opens = {"n": 0}

        def connect(p):
            opens["n"] += 1
            return ws

        t = WebSocketTransport(ws_provider(), connect=connect)
        t.call(ToolCall(tool_name="a"))
        t.call(ToolCall(tool_name="b"))
        assert opens["n"] == 1  # one persistent connection for both calls

    def test_close(self) -> None:
        ws = FakeWebSocket([frame(id=1, data=1)])
        t = WebSocketTransport(ws_provider(), connect=lambda p: ws)
        t.call(ToolCall(tool_name="a"))
        t.close()
        assert ws.closed

    def test_close_without_connecting(self) -> None:
        t = WebSocketTransport(ws_provider(), connect=lambda p: None)  # type: ignore[arg-type,return-value]
        t.close()  # must not raise even though no socket was opened

    def test_call_with_deadline(self) -> None:
        from pyutcp_lab.transports.base import Deadline
        from tests.fakes import FakeClock

        ws = FakeWebSocket([frame(id=1, data="ok")])
        clock = FakeClock(start=0.0)
        t = WebSocketTransport(ws_provider(), connect=lambda p: ws)
        d = Deadline.after(30.0, clock=clock.time)
        assert t.call(ToolCall(tool_name="x"), deadline=d) == "ok"

    def test_stream_with_deadline_and_empty_frame(self) -> None:
        from pyutcp_lab.transports.base import Deadline
        from tests.fakes import FakeClock

        # A keepalive frame with no data is skipped (not yielded); the final
        # frame carries the result.
        ws = FakeWebSocket(
            [
                frame(id=1, final=False),  # data is None -> not yielded
                frame(id=1, data="result", final=True),
            ]
        )
        clock = FakeClock(start=0.0)
        t = WebSocketTransport(ws_provider(), connect=lambda p: ws)
        d = Deadline.after(30.0, clock=clock.time)
        out = list(t.stream(ToolCall(tool_name="gen"), deadline=d))
        assert out == ["result"]


# --------------------------------------------------------------------------
# Async WebSocket transport
# --------------------------------------------------------------------------


class TestAsyncWebSocketTransport:
    def test_rejects_wrong_transport(self) -> None:
        http = Provider(name="h", transport=TransportType.HTTP, url="http://x")
        with pytest.raises(FrameError):
            AsyncWebSocketTransport(http, connect=lambda p: None)  # type: ignore[arg-type,return-value]

    async def test_call(self) -> None:
        ws = AsyncFakeWebSocket([frame(id=1, data={"v": 7})])
        t = AsyncWebSocketTransport(ws_provider(), connect=lambda p: ws)
        assert await t.call(ToolCall(tool_name="x")) == {"v": 7}

    async def test_discover(self) -> None:
        ws = AsyncFakeWebSocket([frame(id=1, data={"tools": [{"name": "w.a"}]})])
        t = AsyncWebSocketTransport(ws_provider(), connect=lambda p: ws)
        assert (await t.discover()).tool_names() == ("w.a",)

    async def test_discover_bad_payload(self) -> None:
        ws = AsyncFakeWebSocket([frame(id=1, data="notdict")])
        t = AsyncWebSocketTransport(ws_provider(), connect=lambda p: ws)
        with pytest.raises(FrameError):
            await t.discover()

    async def test_error_frame_raises(self) -> None:
        ws = AsyncFakeWebSocket([frame(id=1, error="nope")])
        t = AsyncWebSocketTransport(ws_provider(), connect=lambda p: ws)
        with pytest.raises(FrameError):
            await t.call(ToolCall(tool_name="x"))

    async def test_skips_unrelated_frames(self) -> None:
        ws = AsyncFakeWebSocket(
            [frame(id=99, data="other"), frame(id=1, data="mine")]
        )
        t = AsyncWebSocketTransport(ws_provider(), connect=lambda p: ws)
        assert await t.call(ToolCall(tool_name="x")) == "mine"

    async def test_stream(self) -> None:
        ws = AsyncFakeWebSocket(
            [
                frame(id=1, data="a", final=False),
                frame(id=1, data="b", final=True),
            ]
        )
        t = AsyncWebSocketTransport(ws_provider(), connect=lambda p: ws)
        out = [item async for item in t.stream(ToolCall(tool_name="gen"))]
        assert out == ["a", "b"]

    async def test_stream_skips_unrelated(self) -> None:
        ws = AsyncFakeWebSocket(
            [frame(id=7, data="x"), frame(id=1, data="y", final=True)]
        )
        t = AsyncWebSocketTransport(ws_provider(), connect=lambda p: ws)
        out = [item async for item in t.stream(ToolCall(tool_name="gen"))]
        assert out == ["y"]

    async def test_stream_error_raises(self) -> None:
        ws = AsyncFakeWebSocket([frame(id=1, error="boom")])
        t = AsyncWebSocketTransport(ws_provider(), connect=lambda p: ws)
        with pytest.raises(FrameError):
            [item async for item in t.stream(ToolCall(tool_name="gen"))]

    async def test_aclose(self) -> None:
        ws = AsyncFakeWebSocket([frame(id=1, data=1)])
        t = AsyncWebSocketTransport(ws_provider(), connect=lambda p: ws)
        await t.call(ToolCall(tool_name="x"))
        await t.aclose()
        assert ws.closed

    async def test_aclose_without_connecting(self) -> None:
        t = AsyncWebSocketTransport(ws_provider(), connect=lambda p: None)  # type: ignore[arg-type,return-value]
        await t.aclose()  # must not raise

    async def test_call_with_deadline(self) -> None:
        from pyutcp_lab.transports.base import Deadline
        from tests.fakes import FakeClock

        ws = AsyncFakeWebSocket([frame(id=1, data="ok")])
        clock = FakeClock(start=0.0)
        t = AsyncWebSocketTransport(ws_provider(), connect=lambda p: ws)
        d = Deadline.after(30.0, clock=clock.time)
        assert await t.call(ToolCall(tool_name="x"), deadline=d) == "ok"

    async def test_stream_with_deadline_and_empty_frame(self) -> None:
        from pyutcp_lab.transports.base import Deadline
        from tests.fakes import FakeClock

        ws = AsyncFakeWebSocket(
            [
                frame(id=1, final=False),  # no data -> skipped
                frame(id=1, data="result", final=True),
            ]
        )
        clock = FakeClock(start=0.0)
        t = AsyncWebSocketTransport(ws_provider(), connect=lambda p: ws)
        d = Deadline.after(30.0, clock=clock.time)
        out = [x async for x in t.stream(ToolCall(tool_name="gen"), deadline=d)]
        assert out == ["result"]

    async def test_context_manager(self) -> None:
        ws = AsyncFakeWebSocket([frame(id=1, data="ok")])
        async with AsyncWebSocketTransport(ws_provider(), connect=lambda p: ws) as t:
            assert await t.call(ToolCall(tool_name="x")) == "ok"
        assert ws.closed
