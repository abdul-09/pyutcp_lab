"""Connection pool.

A pool hands out connections to hosts under a per-host ceiling, reusing idle
connections instead of opening new ones, and evicting the least-recently-used
idle connection when a global ceiling is reached.

Concurrency is modelled explicitly rather than left to wall-clock timing. Every
point at which a real async pool would yield to the event loop is marked by a
call to an injectable *scheduler hook*. In production the hook does nothing; in
tests it is a deterministic scheduler that interleaves several logical callers at
exactly those points. This keeps the pool's concurrent behaviour fully
reproducible (there are no sleeps and no reliance on thread timing) while still
exercising the real check-then-acquire logic that makes pooling subtle.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..core.errors import TransportError

# A scheduler hook is called at every yield point. The default is a no-op.
SchedulerHook = Callable[[str], None]


def _noop(_point: str) -> None:
    return None


@dataclass
class PooledConnection:
    """A handle to one live connection to a host."""

    host: str
    conn_id: int
    in_use: bool = False
    # Monotonic-ish counter stamped each time the connection goes idle; used to
    # pick the least-recently-used idle connection for eviction.
    idle_seq: int = 0


@dataclass
class _HostState:
    live: int = 0
    idle: list[PooledConnection] = field(default_factory=list)


class ConnectionPool:
    """Caps live connections per host and reuses idle ones.

    Parameters
    ----------
    per_host:
        Maximum number of simultaneously *live* connections to any single host.
    max_total:
        Maximum number of live connections across all hosts. When reached, an
        idle connection (LRU) is evicted to make room.
    factory:
        Called to open a new connection to a host; returns an opaque connection
        id. Defaults to an incrementing counter.
    schedule:
        Hook invoked at each internal yield point. Tests inject a deterministic
        scheduler here; production leaves it as the no-op.
    """

    def __init__(
        self,
        *,
        per_host: int,
        max_total: int,
        factory: Optional[Callable[[str], int]] = None,
        schedule: SchedulerHook = _noop,
    ) -> None:
        if per_host < 1:
            raise ValueError("per_host must be >= 1")
        if max_total < per_host:
            raise ValueError("max_total must be >= per_host")
        self._per_host = per_host
        self._max_total = max_total
        self._schedule = schedule
        self._ids = itertools.count(1)
        self._factory = factory or (lambda host: next(self._ids))
        self._hosts: dict[str, _HostState] = {}
        self._idle_clock = itertools.count(1)
        self._total_live = 0

    # -- introspection (used by tests and metrics) --------------------------

    @property
    def total_live(self) -> int:
        return self._total_live

    def live_for(self, host: str) -> int:
        state = self._hosts.get(host)
        return state.live if state else 0

    def idle_for(self, host: str) -> int:
        state = self._hosts.get(host)
        return len(state.idle) if state else 0

    # -- core operations ----------------------------------------------------

    def _host(self, host: str) -> _HostState:
        return self._hosts.setdefault(host, _HostState())

    def _evict_one_idle(self) -> bool:
        """Evict the globally least-recently-used idle connection.

        Returns True if something was evicted. The LRU connection is the idle
        one with the smallest ``idle_seq`` across all hosts.
        """
        victim_host: Optional[str] = None
        victim: Optional[PooledConnection] = None
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

    def acquire(self, host: str) -> PooledConnection:
        """Acquire a live connection to ``host``, blocking-by-policy on the cap.

        The implementation reuses an idle connection if one exists; otherwise it
        opens a new one, subject to the per-host and global ceilings. The
        scheduler hook is called before the capacity decision so that concurrent
        callers interleave at exactly the moment the cap is checked.
        """
        state = self._host(host)

        # Reuse an idle connection if available.
        if state.idle:
            conn = state.idle.pop()
            conn.in_use = True
            return conn

        # Yield point: a concurrent caller may run here, between our decision to
        # open a new connection and the moment we actually reserve capacity.
        self._schedule("before-capacity-check")

        if state.live >= self._per_host:
            raise TransportError(
                f"per-host connection limit reached for {host!r} "
                f"({self._per_host})"
            )

        if self._total_live >= self._max_total:
            if not self._evict_one_idle():
                raise TransportError(
                    "global connection limit reached and nothing idle to evict"
                )

        # Reserve capacity and open the connection.
        conn = PooledConnection(host=host, conn_id=self._factory(host), in_use=True)
        state.live += 1
        self._total_live += 1
        return conn

    def release(self, conn: PooledConnection) -> None:
        """Return a connection to the idle set for its host."""
        if not conn.in_use:
            raise TransportError("releasing a connection that is not in use")
        state = self._host(conn.host)
        conn.in_use = False
        conn.idle_seq = next(self._idle_clock)
        state.idle.append(conn)

    def discard(self, conn: PooledConnection) -> None:
        """Permanently close a connection, freeing its capacity."""
        state = self._host(conn.host)
        if conn in state.idle:
            state.idle.remove(conn)
        state.live -= 1
        self._total_live -= 1
        conn.in_use = False
