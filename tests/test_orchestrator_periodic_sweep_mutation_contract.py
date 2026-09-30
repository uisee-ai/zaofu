"""Exact contracts for ordered Orchestrator periodic housekeeping sweeps."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime import orchestrator_periodic_sweep as sweep


class _Runtime:
    def __init__(self, *, execute: bool = True) -> None:
        self.execute = execute
        self.housekeeping: list[str] = []
        self.operations: list[str] = []
        self.callbacks: list[Callable[[], None]] = []

    def _safe_housekeeping(self, name: str, callback: Callable[[], None]) -> None:
        self.housekeeping.append(name)
        self.callbacks.append(callback)
        if self.execute:
            callback()

    def _op(self, name: str) -> None:
        self.operations.append(name)

    def _recover_writer_fanout_task_bindings(self) -> None:
        self._op("writer-bindings")

    def _recover_unrecorded_writer_fanout_results(self) -> None:
        self._op("writer-results")

    def _reconcile_reader_fanout_triggers(self) -> None:
        self._op("reader-triggers")

    def _recover_unrecorded_reader_fanout_results(self) -> None:
        self._op("reader-results")

    def _reconcile_run_goal_completion(self) -> None:
        self._op("goal-completion")

    def _check_context_thresholds(self) -> None:
        self._op("context-thresholds")

    def _check_pending_recycles(self) -> None:
        self._op("pending-recycles")

    def _check_orphaned_tasks(self) -> None:
        self._op("orphaned-tasks")

    def _check_unclaimed_new_tasks(self) -> None:
        self._op("unclaimed-new-tasks")

    def _check_drift(self) -> None:
        self._op("drift")

    def _check_refresh_triggers(self) -> None:
        self._op("refresh")

    def _check_channel_reply_remediation(self) -> None:
        self._op("channel-remediation")

    def _check_fanout_timeouts(self) -> None:
        self._op("fanout-timeouts")


def _event(event_type: str) -> ZfEvent:
    return ZfEvent(type=event_type, actor="test", payload={})


@pytest.mark.parametrize("events", [None, [], ()])
def test_replay_empty_batch_runs_complete_ordered_recovery(events: Any) -> None:
    runtime = _Runtime()

    sweep.run_replay_sweep(runtime, events=events)

    assert runtime.housekeeping == [
        "writer_fanout_task_bindings",
        "writer_fanout_result_replay",
        "reader_fanout_trigger_replay",
        "reader_fanout_result_replay",
    ]
    assert runtime.operations == [
        "writer-bindings",
        "writer-results",
        "reader-triggers",
        "reader-results",
        "goal-completion",
    ]


@pytest.mark.parametrize(
    "event_type",
    ["task_map.ready", "product.delivery.wave.ready", "candidate.ready"],
)
def test_replay_ready_event_runs_only_writer_binding_recovery(
    event_type: str,
) -> None:
    runtime = _Runtime()

    sweep.run_replay_sweep(runtime, events=[_event(event_type)])

    assert runtime.housekeeping == ["writer_fanout_task_bindings"]
    assert runtime.operations == ["writer-bindings"]


def test_replay_provider_turn_closed_runs_reader_result_and_goal_only() -> None:
    runtime = _Runtime()

    sweep.run_replay_sweep(runtime, events=[_event("provider.turn.closed")])

    assert runtime.housekeeping == ["reader_fanout_result_replay"]
    assert runtime.operations == ["reader-results", "goal-completion"]


def test_replay_ready_and_provider_close_runs_both_required_groups() -> None:
    runtime = _Runtime()

    sweep.run_replay_sweep(
        runtime,
        events=iter([_event("candidate.ready"), _event("provider.turn.closed")]),
    )

    assert runtime.housekeeping == [
        "writer_fanout_task_bindings",
        "reader_fanout_result_replay",
    ]
    assert runtime.operations == [
        "writer-bindings",
        "reader-results",
        "goal-completion",
    ]


@pytest.mark.parametrize(
    "event_type",
    [
        "task_map.readiness",
        "candidate.readiness",
        "provider.turn.opened",
        "channel.agent.reply.failed",
        "",
    ],
)
def test_replay_irrelevant_nonempty_batch_does_nothing(event_type: str) -> None:
    runtime = _Runtime()

    sweep.run_replay_sweep(runtime, events=[_event(event_type)])

    assert runtime.housekeeping == []
    assert runtime.operations == []


def test_replay_registers_combined_reader_callback_not_two_partial_steps() -> None:
    runtime = _Runtime(execute=False)

    sweep.run_replay_sweep(runtime, events=[_event("provider.turn.closed")])

    assert runtime.housekeeping == ["reader_fanout_result_replay"]
    assert len(runtime.callbacks) == 1
    assert runtime.operations == []
    runtime.callbacks[0]()
    assert runtime.operations == ["reader-results", "goal-completion"]


def test_context_sweep_registers_exact_order_and_callbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    from zf.runtime import orchestrator_agent_recovery

    runtime = _Runtime()
    monkeypatch.setattr(
        orchestrator_agent_recovery,
        "reconcile_orchestrator_agent_operation_liveness",
        lambda current: current._op("operation-liveness"),
    )

    sweep.run_context_sweep(runtime)

    assert runtime.housekeeping == ["context_thresholds", "pending_recycles"]
    assert runtime.operations == [
        "context-thresholds",
        "pending-recycles",
        "operation-liveness",
    ]


def test_provider_liveness_runs_in_finally_after_recycle_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from zf.runtime import orchestrator_agent_recovery

    runtime = _Runtime()

    def fail_recycle() -> None:
        runtime._op("pending-recycles")
        raise RuntimeError("recycle failed")

    runtime._check_pending_recycles = fail_recycle  # type: ignore[method-assign]
    monkeypatch.setattr(
        orchestrator_agent_recovery,
        "reconcile_orchestrator_agent_operation_liveness",
        lambda current: current._op("operation-liveness"),
    )

    with pytest.raises(RuntimeError, match="^recycle failed$"):
        sweep._recover_provider_lifecycle(runtime)

    assert runtime.operations == ["pending-recycles", "operation-liveness"]


def test_provider_liveness_error_propagates_after_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from zf.runtime import orchestrator_agent_recovery

    runtime = _Runtime()

    def fail_liveness(current: _Runtime) -> None:
        current._op("operation-liveness")
        raise LookupError("liveness failed")

    monkeypatch.setattr(
        orchestrator_agent_recovery,
        "reconcile_orchestrator_agent_operation_liveness",
        fail_liveness,
    )

    with pytest.raises(LookupError, match="^liveness failed$"):
        sweep._recover_provider_lifecycle(runtime)

    assert runtime.operations == ["pending-recycles", "operation-liveness"]


def test_post_dispatch_periodic_sweep_runs_all_groups_in_order() -> None:
    runtime = _Runtime()

    sweep.run_post_dispatch_sweep(runtime, events=None, periodic_sweep=True)

    assert runtime.housekeeping == [
        "orphaned_tasks",
        "unclaimed_new_tasks",
        "drift",
        "refresh",
        "channel_reply_remediation",
        "fanout_timeouts",
    ]
    assert runtime.operations == [
        "orphaned-tasks",
        "unclaimed-new-tasks",
        "drift",
        "refresh",
        "channel-remediation",
        "fanout-timeouts",
    ]


@pytest.mark.parametrize("events", [None, [], ()])
def test_post_dispatch_nonperiodic_empty_batch_does_nothing(events: Any) -> None:
    runtime = _Runtime()

    sweep.run_post_dispatch_sweep(runtime, events=events, periodic_sweep=False)

    assert runtime.housekeeping == []
    assert runtime.operations == []


@pytest.mark.parametrize(
    ("events", "expected"),
    [
        (["channel.agent.reply.failed"], ["channel_reply_remediation"]),
        (["candidate.ready"], ["fanout_timeouts"]),
        (
            ["candidate.ready", "channel.agent.reply.failed"],
            ["channel_reply_remediation", "fanout_timeouts"],
        ),
        (["candidate.readiness", "channel.agent.reply.failure"], []),
    ],
)
def test_post_dispatch_event_triggers_are_exact(
    events: list[str],
    expected: list[str],
) -> None:
    runtime = _Runtime()

    sweep.run_post_dispatch_sweep(
        runtime,
        events=[_event(event_type) for event_type in events],
        periodic_sweep=False,
    )

    assert runtime.housekeeping == expected


@pytest.mark.xfail(
    strict=True,
    reason=(
        "ZF-TEST-058: post-dispatch sweep consumes a one-shot event iterable "
        "before checking candidate.ready"
    ),
)
def test_post_dispatch_one_shot_iterable_preserves_all_event_triggers() -> None:
    runtime = _Runtime()

    sweep.run_post_dispatch_sweep(
        runtime,
        events=iter([_event("candidate.ready")]),
        periodic_sweep=False,
    )

    assert runtime.housekeeping == ["fanout_timeouts"]
