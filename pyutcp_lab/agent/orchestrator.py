"""Orchestrator for resumable tool-chain runs.

The orchestrator executes a *plan* (an ordered list of tool calls) through a
:class:`UtcpClient`, tracking which steps have completed and which remain. A run
can be checkpointed at any point and restored later (in this or another process)
to continue from exactly where it paused.

The orchestrator keeps its state split into ``_completed``
and ``_pending`` so a checkpoint can preserve both. Resumption correctness rests
on that split round-tripping faithfully: restore must repopulate the pending
queue, or the remaining steps are silently dropped.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from ..client.client import UtcpClient
from ..core.models import ToolCall
from .checkpoint import Checkpoint, CompletedStep


class Orchestrator:
    """Runs a plan of tool calls with pause/resume via checkpoints."""

    def __init__(self, client: UtcpClient) -> None:
        self._client = client
        self._completed: list[CompletedStep] = []
        self._pending: list[ToolCall] = []

    # -- plan setup ---------------------------------------------------------

    def load_plan(self, calls: Sequence[ToolCall]) -> None:
        """Initialise a fresh run from a plan of tool calls."""
        self._completed = []
        self._pending = list(calls)

    # -- introspection ------------------------------------------------------

    @property
    def completed(self) -> tuple[CompletedStep, ...]:
        return tuple(self._completed)

    @property
    def pending(self) -> tuple[ToolCall, ...]:
        return tuple(self._pending)

    @property
    def is_done(self) -> bool:
        return not self._pending

    def results(self) -> list[Any]:
        """Results of completed steps, in execution order."""
        return [c.result for c in self._completed]

    # -- execution ----------------------------------------------------------

    def step(self) -> Optional[CompletedStep]:
        """Execute the next pending step, or return None if the run is done."""
        if not self._pending:
            return None
        call = self._pending.pop(0)
        result = self._client.call(call)
        done = CompletedStep(
            tool_name=call.tool_name, arguments=dict(call.arguments), result=result
        )
        self._completed.append(done)
        return done

    def run_remaining(self) -> list[Any]:
        """Run every pending step to completion and return all results."""
        while self._pending:
            self.step()
        return self.results()

    # -- checkpointing ------------------------------------------------------

    def checkpoint(self) -> Checkpoint:
        """Snapshot the current run state (completed + pending)."""
        return Checkpoint(
            completed=list(self._completed),
            pending=list(self._pending),
        )

    def restore(self, checkpoint: Checkpoint) -> None:
        """Replace run state with a snapshot, resuming where it left off."""
        self._completed = list(checkpoint.completed)
        self._pending = list(checkpoint.pending)
