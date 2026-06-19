"""GraphQL transport.

A GraphQL provider exposes its tools as fields on a root type. Discovery reads a
manual the provider returns from a conventional ``utcp`` query; a tool call is
issued as a GraphQL operation whose single root field is named after the tool,
with the tool's arguments passed as GraphQL variables.

Like the HTTP transport, this is written against the injectable connection
protocol so it can be tested without a network. A GraphQL response is a JSON
object with optional ``data`` and ``errors`` keys; a non-empty ``errors`` array
turns into a :class:`TransportError`.
"""

from __future__ import annotations

import json
from typing import Any, Iterator, Optional

from ..core.errors import TransportError
from ..core.models import Manual, Provider, Tool, ToolCall, TransportType
from .base import Deadline, Transport
from .http import ConnectionFactory


def build_query(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Build a GraphQL request body for invoking ``tool_name``.

    Arguments are passed as variables and wired into the field's argument list
    by name, which keeps the query string stable and lets the server type-check
    the variables.
    """
    if arguments:
        var_defs = ", ".join(f"${k}: JSON" for k in arguments)
        field_args = ", ".join(f"{k}: ${k}" for k in arguments)
        query = (
            f"query Call({var_defs}) {{ {tool_name}({field_args}) }}"
        )
    else:
        query = f"query Call {{ {tool_name} }}"
    return {"query": query, "variables": dict(arguments)}


class GraphQLTransport(Transport):
    """Talks to a GraphQL provider via an injected connection factory."""

    def __init__(
        self,
        provider: Provider,
        connect: ConnectionFactory,
        *,
        endpoint_path: str = "/graphql",
        default_timeout: float = 30.0,
    ) -> None:
        if provider.transport is not TransportType.GRAPHQL:
            raise TransportError(
                f"GraphQLTransport cannot serve transport {provider.transport.value!r}"
            )
        self._provider = provider
        self._connect = connect
        self._endpoint = endpoint_path
        self._default_timeout = default_timeout

    def _timeout(self, deadline: Optional[Deadline]) -> float:
        if deadline is None:
            return self._default_timeout
        return min(self._default_timeout, deadline.remaining())

    def _read_all(self, conn: Any, deadline: Optional[Deadline], what: str) -> bytes:
        chunks: list[bytes] = []
        while True:
            if deadline is not None:
                deadline.check(what=what)
            chunk = conn.read(timeout=self._timeout(deadline))
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)

    def _post(self, body: dict[str, Any], deadline: Optional[Deadline], what: str) -> Any:
        conn = self._connect(self._provider)
        try:
            conn.request("POST", self._endpoint, json.dumps(body).encode("utf-8"))
            raw = self._read_all(conn, deadline, what)
        finally:
            conn.close()
        return self._decode(raw)

    @staticmethod
    def _decode(raw: bytes) -> Any:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise TransportError(f"invalid GraphQL response: {exc}") from exc
        if not isinstance(payload, dict):
            raise TransportError("GraphQL response must be a JSON object")
        errors = payload.get("errors")
        if errors:
            messages = [
                e.get("message", str(e)) if isinstance(e, dict) else str(e)
                for e in errors
            ]
            raise TransportError("GraphQL errors: " + "; ".join(messages))
        return payload.get("data")

    def discover(self, *, deadline: Optional[Deadline] = None) -> Manual:
        data = self._post(
            {"query": "query { utcp }"}, deadline, "GraphQL discovery"
        )
        manual = (data or {}).get("utcp", {})
        if not isinstance(manual, dict):
            raise TransportError("GraphQL manual payload must be an object")
        tools = tuple(Tool(**t) for t in manual.get("tools", []))
        return Manual(provider=self._provider, tools=tools)

    def call(self, call: ToolCall, *, deadline: Optional[Deadline] = None) -> Any:
        body = build_query(call.tool_name, call.arguments)
        data = self._post(body, deadline, "GraphQL call")
        if isinstance(data, dict):
            # Unwrap the single root field named after the tool.
            return data.get(call.tool_name, data)
        return data

    def stream(
        self, call: ToolCall, *, deadline: Optional[Deadline] = None
    ) -> Iterator[Any]:
        result = self.call(call, deadline=deadline)
        if result is not None:
            yield result
