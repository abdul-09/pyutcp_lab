"""Asynchronous transport machinery.

These mirror the synchronous transports but run on ``asyncio``: the connection
protocol's methods are awaitable, ``discover``/``call`` are coroutines, and
``stream`` is an async iterator. The same :class:`~pyutcp_lab.transports.base.Deadline`
type bounds each operation, so deadline handling carries over unchanged.

An async transport satisfies the :class:`~pyutcp_lab.client.async_client.AsyncTransport`
protocol (it exposes an awaitable ``call``), so anything built here plugs
straight into :class:`~pyutcp_lab.client.async_client.AsyncUtcpClient`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Awaitable, Optional, Protocol

from ..core.models import Manual, ToolCall
from .base import Deadline


class AsyncConnection(Protocol):
    """An awaitable request/response connection."""

    def request(
        self, method: str, path: str, body: Optional[bytes]
    ) -> Awaitable[None]: ...

    def read(self, *, timeout: float) -> Awaitable[bytes]: ...

    def close(self) -> Awaitable[None]: ...


class AsyncTransportBase(ABC):
    """Base class for asyncio transports."""

    @abstractmethod
    async def discover(self, *, deadline: Optional[Deadline] = None) -> Manual:
        """Return the provider's manual."""

    @abstractmethod
    async def call(
        self, call: ToolCall, *, deadline: Optional[Deadline] = None
    ) -> Any:
        """Invoke one tool and return its decoded result."""

    @abstractmethod
    def stream(
        self, call: ToolCall, *, deadline: Optional[Deadline] = None
    ) -> AsyncIterator[Any]:
        """Invoke one tool and yield result chunks as they arrive."""

    async def aclose(self) -> None:  # noqa: B027 - intentional no-op default
        """Release resources. Default is a no-op; override if needed."""

    async def __aenter__(self) -> "AsyncTransportBase":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()
