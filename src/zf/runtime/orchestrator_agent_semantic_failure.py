"""Compile repeated semantic failures into durable OA checkpoints."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.runtime.candidate_plan_recovery import candidate_checkpoint_binding
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.orchestrator_agent_operations import (
    PreparedOrchestratorAgentOperation,
    request_orchestrator_agent_checkpoint,
)
from zf.runtime.orchestrator_agent_policy import (
    checkpoint_policy,
    orchestration_flow_kind,
)
from zf.runtime.orchestrator_agent_plan_binding import current_checkpoint_plan_package
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_verification_failure_sources import task_verification_failure_sources


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
    current: dict[str, Any] = {}
    bound_operation: Mapping[str, Any] = {}
    workflow_run_id, package = _current_plan_package(
        events,
        task_id=task_id,
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
    candidate = {}
    operation_id = str(payload.get("target_operation_id") or "")
    aggregate_candidate = payload.get("recovery_scope") == "candidate" and not operation_id
    if aggregate_candidate:
        from zf.runtime.candidate_failure_checkpoint import aggregate_candidate_binding
        try:
            candidate = aggregate_candidate_binding(runtime.state_dir, events,
                failure_id=failures[-1].id, run_id=workflow_run_id, task_id=task_id)
        except (ValueError, OSError) as exc:
            raise SemanticFailureCheckpointError(str(exc)) from exc
        operation_id = candidate["operation_id"]
        if (candidate["task_map_generation"] != package.get("task_map_generation")
                or candidate["plan_artifact_package_digest"] != package.get("package_digest")):
            raise SemanticFailureCheckpointError("candidate Plan package superseded")
    elif operation_id:
        from zf.runtime.orchestrator_agent_execution_policy import operation_bindings
        from zf.runtime.workflow_operation import reduce_workflow_operations

        bound_operation = reduce_workflow_operations(events).get(operation_id, {})
        if bound_operation.get("operation_type") == "fanout_reader_child":
            try:
                candidate = candidate_checkpoint_binding(
                    runtime.state_dir, events, failure_id=failures[-1].id,
                    operation_id=operation_id, run_id=workflow_run_id, task_id=task_id,
                )
            except (ValueError, OSError) as exc:
                raise SemanticFailureCheckpointError(str(exc)) from exc
            if (candidate["task_map_generation"] != package.get("task_map_generation")
                    or candidate["plan_artifact_package_digest"] != package.get("package_digest")):
                raise SemanticFailureCheckpointError("candidate Plan package superseded")
        else:
            current = operation_bindings(events, workflow_run_id, {task_id}).get(task_id, {})
            if current.get("operation_id") != operation_id or current.get("status") != "settled":
                raise SemanticFailureCheckpointError("target_operation_stale")
    if candidate:
        for key, bound_key in (("active_attempt_id", "attempt_id"), ("dispatch_id", "attempt_id"),
                               ("role", "role_instance"), ("target_stage_id", "stage_id")):
            if payload.get(key) and payload[key] != candidate[bound_key]:
                raise SemanticFailureCheckpointError(f"candidate target mismatch: {key}")
    latest_failure_payload = (
        failures[-1].payload if failures and isinstance(failures[-1].payload, dict)
        else {}
    )
    active_attempt_id = str(
        candidate.get("attempt_id")
        or payload.get("active_attempt_id")
        or getattr(task, "active_dispatch_id", "")
        or current.get("active_attempt_id")
        or payload.get("dispatch_id")
        or latest_failure_payload.get("attempt_id")
        or latest_failure_payload.get("dispatch_id")
        or ""
    )
    if not active_attempt_id:
        raise SemanticFailureCheckpointError("target_attempt_missing")
    target_role = str(
        candidate.get("role_instance")
        or bound_operation.get("role_instance")
        or getattr(getattr(task, "contract", None), "owner_instance", "")
        or getattr(task, "assigned_to", "")
        or payload.get("role")
        or ""
    )
    target_stage = str(
        candidate.get("stage_id")
        or payload.get("target_stage_id")
        or getattr(getattr(task, "contract", None), "phase", "")
        or payload.get("trigger_event_type")
        or "semantic-recovery"
    )
    from zf.runtime.orchestrator_agent_execution_policy import run_goal_objective_snapshot

    goal_snapshot = run_goal_objective_snapshot(events, workflow_run_id)
    fingerprint = (f"{operation_id}/{failures[-1].id}" if candidate
                   else str(payload.get("failure_fingerprint") or ""))
    failure_body = {
        "schema_version": SEMANTIC_FAILURE_INPUT_SCHEMA,
        "workflow_run_id": workflow_run_id,
        "run_goal": goal_snapshot,
        "request_event_id": event.id,
        "failure_fingerprint": fingerprint,
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
    if candidate:
        from zf.runtime.orchestrator_agent_execution_policy import bounded_actions

        can_rebind = aggregate_candidate and "rebind" in (bounded_actions(runtime.config, flow_kind) or [])
        failure_body["candidate_verify"] = {
            **candidate, "failure_event_id": failures[-1].id, "operation_id": operation_id,
            "permitted_recovery_actions": ["return_to_plan", "rework", *(["rebind"] if can_rebind else [])],
            "verification_recovery_contract": (
                "rebind requires one exact-target directive with candidate_verification_recovery: "
                "classification=verification_recovery, contract_changed=false, max_replays=1, "
                "failure_event_id, candidate_snapshot_event_id and rationale. member_reviews must "
                "account for all members and unresolved_members with their exact child_id, operation_id, "
                "control_result_ref or child_event_id, disposition=reverify and rationale. Keep all findings. "
                "It grants one stage replay per native freeze, no host execution, product verdict or contract change."
                if can_rebind else "Verification rebind is not authorized by the current configuration."
            ),
            **({"return_to_plan_contract": "For an aggregate return_to_plan, put finding_dispositions on the "
                "directive. Account for each supplied finding_id exactly once with classification "
                "product_defect/contract_question/execution_unknown, owner, next_action and rationale. "
                "Identify the independent evidence to obtain; the source Task alone does not authorize "
                "Writer repair. Separate evidence recovery from a protected scope decision."}
               if aggregate_candidate else {}),
            "rework_contract": "An unchanged-contract product repair requires candidate_repair in the "
            "exact-target directive: classification=product_defect, contract_changed=false, rationale, "
            "and gap_tasks copied exactly from the pinned Task Map. For mixed outcomes supply "
            "finding_dispositions covering every supplied finding_id exactly once: classification "
            "product_defect/contract_question/execution_unknown, owner, next_action and rationale. "
            "Only product_repair_eligible findings can authorize product_defect; gap_tasks cover exactly "
            "their task_ids. All findings remain pending final independent disposition. Only task_id, "
            "base_commit (frozen Candidate head), and supersedes_task_ids (one predecessor) may change. "
            "Unknown/input/blocked outcomes require diagnosis; changed obligations require return_to_plan.",
        }
    media_refs = []
    if aggregate_candidate:
        from zf.runtime.diagnosis_host_recovery import host_recovery_context
        from zf.runtime.host_evidence_media import materialize_container_media
        from zf.runtime.milestone_task_lifecycles import aggregate_recovery_lifecycles

        lifecycles = aggregate_recovery_lifecycles(runtime.state_dir, events, workflow_run_id, candidate)
        if lifecycles:
            failure_body["candidate_verify"]["task_pipeline_lifecycles"] = lifecycles
        host = host_recovery_context(runtime.state_dir, events, runtime.config, workflow_run_id,
            operation_ids=frozenset(member["operation_id"] for member in candidate["members"]))
        failure_body["preauthorized_host_recovery"] = host
        receipt_refs = [row["receipt_ref"] for row in host["effects"]
                        if row.get("receipt_status") == "observed"
                        and row.get("target_commit") == candidate["source_commit"]]
        image = host["host_verification"].get("image")
        if receipt_refs and image:
            try:
                media_refs = materialize_container_media(runtime.state_dir, receipt_refs, image=image,
                                                        policy=runtime.config.runtime.host_verification)
            except (ValueError, OSError) as exc:
                failure_body["host_media_unavailable"] = str(exc)
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
    if payload.get("reader_marker_id"):
        from zf.runtime.candidate_obligation_followup import report_sources
        try:
            artifact_refs.extend(report_sources(runtime.state_dir, events, event))
        except (OSError, ValueError, KeyError) as exc:
            raise SemanticFailureCheckpointError(str(exc)) from exc
    elif aggregate_candidate:
        from zf.runtime.candidate_reader_observation import admitted_reader_observation

        observation = admitted_reader_observation(runtime.state_dir, events, {m["child_event_id"] for m in candidate["members"]})
        if observation:
            artifact_refs.append(observation)
        artifact_refs.extend({**member["control_result_ref"],
            "source_id": f"candidate-verification-{member['child_id']}"}
            for member in candidate["members"])
    elif candidate:
        artifact_refs.append({**candidate["control_result_ref"], "source_id": "candidate-verification"})
    else:
        try:
            artifact_refs.extend(task_verification_failure_sources(
                runtime.state_dir, events, set(failure_ids), task_id=task_id,
                workflow_run_id=workflow_run_id, package=package,
            ))
        except ValueError as exc:
            raise SemanticFailureCheckpointError(str(exc)) from exc
    artifact_refs.extend({**ref, "source_id": f"candidate-host-media-{index}"}
                         for index, ref in enumerate(media_refs))
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
        "failure_fingerprint": fingerprint,
        "candidate_failure_id": failures[-1].id if candidate else "",
        "candidate_operation_id": operation_id if candidate else "",
        "candidate_failure_scope": ("reader_obligation_report" if payload.get("reader_marker_id")
                                    else "aggregate" if aggregate_candidate else ""),
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
        "run_goal_objective_event_id": goal_snapshot["objective_event_id"],
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
    task_id: str = "",
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
        current = current_checkpoint_plan_package(
            events,
            workflow_run_id=run_id,
            task_id=task_id,
        )
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
