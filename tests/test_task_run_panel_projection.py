from __future__ import annotations

from datetime import datetime, timezone

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.runtime.execution_route import project_execution_route
from zf.runtime.task_run_panel import project_task_run_panel


def _event(seq: int, type_: str, actor: str, **payload):
    return (
        seq,
        ZfEvent(
            id=f"evt-{seq}",
            ts=f"2026-05-26T06:0{seq}:00+00:00",
            type=type_,
            actor=actor,
            task_id="TASK-1",
            payload=payload,
        ),
    )


def test_task_run_panel_prefers_operation_projection_over_run_fallback() -> None:
    events = [
        _event(1, "task.dispatched", "orchestrator", dispatch_id="disp-1", assignee="dev-1"),
        _event(
            2,
            "worker.progress",
            "dev-1",
            dispatch_id="disp-1",
            role="dev",
            instance_id="dev-1",
            phase="implement",
            message="working",
            context_usage_ratio=0.82,
        ),
        _event(3, "worker.heartbeat", "dev-1", dispatch_id="disp-1", instance_id="dev-1"),
    ]
    route = project_execution_route(events, task_id="TASK-1", trace_id="trace-1")

    panel = project_task_run_panel(
        task=Task(id="TASK-1", status="in_progress", assigned_to="dev-1"),
        task_events=events,
        operations_projection={
            "operations": [{
                "dispatch_id": "disp-1",
                "operation_id": "op-disp-1",
                "role": "dev",
                "instance_id": "dev-1",
                "backend": "codex",
                "provider_session_ref": "sess-1",
                "state": "in_progress",
                "last_event_id": "evt-2",
                "last_event_type": "worker.progress",
                "last_event_at": "2026-05-26T06:02:00+00:00",
                "health": {"status": "ok"},
            }],
        },
        progress_projection={
            "current_phase": "implement",
            "latest_progress": {
                "event_id": "evt-2",
                "message": "working",
                "context_usage_ratio": 0.82,
            },
            "freshness": {"last_progress_at": "2026-05-26T06:02:00+00:00"},
        },
        runs=[{"run_id": "event-log-latest", "status": "projected"}],
        execution_route=route,
        role_instance="dev-1",
        transcript_count=2,
        now=datetime(2026, 5, 26, 6, 5, tzinfo=timezone.utc),
    )

    assert panel["schema_version"] == "task-run-panel.v1"
    assert panel["active_operation"]["dispatch_id"] == "disp-1"
    assert panel["active_operation"]["provider_session_ref"] == "sess-1"
    assert panel["active_operation"]["state"] == "in_progress"
    assert panel["latest_progress"]["message"] == "working"
    assert panel["health"]["context_status"] == "warning"
    assert panel["health"]["heartbeat_age_seconds"] == 120
    assert panel["counts"] == {
        "events": 3,
        "operations": 1,
        "runs": 1,
        "transcripts": 2,
    }
    assert "evt-2" in panel["source_event_ids"]
    assert panel["route_summary"]["current_stage"] == "dev"
