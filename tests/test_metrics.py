"""Tests for pyutcp_lab.client.metrics."""

from __future__ import annotations

import pytest

from pyutcp_lab.client.metrics import MetricsCollector, ToolStats
from tests.fakes import FakeClock


class TestToolStats:
    def test_records_latency(self) -> None:
        s = ToolStats()
        s.record(1.0, failed=False)
        s.record(3.0, failed=False)
        assert s.calls == 2
        assert s.avg_latency == pytest.approx(2.0)
        assert s.min_latency == 1.0
        assert s.max_latency == 3.0

    def test_success_rate(self) -> None:
        s = ToolStats()
        s.record(1.0, failed=False)
        s.record(1.0, failed=True)
        assert s.success_rate == pytest.approx(0.5)

    def test_empty_stats(self) -> None:
        s = ToolStats()
        assert s.avg_latency == 0.0
        assert s.success_rate == 0.0

    def test_snapshot_shape(self) -> None:
        s = ToolStats()
        s.record(2.0, failed=False)
        snap = s.snapshot()
        assert snap["calls"] == 1
        assert snap["avg_latency"] == 2.0


class TestMetricsCollector:
    def test_record_aggregates(self) -> None:
        m = MetricsCollector()
        m.record("a.tool", 1.0, failed=False)
        m.record("a.tool", 2.0, failed=True)
        m.record("b.tool", 0.5, failed=False)
        assert m.total_calls == 3
        assert m.total_failures == 1
        assert m.stats_for("a.tool").calls == 2
        assert m.stats_for("b.tool").calls == 1

    def test_snapshot(self) -> None:
        m = MetricsCollector()
        m.record("a", 1.0, failed=False)
        snap = m.snapshot()
        assert snap["total_calls"] == 1
        assert "a" in snap["tools"]

    def test_reset(self) -> None:
        m = MetricsCollector()
        m.record("a", 1.0, failed=False)
        m.reset()
        assert m.total_calls == 0
        assert m.snapshot()["tools"] == {}

    def test_time_call_success(self) -> None:
        clock = FakeClock(start=0.0)
        m = MetricsCollector(clock=clock.time)
        with m.time_call("slow.tool"):
            clock.advance(2.5)
        stats = m.stats_for("slow.tool")
        assert stats.calls == 1
        assert stats.failures == 0
        assert stats.avg_latency == pytest.approx(2.5)

    def test_time_call_records_failure(self) -> None:
        clock = FakeClock(start=0.0)
        m = MetricsCollector(clock=clock.time)
        with pytest.raises(ValueError):
            with m.time_call("bad.tool"):
                clock.advance(1.0)
                raise ValueError("boom")
        stats = m.stats_for("bad.tool")
        assert stats.calls == 1
        assert stats.failures == 1
