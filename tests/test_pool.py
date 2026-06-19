"""Tests for pyutcp_lab.transports.pool.ConnectionPool."""

from __future__ import annotations

import pytest

from pyutcp_lab.core.errors import TransportError
from pyutcp_lab.transports.pool import ConnectionPool
from tests.fakes import Interleaver


class TestConstruction:
    def test_rejects_zero_per_host(self) -> None:
        with pytest.raises(ValueError):
            ConnectionPool(per_host=0, max_total=10)

    def test_rejects_total_below_per_host(self) -> None:
        with pytest.raises(ValueError):
            ConnectionPool(per_host=5, max_total=3)


class TestBasicAcquireRelease:
    def test_acquire_opens_new_connection(self) -> None:
        pool = ConnectionPool(per_host=2, max_total=10)
        c = pool.acquire("host-a")
        assert c.in_use
        assert pool.live_for("host-a") == 1
        assert pool.total_live == 1

    def test_release_makes_idle(self) -> None:
        pool = ConnectionPool(per_host=2, max_total=10)
        c = pool.acquire("host-a")
        pool.release(c)
        assert not c.in_use
        assert pool.idle_for("host-a") == 1
        assert pool.live_for("host-a") == 1  # still live, just idle

    def test_acquire_reuses_idle(self) -> None:
        pool = ConnectionPool(per_host=2, max_total=10)
        c1 = pool.acquire("host-a")
        pool.release(c1)
        c2 = pool.acquire("host-a")
        assert c2.conn_id == c1.conn_id  # reused, not freshly opened
        assert pool.total_live == 1

    def test_release_not_in_use_raises(self) -> None:
        pool = ConnectionPool(per_host=2, max_total=10)
        c = pool.acquire("host-a")
        pool.release(c)
        with pytest.raises(TransportError):
            pool.release(c)

    def test_discard_frees_capacity(self) -> None:
        pool = ConnectionPool(per_host=1, max_total=10)
        c = pool.acquire("host-a")
        pool.discard(c)
        assert pool.live_for("host-a") == 0
        # capacity freed, so a new acquire succeeds
        c2 = pool.acquire("host-a")
        assert c2.conn_id != c.conn_id

    def test_discard_idle_connection(self) -> None:
        pool = ConnectionPool(per_host=2, max_total=10)
        c = pool.acquire("host-a")
        pool.release(c)
        pool.discard(c)
        assert pool.idle_for("host-a") == 0
        assert pool.live_for("host-a") == 0


class TestPerHostCap:
    def test_cap_enforced(self) -> None:
        pool = ConnectionPool(per_host=2, max_total=10)
        pool.acquire("host-a")
        pool.acquire("host-a")
        with pytest.raises(TransportError, match="per-host"):
            pool.acquire("host-a")

    def test_cap_is_per_host_not_global(self) -> None:
        pool = ConnectionPool(per_host=1, max_total=10)
        a = pool.acquire("host-a")
        b = pool.acquire("host-b")
        assert a.host == "host-a"
        assert b.host == "host-b"
        assert pool.total_live == 2


class TestGlobalCapAndEviction:
    def test_evicts_lru_idle_when_global_cap_hit(self) -> None:
        pool = ConnectionPool(per_host=2, max_total=2)
        a = pool.acquire("host-a")
        b = pool.acquire("host-b")
        # Release a first (older idle), then b (newer idle).
        pool.release(a)
        pool.release(b)
        # New host needs a slot; global cap is 2, both are idle -> evict LRU (a).
        c = pool.acquire("host-c")
        assert c.host == "host-c"
        assert pool.total_live == 2
        # host-a's connection was the LRU idle and should be gone.
        assert pool.idle_for("host-a") == 0
        assert pool.idle_for("host-b") == 1

    def test_global_cap_with_nothing_idle_raises(self) -> None:
        pool = ConnectionPool(per_host=2, max_total=2)
        pool.acquire("host-a")
        pool.acquire("host-b")  # both in use, nothing idle
        with pytest.raises(TransportError, match="global"):
            pool.acquire("host-c")


class TestConcurrentAcquireInvariant:
    """Concurrent acquires must respect the per-host cap.

    No matter how acquires interleave against a single host, the live
    connection count can't go over ``per_host``. The Interleaver runs a second
    caller's whole acquire right at the yield point inside the first caller's
    acquire, so the check-then-acquire ordering that a naive pool gets wrong
    happens every run instead of once in a blue moon.
    """

    def test_two_callers_never_exceed_per_host_cap(self) -> None:
        results: dict[str, object] = {}

        def caller_two() -> None:
            try:
                results["c2"] = pool.acquire("host-a")
            except TransportError as exc:
                results["c2"] = exc

        interleaver = Interleaver("before-capacity-check", caller_two)
        pool = ConnectionPool(per_host=1, max_total=10, schedule=interleaver.hook)

        try:
            results["c1"] = pool.acquire("host-a")
        except TransportError as exc:
            results["c1"] = exc

        # The cap is 1, so live connections must never exceed 1 even though two
        # callers raced. With correct code one caller wins and the other is
        # rejected; a buggy check-then-act pool would let both through (live=2).
        assert pool.live_for("host-a") == 1
        assert pool.total_live == 1

        outcomes = [results["c1"], results["c2"]]
        successes = [r for r in outcomes if not isinstance(r, TransportError)]
        failures = [r for r in outcomes if isinstance(r, TransportError)]
        assert len(successes) == 1
        assert len(failures) == 1

    def test_interleaver_only_fires_once(self) -> None:
        calls = {"n": 0}

        def action() -> None:
            calls["n"] += 1

        interleaver = Interleaver("before-capacity-check", action)
        pool = ConnectionPool(per_host=5, max_total=10, schedule=interleaver.hook)
        pool.acquire("host-a")
        pool.acquire("host-a")
        # Two acquires hit the yield point, but the action fires only once.
        assert calls["n"] == 1
        assert interleaver.points.count("before-capacity-check") == 2

    def test_reused_idle_skips_yield_point(self) -> None:
        # Reusing an idle connection must not hit the capacity yield point.
        fired = {"n": 0}
        interleaver = Interleaver(
            "before-capacity-check", lambda: fired.__setitem__("n", fired["n"] + 1)
        )
        pool = ConnectionPool(per_host=2, max_total=10, schedule=interleaver.hook)
        c = pool.acquire("host-a")  # fires hook (n=1)
        pool.release(c)
        pool.acquire("host-a")  # reuses idle, no hook
        assert fired["n"] == 1
