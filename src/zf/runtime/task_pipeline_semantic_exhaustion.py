"""Expose exhausted Task Pipeline semantic rework as a durable fact."""

from __future__ import annotations

from typing import Any, Mapping

from zf.core.events.model import ZfEvent


EVENT_TYPE = "task.pipeline.semantic_rework.exhausted"
SEMANTIC_TRIAGE_CONTRACT_VERSION = 1


def reconcile_task_pipeline_semantic_exhaustion(
    runtime: Any,
    *,
    projection: Mapping[str, Any],
    generation_contexts: Mapping[str, Mapping[str, Any]],
) -> list[ZfEvent]:
    """Emit one fact per exhausted operation without choosing recovery."""

    existing = {
        _event_key(event)
        for event in runtime.event_log.read_all()
        if event.type == EVENT_TYPE
    }
    emitted: list[ZfEvent] = []
    for view in projection.get("tasks") or []:
        if not isinstance(view, Mapping):
            continue
        blocker = view.get("semantic_blocker")
        if not isinstance(blocker, Mapping) or not blocker:
            continue
        task_id = str(view.get("task_id") or "").strip()
        context = generation_contexts.get(task_id)
        if not task_id or context is None:
            continue
        stage_id = str(blocker.get("stage") or "verify")
        semantic_verdict = str(blocker.get("semantic_verdict") or "")
        max_rework_attempts = int(blocker.get("max_rework_attempts") or 0)
        control_result_ref = dict(blocker.get("control_result_ref") or {})
        source_event_id = str(blocker.get("source_event_id") or "")
        payload = {
            "schema_version": "task-pipeline.semantic-rework-exhausted.v1",
            "semantic_triage_contract_version": SEMANTIC_TRIAGE_CONTRACT_VERSION,
            "task_id": task_id,
            "stage_id": stage_id,
            "operation_id": str(blocker.get("operation_id") or ""),
            "operation_generation": int(
                blocker.get("operation_generation") or 1
            ),
            "semantic_verdict": semantic_verdict,
            "max_rework_attempts": max_rework_attempts,
            "workflow_run_id": str(
                context.get("workflow_run_id") or ""
            ),
            "flow_kind": str(context.get("flow_kind") or ""),
            "task_map_generation": str(
                context.get("task_map_generation") or ""
            ),
            "profile_id": str(context.get("profile_id") or ""),
            "profile_digest": str(context.get("profile_digest") or ""),
            "control_result_ref": control_result_ref,
            "source_event_id": source_event_id,
            "failure_class": "task_pipeline_semantic_rework_exhausted",
            "failure_scope": "task",
            "failure_fingerprint": ":".join((
                "task-pipeline-semantic",
                task_id,
                stage_id,
                semantic_verdict or "unknown",
                str(control_result_ref.get("sha256") or "")[:12],
            )),
            "failure_count": max_rework_attempts + 1,
            "retry_count": max_rework_attempts,
            "failure_event_ids": [source_event_id] if source_event_id else [],
            "semantic_triage_required": True,
            "reason": "semantic_rework_exhausted",
            "last_reason": (
                f"{stage_id} semantic verdict {semantic_verdict or 'unknown'} "
                f"exhausted {max_rework_attempts} rework attempts"
            ),
            "owner_route": "run_manager",
            "recommended_route": "semantic_replan_or_diagnosis",
        }
        key = _payload_key(payload)
        if key in existing:
            continue
        source_event_id = str(payload.get("source_event_id") or "")
        event = runtime.event_writer.append(ZfEvent(
            type=EVENT_TYPE,
            actor="zf-runtime",
            task_id=task_id,
            causation_id=source_event_id or None,
            correlation_id=(
                str(payload.get("workflow_run_id") or "") or None
            ),
            payload=payload,
        ))
        existing.add(key)
        emitted.append(event)
    return emitted


def _event_key(event: ZfEvent) -> tuple[str, str, str, int, int]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    return _payload_key(payload)


def _payload_key(payload: Mapping[str, Any]) -> tuple[str, str, str, int, int]:
    return (
        str(payload.get("workflow_run_id") or ""),
        str(payload.get("task_map_generation") or ""),
        str(payload.get("operation_id") or ""),
        int(payload.get("operation_generation") or 0),
        int(payload.get("semantic_triage_contract_version") or 0),
    )


__all__ = [
    "EVENT_TYPE",
    "SEMANTIC_TRIAGE_CONTRACT_VERSION",
    "reconcile_task_pipeline_semantic_exhaustion",
]
