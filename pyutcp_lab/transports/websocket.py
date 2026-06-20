"""Synchronous WebSocket transport.

A WebSocket provider keeps one persistent connection that carries every request
and response. This transport opens that connection lazily on first use and keeps
it for the life of the transport, sending a correlated request frame per call and
reading frames until the matching response is complete.

The connection is injected as a small protocol (``send``/``recv``/``close``) so
the transport is tested without a real socket. ``discover`` and ``call`` read
until the final frame for their request; ``stream`` yields each intermediate
frame's data as it arrives and stops at the final one.
"""

from __future__ import annotations

from typing import Any, Callable, Iterator, Optional, Protocol

from ..core.models import Manual, Provider, Tool, ToolCall, TransportType
from .base import Deadline, Transport
from .ws_protocol import (
    FrameError,
    Request,
    RequestIds,
    decode_response,
    matches,
)


class WebSocket(Protocol):
    """A minimal synchronous WebSocket connection."""

    def send(self, message: str) -> None: ...

    def recv(self, *, timeout: float) -> str: ...

    def close(self) -> None: ...


WebSocketFactory = Callable[[Provider], WebSocket]


class WebSocketTransport(Transport):
    """Talks to a WebSocket provider over one persistent connection."""

    def __init__(
        self,
        provider: Provider,
        connect: WebSocketFactory,
        *,
        default_timeout: float = 30.0,
    ) -> None:
        if provider.transport is not TransportType.WEBSOCKET:
            raise FrameError(
                f"WebSocketTransport cannot serve transport {provider.transport.value!r}"
            )
        self._provider = provider
        self._connect = connect
        self._default_timeout = default_timeout
        self._ws: Optional[WebSocket] = None
        self._ids = RequestIds()

    def _socket(self) -> WebSocket:
        if self._ws is None:
            self._ws = self._connect(self._provider)
        return self._ws

    def _timeout(self, deadline: Optional[Deadline]) -> float:
        if deadline is None:
            return self._default_timeout
        return min(self._default_timeout, deadline.remaining())

    def _send(self, request: Request) -> None:
        self._socket().send(request.encode())

    def _recv_for(self, request_id: int, deadline: Optional[Deadline], what: str):
        """Read frames until one matching the request arrives; return it."""
        ws = self._socket()
        while True:
            if deadline is not None:
                deadline.check(what=what)
            raw = ws.recv(timeout=self._timeout(deadline))
            response = decode_response(raw)
            if not matches(response, request_id):
                continue  # a frame for a different request; skip it
            if response.is_error:
                raise FrameError(f"server error: {response.error}")
            return response

    def discover(self, *, deadline: Optional[Deadline] = None) -> Manual:
        request_id = self._ids.next()
        self._send(Request(id=request_id, method="discover"))
        response = self._recv_for(request_id, deadline, "WebSocket discovery")
        payload = response.data or {}
        if not isinstance(payload, dict):
            raise FrameError("WebSocket manual payload must be an object")
        tools = tuple(Tool(**t) for t in payload.get("tools", []))
        return Manual(provider=self._provider, tools=tools)

    def call(self, call: ToolCall, *, deadline: Optional[Deadline] = None) -> Any:
        request_id = self._ids.next()
        self._send(
            Request(
                id=request_id,
                method="call",
                tool=call.tool_name,
                arguments=call.arguments,
            )
        )
        response = self._recv_for(request_id, deadline, "WebSocket call")
        return response.data

    def stream(
        self, call: ToolCall, *, deadline: Optional[Deadline] = None
    ) -> Iterator[Any]:
        request_id = self._ids.next()
        self._send(
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
            raw = ws.recv(timeout=self._timeout(deadline))
            response = decode_response(raw)
            if not matches(response, request_id):
                continue
            if response.is_error:
                raise FrameError(f"server error: {response.error}")
            if response.data is not None:
                yield response.data
            if response.final:
                break

    def close(self) -> None:
        if self._ws is not None:
            self._ws.close()
            self._ws = None
