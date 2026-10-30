"""Mechanical routes for admitted Task Pipeline risk-review verdicts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.runtime.integration_acceptance_result import (
    validate_integration_acceptance_result,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_runtime_types import WorkflowRuntimeDecision


def reconcile_task_pipeline_acceptance_routes(
    runtime: Any,
    *,
    generation_contexts: Mapping[str, Mapping[str, Any]],
    operation_rows: list[dict[str, Any]],
) -> list[WorkflowRuntimeDecision]:
    events = runtime.event_log.read_all()
    decisions: list[WorkflowRuntimeDecision] = []
    for operation in operation_rows:
        if (
            str(operation.get("task_pipeline_stage") or "")
            != "acceptance_review"
            or str(operation.get("status") or "") != "settled"
        ):
            continue
        task_id = str(operation.get("task_id") or "")
        context = generation_contexts.get(task_id)
        if context is None:
            continue
        if any(
            str(operation.get(key) or "") != str(context.get(key) or "")
            for key in ("workflow_run_id", "task_map_generation")
        ):
            continue
        descriptor = operation.get("admitted_control_result_ref")
        if not isinstance(descriptor, Mapping):
            continue
        result_event = _canonical_result_event(
            events,
            operation_id=str(operation.get("operation_id") or ""),
            task_id=task_id,
        )
        if result_event is None:
            continue
        try:
            hydrated = hydrate_sidecar_ref(
                Path(runtime.state_dir),
                dict(descriptor),
                purpose="task_pipeline_acceptance_route",
                actor="orchestrator",
            )
            result = (
                hydrated.payload
                if isinstance(hydrated.payload, Mapping)
                else {}
            )
            validate_integration_acceptance_result(
                result,
                require_read_ledger=True,
            )
        except Exception:
            _block_task_once(
                runtime,
                task_id=task_id,
                operation=operation,
                source_event=result_event,
                descriptor=descriptor,
                reason="integration_acceptance_result_invalid",
                blocker={"class": "protocol", "owner": "run_manager"},
            )
            continue
        verdict = str(result.get("verdict") or "")
        if verdict == "admit":
            continue
        if verdict == "revise":
            event = _emit_route_once(
                runtime,
                event_type="task.pipeline.acceptance.revision_requested",
                task_id=task_id,
                operation=operation,
                source_event=result_event,
                descriptor=descriptor,
                payload={
                    "feedback": list(result.get("feedback") or []),
                    "feedback_refs": list(result.get("feedback_refs") or []),
                },
            )
            if event is not None:
                decisions.append(WorkflowRuntimeDecision(
                    action="task_pipeline_acceptance_revise",
                    task_id=task_id,
                    reason="risk review requested Task-local revision",
                ))
            continue
        if verdict == "replan":
            routed = _emit_route_once(
                runtime,
                event_type="task.pipeline.acceptance.replan_requested",
                task_id=task_id,
                operation=operation,
                source_event=result_event,
                descriptor=descriptor,
                payload={"delta_intent": dict(result.get("delta_intent") or {})},
            )
            route_event = routed or next((
                event for event in reversed(runtime.event_log.read_all())
                if event.type == "task.pipeline.acceptance.replan_requested"
                and (event.payload or {}).get("operation_id") == operation.get("operation_id")
            ), None)
            if route_event is not None:
                _emit_oa_delta_request(
                    runtime,
                    task_id=task_id,
                    operation=operation,
                    source_event=result_event,
                    route_event=route_event,
                    descriptor=descriptor,
                    context=context,
                )
                _set_task_blocked(
                    runtime,
                    task_id,
                    "risk review requires OA/Planner task-map replan",
                )
            if routed is not None:
                decisions.append(WorkflowRuntimeDecision(
                    action="task_pipeline_acceptance_replan",
                    task_id=task_id,
                    reason="risk review requested controlled OA delta",
                ))
            continue
        blocker = dict(result.get("blocker") or {})
        if verdict != "block":
            blocker = {
                "class": "reviewer_execution",
                "owner": "run_manager",
            }
        blocked = _block_task_once(
            runtime,
            task_id=task_id,
            operation=operation,
            source_event=result_event,
            descriptor=descriptor,
            reason=(
                "integration_acceptance_blocked"
                if verdict == "block"
                else "integration_acceptance_reviewer_abstained"
            ),
            blocker=blocker,
        )
        if blocked is not None:
            decisions.append(WorkflowRuntimeDecision(
                action="task_pipeline_acceptance_block",
                task_id=task_id,
                reason="risk review produced a typed blocker",
            ))
    return decisions


def _emit_oa_delta_request(
    runtime: Any,
    *,
    task_id: str,
    operation: Mapping[str, Any],
    source_event: ZfEvent,
    route_event: ZfEvent,
    descriptor: Mapping[str, Any],
    context: Mapping[str, Any],
) -> ZfEvent | None:
    from zf.runtime.orchestrator_agent_semantic_failure import (
        semantic_failure_request_type,
    )

    event_type = semantic_failure_request_type(
        runtime.config,
        flow_kind=str(context.get("flow_kind") or ""),
    )
    operation_id = str(operation.get("operation_id") or "")
    if any(
        event.type == event_type
        and isinstance(event.payload, Mapping)
        and str(event.payload.get("source_operation_id") or "") == operation_id
        for event in runtime.event_log.read_all()
    ):
        return None
    payload = {
        "schema_version": "task-pipeline-acceptance-replan-intent.v1",
        "workflow_run_id": str(context.get("workflow_run_id") or ""),
        "task_id": task_id,
        "task_map_generation": str(context.get("task_map_generation") or ""),
        "problem_class": "semantic",
        "failure_event_ids": [source_event.id],
        "failure_fingerprint": _digest({
            "operation_id": operation_id,
            "control_result_digest": descriptor.get("sha256"),
        }),
        "source_operation_id": operation_id,
        "target_stage_id": "acceptance_review",
        "active_attempt_id": str(operation.get("active_attempt_id") or ""),
        "recovery_context_ref": dict(descriptor),
        "route_event_id": route_event.id,
    }
    return runtime.event_writer.append(ZfEvent(
        type=event_type,
        actor="zf-cli",
        origin="kernel",
        task_id=task_id,
        payload=payload,
        causation_id=route_event.id,
        correlation_id=str(context.get("workflow_run_id") or "") or None,
    ))


def _block_task_once(
    runtime: Any,
    *,
    task_id: str,
    operation: Mapping[str, Any],
    source_event: ZfEvent,
    descriptor: Mapping[str, Any],
    reason: str,
    blocker: Mapping[str, Any],
) -> ZfEvent | None:
    event = _emit_route_once(
        runtime,
        event_type="task.pipeline.acceptance.blocked",
        task_id=task_id,
        operation=operation,
        source_event=source_event,
        descriptor=descriptor,
        payload={"reason": reason, "blocker": dict(blocker)},
    )
    # The route ledger and TaskStore are separate durable writes. Replaying
    # an existing route must finish the state update lost in that window.
    _set_task_blocked(runtime, task_id, reason)
    return event


def _emit_route_once(
    runtime: Any,
    *,
    event_type: str,
    task_id: str,
    operation: Mapping[str, Any],
    source_event: ZfEvent,
    descriptor: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> ZfEvent | None:
    operation_id = str(operation.get("operation_id") or "")
    if any(
        event.type == event_type
        and isinstance(event.payload, Mapping)
        and str(event.payload.get("operation_id") or "") == operation_id
        for event in runtime.event_log.read_all()
    ):
        return None
    return runtime.event_writer.append(ZfEvent(
        type=event_type,
        actor="zf-cli",
        origin="kernel",
        task_id=task_id,
        payload={
            "schema_version": "task-pipeline-acceptance-route.v1",
            "workflow_run_id": str(operation.get("workflow_run_id") or ""),
            "task_map_generation": str(
                operation.get("task_map_generation") or ""
            ),
            "operation_id": operation_id,
            "operation_generation": int(
                operation.get("operation_generation") or 0
            ),
            "acceptance_result_ref": dict(descriptor),
            **dict(payload),
        },
        causation_id=source_event.id,
        correlation_id=str(operation.get("workflow_run_id") or "") or None,
    ))


def _canonical_result_event(
    events: list[ZfEvent],
    *,
    operation_id: str,
    task_id: str,
) -> ZfEvent | None:
    return next((
        event for event in reversed(events)
        if event.type in {
            "task.pipeline.acceptance.completed",
            "task.pipeline.acceptance.failed",
        }
        and str(event.task_id or "") == task_id
        and isinstance(event.payload, Mapping)
        and str(event.payload.get("operation_id") or "") == operation_id
    ), None)


def _set_task_blocked(runtime: Any, task_id: str, reason: str) -> None:
    task = runtime.task_store.get(task_id)
    if task is None or str(task.status) in {"done", "cancelled"}:
        return
    if task.status == "blocked" and task.blocked_reason == reason:
        return
    runtime.task_store.update(
        task_id,
        status="blocked",
        blocked_reason=reason,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")).hexdigest()


__all__ = ["reconcile_task_pipeline_acceptance_routes"]
