"""Server-Sent Events (SSE) transport.

SSE delivers a stream of *events*, each a block of lines terminated by a blank
line — that is, events are separated by ``\\n\\n``. Within an event, one or more
``data:`` lines carry the payload (multiple ``data:`` lines are joined with
newlines), and other fields (``event:``, ``id:``) may appear.

The wire does not respect event boundaries when it splits the byte stream into
chunks: a single event may arrive across two reads, and one read may contain
several events plus the start of another. The parser must therefore keep a
buffer of bytes not yet forming a complete event and only emit events once their
terminating blank line has been seen. Parsing each chunk independently — or
splitting on single newlines instead of the blank-line delimiter — corrupts any
event that straddles a chunk boundary or spans multiple ``data:`` lines.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

from ..core.errors import TransportError
from ..core.models import Manual, Provider, Tool, ToolCall, TransportType
from .base import Deadline, Transport
from .http import ConnectionFactory


@dataclass
class SSEEvent:
    """One parsed SSE event."""

    data: str
    event: str = "message"
    event_id: Optional[str] = None

    def json(self) -> Any:
        try:
            return json.loads(self.data)
        except json.JSONDecodeError as exc:
            raise TransportError(f"SSE data is not valid JSON: {exc}") from exc


@dataclass
class SSEParser:
    """Incrementally parses SSE bytes into events, buffering partial frames."""

    _buffer: str = field(default="", init=False)

    def feed(self, chunk: bytes) -> list[SSEEvent]:
        """Add a chunk and return any events completed by it.

        Bytes that do not yet complete an event are retained for the next call,
        so an event split across chunk boundaries is parsed correctly once its
        terminating blank line arrives.
        """
        self._buffer += chunk.decode("utf-8")
        events: list[SSEEvent] = []
        # Events are separated by a blank line. Split off every complete frame,
        # leaving any trailing partial frame in the buffer.
        while "\n\n" in self._buffer:
            frame, self._buffer = self._buffer.split("\n\n", 1)
            parsed = self._parse_frame(frame)
            if parsed is not None:
                events.append(parsed)
        return events

    def flush(self) -> Optional[SSEEvent]:
        """Parse any remaining buffered frame at end of stream."""
        if not self._buffer.strip():
            self._buffer = ""
            return None
        frame = self._buffer
        self._buffer = ""
        return self._parse_frame(frame)

    @staticmethod
    def _parse_frame(frame: str) -> Optional[SSEEvent]:
        data_lines: list[str] = []
        event_type = "message"
        event_id: Optional[str] = None
        for line in frame.split("\n"):
            if not line or line.startswith(":"):
                continue  # blank or comment line
            field_name, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if field_name == "data":
                data_lines.append(value)
            elif field_name == "event":
                event_type = value
            elif field_name == "id":
                event_id = value
        if not data_lines:
            return None
        return SSEEvent(
            data="\n".join(data_lines), event=event_type, event_id=event_id
        )


class SSETransport(Transport):
    """Streams tool results from an SSE provider."""

    def __init__(
        self,
        provider: Provider,
        connect: ConnectionFactory,
        *,
        discovery_path: str = "/utcp",
        default_timeout: float = 30.0,
    ) -> None:
        if provider.transport is not TransportType.SSE:
            raise TransportError(
                f"SSETransport cannot serve transport {provider.transport.value!r}"
            )
        self._provider = provider
        self._connect = connect
        self._discovery_path = discovery_path
        self._default_timeout = default_timeout

    def _timeout(self, deadline: Optional[Deadline]) -> float:
        if deadline is None:
            return self._default_timeout
        return min(self._default_timeout, deadline.remaining())

    def discover(self, *, deadline: Optional[Deadline] = None) -> Manual:
        conn = self._connect(self._provider)
        parser = SSEParser()
        events: list[SSEEvent] = []
        try:
            conn.request("GET", self._discovery_path, None)
            while True:
                if deadline is not None:
                    deadline.check(what="SSE discovery")
                chunk = conn.read(timeout=self._timeout(deadline))
                if not chunk:
                    break
                events.extend(parser.feed(chunk))
            tail = parser.flush()
            if tail is not None:
                events.append(tail)
        finally:
            conn.close()
        if not events:
            raise TransportError("SSE discovery produced no manual event")
        payload = events[0].json()
        tools = tuple(Tool(**t) for t in payload.get("tools", []))
        return Manual(provider=self._provider, tools=tools)

    def call(self, call: ToolCall, *, deadline: Optional[Deadline] = None) -> Any:
        # A non-streaming call returns the first event's payload.
        for item in self.stream(call, deadline=deadline):
            return item
        return None

    def stream(
        self, call: ToolCall, *, deadline: Optional[Deadline] = None
    ) -> Iterator[Any]:
        conn = self._connect(self._provider)
        parser = SSEParser()
        try:
            body = json.dumps(call.arguments).encode("utf-8")
            conn.request("POST", f"/tools/{call.tool_name}", body)
            while True:
                if deadline is not None:
                    deadline.check(what="SSE stream")
                chunk = conn.read(timeout=self._timeout(deadline))
                if not chunk:
                    break
                for event in parser.feed(chunk):
                    yield event.json()
            tail = parser.flush()
            if tail is not None:
                yield tail.json()
        finally:
            conn.close()
