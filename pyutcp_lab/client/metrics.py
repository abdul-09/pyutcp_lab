"""Observability for tool calls.

A :class:`MetricsCollector` records what happened on every call: how many ran,
how many failed, and how long each took. It aggregates per tool so a caller can
see which tools are slow or flaky, and it exposes a simple snapshot suitable for
logging or shipping to a real metrics backend.

Timing reads an injectable clock, so latency aggregation is deterministic in
tests. Recording is cheap and allocation-light; the collector keeps running
summary statistics rather than retaining every sample.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

ClockFn = Callable[[], float]


@dataclass
class ToolStats:
    """Running statistics for one tool."""

    calls: int = 0
    failures: int = 0
    total_latency: float = 0.0
    min_latency: Optional[float] = None
    max_latency: Optional[float] = None

    def record(self, latency: float, *, failed: bool) -> None:
        self.calls += 1
        if failed:
            self.failures += 1
        self.total_latency += latency
        if self.min_latency is None or latency < self.min_latency:
            self.min_latency = latency
        if self.max_latency is None or latency > self.max_latency:
            self.max_latency = latency

    @property
    def avg_latency(self) -> float:
        return self.total_latency / self.calls if self.calls else 0.0

    @property
    def success_rate(self) -> float:
        if not self.calls:
            return 0.0
        return (self.calls - self.failures) / self.calls

    def snapshot(self) -> dict[str, float]:
        return {
            "calls": self.calls,
            "failures": self.failures,
            "avg_latency": self.avg_latency,
            "min_latency": self.min_latency or 0.0,
            "max_latency": self.max_latency or 0.0,
            "success_rate": self.success_rate,
        }


class MetricsCollector:
    """Collects per-tool call statistics."""

    def __init__(self, *, clock: ClockFn = time.monotonic) -> None:
        self._clock = clock
        self._tools: dict[str, ToolStats] = {}
        self._total_calls = 0
        self._total_failures = 0

    @property
    def total_calls(self) -> int:
        return self._total_calls

    @property
    def total_failures(self) -> int:
        return self._total_failures

    def stats_for(self, tool_name: str) -> ToolStats:
        return self._tools.setdefault(tool_name, ToolStats())

    def record(self, tool_name: str, latency: float, *, failed: bool) -> None:
        """Record a single completed call."""
        self.stats_for(tool_name).record(latency, failed=failed)
        self._total_calls += 1
        if failed:
            self._total_failures += 1

    def time_call(self, tool_name: str) -> "_CallTimer":
        """Context manager that times a call and records success or failure."""
        return _CallTimer(self, tool_name)

    def snapshot(self) -> dict[str, object]:
        return {
            "total_calls": self._total_calls,
            "total_failures": self._total_failures,
            "tools": {name: s.snapshot() for name, s in self._tools.items()},
        }

    def reset(self) -> None:
        self._tools.clear()
        self._total_calls = 0
        self._total_failures = 0


class _CallTimer:
    """Times a block and reports the outcome to a collector."""

    def __init__(self, collector: MetricsCollector, tool_name: str) -> None:
        self._collector = collector
        self._tool = tool_name
        self._start = 0.0
        self._failed = False

    def __enter__(self) -> "_CallTimer":
        self._start = self._collector._clock()
        return self

    def __exit__(self, exc_type: object, *_rest: object) -> None:
        latency = self._collector._clock() - self._start
        self._failed = exc_type is not None
        self._collector.record(self._tool, latency, failed=self._failed)
