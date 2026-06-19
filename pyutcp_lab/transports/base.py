"""Transport abstraction.

A *transport* knows how to talk to one provider over one wire protocol. Every
transport implements the same four-method contract so the client can treat HTTP,
SSE, CLI, and the rest uniformly:

* :meth:`Transport.discover`: fetch the provider's manual (its tool list).
* :meth:`Transport.call`: invoke one tool and return its result.
* :meth:`Transport.stream`: invoke one tool and yield result chunks.
* :meth:`Transport.close`: release any held resources.

Calls carry a :class:`Deadline` so that a slow transport can be abandoned once
the caller's time budget is spent. Concrete transports are responsible for
honouring the deadline across *every* blocking step they perform, not just the
first one. The most common latency bug is a transport that bounds connect time
but lets reads run unbounded.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Optional

from ..core.errors import TimeoutError
from ..core.models import Manual, ToolCall


@dataclass(frozen=True)
class Deadline:
    """An absolute point in time by which an operation must finish.

    The clock is injectable: by default it is :func:`time.monotonic`, but tests
    pass a controllable clock so deadline behaviour is deterministic without
    real waiting. Use :meth:`after` to construct one relative to now.
    :meth:`remaining` reports seconds left (clamped at zero) and :meth:`check`
    raises :class:`TimeoutError` once the deadline has passed.
    """

    monotonic_at: float
    clock: Callable[[], float] = field(default=time.monotonic, compare=False)

    @classmethod
    def after(
        cls, seconds: float, *, clock: Callable[[], float] = time.monotonic
    ) -> "Deadline":
        return cls(monotonic_at=clock() + seconds, clock=clock)

    def remaining(self) -> float:
        return max(0.0, self.monotonic_at - self.clock())

    def expired(self) -> bool:
        return self.remaining() <= 0.0

    def check(self, *, what: str = "operation") -> None:
        if self.expired():
            raise TimeoutError(f"{what} exceeded its deadline")


class Transport(ABC):
    """Base class for all provider transports."""

    @abstractmethod
    def discover(self, *, deadline: Optional[Deadline] = None) -> Manual:
        """Return the provider's manual (its current tool list)."""

    @abstractmethod
    def call(
        self, call: ToolCall, *, deadline: Optional[Deadline] = None
    ) -> Any:
        """Invoke one tool and return its decoded result."""

    @abstractmethod
    def stream(
        self, call: ToolCall, *, deadline: Optional[Deadline] = None
    ) -> Iterator[Any]:
        """Invoke one tool and yield result chunks as they arrive."""

    def close(self) -> None:  # noqa: B027 - intentional no-op default
        """Release resources. Default is a no-op; override if needed."""

    def __enter__(self) -> "Transport":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
