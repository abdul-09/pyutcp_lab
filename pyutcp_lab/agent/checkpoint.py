"""Checkpoint state for a resumable orchestration run.

A long tool-chain run can be paused and resumed: the orchestrator snapshots its
state into a plain, JSON-serialisable dict, and a later process restores that
dict to continue exactly where it left off. For resumption to be correct the
snapshot must capture *both* halves of the run state:

* ``completed`` — the steps already executed and their results, so finished work
  is not repeated; and
* ``pending`` — the steps not yet executed, so remaining work is not lost.

Dropping either half breaks resumption. Omitting ``completed`` re-runs finished
steps (wasteful, and wrong if a tool has side effects); omitting ``pending``
silently abandons the rest of the plan, so a restored run reports success with
only the work that happened to be done before the snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..core.models import ToolCall


@dataclass
class CompletedStep:
    """A step that ran, with the call made and the result returned."""

    tool_name: str
    arguments: dict[str, Any]
    result: Any

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "result": self.result,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CompletedStep":
        return cls(
            tool_name=data["tool_name"],
            arguments=data["arguments"],
            result=data["result"],
        )


@dataclass
class Checkpoint:
    """A serialisable snapshot of an orchestration run's progress."""

    completed: list[CompletedStep] = field(default_factory=list)
    pending: list[ToolCall] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialise the full run state — both completed and pending work."""
        return {
            "completed": [c.to_dict() for c in self.completed],
            "pending": [
                {"tool_name": p.tool_name, "arguments": p.arguments}
                for p in self.pending
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Checkpoint":
        completed = [CompletedStep.from_dict(c) for c in data.get("completed", [])]
        pending = [
            ToolCall(tool_name=p["tool_name"], arguments=p.get("arguments", {}))
            for p in data.get("pending", [])
        ]
        return cls(completed=completed, pending=pending)
