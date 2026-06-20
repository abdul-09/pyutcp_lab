"""The UTCP client.

The client is the front door: it owns a :class:`ToolRepository` of registered
providers and resolves the right :class:`Transport` for whichever provider owns
a given tool, then invokes it. A single call is straightforward; the interesting
behaviour is :meth:`call_chain`, which runs several tool calls in sequence under
one shared :class:`Budget`.

For the chain to honour its end-to-end budget, each individual call must run
under a deadline derived from the budget's *remaining* time, not under the
transport's own default timeout. The client therefore asks the budget for a
fresh deadline before every step and threads it into the transport call.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, Sequence

from ..core.errors import TransportError
from ..core.models import Manual, ToolCall
from ..core.schema import ensure_valid_arguments
from ..registry.cache import ResultCache
from ..registry.repository import ToolRepository
from ..transports.base import Deadline, Transport
from .budget import Budget
from .cache_key import cache_key
from .metrics import MetricsCollector

# Resolves the transport to use for a given provider name.
TransportResolver = Callable[[str], Transport]


class UtcpClient:
    """Registers providers and calls their tools through the right transport."""

    def __init__(
        self,
        resolve_transport: TransportResolver,
        *,
        repository: Optional[ToolRepository] = None,
        metrics: Optional[MetricsCollector] = None,
        cache: Optional[ResultCache] = None,
        validate_arguments: bool = False,
    ) -> None:
        self._repo = repository or ToolRepository()
        self._resolve = resolve_transport
        self._metrics = metrics
        self._cache = cache
        self._validate = validate_arguments

    @property
    def repository(self) -> ToolRepository:
        return self._repo

    @property
    def metrics(self) -> Optional[MetricsCollector]:
        return self._metrics

    @property
    def cache(self) -> Optional[ResultCache]:
        return self._cache

    def register(self, manual: Manual, *, replace: bool = False) -> None:
        self._repo.register(manual, replace=replace)

    def _transport_for_tool(self, tool_name: str) -> Transport:
        owner = self._repo.owner_of(tool_name)
        if owner is None:
            raise TransportError(f"unknown tool {tool_name!r}")
        return self._resolve(owner)

    def _check_arguments(self, call: ToolCall) -> None:
        """Validate a call's arguments against the tool's input schema."""
        tool = self._repo.get_tool(call.tool_name)
        if tool is not None and tool.inputs:
            ensure_valid_arguments(call.arguments, tool.inputs)

    def _invoke(self, call: ToolCall, deadline: Optional[Deadline]) -> Any:
        """Run the transport call, timing it when metrics are attached."""
        transport = self._transport_for_tool(call.tool_name)
        if self._metrics is None:
            return transport.call(call, deadline=deadline)
        with self._metrics.time_call(call.tool_name):
            return transport.call(call, deadline=deadline)

    def call(
        self,
        call: ToolCall,
        *,
        deadline: Optional[Deadline] = None,
        use_cache: bool = True,
    ) -> Any:
        """Invoke a single tool through its provider's transport.

        When the client was built with ``validate_arguments=True``, the call's
        arguments are checked against the tool's declared input schema first.
        When a result cache is attached and ``use_cache`` is True, a cached
        result for identical arguments is returned without hitting the transport,
        and fresh results are stored. When a metrics collector is attached, the
        transport call is timed and recorded (cache hits are not timed).
        """
        if self._validate:
            self._check_arguments(call)

        if self._cache is not None and use_cache:
            key = cache_key(call)
            cached = self._cache.get(key)
            if cached is not None:
                return cached
            result = self._invoke(call, deadline)
            self._cache.set(key, result)
            return result

        return self._invoke(call, deadline)

    def call_chain(
        self,
        calls: Sequence[ToolCall],
        *,
        budget: Budget,
    ) -> list[Any]:
        """Run several tool calls in sequence under one shared budget.

        Before each call the budget is checked and a fresh deadline derived from
        the time still remaining is threaded into the transport, so the chain as
        a whole cannot outlast the budget even if early calls are slow.
        """
        results: list[Any] = []
        for index, call in enumerate(calls):
            budget.check(what=f"chain step {index} ({call.tool_name})")
            deadline = budget.remaining_deadline()
            results.append(self.call(call, deadline=deadline))
        return results
