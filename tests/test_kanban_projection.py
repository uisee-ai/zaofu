from __future__ import annotations

from zf.core.task.kanban_projection import (
    KANBAN_COLUMN_OPTIONS,
    kanban_column_projection,
    workflow_projection,
)
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task


def test_kanban_projection_uses_five_operator_columns() -> None:
    assert KANBAN_COLUMN_OPTIONS == (
        "Todo",
        "In Progress",
        "Verify",
        "Blocked",
        "Done",
    )


def test_kanban_projection_maps_legacy_statuses_without_changing_truth() -> None:
    assert kanban_column_projection(Task(id="T1", title="todo", status="backlog")).column == "ready"
    assert kanban_column_projection(Task(id="T2", title="review", status="review")).column == "testing"
    assert kanban_column_projection(Task(id="T3", title="testing", status="testing")).label == "Verify"
    assert kanban_column_projection(Task(id="T4", title="cancelled", status="cancelled")).column == "done"


def test_kanban_projection_maps_fanout_queue_wait_to_todo() -> None:
    task = Task(
        id="TQ",
        title="queued fanout child",
        status="blocked",
        blocked_reason="fanout_queue:F1:queued-TQ-1",
    )

    projection = kanban_column_projection(task)

    assert projection.column == "ready"
    assert projection.reason == "fanout_queue"
    assert "queued" in projection.badges


def test_kanban_projection_uses_workflow_handoff_for_in_progress_tasks() -> None:
    review_task = Task(id="T1", title="review", status="in_progress", assigned_to="review")
    build_done_task = Task(id="T2", title="handoff", status="in_progress", assigned_to="dev")
    plan_task = Task(id="T3", title="design", status="in_progress", assigned_to="critic")
    rejected_task = Task(id="T4", title="rework", status="in_progress", assigned_to="dev")

    assert kanban_column_projection(review_task).column == "testing"
    assert kanban_column_projection(build_done_task, phase="build_done").column == "testing"
    assert kanban_column_projection(plan_task, phase="design_critiqued").column == "in_progress"
    assert kanban_column_projection(rejected_task, phase="review_rejected").column == "in_progress"


def test_workflow_projection_rolls_legacy_events_into_impl_verify_judge() -> None:
    task = Task(id="T1", title="handoff", status="in_progress", assigned_to="test")
    events = [
        ZfEvent(type="dev.build.done", actor="dev", task_id="T1"),
        ZfEvent(type="static_gate.passed", actor="zf-cli", task_id="T1"),
        ZfEvent(type="review.approved", actor="review", task_id="T1"),
        ZfEvent(type="test.passed", actor="test", task_id="T1"),
    ]

    projection = workflow_projection(
        task,
        events,
        judge_configured=True,
        terminal_success_event="judge.passed",
    )

    assert projection.workflow_phase == "judge"
    assert projection.impl_exit_gate_state == "passed"
    assert projection.verify_state == "passed"
    assert projection.judge_state == "pending"
    assert projection.terminal_required_event == "judge.passed"


def test_workflow_projection_maps_static_gate_to_impl_exit_gate() -> None:
    task = Task(id="T1", title="gate", status="in_progress", assigned_to="review")

    projection = workflow_projection(
        task,
        [ZfEvent(type="static_gate.passed", actor="zf-cli", task_id="T1")],
    )

    assert projection.workflow_phase == "verify"
    assert projection.impl_exit_gate_state == "passed"
    assert projection.verify_state == "pending"


def test_workflow_projection_verify_failed_routes_to_impl_rework_hint() -> None:
    task = Task(id="T1", title="verify", status="in_progress", assigned_to="verify")

    projection = workflow_projection(
        task,
        [ZfEvent(
            type="verify.failed",
            actor="verify",
            task_id="T1",
            payload={"reason": "focused tests failed", "rework_target": "dev"},
        )],
    )

    assert projection.workflow_phase == "impl"
    assert projection.verify_state == "failed"
    assert projection.rework_target == "dev"
    assert "focused tests failed" in projection.rework_reason
