"""Tests for the agent orchestrator and checkpointing."""

from __future__ import annotations

import json

import pytest

from pyutcp_lab.core.models import ToolCall
from pyutcp_lab.agent.checkpoint import Checkpoint, CompletedStep
from pyutcp_lab.agent.orchestrator import Orchestrator
from tests.fakes import FakeClient


def plan(*names: str) -> list[ToolCall]:
    return [ToolCall(tool_name=n) for n in names]


@pytest.fixture
def orch() -> Orchestrator:
    return Orchestrator(FakeClient())  # type: ignore[arg-type]


class TestBasicRun:
    def test_run_remaining_executes_all(self, orch: Orchestrator) -> None:
        orch.load_plan(plan("a.one", "a.two", "a.three"))
        results = orch.run_remaining()
        assert [r["tool"] for r in results] == ["a.one", "a.two", "a.three"]
        assert orch.is_done

    def test_step_executes_one(self, orch: Orchestrator) -> None:
        orch.load_plan(plan("a.one", "a.two"))
        done = orch.step()
        assert done is not None
        assert done.tool_name == "a.one"
        assert len(orch.completed) == 1
        assert len(orch.pending) == 1

    def test_step_when_done_returns_none(self, orch: Orchestrator) -> None:
        orch.load_plan(plan())
        assert orch.step() is None
        assert orch.is_done

    def test_results_in_order(self, orch: Orchestrator) -> None:
        orch.load_plan(plan("a", "b"))
        orch.run_remaining()
        assert [r["n"] for r in orch.results()] == [0, 1]


class TestCheckpointRoundTrip:
    def test_checkpoint_dict_round_trip(self) -> None:
        cp = Checkpoint(
            completed=[CompletedStep("a", {"x": 1}, {"r": 2})],
            pending=[ToolCall(tool_name="b", arguments={"y": 3})],
        )
        restored = Checkpoint.from_dict(cp.to_dict())
        assert len(restored.completed) == 1
        assert restored.completed[0].tool_name == "a"
        assert restored.completed[0].result == {"r": 2}
        assert len(restored.pending) == 1
        assert restored.pending[0].tool_name == "b"
        assert restored.pending[0].arguments == {"y": 3}

    def test_checkpoint_is_json_serialisable(self) -> None:
        cp = Checkpoint(
            completed=[CompletedStep("a", {}, {"ok": True})],
            pending=[ToolCall(tool_name="b")],
        )
        # Must survive a real JSON encode/decode cycle.
        blob = json.dumps(cp.to_dict())
        restored = Checkpoint.from_dict(json.loads(blob))
        assert restored.pending[0].tool_name == "b"

    def test_empty_checkpoint(self) -> None:
        restored = Checkpoint.from_dict(Checkpoint().to_dict())
        assert restored.completed == []
        assert restored.pending == []


class TestResumeFromCheckpoint:
    """Checkpoint mid-run, then resume in a fresh orchestrator.

    The resumed run has to finish with the same results as one that was never
    interrupted. That only works if the checkpoint carries the pending queue
    through serialisation. Drop it and the leftover steps just vanish: the
    resumed run looks successful but only reports the work done before the
    snapshot.
    """

    def test_mid_run_checkpoint_resumes_completely(self) -> None:
        # Uninterrupted baseline.
        baseline = Orchestrator(FakeClient())  # type: ignore[arg-type]
        baseline.load_plan(plan("a", "b", "c", "d"))
        expected = [r["tool"] for r in baseline.run_remaining()]

        # Interrupted run: execute two steps, snapshot, serialise, restore.
        first = Orchestrator(FakeClient())  # type: ignore[arg-type]
        first.load_plan(plan("a", "b", "c", "d"))
        first.step()
        first.step()
        blob = json.dumps(first.checkpoint().to_dict())

        resumed = Orchestrator(FakeClient())  # type: ignore[arg-type]
        resumed.restore(Checkpoint.from_dict(json.loads(blob)))
        final = [r["tool"] for r in resumed.run_remaining()]

        assert final == expected
        assert resumed.is_done

    def test_pending_survives_snapshot(self) -> None:
        orch = Orchestrator(FakeClient())  # type: ignore[arg-type]
        orch.load_plan(plan("a", "b", "c"))
        orch.step()  # complete 'a'; 'b','c' pending
        cp = Checkpoint.from_dict(json.loads(json.dumps(orch.checkpoint().to_dict())))
        assert [c.tool_name for c in cp.completed] == ["a"]
        assert [p.tool_name for p in cp.pending] == ["b", "c"]

    def test_resume_does_not_repeat_completed(self) -> None:
        first = Orchestrator(FakeClient())  # type: ignore[arg-type]
        first.load_plan(plan("a", "b", "c"))
        first.step()

        client = FakeClient()
        resumed = Orchestrator(client)  # type: ignore[arg-type]
        resumed.restore(Checkpoint.from_dict(first.checkpoint().to_dict()))
        resumed.run_remaining()
        # Only the two pending steps should have actually been called.
        assert client.calls == ["b", "c"]

    def test_checkpoint_after_completion_round_trips(self) -> None:
        # A checkpoint taken once the run is finished has no pending work; this
        # case round-trips even under the buggy serialiser, which is why the
        # mid-run test above is the one that actually exercises the invariant.
        orch = Orchestrator(FakeClient())  # type: ignore[arg-type]
        orch.load_plan(plan("a", "b"))
        orch.run_remaining()
        resumed = Orchestrator(FakeClient())  # type: ignore[arg-type]
        resumed.restore(Checkpoint.from_dict(orch.checkpoint().to_dict()))
        assert resumed.is_done
        assert [c.tool_name for c in resumed.completed] == ["a", "b"]
