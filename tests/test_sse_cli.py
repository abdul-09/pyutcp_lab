"""Tests for the SSE and CLI transports."""

from __future__ import annotations

import json

import pytest

from pyutcp_lab.core.errors import TransportError
from pyutcp_lab.core.models import Provider, ToolCall, TransportType
from pyutcp_lab.transports.cli import CliTransport
from pyutcp_lab.transports.sse import SSEParser, SSETransport
from tests.fakes import FakeClock, FakeConnection


# --------------------------------------------------------------------------
# SSE parser
# --------------------------------------------------------------------------


class TestSSEParser:
    def test_single_event(self) -> None:
        parser = SSEParser()
        events = parser.feed(b"data: hello\n\n")
        assert len(events) == 1
        assert events[0].data == "hello"

    def test_multiple_events_in_one_chunk(self) -> None:
        parser = SSEParser()
        events = parser.feed(b"data: one\n\ndata: two\n\n")
        assert [e.data for e in events] == ["one", "two"]

    def test_multiline_data_joined(self) -> None:
        parser = SSEParser()
        events = parser.feed(b"data: line1\ndata: line2\n\n")
        assert events[0].data == "line1\nline2"

    def test_event_and_id_fields(self) -> None:
        parser = SSEParser()
        events = parser.feed(b"event: update\nid: 7\ndata: x\n\n")
        assert events[0].event == "update"
        assert events[0].event_id == "7"

    def test_comment_lines_ignored(self) -> None:
        parser = SSEParser()
        events = parser.feed(b": this is a comment\ndata: real\n\n")
        assert events[0].data == "real"

    def test_frame_without_data_skipped(self) -> None:
        parser = SSEParser()
        assert parser.feed(b"event: ping\n\n") == []

    def test_partial_frame_buffered_until_complete(self) -> None:
        """An event split across reads should parse as one event.

        When an event's bytes show up across two reads, the parser has to
        buffer the partial frame and emit it once the terminating blank line
        arrives, instead of splitting or losing it at the chunk boundary.
        """
        parser = SSEParser()
        # First chunk ends mid-event (no terminating blank line yet).
        assert parser.feed(b"data: first half ") == []
        # Second chunk completes the event.
        events = parser.feed(b"second half\n\n")
        assert len(events) == 1
        assert events[0].data == "first half second half"

    def test_event_split_across_three_chunks(self) -> None:
        parser = SSEParser()
        assert parser.feed(b"data: a") == []
        assert parser.feed(b"bc") == []
        events = parser.feed(b"def\n\n")
        assert events[0].data == "abcdef"

    def test_boundary_delimiter_split_across_chunks(self) -> None:
        # The blank-line delimiter itself is split: "\n" then "\n".
        parser = SSEParser()
        assert parser.feed(b"data: x\n") == []
        events = parser.feed(b"\n")
        assert len(events) == 1
        assert events[0].data == "x"

    def test_flush_emits_trailing_unterminated_frame(self) -> None:
        parser = SSEParser()
        parser.feed(b"data: trailing")
        event = parser.flush()
        assert event is not None
        assert event.data == "trailing"

    def test_flush_empty_returns_none(self) -> None:
        parser = SSEParser()
        assert parser.flush() is None

    def test_unknown_field_ignored(self) -> None:
        parser = SSEParser()
        events = parser.feed(b"retry: 3000\ndata: payload\n\n")
        assert events[0].data == "payload"

    def test_event_json(self) -> None:
        parser = SSEParser()
        events = parser.feed(b'data: {"k": 1}\n\n')
        assert events[0].json() == {"k": 1}

    def test_event_bad_json_raises(self) -> None:
        parser = SSEParser()
        events = parser.feed(b"data: not json\n\n")
        with pytest.raises(TransportError):
            events[0].json()


# --------------------------------------------------------------------------
# SSE transport
# --------------------------------------------------------------------------


def sse_provider() -> Provider:
    return Provider(name="s", transport=TransportType.SSE, url="http://x")


class TestSSETransport:
    def test_rejects_wrong_transport(self) -> None:
        http = Provider(name="h", transport=TransportType.HTTP, url="http://x")
        with pytest.raises(TransportError):
            SSETransport(http, connect=lambda p: None)  # type: ignore[arg-type,return-value]

    def test_stream_yields_events_across_chunks(self) -> None:
        # The result event is split across two reads on purpose.
        conn = FakeConnection(
            [b'data: {"n": ', b'1}\n\n', b'data: {"n": 2}\n\n'], FakeClock()
        )
        t = SSETransport(sse_provider(), connect=lambda p: conn)
        out = list(t.stream(ToolCall(tool_name="gen")))
        assert out == [{"n": 1}, {"n": 2}]
        assert conn.closed

    def test_call_returns_first_event(self) -> None:
        conn = FakeConnection([b'data: {"v": 42}\n\n'], FakeClock())
        t = SSETransport(sse_provider(), connect=lambda p: conn)
        assert t.call(ToolCall(tool_name="one")) == {"v": 42}

    def test_call_no_events_returns_none(self) -> None:
        conn = FakeConnection([], FakeClock())
        t = SSETransport(sse_provider(), connect=lambda p: conn)
        assert t.call(ToolCall(tool_name="empty")) is None

    def test_discover_parses_manual(self) -> None:
        manual = json.dumps({"tools": [{"name": "s.a"}, {"name": "s.b"}]})
        conn = FakeConnection([f"data: {manual}\n\n".encode()], FakeClock())
        t = SSETransport(sse_provider(), connect=lambda p: conn)
        assert t.discover().tool_names() == ("s.a", "s.b")

    def test_discover_no_event_raises(self) -> None:
        conn = FakeConnection([b": just a comment\n\n"], FakeClock())
        t = SSETransport(sse_provider(), connect=lambda p: conn)
        with pytest.raises(TransportError):
            t.discover()

    def test_discover_with_deadline(self) -> None:
        from pyutcp_lab.transports.base import Deadline

        manual = json.dumps({"tools": [{"name": "s.a"}]})
        clock = FakeClock(start=0.0)
        conn = FakeConnection([f"data: {manual}\n\n".encode()], clock)
        t = SSETransport(sse_provider(), connect=lambda p: conn)
        d = Deadline.after(30.0, clock=clock.time)
        assert t.discover(deadline=d).tool_names() == ("s.a",)

    def test_discover_unterminated_manual_via_flush(self) -> None:
        # Manual event has no trailing blank line; flush() must still emit it.
        manual = json.dumps({"tools": [{"name": "s.a"}]})
        conn = FakeConnection([f"data: {manual}".encode()], FakeClock())
        t = SSETransport(sse_provider(), connect=lambda p: conn)
        assert t.discover().tool_names() == ("s.a",)

    def test_stream_with_deadline(self) -> None:
        from pyutcp_lab.transports.base import Deadline

        clock = FakeClock(start=0.0)
        conn = FakeConnection([b'data: {"n": 1}\n\n'], clock)
        t = SSETransport(sse_provider(), connect=lambda p: conn)
        d = Deadline.after(30.0, clock=clock.time)
        assert list(t.stream(ToolCall(tool_name="g"), deadline=d)) == [{"n": 1}]

    def test_stream_unterminated_final_event_via_flush(self) -> None:
        # Final event lacks the trailing blank line; flush() yields it.
        conn = FakeConnection([b'data: {"n": 9}'], FakeClock())
        t = SSETransport(sse_provider(), connect=lambda p: conn)
        assert list(t.stream(ToolCall(tool_name="g"))) == [{"n": 9}]


# --------------------------------------------------------------------------
# CLI transport
# --------------------------------------------------------------------------


def cli_provider() -> Provider:
    return Provider(
        name="c", transport=TransportType.CLI, command=("mytool", "--json")
    )


class TestCliTransport:
    def test_rejects_wrong_transport(self) -> None:
        http = Provider(name="h", transport=TransportType.HTTP, url="http://x")
        with pytest.raises(TransportError):
            CliTransport(http, runner=lambda argv, stdin: (0, b"", b""))

    def test_rejects_missing_command(self) -> None:
        # A CLI provider with no command cannot be constructed into a transport.
        p = Provider(name="c", transport=TransportType.CLI, command=None)
        with pytest.raises(TransportError):
            CliTransport(p, runner=lambda argv, stdin: (0, b"", b""))

    def test_call_passes_args_and_parses_result(self) -> None:
        seen: dict[str, object] = {}

        def runner(argv: list[str], stdin: bytes) -> tuple[int, bytes, bytes]:
            seen["argv"] = argv
            seen["stdin"] = stdin
            return 0, b'{"ok": true}', b""

        t = CliTransport(cli_provider(), runner=runner)
        result = t.call(ToolCall(tool_name="do.it", arguments={"a": 1}))
        assert result == {"ok": True}
        assert seen["argv"] == ["mytool", "--json", "do.it"]
        assert json.loads(seen["stdin"]) == {"a": 1}

    def test_nonzero_exit_raises_with_stderr(self) -> None:
        def runner(argv: list[str], stdin: bytes) -> tuple[int, bytes, bytes]:
            return 1, b"", b"boom"

        t = CliTransport(cli_provider(), runner=runner)
        with pytest.raises(TransportError, match="boom"):
            t.call(ToolCall(tool_name="x"))

    def test_discover_parses_manual(self) -> None:
        def runner(argv: list[str], stdin: bytes) -> tuple[int, bytes, bytes]:
            assert argv[-1] == "--utcp-manual"
            return 0, json.dumps({"tools": [{"name": "c.a"}]}).encode(), b""

        t = CliTransport(cli_provider(), runner=runner)
        assert t.discover().tool_names() == ("c.a",)

    def test_discover_non_object_raises(self) -> None:
        t = CliTransport(
            cli_provider(), runner=lambda argv, stdin: (0, b"[1,2]", b"")
        )
        with pytest.raises(TransportError):
            t.discover()

    def test_empty_output_is_none(self) -> None:
        t = CliTransport(cli_provider(), runner=lambda argv, stdin: (0, b"  ", b""))
        assert t.call(ToolCall(tool_name="x")) is None

    def test_bad_json_raises(self) -> None:
        t = CliTransport(
            cli_provider(), runner=lambda argv, stdin: (0, b"{bad", b"")
        )
        with pytest.raises(TransportError):
            t.call(ToolCall(tool_name="x"))

    def test_stream_yields_single_result(self) -> None:
        t = CliTransport(
            cli_provider(), runner=lambda argv, stdin: (0, b'"hi"', b"")
        )
        assert list(t.stream(ToolCall(tool_name="x"))) == ["hi"]

    def test_stream_null_yields_nothing(self) -> None:
        t = CliTransport(cli_provider(), runner=lambda argv, stdin: (0, b"null", b""))
        assert list(t.stream(ToolCall(tool_name="x"))) == []
