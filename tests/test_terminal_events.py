"""Regression coverage for canonical run-terminal scope."""

from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.runtime.terminal_events import (
    is_successful_run_terminal,
    latest_quiescent_run_terminal,
    terminal_after_event,
)
from zf.runtime.run_scope import known_run_ids


def test_task_level_completion_does_not_quiesce_a_run() -> None:
    events = [
        ZfEvent(
            type="task.dispatched",
            task_id="TASK-1",
            correlation_id="run-1",
            payload={"workflow_run_id": "run-1"},
        ),
        ZfEvent(
            type="judge.passed",
            task_id="TASK-1",
            correlation_id="run-1",
            payload={"workflow_run_id": "run-1"},
        ),
        ZfEvent(
            type="task.done",
            task_id="TASK-1",
            correlation_id="run-1",
            payload={"workflow_run_id": "run-1"},
        ),
    ]

    assert latest_quiescent_run_terminal(events, run_id="run-1") is None


def test_terminal_scope_uses_explicit_identity_without_goal_start() -> None:
    dispatch_a = ZfEvent(
        id="dispatch-a",
        type="task.dispatched",
        correlation_id="run-a",
        payload={"workflow_run_id": "run-a"},
    )
    events = [
        dispatch_a,
        ZfEvent(
            type="ship.completed",
            correlation_id="run-a",
            payload={"workflow_run_id": "run-a"},
        ),
        ZfEvent(
            type="task.dispatched",
            correlation_id="run-b",
            payload={"workflow_run_id": "run-b"},
        ),
    ]

    assert terminal_after_event(events, dispatch_a).type == "ship.completed"
    assert latest_quiescent_run_terminal(events, run_id="run-b") is None
    assert latest_quiescent_run_terminal(events) is None


def test_control_plane_correlation_ids_do_not_manufacture_runs() -> None:
    events = [
        ZfEvent(
            type="run.completed",
            payload={"run_id": "run-product", "status": "passed"},
        ),
        ZfEvent(
            type="run.manager.agent.recommendation.consumed",
            correlation_id="evt-recommendation",
            payload={"checkpoint_id": "checkpoint-1"},
        ),
        ZfEvent(
            type="run.manager.autoresearch.requested",
            correlation_id="rmar-request-1",
            payload={"request_id": "rmar-request-1"},
        ),
    ]

    assert known_run_ids(events) == {"run-product"}
    assert latest_quiescent_run_terminal(events).type == "run.completed"


def test_correlation_id_is_alias_when_bound_to_explicit_run() -> None:
    events = [
        ZfEvent(
            type="task.dispatched",
            correlation_id="trace-product",
            payload={"workflow_run_id": "run-product"},
        ),
        ZfEvent(type="ship.completed", correlation_id="trace-product"),
    ]

    assert known_run_ids(events) == {"run-product"}
    assert latest_quiescent_run_terminal(events, run_id="run-product").type == (
        "ship.completed"
    )


def test_goal_closure_compat_judge_is_not_authoritative_terminal() -> None:
    compat = ZfEvent(
        type="judge.passed",
        correlation_id="run-compat",
        payload={
            "workflow_run_id": "run-compat",
            "authority": "compat_projection",
        },
    )

    assert is_successful_run_terminal(compat) is False
    assert latest_quiescent_run_terminal([compat], run_id="run-compat") is None


def test_late_writer_result_with_stale_audit_does_not_reopen_terminal() -> None:
    terminal = ZfEvent(
        id="goal-done",
        type="run.goal.completed",
        correlation_id="run-1",
        payload={"workflow_run_id": "run-1"},
    )
    late = ZfEvent(
        id="late-result",
        type="dev.build.done",
        correlation_id="run-1",
        payload={"workflow_run_id": "run-1", "fanout_id": "fanout-old"},
    )
    stale = ZfEvent(
        type="fanout.child.stale_completion",
        correlation_id="run-1",
        payload={
            "workflow_run_id": "run-1",
            "result_event_id": late.id,
            "reason": "run_terminal",
        },
    )

    assert latest_quiescent_run_terminal(
        [terminal, late, stale],
        run_id="run-1",
    ) is terminal
