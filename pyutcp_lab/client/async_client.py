"""Asynchronous client with bounded concurrency.

UTCP's real value shows up when a caller fans out across many tools at once:
gathering data from a dozen providers in parallel is the common case, not the
exception. :class:`AsyncUtcpClient` runs tool calls concurrently under a
configurable concurrency ceiling, so a plan of a hundred calls does not open a
hundred simultaneous connections.

The client is transport-agnostic: it resolves an *async transport* (anything
exposing an awaitable ``call``) for the provider that owns each tool. The
concurrency limit is enforced with an :class:`asyncio.Semaphore`, and an overall
:class:`~pyutcp_lab.client.budget.Budget` can bound the wall-clock time of a
whole fan-out.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Optional, Protocol, Sequence

from ..core.errors import TransportError
from ..core.models import Manual, ToolCall
from ..registry.repository import ToolRepository
from .budget import Budget


class AsyncTransport(Protocol):
    """An async transport: its ``call`` returns an awaitable result."""

    def call(self, call: ToolCall) -> Awaitable[Any]: ...


AsyncTransportResolver = Callable[[str], AsyncTransport]


class AsyncUtcpClient:
    """Runs tool calls concurrently under a bounded concurrency limit."""

    def __init__(
        self,
        resolve_transport: AsyncTransportResolver,
        *,
        repository: Optional[ToolRepository] = None,
        max_concurrency: int = 8,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self._repo = repository or ToolRepository()
        self._resolve = resolve_transport
        self._max_concurrency = max_concurrency

    @property
    def repository(self) -> ToolRepository:
        return self._repo

    @property
    def max_concurrency(self) -> int:
        return self._max_concurrency

    def register(self, manual: Manual, *, replace: bool = False) -> None:
        self._repo.register(manual, replace=replace)

    def _transport_for_tool(self, tool_name: str) -> AsyncTransport:
        owner = self._repo.owner_of(tool_name)
        if owner is None:
            raise TransportError(f"unknown tool {tool_name!r}")
        return self._resolve(owner)

    async def call(self, call: ToolCall) -> Any:
        """Invoke a single tool through its provider's async transport."""
        transport = self._transport_for_tool(call.tool_name)
        return await transport.call(call)

    async def gather(
        self,
        calls: Sequence[ToolCall],
        *,
        budget: Optional[Budget] = None,
        return_exceptions: bool = False,
    ) -> list[Any]:
        """Run all calls concurrently, capped at ``max_concurrency``.

        Results come back in the same order as ``calls``. When ``budget`` is
        given, the whole fan-out is wrapped in a timeout equal to the budget's
        remaining time; exceeding it raises :class:`asyncio.TimeoutError`. With
        ``return_exceptions`` set, a failing call yields its exception in place
        of a result instead of cancelling the batch.
        """
        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def run_one(call: ToolCall) -> Any:
            async with semaphore:
                return await self.call(call)

        coros = [run_one(c) for c in calls]
        gathered = asyncio.gather(*coros, return_exceptions=return_exceptions)

        if budget is not None:
            remaining = budget.remaining()
            if remaining <= 0:
                gathered.cancel()
                raise asyncio.TimeoutError("budget exhausted before gather started")
            return await asyncio.wait_for(gathered, timeout=remaining)
        return await gathered

    async def map_tool(
        self,
        tool_name: str,
        argument_sets: Sequence[dict[str, Any]],
        *,
        budget: Optional[Budget] = None,
    ) -> list[Any]:
        """Call one tool repeatedly with different arguments, concurrently."""
        calls = [
            ToolCall(tool_name=tool_name, arguments=args) for args in argument_sets
        ]
        return await self.gather(calls, budget=budget)
