"""Tests for pyutcp_lab.transports.async_pool.AsyncConnectionPool."""

from __future__ import annotations

import asyncio

import pytest

from pyutcp_lab.core.errors import TransportError
from pyutcp_lab.transports.async_pool import (
    AsyncConnectionPool,
    PoolTimeoutError,
)


class TestConstruction:
    def test_rejects_zero_per_host(self) -> None:
        with pytest.raises(ValueError):
            AsyncConnectionPool(per_host=0, max_total=10)

    def test_rejects_total_below_per_host(self) -> None:
        with pytest.raises(ValueError):
            AsyncConnectionPool(per_host=5, max_total=3)


class TestBasicAcquireRelease:
    async def test_acquire_opens(self) -> None:
        pool = AsyncConnectionPool(per_host=2, max_total=10)
        c = await pool.acquire("h")
        assert c.in_use
        assert pool.live_for("h") == 1
        assert pool.total_live == 1

    async def test_release_makes_idle(self) -> None:
        pool = AsyncConnectionPool(per_host=2, max_total=10)
        c = await pool.acquire("h")
        await pool.release(c)
        assert not c.in_use
        assert pool.idle_for("h") == 1

    async def test_acquire_reuses_idle(self) -> None:
        pool = AsyncConnectionPool(per_host=2, max_total=10)
        c1 = await pool.acquire("h")
        await pool.release(c1)
        c2 = await pool.acquire("h")
        assert c2.conn_id == c1.conn_id
        assert pool.total_live == 1

    async def test_release_not_in_use_raises(self) -> None:
        pool = AsyncConnectionPool(per_host=2, max_total=10)
        c = await pool.acquire("h")
        await pool.release(c)
        with pytest.raises(TransportError):
            await pool.release(c)

    async def test_discard_frees_capacity(self) -> None:
        pool = AsyncConnectionPool(per_host=1, max_total=10)
        c = await pool.acquire("h")
        await pool.discard(c)
        assert pool.live_for("h") == 0
        c2 = await pool.acquire("h")
        assert c2.conn_id != c.conn_id

    async def test_discard_idle_connection(self) -> None:
        pool = AsyncConnectionPool(per_host=2, max_total=10)
        c = await pool.acquire("h")
        await pool.release(c)
        await pool.discard(c)
        assert pool.idle_for("h") == 0
        assert pool.live_for("h") == 0


class TestPerHostCap:
    async def test_separate_hosts_independent(self) -> None:
        pool = AsyncConnectionPool(per_host=1, max_total=10)
        a = await pool.acquire("a")
        b = await pool.acquire("b")
        assert a.host == "a" and b.host == "b"
        assert pool.total_live == 2


class TestWaiting:
    async def test_saturated_acquire_waits_for_release(self) -> None:
        pool = AsyncConnectionPool(per_host=1, max_total=10)
        first = await pool.acquire("h")  # host now saturated

        order: list[str] = []

        async def waiter() -> None:
            order.append("waiting")
            conn = await pool.acquire("h")  # must block until release
            order.append("acquired")
            assert conn.conn_id == first.conn_id  # reused the released one

        task = asyncio.create_task(waiter())
        await asyncio.sleep(0)  # let the waiter reach the blocked acquire
        assert order == ["waiting"]  # it has not acquired yet

        await pool.release(first)
        await task
        assert order == ["waiting", "acquired"]

    async def test_timeout_when_never_released(self) -> None:
        pool = AsyncConnectionPool(per_host=1, max_total=10)
        await pool.acquire("h")  # saturate and never release
        with pytest.raises(PoolTimeoutError):
            await pool.acquire("h", timeout=0.05)

    async def test_discard_wakes_waiter(self) -> None:
        pool = AsyncConnectionPool(per_host=1, max_total=10)
        first = await pool.acquire("h")

        async def waiter() -> int:
            conn = await pool.acquire("h")
            return conn.conn_id

        task = asyncio.create_task(waiter())
        await asyncio.sleep(0)
        await pool.discard(first)  # frees capacity, wakes the waiter
        new_id = await task
        assert new_id != first.conn_id  # a fresh connection was opened


class TestGlobalCapEviction:
    async def test_evicts_idle_when_global_full(self) -> None:
        pool = AsyncConnectionPool(per_host=2, max_total=2)
        a = await pool.acquire("a")
        b = await pool.acquire("b")
        await pool.release(a)
        await pool.release(b)
        # Global cap is 2 and both are idle; acquiring a third host evicts LRU.
        c = await pool.acquire("c")
        assert c.host == "c"
        assert pool.total_live == 2
        assert pool.idle_for("a") == 0  # 'a' was the LRU idle, evicted

    async def test_custom_factory(self) -> None:
        async def factory(host: str) -> int:
            return 999

        pool = AsyncConnectionPool(per_host=1, max_total=2, factory=factory)
        c = await pool.acquire("h")
        assert c.conn_id == 999

    async def test_global_cap_all_in_use_blocks_new_host(self) -> None:
        # Two hosts each hold one in-use connection, hitting the global cap of 2
        # with nothing idle to evict. A third host must wait, then time out.
        pool = AsyncConnectionPool(per_host=1, max_total=2)
        await pool.acquire("a")
        await pool.acquire("b")
        assert pool.total_live == 2
        with pytest.raises(PoolTimeoutError):
            await pool.acquire("c", timeout=0.05)

    async def test_global_cap_frees_after_release(self) -> None:
        pool = AsyncConnectionPool(per_host=1, max_total=2)
        a = await pool.acquire("a")
        await pool.acquire("b")

        async def waiter() -> str:
            conn = await pool.acquire("c")
            return conn.host

        task = asyncio.create_task(waiter())
        await asyncio.sleep(0)
        await pool.release(a)  # frees a slot; 'a' becomes idle and is evicted
        host = await task
        assert host == "c"
        assert pool.total_live == 2
