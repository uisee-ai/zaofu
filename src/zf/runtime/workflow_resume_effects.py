"""Bind batch resume request receipts to their subsequent admission effect."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from zf.core.events.model import ZfEvent


BATCH_RESUME_ACTIONS = frozenset({"workflow-batch-resume", "workflow-task-resume"})
_TASK_MAP_RESUMES = frozenset({"repair_failed_children", "trigger_rework"})


def resume_admission_effect(action: Mapping[str, Any]) -> dict[str, Any]:
    if action.get("action") not in BATCH_RESUME_ACTIONS:
        return {}
    if action.get("safe_resume_action") not in _TASK_MAP_RESUMES:
        return {}
    return {
        "effect_expected_events": ["fanout.started", "task.pipeline.generation.admitted"],
        "effect_failure_events": ["fanout.cancelled", "task_map.blocked", "plan.rejected"],
        "effect_timeout_seconds": int(action.get("effect_timeout_seconds") or 1800),
    }


def bind_resume_request(
    action: Mapping[str, Any], result: Mapping[str, Any], events: Sequence[ZfEvent],
) -> dict[str, Any]:
    """Select only the task-map request emitted by this controlled action."""
    effect = resume_admission_effect(action)
    if not effect:
        return {}
    resume = result.get("resume_result")
    resume = resume if isinstance(resume, Mapping) else {}
    emitted_ids: set[str] = set()
    for key in ("results", "batch_results"):
        for item in resume.get(key) or []:
            if isinstance(item, Mapping):
                emitted_ids.update(str(value) for value in item.get("emitted_event_ids") or [])
    requests = [event for event in events if event.id in emitted_ids and event.type == "task_map.ready"]
    effect["effect_request_event_id"] = requests[0].id if len(requests) == 1 else ""
    return effect


def matches_resume_admission(event: ZfEvent, action: Mapping[str, Any]) -> bool:
    request_id = str(action.get("effect_request_event_id") or "")
    if not request_id:
        return False
    body = event.payload if isinstance(event.payload, Mapping) else {}
    return request_id in {
        str(event.causation_id or ""), str(body.get("trigger_event_id") or ""),
        str(body.get("source_event_id") or ""), str(body.get("task_map_event_id") or ""),
        str(body.get("plan_id") or ""),
    }
