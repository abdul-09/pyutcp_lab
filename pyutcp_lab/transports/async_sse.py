"""Asynchronous SSE transport.

The async counterpart to :class:`~pyutcp_lab.transports.sse.SSETransport`. Frame
parsing is pure and has no I/O, so this reuses :class:`~pyutcp_lab.transports.sse.SSEParser`
unchanged and only awaits the reads. The same chunk-buffering guarantee holds:
an event split across reads is reassembled before it is emitted.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Optional

from ..core.errors import TransportError
from ..core.models import Manual, Provider, Tool, ToolCall, TransportType
from .async_base import AsyncConnection, AsyncTransportBase
from .async_http import AsyncConnectionFactory
from .base import Deadline
from .sse import SSEParser


class AsyncSSETransport(AsyncTransportBase):
    """Streams tool results from an SSE provider over asyncio."""

    def __init__(
        self,
        provider: Provider,
        connect: AsyncConnectionFactory,
        *,
        discovery_path: str = "/utcp",
        default_timeout: float = 30.0,
    ) -> None:
        if provider.transport is not TransportType.SSE:
            raise TransportError(
                f"AsyncSSETransport cannot serve transport {provider.transport.value!r}"
            )
        self._provider = provider
        self._connect = connect
        self._discovery_path = discovery_path
        self._default_timeout = default_timeout

    def _timeout(self, deadline: Optional[Deadline]) -> float:
        if deadline is None:
            return self._default_timeout
        return min(self._default_timeout, deadline.remaining())

    async def discover(self, *, deadline: Optional[Deadline] = None) -> Manual:
        conn = self._connect(self._provider)
        parser = SSEParser()
        events = []
        try:
            await conn.request("GET", self._discovery_path, None)
            while True:
                if deadline is not None:
                    deadline.check(what="SSE discovery")
                chunk = await conn.read(timeout=self._timeout(deadline))
                if not chunk:
                    break
                events.extend(parser.feed(chunk))
            tail = parser.flush()
            if tail is not None:
                events.append(tail)
        finally:
            await conn.close()
        if not events:
            raise TransportError("SSE discovery produced no manual event")
        payload = events[0].json()
        tools = tuple(Tool(**t) for t in payload.get("tools", []))
        return Manual(provider=self._provider, tools=tools)

    async def call(
        self, call: ToolCall, *, deadline: Optional[Deadline] = None
    ) -> Any:
        async for item in self.stream(call, deadline=deadline):
            return item
        return None

    async def stream(
        self, call: ToolCall, *, deadline: Optional[Deadline] = None
    ) -> AsyncIterator[Any]:
        conn = self._connect(self._provider)
        parser = SSEParser()
        try:
            body = json.dumps(call.arguments).encode("utf-8")
            await conn.request("POST", f"/tools/{call.tool_name}", body)
            while True:
                if deadline is not None:
                    deadline.check(what="SSE stream")
                chunk = await conn.read(timeout=self._timeout(deadline))
                if not chunk:
                    break
                for event in parser.feed(chunk):
                    yield event.json()
            tail = parser.flush()
            if tail is not None:
                yield tail.json()
        finally:
            await conn.close()
