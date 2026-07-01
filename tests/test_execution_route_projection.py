from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.runtime.execution_route import project_execution_route, project_route_summary


def _event(seq: int, type_: str, actor: str, *, task_id: str = "TASK-1", **payload):
    return (
        seq,
        ZfEvent(
            id=f"evt-{seq}",
            ts=f"2026-05-22T00:{seq:02d}:00+00:00",
            type=type_,
            actor=actor,
            task_id=task_id,
            payload=payload,
            correlation_id="trace-1",
        ),
    )


def test_execution_route_projects_linear_summary_and_parallel_dev_dag():
    route = project_execution_route([
        _event(1, "task.created", "planner-1"),
        _event(2, "task.dispatched", "orchestrator", assignee="dev-1"),
        _event(3, "task.dispatched", "orchestrator", assignee="dev-2"),
        _event(4, "dev.build.done", "dev-1"),
        _event(5, "dev.build.done", "dev-2"),
        _event(6, "review.approved", "critic-1"),
        _event(7, "test.passed", "test-1"),
        _event(8, "judge.passed", "gate-1"),
        _event(9, "task.done", "kernel"),
    ], task_id="TASK-1", trace_id="trace-1")

    assert route["summary"] == (
        "planner-1 -> dev-1/dev-2 -> critic-1 -> test-1 -> gate-1 -> done"
    )
    assert route["status"] == "done"
    assert [step["stage"] for step in route["linear"]] == [
        "plan",
        "dev",
        "review",
        "test",
        "gate",
        "done",
    ]
    dev_step = route["linear"][1]
    assert dev_step["label"] == "Dev Fanout"
    assert dev_step["parallel"] is True
    edges = {(edge["from"], edge["to"]) for edge in route["dag"]["edges"]}
    assert ("plan:planner-1", "dev:dev-1") in edges
    assert ("plan:planner-1", "dev:dev-2") in edges
    assert ("dev:dev-1", "review:critic-1") in edges
    assert ("dev:dev-2", "review:critic-1") in edges


def test_execution_route_tracks_failed_rework_then_actual_success():
    route = project_execution_route([
        _event(1, "task.dispatched", "orchestrator", assignee="dev-1"),
        _event(2, "review.rejected", "critic-1", reason="missing test"),
        _event(3, "task.dispatched", "orchestrator", assignee="dev-1"),
        _event(4, "dev.build.done", "dev-1"),
        _event(5, "review.approved", "critic-1"),
    ])

    review_node = next(
        node for node in route["dag"]["nodes"]
        if node["id"] == "review:critic-1"
    )
    assert review_node["status"] == "done"
    assert review_node["failed_count"] == 1
    assert route["status"] == "observed"
    assert route["summary"] == "dev-1 -> critic-1"


def test_route_summary_is_small_card_payload():
    summary = project_route_summary([
        _event(1, "task.dispatched", "orchestrator", assignee="dev-1"),
        _event(2, "worker.progress", "dev-1", phase="implement"),
    ], task_id="TASK-1")

    assert summary == {
        "schema_version": "execution-route.v1",
        "summary": "dev-1",
        "status": "running",
        "current_stage": "dev",
        "current_stage_label": "Development",
        "step_count": 1,
        "parallel": False,
        "empty": False,
    }
