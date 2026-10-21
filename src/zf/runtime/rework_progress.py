"""Task-scoped progress that supersedes old recovery requests."""

from __future__ import annotations

from zf.core.events.model import ZfEvent


_PROGRESS_EVENTS = frozenset({
    "task.dispatched",
    "task.rework.requested",
    "dev.build.done",
    "impl.child.completed",
    "review.approved",
    "verify.passed",
    "test.passed",
    "judge.passed",
    "task.done",
    "orchestrator.replan_requested",
    "task_map.ready",
    "candidate.ready",
    "run.goal.completed",
})


def has_later_task_progress(events: list[ZfEvent], start: int, task_id: str) -> bool:
    for event in events[start + 1:]:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if (
            event.type in {
                "task_map.ready",
                "task_map.amended",
                "product_delivery.task_map.adopted",
            }
            and task_id in {
                str(item) for item in payload.get("supersedes_task_ids") or []
                if str(item).strip()
            }
        ):
            return True
        event_task = str(
            event.task_id
            or payload.get("task_id")
            or payload.get("pdd_id")
            or payload.get("feature_id")
            or ""
        )
        admitted_completion = (
            event.type == "fanout.child.completed"
            and payload.get("status") == "completed"
            and isinstance(payload.get("admitted_call_result_ref"), dict)
            and bool(payload["admitted_call_result_ref"].get("ref"))
            and bool(payload["admitted_call_result_ref"].get("sha256"))
        )
        if event_task == task_id and (
            event.type in _PROGRESS_EVENTS or admitted_completion
        ):
            return True
    return False
