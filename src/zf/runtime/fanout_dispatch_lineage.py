"""Fanout dispatch event construction with stable Workflow Run lineage."""

from __future__ import annotations

from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.runtime.workflow_lineage import (
    WorkflowLineageError,
    bind_workflow_task_lineage,
)


_LINEAGE_FIELDS = ("task_id", "parent_task_id", "workflow_run_id")


def bind_child_event_lineage(event: ZfEvent, payload: Mapping[str, Any]) -> None:
    task_id = str(payload.get("task_id") or "")
    event.task_id = task_id or None
    for key in _LINEAGE_FIELDS:
        value = payload.get(key)
        if value not in (None, ""):
            event.payload[key] = value


def reader_child_lineage_payload(
    payload: Mapping[str, Any],
    child: Mapping[str, Any],
    child_payload: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        key: value
        for key in _LINEAGE_FIELDS
        if (
            value := payload.get(key)
            or child.get(key)
            or child_payload.get(key)
        ) not in (None, "")
    }


def build_child_dispatch_outcome_event(
    context: Any,
    *,
    child: Any,
    run_id: str,
    reason: str,
    event_type: str,
    causation_id: str,
) -> ZfEvent:
    child_payload = child.payload
    return ZfEvent(
        type=event_type,
        actor="zf-cli",
        task_id=str(child_payload.get("task_id") or "") or None,
        payload={
            "fanout_id": context.fanout_id,
            "trace_id": context.trace_id,
            "stage_id": context.stage_id,
            "child_id": child.child_id,
            "run_id": run_id,
            "role_instance": child.role_instance,
            **{key: str(child_payload.get(key) or "") for key in _LINEAGE_FIELDS},
            "reason": reason,
        },
        causation_id=causation_id,
        correlation_id=context.trace_id,
    )


def bind_reader_fanout_lineage(
    runtime: Any,
    *,
    event: ZfEvent,
    context: Any,
    stage_id: str,
    trace_id: str,
    trigger_payload: dict[str, Any],
) -> tuple[str, str] | None:
    workflow_run_id = str(
        trigger_payload.get("workflow_run_id")
        or trigger_payload.get("run_id")
        or trace_id
        or ""
    ).strip()
    if not workflow_run_id:
        return "", ""
    events = runtime.event_log.read_all()
    trigger_task_id = str(trigger_payload.get("task_id") or "").strip()
    try:
        trigger_task_id, parent_task_id = bind_workflow_task_lineage(
            events,
            workflow_run_id=workflow_run_id,
            payload=trigger_payload,
            task_id=str(event.task_id or ""),
        )
        for child in context.expected_children:
            # Task-scoped discovery/replan requests execute under the leaf
            # contract while the Workflow Run remains anchored to its root
            # Task. Reader children are initially role-only, so preserve the
            # trigger's leaf identity before lineage binding fills the parent.
            if trigger_task_id:
                child.payload.setdefault("task_id", trigger_task_id)
            bind_workflow_task_lineage(
                events,
                workflow_run_id=workflow_run_id,
                payload=child.payload,
                task_id=str(
                    child.payload.get("task_id")
                    or trigger_task_id
                    or ""
                ),
            )
    except WorkflowLineageError as exc:
        runtime.event_writer.append(ZfEvent(
            type="fanout.cancelled",
            actor="zf-cli",
            task_id=event.task_id,
            payload={
                "fanout_id": context.fanout_id,
                "trace_id": trace_id,
                "stage_id": stage_id,
                "workflow_run_id": workflow_run_id,
                "reason": "workflow_lineage_invalid",
                "error": str(exc),
            },
            causation_id=event.id,
            correlation_id=trace_id,
        ))
        return None
    return workflow_run_id, parent_task_id


def apply_stage_attempt_domain(stage: Any, children: list[Any]) -> None:
    attempt_domain = str(getattr(stage, "attempt_domain", "") or "").strip()
    if attempt_domain:
        for child in children:
            child.payload.setdefault("attempt_domain", attempt_domain)


def apply_stage_result_semantics(stage: Any, children: list[Any]) -> None:
    """Pin operation-vs-subject result semantics for every Flow family."""

    result_semantics = str(
        getattr(stage, "result_semantics", "") or ""
    ).strip()
    specialists = {member.role_instance for member in (getattr(stage, "children", []) or [])
                   if stage.id == "prd-post-verify-discovery" and member.payload.get("stage_obligation")}
    for child in children:
        if specialists and getattr(child, "role_instance", "") in specialists:
            child.payload["result_semantics"] = "artifact_production"
        elif result_semantics:
            child.payload["result_semantics"] = result_semantics


def bind_started_event_lineage(
    event: ZfEvent,
    *,
    workflow_run_id: str,
    parent_task_id: str,
) -> None:
    if not parent_task_id:
        return
    event.task_id = parent_task_id
    event.payload.update({
        "task_id": parent_task_id,
        "parent_task_id": parent_task_id,
        "workflow_run_id": workflow_run_id,
    })


__all__ = [
    "apply_stage_attempt_domain",
    "apply_stage_result_semantics",
    "bind_child_event_lineage",
    "bind_reader_fanout_lineage",
    "bind_started_event_lineage",
    "build_child_dispatch_outcome_event",
    "reader_child_lineage_payload",
]
