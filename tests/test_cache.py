"""Tests for pyutcp_lab.registry.cache.ResultCache."""

from __future__ import annotations

import pytest

from pyutcp_lab.registry.cache import ResultCache
from tests.fakes import FakeClock


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(start=0.0)


class TestConstruction:
    def test_rejects_zero_capacity(self) -> None:
        with pytest.raises(ValueError):
            ResultCache(max_entries=0, ttl=10)

    def test_rejects_nonpositive_ttl(self) -> None:
        with pytest.raises(ValueError):
            ResultCache(max_entries=1, ttl=0)


class TestBasic:
    def test_set_then_get(self, clock: FakeClock) -> None:
        cache = ResultCache(max_entries=4, ttl=10, clock=clock.time)
        cache.set("k", 42)
        assert cache.get("k") == 42
        assert cache.hits == 1
        assert cache.misses == 0

    def test_miss_on_absent(self, clock: FakeClock) -> None:
        cache = ResultCache(max_entries=4, ttl=10, clock=clock.time)
        assert cache.get("absent") is None
        assert cache.misses == 1

    def test_overwrite_updates_value(self, clock: FakeClock) -> None:
        cache = ResultCache(max_entries=4, ttl=10, clock=clock.time)
        cache.set("k", 1)
        cache.set("k", 2)
        assert cache.get("k") == 2
        assert len(cache) == 1

    def test_invalidate(self, clock: FakeClock) -> None:
        cache = ResultCache(max_entries=4, ttl=10, clock=clock.time)
        cache.set("k", 1)
        assert cache.invalidate("k") is True
        assert cache.get("k") is None
        assert cache.invalidate("k") is False

    def test_clear(self, clock: FakeClock) -> None:
        cache = ResultCache(max_entries=4, ttl=10, clock=clock.time)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.clear()
        assert len(cache) == 0


class TestTTL:
    def test_expires_after_ttl(self, clock: FakeClock) -> None:
        cache = ResultCache(max_entries=4, ttl=10, clock=clock.time)
        cache.set("k", 1)
        clock.advance(9.0)
        assert cache.get("k") == 1  # still fresh
        clock.advance(2.0)  # now 11s old, ttl 10
        assert cache.get("k") is None

    def test_expired_entry_removed_on_access(self, clock: FakeClock) -> None:
        cache = ResultCache(max_entries=4, ttl=5, clock=clock.time)
        cache.set("k", 1)
        clock.advance(6.0)
        cache.get("k")
        assert len(cache) == 0

    def test_set_evicts_expired_first(self, clock: FakeClock) -> None:
        cache = ResultCache(max_entries=2, ttl=5, clock=clock.time)
        cache.set("a", 1)
        clock.advance(6.0)  # 'a' now expired
        cache.set("b", 2)  # eviction of expired 'a' makes room
        cache.set("c", 3)
        assert cache.get("a") is None
        assert cache.get("b") == 2
        assert cache.get("c") == 3


class TestLRUEviction:
    def test_evicts_least_recently_used_on_overflow(self, clock: FakeClock) -> None:
        cache = ResultCache(max_entries=2, ttl=100, clock=clock.time)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)  # overflow -> evict 'a' (LRU)
        assert cache.get("a") is None
        assert cache.get("b") == 2
        assert cache.get("c") == 3

    def test_set_existing_key_does_not_grow(self, clock: FakeClock) -> None:
        cache = ResultCache(max_entries=2, ttl=100, clock=clock.time)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("a", 10)  # update, not insert
        assert len(cache) == 2
        assert cache.get("a") == 10

    def test_set_refreshes_recency(self, clock: FakeClock) -> None:
        cache = ResultCache(max_entries=2, ttl=100, clock=clock.time)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("a", 1)  # 'a' now most-recently-used
        cache.set("c", 3)  # evict LRU, which is now 'b'
        assert cache.get("a") == 1
        assert cache.get("b") is None
        assert cache.get("c") == 3


class TestHitUpdatesRecency:
    """A read should keep an entry alive under eviction pressure.

    Reading an entry has to mark it most-recently-used. If it doesn't, an entry
    that gets read all the time but happened to be inserted early gets evicted
    like a cold one, while genuinely colder entries that were inserted later
    stick around.
    """

    def test_read_protects_entry_from_eviction(self, clock: FakeClock) -> None:
        cache = ResultCache(max_entries=2, ttl=100, clock=clock.time)
        cache.set("hot", 1)
        cache.set("cold", 2)
        # Read 'hot' so it becomes most-recently-used.
        assert cache.get("hot") == 1
        # Insert a third entry; the LRU should now be 'cold', not 'hot'.
        cache.set("new", 3)
        assert cache.get("hot") == 1  # survived because it was read
        assert cache.get("cold") is None  # evicted as the true LRU
        assert cache.get("new") == 3

    def test_repeated_reads_keep_entry_alive(self, clock: FakeClock) -> None:
        cache = ResultCache(max_entries=3, ttl=1000, clock=clock.time)
        cache.set("keep", 0)
        # Churn many other keys, reading 'keep' before each insert.
        for i in range(20):
            assert cache.get("keep") == 0  # keeps 'keep' hot
            cache.set(f"f{i}", i)
        # 'keep' must still be present despite 20 inserts past a capacity of 3.
        assert cache.get("keep") == 0

    def test_lru_order_reflects_reads(self, clock: FakeClock) -> None:
        cache = ResultCache(max_entries=3, ttl=1000, clock=clock.time)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)
        cache.get("a")  # a becomes MRU; order should be b, c, a
        assert cache.keys() == ("b", "c", "a")
