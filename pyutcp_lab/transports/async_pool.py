"""Asynchronous connection pool.

The synchronous :class:`~pyutcp_lab.transports.pool.ConnectionPool` rejects a
request once a host is at its connection ceiling. Under ``asyncio`` a better
contract is available: a caller that finds the host saturated can *wait* for a
connection to be released instead of failing, because suspending one coroutine
is cheap and does not block anything else.

This pool enforces a per-host ceiling and a global ceiling, reuses idle
connections, and when a ceiling is reached it awaits an :class:`asyncio.Condition`
until capacity frees up (or an optional timeout elapses). Acquisition and release
are coordinated through a single lock, so the check-and-reserve step is atomic
with respect to other coroutines.
"""

from __future__ import annotations

import asyncio
import itertools
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from ..core.errors import TransportError


class PoolTimeoutError(TransportError):
    """Raised when waiting for a free connection exceeds the timeout."""


@dataclass
class AsyncPooledConnection:
    """A handle to one live async connection to a host."""

    host: str
    conn_id: int
    in_use: bool = False
    idle_seq: int = 0


@dataclass
class _HostState:
    live: int = 0
    idle: list[AsyncPooledConnection] = field(default_factory=list)


# Opens a new connection to a host, returning an opaque id.
AsyncConnFactory = Callable[[str], Awaitable[int]]


async def _default_factory(_host: str, _counter=itertools.count(1)) -> int:
    return next(_counter)


class AsyncConnectionPool:
    """Caps live async connections per host, awaiting capacity when saturated."""

    def __init__(
        self,
        *,
        per_host: int,
        max_total: int,
        factory: Optional[AsyncConnFactory] = None,
    ) -> None:
        if per_host < 1:
            raise ValueError("per_host must be >= 1")
        if max_total < per_host:
            raise ValueError("max_total must be >= per_host")
        self._per_host = per_host
        self._max_total = max_total
        self._factory = factory or _default_factory
        self._hosts: dict[str, _HostState] = {}
        self._idle_clock = itertools.count(1)
        self._total_live = 0
        self._lock = asyncio.Lock()
        self._released = asyncio.Condition(self._lock)

    # -- introspection ------------------------------------------------------

    @property
    def total_live(self) -> int:
        return self._total_live

    def live_for(self, host: str) -> int:
        state = self._hosts.get(host)
        return state.live if state else 0

    def idle_for(self, host: str) -> int:
        state = self._hosts.get(host)
        return len(state.idle) if state else 0

    # -- internals ----------------------------------------------------------

    def _host(self, host: str) -> _HostState:
        return self._hosts.setdefault(host, _HostState())

    def _evict_one_idle(self) -> bool:
        victim_host: Optional[str] = None
        victim: Optional[AsyncPooledConnection] = None
        for host, state in self._hosts.items():
            for conn in state.idle:
                if victim is None or conn.idle_seq < victim.idle_seq:
                    victim = conn
                    victim_host = host
        if victim is None or victim_host is None:
            return False
        self._hosts[victim_host].idle.remove(victim)
        self._total_live -= 1
        return True

    def _try_acquire_locked(self, host: str) -> Optional[AsyncPooledConnection]:
        """Attempt one acquisition under the lock. None means no capacity yet."""
        state = self._host(host)
        if state.idle:
            conn = state.idle.pop()
            conn.in_use = True
            return conn
        if state.live >= self._per_host:
            return None
        if self._total_live >= self._max_total and not self._evict_one_idle():
            return None
        return state  # signal caller to open a new connection

    # -- operations ---------------------------------------------------------

    async def acquire(
        self, host: str, *, timeout: Optional[float] = None
    ) -> AsyncPooledConnection:
        """Acquire a connection, awaiting capacity if the host is saturated.

        Reuses an idle connection if available. Otherwise opens a new one if the
        per-host and global ceilings allow; if not, waits for another coroutine
        to release a connection. A ``timeout`` bounds the total wait.
        """
        async def _attempt() -> AsyncPooledConnection:
            async with self._lock:
                while True:
                    outcome = self._try_acquire_locked(host)
                    if isinstance(outcome, AsyncPooledConnection):
                        return outcome
                    if outcome is not None:
                        # Capacity exists; open a new connection.
                        state = outcome
                        conn = AsyncPooledConnection(
                            host=host,
                            conn_id=await self._factory(host),
                            in_use=True,
                        )
                        state.live += 1
                        self._total_live += 1
                        return conn
                    # Saturated: wait for a release, then retry.
                    await self._released.wait()

        if timeout is None:
            return await _attempt()
        try:
            return await asyncio.wait_for(_attempt(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise PoolTimeoutError(
                f"timed out waiting for a connection to {host!r}"
            ) from exc

    async def release(self, conn: AsyncPooledConnection) -> None:
        """Return a connection to its host's idle set and wake one waiter."""
        async with self._lock:
            if not conn.in_use:
                raise TransportError("releasing a connection that is not in use")
            state = self._host(conn.host)
            conn.in_use = False
            conn.idle_seq = next(self._idle_clock)
            state.idle.append(conn)
            self._released.notify()

    async def discard(self, conn: AsyncPooledConnection) -> None:
        """Permanently close a connection, freeing capacity and waking a waiter."""
        async with self._lock:
            state = self._host(conn.host)
            if conn in state.idle:
                state.idle.remove(conn)
            state.live -= 1
            self._total_live -= 1
            conn.in_use = False
            self._released.notify()
