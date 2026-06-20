"""Asynchronous WebSocket transport.

The async counterpart to :class:`~pyutcp_lab.transports.websocket.WebSocketTransport`.
Framing is pure and shared, so this reuses :mod:`pyutcp_lab.transports.ws_protocol`
unchanged and only awaits the socket I/O. The persistent connection is opened
lazily on first use and held for the life of the transport.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Awaitable, Callable, Optional, Protocol

from ..core.models import Manual, Provider, Tool, ToolCall, TransportType
from .async_base import AsyncTransportBase
from .base import Deadline
from .ws_protocol import (
    FrameError,
    Request,
    RequestIds,
    decode_response,
    matches,
)


class AsyncWebSocket(Protocol):
    """A minimal asynchronous WebSocket connection."""

    def send(self, message: str) -> Awaitable[None]: ...

    def recv(self, *, timeout: float) -> Awaitable[str]: ...

    def close(self) -> Awaitable[None]: ...


AsyncWebSocketFactory = Callable[[Provider], AsyncWebSocket]


class AsyncWebSocketTransport(AsyncTransportBase):
    """Talks to a WebSocket provider over one persistent async connection."""

    def __init__(
        self,
        provider: Provider,
        connect: AsyncWebSocketFactory,
        *,
        default_timeout: float = 30.0,
    ) -> None:
        if provider.transport is not TransportType.WEBSOCKET:
            raise FrameError(
                f"AsyncWebSocketTransport cannot serve transport "
                f"{provider.transport.value!r}"
            )
        self._provider = provider
        self._connect = connect
        self._default_timeout = default_timeout
        self._ws: Optional[AsyncWebSocket] = None
        self._ids = RequestIds()

    def _socket(self) -> AsyncWebSocket:
        if self._ws is None:
            self._ws = self._connect(self._provider)
        return self._ws

    def _timeout(self, deadline: Optional[Deadline]) -> float:
        if deadline is None:
            return self._default_timeout
        return min(self._default_timeout, deadline.remaining())

    async def _send(self, request: Request) -> None:
        await self._socket().send(request.encode())

    async def _recv_for(self, request_id: int, deadline: Optional[Deadline], what: str):
        ws = self._socket()
        while True:
            if deadline is not None:
                deadline.check(what=what)
            raw = await ws.recv(timeout=self._timeout(deadline))
            response = decode_response(raw)
            if not matches(response, request_id):
                continue
            if response.is_error:
                raise FrameError(f"server error: {response.error}")
            return response

    async def discover(self, *, deadline: Optional[Deadline] = None) -> Manual:
        request_id = self._ids.next()
        await self._send(Request(id=request_id, method="discover"))
        response = await self._recv_for(request_id, deadline, "WebSocket discovery")
        payload = response.data or {}
        if not isinstance(payload, dict):
            raise FrameError("WebSocket manual payload must be an object")
        tools = tuple(Tool(**t) for t in payload.get("tools", []))
        return Manual(provider=self._provider, tools=tools)

    async def call(
        self, call: ToolCall, *, deadline: Optional[Deadline] = None
    ) -> Any:
        request_id = self._ids.next()
        await self._send(
            Request(
                id=request_id,
                method="call",
                tool=call.tool_name,
                arguments=call.arguments,
            )
        )
        response = await self._recv_for(request_id, deadline, "WebSocket call")
        return response.data

    async def stream(
        self, call: ToolCall, *, deadline: Optional[Deadline] = None
    ) -> AsyncIterator[Any]:
        request_id = self._ids.next()
        await self._send(
            Request(
                id=request_id,
                method="call",
                tool=call.tool_name,
                arguments=call.arguments,
            )
        )
        ws = self._socket()
        while True:
            if deadline is not None:
                deadline.check(what="WebSocket stream")
            raw = await ws.recv(timeout=self._timeout(deadline))
            response = decode_response(raw)
            if not matches(response, request_id):
                continue
            if response.is_error:
                raise FrameError(f"server error: {response.error}")
            if response.data is not None:
                yield response.data
            if response.final:
                break

    async def aclose(self) -> None:
        if self._ws is not None:
            await self._ws.close()
            self._ws = None
