"""In-process fakes for transport tests.

These let the suite exercise time-sensitive transport behaviour with no network
and no real clock. A :class:`FakeClock` advances only when the test (or the
simulated read) says so, so deadline handling is fully deterministic.
"""

from __future__ import annotations

from typing import Callable, Optional

from pyutcp_lab.core.errors import TransportError
from pyutcp_lab.transports.http import Connection


class FakeClock:
    """A manually advanced monotonic clock."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def time(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeConnection(Connection):
    """A scripted HTTP connection.

    ``chunks`` is the sequence of body fragments ``read`` will return, one per
    call, followed by ``b""`` (end of stream). Each read advances ``clock`` by
    ``read_cost`` seconds to simulate latency, and raises if the requested
    ``timeout`` is smaller than the cost (i.e. the read could not complete in
    the time allowed).
    """

    def __init__(
        self,
        chunks: list[bytes],
        clock: FakeClock,
        *,
        read_cost: float = 0.0,
    ) -> None:
        self._chunks = list(chunks)
        self._clock = clock
        self._read_cost = read_cost
        self.closed = False
        self.requests: list[tuple[str, str, Optional[bytes]]] = []

    def request(self, method: str, path: str, body: Optional[bytes]) -> None:
        self.requests.append((method, path, body))

    def read(self, *, timeout: float) -> bytes:
        if self._read_cost > timeout:
            raise TransportError(
                f"read timed out: needed {self._read_cost}s, allowed {timeout}s"
            )
        self._clock.advance(self._read_cost)
        if not self._chunks:
            return b""
        return self._chunks.pop(0)

    def close(self) -> None:
        self.closed = True


class Interleaver:
    """Runs an injected action at a chosen pool yield point.

    The pool calls :meth:`hook` at each cooperative yield point, passing the
    point's name. When the hook fires for the target point for the first time,
    the interleaver runs ``action`` once — simulating another caller running to
    completion in the middle of the first caller's ``acquire``. This makes a
    check-then-act race deterministic: it triggers on every run or never.
    """

    def __init__(self, at_point: str, action: Callable[[], None]) -> None:
        self._at_point = at_point
        self._action = action
        self._fired = False
        self.points: list[str] = []

    def hook(self, point: str) -> None:
        self.points.append(point)
        if point == self._at_point and not self._fired:
            self._fired = True
            self._action()

