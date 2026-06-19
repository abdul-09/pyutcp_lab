"""Result cache with TTL expiry and LRU eviction.

Tool-call results are cached under a caller-supplied key. Two independent
policies bound the cache:

* **TTL**: an entry older than ``ttl`` seconds is treated as absent and is
  evicted lazily on the next access.
* **LRU capacity**: at most ``max_entries`` live entries are kept; when a new
  entry would exceed that, the least-recently-*used* entry is evicted.

"Used" means accessed, not merely inserted. Both :meth:`get` (on a hit) and
:meth:`set` must mark an entry as most-recently-used, so that an entry which is
read frequently is protected from eviction even if it was inserted long ago. A
cache that updates recency only on insertion degrades to first-in-first-out
under load and will evict hot entries, returning correct values but in the wrong eviction order.

Time is read through an injectable clock so TTL behaviour is deterministic in
tests with no real waiting.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass
class _Entry:
    value: Any
    stored_at: float


class ResultCache:
    """A bounded TTL + LRU cache for tool-call results."""

    def __init__(
        self,
        *,
        max_entries: int,
        ttl: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        if ttl <= 0:
            raise ValueError("ttl must be positive")
        self._max = max_entries
        self._ttl = ttl
        self._clock = clock
        # Ordered most-recently-used last; popitem(last=False) drops the LRU.
        self._entries: "OrderedDict[str, _Entry]" = OrderedDict()
        self._hits = 0
        self._misses = 0

    # -- stats --------------------------------------------------------------

    @property
    def hits(self) -> int:
        return self._hits

    @property
    def misses(self) -> int:
        return self._misses

    def __len__(self) -> int:
        return len(self._entries)

    def keys(self) -> tuple[str, ...]:
        """Live keys in LRU order (least-recently-used first)."""
        return tuple(self._entries.keys())

    # -- internal -----------------------------------------------------------

    def _is_fresh(self, entry: _Entry, now: float) -> bool:
        return (now - entry.stored_at) < self._ttl

    def _evict_expired(self, now: float) -> None:
        expired = [k for k, e in self._entries.items() if not self._is_fresh(e, now)]
        for key in expired:
            del self._entries[key]

    # -- operations ---------------------------------------------------------

    def get(self, key: str) -> Optional[Any]:
        """Return the cached value for ``key`` or ``None`` if absent/expired.

        On a hit the entry is marked most-recently-used so frequent reads keep
        it alive under LRU pressure.
        """
        now = self._clock()
        entry = self._entries.get(key)
        if entry is None:
            self._misses += 1
            return None
        if not self._is_fresh(entry, now):
            del self._entries[key]
            self._misses += 1
            return None
        # Mark as most-recently-used.
        self._entries.move_to_end(key, last=True)
        self._hits += 1
        return entry.value

    def set(self, key: str, value: Any) -> None:
        """Store ``value`` under ``key``, evicting expired then LRU entries."""
        now = self._clock()
        self._evict_expired(now)
        if key in self._entries:
            self._entries[key] = _Entry(value=value, stored_at=now)
            self._entries.move_to_end(key, last=True)
            return
        if len(self._entries) >= self._max:
            # Evict the least-recently-used live entry.
            self._entries.popitem(last=False)
        self._entries[key] = _Entry(value=value, stored_at=now)

    def invalidate(self, key: str) -> bool:
        """Drop ``key`` if present. Returns True if something was removed."""
        if key in self._entries:
            del self._entries[key]
            return True
        return False

    def clear(self) -> None:
        self._entries.clear()
