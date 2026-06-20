"""Asynchronous HTTP transport.

The async counterpart to :class:`~pyutcp_lab.transports.http.HttpTransport`. It
talks to a provider through an injected async connection factory, drains the
response while charging each read against the caller's deadline, and decodes the
UTCP-shaped JSON manual or result. Behaviour matches the sync transport; only the
I/O is awaited.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

from ..core.errors import TransportError
from ..core.models import Manual, Provider, Tool, ToolCall, TransportType
from .async_base import AsyncConnection, AsyncTransportBase
from .base import Deadline

AsyncConnectionFactory = Callable[[Provider], AsyncConnection]


class AsyncHttpTransport(AsyncTransportBase):
    """Async HTTP transport over an injected awaitable connection factory."""

    def __init__(
        self,
        provider: Provider,
        connect: AsyncConnectionFactory,
        *,
        discovery_path: str = "/utcp",
        default_timeout: float = 30.0,
    ) -> None:
        if provider.transport not in (
            TransportType.HTTP,
            TransportType.STREAMING_HTTP,
        ):
            raise TransportError(
                f"AsyncHttpTransport cannot serve transport {provider.transport.value!r}"
            )
        self._provider = provider
        self._connect = connect
        self._discovery_path = discovery_path
        self._default_timeout = default_timeout

    def _budget(self, deadline: Optional[Deadline]) -> float:
        if deadline is None:
            return self._default_timeout
        return min(self._default_timeout, deadline.remaining())

    async def _read_all(
        self, conn: AsyncConnection, deadline: Optional[Deadline], *, what: str
    ) -> bytes:
        chunks: list[bytes] = []
        while True:
            if deadline is not None:
                deadline.check(what=what)
            chunk = await conn.read(timeout=self._budget(deadline))
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

    @staticmethod
    def _decode_result(raw: bytes) -> Any:
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise TransportError(f"invalid tool result: {exc}") from exc

    async def discover(self, *, deadline: Optional[Deadline] = None) -> Manual:
        conn = self._connect(self._provider)
        try:
            await conn.request("GET", self._discovery_path, None)
            raw = await self._read_all(conn, deadline, what="discovery")
        finally:
            await conn.close()
        return self._decode_manual(raw)

    async def call(
        self, call: ToolCall, *, deadline: Optional[Deadline] = None
    ) -> Any:
        conn = self._connect(self._provider)
        try:
            body = json.dumps(call.arguments).encode("utf-8")
            await conn.request("POST", f"/tools/{call.tool_name}", body)
            raw = await self._read_all(conn, deadline, what="tool call")
        finally:
            await conn.close()
        return self._decode_result(raw)

    async def stream(
        self, call: ToolCall, *, deadline: Optional[Deadline] = None
    ) -> AsyncIterator[Any]:
        conn = self._connect(self._provider)
        try:
            body = json.dumps(call.arguments).encode("utf-8")
            await conn.request("POST", f"/tools/{call.tool_name}", body)
            while True:
                if deadline is not None:
                    deadline.check(what="tool stream")
                chunk = await conn.read(timeout=self._budget(deadline))
                if not chunk:
                    break
                yield self._decode_result(chunk)
        finally:
            await conn.close()
