"""WebSocket framing for UTCP.

WebSocket is bidirectional and persistent: one connection carries many messages
in both directions, so responses are not implicitly paired with requests the way
they are over HTTP. UTCP pairs them with a *correlation id*. Each request frame
carries an ``id``; the server echoes that ``id`` on every response frame it sends
for the request, and marks the final frame of a response with ``"final": true``.

This module holds the pure framing logic, shared by the synchronous and
asynchronous transports: building request frames, allocating ids, and
classifying an incoming frame as a result, an error, or the end of a stream. It
performs no I/O, so both transports reuse it unchanged.
"""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass
from typing import Any, Optional

from ..core.errors import TransportError


class FrameError(TransportError):
    """Raised when a WebSocket frame is malformed or reports a server error."""


@dataclass(frozen=True)
class Request:
    """An outgoing request frame."""

    id: int
    method: str
    tool: Optional[str] = None
    arguments: Optional[dict[str, Any]] = None

    def encode(self) -> str:
        body: dict[str, Any] = {"id": self.id, "method": self.method}
        if self.tool is not None:
            body["tool"] = self.tool
        if self.arguments is not None:
            body["arguments"] = self.arguments
        return json.dumps(body)


@dataclass(frozen=True)
class Response:
    """A decoded response frame."""

    id: Optional[int]
    data: Any = None
    error: Optional[str] = None
    final: bool = True

    @property
    def is_error(self) -> bool:
        return self.error is not None


class RequestIds:
    """Hands out monotonically increasing request ids."""

    def __init__(self) -> None:
        self._counter = itertools.count(1)

    def next(self) -> int:
        return next(self._counter)


def decode_response(raw: str) -> Response:
    """Parse a raw text frame into a :class:`Response`.

    A frame is a JSON object. ``id`` correlates it to a request; ``data`` carries
    the payload; ``error`` (if present) marks a failure; ``final`` defaults to
    True so a frame without it is treated as the end of its response.
    """
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FrameError(f"invalid WebSocket frame: {exc}") from exc
    if not isinstance(payload, dict):
        raise FrameError("WebSocket frame must be a JSON object")
    return Response(
        id=payload.get("id"),
        data=payload.get("data"),
        error=payload.get("error"),
        final=bool(payload.get("final", True)),
    )


def matches(response: Response, request_id: int) -> bool:
    """Whether a response frame belongs to the given request.

    A frame with no id is treated as a broadcast and accepted, which lets a
    server push an unsolicited result on the same connection.
    """
    return response.id is None or response.id == request_id
