"""Compile repeated semantic failures into durable OA checkpoints."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.orchestrator_agent_operations import (
    PreparedOrchestratorAgentOperation,
    request_orchestrator_agent_checkpoint,
)
from zf.runtime.orchestrator_agent_policy import (
    checkpoint_policy,
    orchestration_flow_kind,
)
from zf.runtime.plan_artifact_package import reduce_plan_artifact_packages
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


SEMANTIC_FAILURE_REQUESTED = "orchestrator.semantic.failure.requested"
SEMANTIC_FAILURE_INPUT_SCHEMA = "semantic-failure-input.v1"


class SemanticFailureCheckpointError(ValueError):
    """A semantic failure request is incomplete or no longer current."""


def request_semantic_failure_checkpoint(
    runtime: Any,
    event: ZfEvent,
) -> PreparedOrchestratorAgentOperation:
    if event.type != SEMANTIC_FAILURE_REQUESTED:
        raise SemanticFailureCheckpointError("not_semantic_failure_request")
    payload = event.payload if isinstance(event.payload, dict) else {}
    task_id = str(event.task_id or payload.get("task_id") or "").strip()
    task = runtime.task_store.get(task_id) if task_id else None
    flow_kind = orchestration_flow_kind(
        event,
        getattr(getattr(task, "contract", None), "evidence_contract", None),
    )
    policy = checkpoint_policy(
        runtime.config,
        "semantic_failure",
        flow_kind=flow_kind,
    )
    if not policy:
        raise SemanticFailureCheckpointError("semantic_failure_checkpoint_disabled")
    if str(payload.get("problem_class") or "") != "semantic":
        raise SemanticFailureCheckpointError("problem_class_not_semantic")
    if task is None or str(task.status) in {"done", "cancelled"}:
        raise SemanticFailureCheckpointError("target_task_not_current")
    events = runtime.event_log.read_all()
    workflow_run_id, package = _current_plan_package(
        events,
        preferred_run_id=str(
            payload.get("workflow_run_id")
            or payload.get("trace_id")
            or ""
        ),
    )
    if not package:
        raise SemanticFailureCheckpointError("current_plan_package_missing")
    claimed_generation = str(payload.get("task_map_generation") or "")
    if claimed_generation and claimed_generation != str(
        package.get("task_map_generation") or ""
    ):
        raise SemanticFailureCheckpointError("target_generation_stale")
    failure_ids = _failure_event_ids(payload)
    failures = [item for item in events if item.id in failure_ids]
    if not failure_ids or {item.id for item in failures} != set(failure_ids):
        raise SemanticFailureCheckpointError("exact_failure_events_missing")
    for failure in failures:
        body = failure.payload if isinstance(failure.payload, Mapping) else {}
        run_id = str(
            body.get("workflow_run_id") or body.get("run_id")
            or body.get("trace_id") or failure.correlation_id or ""
        )
        if run_id != workflow_run_id or (failure.task_id and failure.task_id != task_id):
            raise SemanticFailureCheckpointError("failure_target_scope_mismatch")
    latest_failure_payload = (
        failures[-1].payload if failures and isinstance(failures[-1].payload, dict)
        else {}
    )
    active_attempt_id = str(
        getattr(task, "active_dispatch_id", "")
        or payload.get("active_attempt_id")
        or payload.get("dispatch_id")
        or latest_failure_payload.get("attempt_id")
        or latest_failure_payload.get("dispatch_id")
        or ""
    )
    if not active_attempt_id:
        raise SemanticFailureCheckpointError("target_attempt_missing")
    target_role = str(
        getattr(getattr(task, "contract", None), "owner_instance", "")
        or getattr(task, "assigned_to", "")
        or payload.get("role")
        or ""
    )
    target_stage = str(
        payload.get("target_stage_id")
        or getattr(getattr(task, "contract", None), "phase", "")
        or payload.get("trigger_event_type")
        or "semantic-recovery"
    )
    failure_body = {
        "schema_version": SEMANTIC_FAILURE_INPUT_SCHEMA,
        "workflow_run_id": workflow_run_id,
        "request_event_id": event.id,
        "failure_fingerprint": str(payload.get("failure_fingerprint") or ""),
        "target": {
            "task_id": task_id,
            "stage_id": target_stage,
            "attempt_id": active_attempt_id,
            "role_instance": target_role,
            "task_status": str(task.status),
            "task_map_generation": str(package.get("task_map_generation") or ""),
        },
        "task_contract": asdict(task.contract),
        "failure_events": [_event_row(item) for item in failures],
    }
    failure_ref = write_immutable_json_sidecar(
        runtime.state_dir,
        failure_body,
        root="orchestrator-agent/semantic-failures",
        kind="semantic_failure_input",
        schema_version=SEMANTIC_FAILURE_INPUT_SCHEMA,
        created_by="orchestrator-agent-semantic-failure",
        source_event_id=event.id,
    )
    artifact_refs: list[dict[str, Any]] = [{
        **failure_ref,
        "source_id": "semantic-failure-input",
    }]
    recovery_ref = payload.get("recovery_context_ref")
    if isinstance(recovery_ref, Mapping):
        hydrate_sidecar_ref(runtime.state_dir, dict(recovery_ref))
        artifact_refs.append({
            **dict(recovery_ref),
            "source_id": "task-recovery-context",
            "kind": "recovery_context",
        })
    checkpoint_payload = {
        **payload,
        "workflow_run_id": workflow_run_id,
        "flow_kind": flow_kind,
        "plan_revision": str(package.get("plan_revision") or ""),
        "task_map_generation": str(package.get("task_map_generation") or ""),
        "plan_artifact_package_id": str(package.get("package_id") or ""),
        "plan_artifact_package_ref": str(package.get("package_ref") or ""),
        "plan_artifact_package_digest": str(package.get("package_digest") or ""),
        "target_task_id": task_id,
        "target_stage_id": target_stage,
        "target_attempt_id": active_attempt_id,
        "target_role_instance": target_role,
        "artifact_refs": artifact_refs,
    }
    return request_orchestrator_agent_checkpoint(
        runtime,
        checkpoint="semantic_failure",
        checkpoint_policy=policy,
        workflow_run_id=workflow_run_id,
        source_event=event,
        payload=checkpoint_payload,
    )


def semantic_failure_request_type(
    config: Any,
    *,
    flow_kind: str = "",
    source: Any = None,
) -> str:
    resolved = orchestration_flow_kind(
        {"flow_kind": flow_kind},
        source,
    )
    return (
        SEMANTIC_FAILURE_REQUESTED
        if checkpoint_policy(
            config,
            "semantic_failure",
            flow_kind=resolved,
        )
        else "orchestrator.rework.triage.requested"
    )


def _current_plan_package(
    events: list[ZfEvent],
    *,
    preferred_run_id: str,
) -> tuple[str, dict[str, Any]]:
    run_ids: list[str] = []
    if preferred_run_id:
        run_ids.append(preferred_run_id)
    for event in reversed(events) if not preferred_run_id else []:
        if event.type != "plan.artifact_package.admitted":
            continue
        run_id = str((event.payload or {}).get("workflow_run_id") or "")
        if run_id and run_id not in run_ids:
            run_ids.append(run_id)
    if not preferred_run_id and len(run_ids) != 1:
        return "", {}
    for run_id in run_ids:
        current = reduce_plan_artifact_packages(
            events,
            workflow_run_id=run_id,
        ).get("current")
        if isinstance(current, Mapping) and current:
            return run_id, dict(current)
    return "", {}


def _failure_event_ids(payload: Mapping[str, Any]) -> list[str]:
    rows = payload.get("failure_event_ids") or payload.get("source_event_ids") or []
    if not isinstance(rows, list):
        return []
    return list(dict.fromkeys(
        str(item).strip() for item in rows if str(item).strip()
    ))


def _event_row(event: ZfEvent) -> dict[str, Any]:
    return {
        "event_id": event.id,
        "event_type": event.type,
        "actor": str(event.actor or ""),
        "task_id": str(event.task_id or ""),
        "ts": event.ts,
        "causation_id": str(event.causation_id or ""),
        "correlation_id": str(event.correlation_id or ""),
        "payload": dict(event.payload or {}),
    }


__all__ = [
    "SEMANTIC_FAILURE_INPUT_SCHEMA",
    "SEMANTIC_FAILURE_REQUESTED",
    "SemanticFailureCheckpointError",
    "request_semantic_failure_checkpoint",
    "semantic_failure_request_type",
]
