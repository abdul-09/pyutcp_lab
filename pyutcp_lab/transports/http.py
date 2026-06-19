"""HTTP transport.

The transport is written against a small :class:`Connection` protocol rather
than a concrete HTTP client, so the time-sensitive behaviour can be exercised
deterministically with an in-process fake. A real deployment supplies a
connection factory backed by an actual HTTP library; the tests supply a fake
that can simulate slow, trickling responses.

The provider's manual is fetched from a discovery endpoint and decoded from
UTCP-shaped JSON: a top-level object with a ``tools`` array.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Iterator, Optional, Protocol

from ..core.errors import TransportError
from ..core.models import Manual, Provider, Tool, ToolCall, TransportType
from .base import Deadline, Transport


class Connection(Protocol):
    """A minimal request/response connection.

    ``read`` pulls the next chunk of the response body, blocking up to
    ``timeout`` seconds. It returns ``b""`` at end of stream. Implementations
    raise on transport faults.
    """

    def request(self, method: str, path: str, body: Optional[bytes]) -> None: ...

    def read(self, *, timeout: float) -> bytes: ...

    def close(self) -> None: ...


ConnectionFactory = Callable[[Provider], Connection]


class HttpTransport(Transport):
    """Talks to an HTTP provider via an injected connection factory."""

    def __init__(
        self,
        provider: Provider,
        connect: ConnectionFactory,
        *,
        discovery_path: str = "/utcp",
        default_timeout: float = 30.0,
    ) -> None:
        if provider.transport not in (
            TransportType.HTTP,
            TransportType.STREAMING_HTTP,
        ):
            raise TransportError(
                f"HttpTransport cannot serve transport {provider.transport.value!r}"
            )
        self._provider = provider
        self._connect = connect
        self._discovery_path = discovery_path
        self._default_timeout = default_timeout

    # -- internal helpers ---------------------------------------------------

    def _budget(self, deadline: Optional[Deadline]) -> float:
        """Per-read timeout: whatever the deadline still allows, capped at default."""
        if deadline is None:
            return self._default_timeout
        return min(self._default_timeout, deadline.remaining())

    def _read_all(
        self, conn: Connection, deadline: Optional[Deadline], *, what: str
    ) -> bytes:
        """Drain the response, charging every read against the deadline.

        This is what keeps the latency behaviour correct: the deadline is checked
        before *each* read and the per-read timeout shrinks as the budget is
        consumed, so a slow trickling response cannot outlast the budget.
        """
        chunks: list[bytes] = []
        while True:
            if deadline is not None:
                deadline.check(what=what)
            timeout = self._budget(deadline)
            chunk = conn.read(timeout=timeout)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)

    def _decode_manual(self, raw: bytes) -> Manual:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise TransportError(f"invalid manual payload: {exc}") from exc
        if not isinstance(payload, dict):
            raise TransportError("manual payload must be a JSON object")
        tools_raw = payload.get("tools", [])
        if not isinstance(tools_raw, list):
            raise TransportError("manual 'tools' must be an array")
        tools = tuple(Tool(**t) for t in tools_raw)
        return Manual(provider=self._provider, tools=tools)

    def _decode_result(self, raw: bytes) -> Any:
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise TransportError(f"invalid tool result: {exc}") from exc

    # -- Transport contract -------------------------------------------------

    def discover(self, *, deadline: Optional[Deadline] = None) -> Manual:
        conn = self._connect(self._provider)
        try:
            conn.request("GET", self._discovery_path, None)
            raw = self._read_all(conn, deadline, what="discovery")
        finally:
            conn.close()
        return self._decode_manual(raw)

    def call(self, call: ToolCall, *, deadline: Optional[Deadline] = None) -> Any:
        conn = self._connect(self._provider)
        try:
            body = json.dumps(call.arguments).encode("utf-8")
            conn.request("POST", f"/tools/{call.tool_name}", body)
            raw = self._read_all(conn, deadline, what="tool call")
        finally:
            conn.close()
        return self._decode_result(raw)

    def stream(
        self, call: ToolCall, *, deadline: Optional[Deadline] = None
    ) -> Iterator[Any]:
        conn = self._connect(self._provider)
        try:
            body = json.dumps(call.arguments).encode("utf-8")
            conn.request("POST", f"/tools/{call.tool_name}", body)
            while True:
                if deadline is not None:
                    deadline.check(what="tool stream")
                chunk = conn.read(timeout=self._budget(deadline))
                if not chunk:
                    break
                yield self._decode_result(chunk)
        finally:
            conn.close()
