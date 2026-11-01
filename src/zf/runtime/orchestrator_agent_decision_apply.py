"""Apply admitted OA intent through deterministic Product Flow actions."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.runtime.orchestrator_agent_admission import (
    admit_orchestrator_agent_decision,
)
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import load_workflow_operation


def apply_orchestrator_agent_decision(runtime: Any, event: ZfEvent) -> dict[str, Any]:
    existing = _existing_outcome(runtime, event.id)
    if existing:
        return existing
    admission = admit_orchestrator_agent_decision(runtime, event)
    if not admission.admitted:
        payload = {
            "schema_version": "orchestrator-semantic-decision-admission.v1",
            "source_event_id": event.id,
            "operation_id": admission.operation_id,
            "status": "rejected",
            "reason": admission.reason,
        }
        runtime.event_writer.append(ZfEvent(
            type="orchestrator.semantic.decision.rejected",
            actor="zf-cli",
            origin="kernel",
            payload=payload,
            causation_id=event.id,
            correlation_id=event.correlation_id,
        ))
        return payload
    decision = admission.decision or {}
    action = str(decision.get("decision") or "")
    base = {
        "schema_version": "orchestrator-semantic-decision-admission.v1",
        "workflow_run_id": str(
            decision.get("identity", {}).get("workflow_run_id") or ""
        ),
        "operation_id": admission.operation_id,
        "checkpoint": admission.checkpoint,
        "checkpoint_policy": admission.checkpoint_policy,
        "source_event_id": admission.source_event_id,
        "decision_event_id": event.id,
        "decision": action,
        "decision_fingerprint": _decision_fingerprint(decision),
        "reason_codes": list(decision.get("reason_codes") or []),
        "summary": str(decision.get("summary") or ""),
        "explanation_status": str(
            decision.get("explanation_status") or "degraded"
        ),
        "decision_ref": dict(admission.decision_ref or {}),
        "status": "admitted",
    }
    runtime.event_writer.append(ZfEvent(
        type="orchestrator.semantic.decision.admitted",
        actor="zf-cli",
        origin="kernel",
        payload=base,
        causation_id=event.id,
        correlation_id=event.correlation_id,
    ))
    if admission.checkpoint_policy == "shadow":
        shadow = {**base, "status": "shadowed", "applied": False}
        runtime.event_writer.append(ZfEvent(
            type="orchestrator.semantic.decision.shadowed",
            actor="zf-cli",
            origin="kernel",
            payload=shadow,
            causation_id=event.id,
            correlation_id=event.correlation_id,
        ))
        return shadow
    outcome = {**base, "status": "applied", "applied": True}
    if admission.checkpoint in {"run_start", "pre_impl"}:
        outcome = _apply_run_plan(runtime, event, decision, outcome)
    elif admission.checkpoint == "plan_candidate":
        outcome = _apply_plan_candidate(runtime, event, decision, outcome)
    elif admission.checkpoint == "semantic_failure":
        outcome = _apply_semantic_failure(runtime, event, decision, outcome)
    elif admission.checkpoint in {"stage_barrier", "pre_closeout"}:
        from zf.runtime.orchestrator_agent_aggregation import (
            compile_aggregation_admission,
            pause_for_semantic_stop,
        )

        outcome = compile_aggregation_admission(
            runtime,
            event=event,
            decision=decision,
            outcome=outcome,
        )
        if action in {"halt", "escalate", "replan"}:
            outcome = pause_for_semantic_stop(
                runtime,
                event=event,
                outcome=outcome,
            )
    runtime.event_writer.append(ZfEvent(
        type="orchestrator.semantic.decision.applied",
        actor="zf-cli",
        origin="kernel",
        payload=outcome,
        causation_id=event.id,
        correlation_id=event.correlation_id,
    ))
    return outcome


def _apply_run_plan(
    runtime: Any,
    event: ZfEvent,
    decision: Mapping[str, Any],
    outcome: dict[str, Any],
) -> dict[str, Any]:
    action = str(decision.get("decision") or "")
    if action == "adopt":
        from zf.runtime.orchestrator_agent_run_plan import (
            compile_run_plan_admission,
        )

        return compile_run_plan_admission(
            runtime,
            event=event,
            decision=decision,
            outcome=outcome,
        )
    runtime.event_writer.append(ZfEvent(
        type="human.escalate",
        actor="zf-cli",
        origin="kernel",
        payload={
            "reason": (
                "OA run-plan decision requires owner input: " + action
            ),
            "orchestrator_operation_id": outcome.get("operation_id"),
            "orchestrator_decision_ref": outcome.get("decision_ref"),
        },
        causation_id=event.id,
        correlation_id=event.correlation_id,
    ))
    return {**outcome, "status": "escalated", "applied": False}


def _apply_plan_candidate(
    runtime: Any,
    event: ZfEvent,
    decision: Mapping[str, Any],
    outcome: dict[str, Any],
) -> dict[str, Any]:
    action = str(decision.get("decision") or "")
    plan_id = str(outcome.get("source_event_id") or "")
    shared = {
        "plan_id": plan_id,
        "semantic_control": True,
        "orchestrator_operation_id": str(outcome.get("operation_id") or ""),
        "orchestrator_decision_event_id": event.id,
        "orchestrator_decision_ref": dict(outcome.get("decision_ref") or {}),
        "reason_codes": list(outcome.get("reason_codes") or []),
    }
    if action == "adopt":
        if getattr(runtime.config.workflow, "plan_approval_enabled", False):
            return {**outcome, "owner_approval_required": True}
        runtime.event_writer.append(ZfEvent(
            type="plan.approved",
            actor="zf-cli",
            origin="kernel",
            payload=shared,
            causation_id=event.id,
            correlation_id=event.correlation_id,
        ))
        return outcome
    if action == "revise" and not _revision_breaker_open(runtime, decision):
        runtime.event_writer.append(ZfEvent(
            type="plan.rejected",
            actor="zf-cli",
            origin="kernel",
            payload={
                **shared,
                "reason": "; ".join(shared["reason_codes"]) or "OA requested revision",
                "orchestration_delta": dict(decision.get("delta") or {}),
            },
            causation_id=event.id,
            correlation_id=event.correlation_id,
        ))
        return outcome
    runtime.event_writer.append(ZfEvent(
        type="human.escalate",
        actor="zf-cli",
        origin="kernel",
        payload={
            **shared,
            "reason": (
                "OA plan revision breaker opened"
                if action == "revise"
                else f"OA plan decision requires owner input: {action}"
            ),
        },
        causation_id=event.id,
        correlation_id=event.correlation_id,
    ))
    return {**outcome, "status": "escalated", "applied": False}


def _apply_semantic_failure(
    runtime: Any,
    event: ZfEvent,
    decision: Mapping[str, Any],
    outcome: dict[str, Any],
) -> dict[str, Any]:
    action = str(decision.get("decision") or "")
    if action == "continue":
        return outcome
    if action not in {"rework", "rebind", "invalidate", "return_to_plan"}:
        runtime.event_writer.append(ZfEvent(
            type="human.escalate",
            actor="zf-cli",
            origin="kernel",
            payload={
                "reason": f"OA semantic failure decision requires owner input: {action}",
                "orchestrator_operation_id": outcome.get("operation_id"),
                "orchestrator_decision_ref": outcome.get("decision_ref"),
            },
            causation_id=event.id,
            correlation_id=event.correlation_id,
        ))
        return {**outcome, "status": "escalated", "applied": False}
    delta = decision.get("delta")
    directives = delta.get("directives") if isinstance(delta, Mapping) else []
    action_directives = [
        item
        for item in directives if isinstance(item, Mapping)
        and str(item.get("action") or "") == action
    ]
    if not action_directives:
        return _reject_semantic_directive(
            runtime,
            event,
            outcome,
            f"{action}_directive_missing",
        )
    delta_ref = write_immutable_json_sidecar(
        runtime.state_dir,
        dict(delta or {}),
        root="orchestrator-agent/deltas",
        kind="orchestration_delta",
        schema_version="orchestration-delta.v1",
        created_by="orchestrator-agent-admission",
        source_event_id=event.id,
    )
    expected = _operation_checkpoint_context(runtime, outcome)
    allowed_refs = _operation_source_descriptors(runtime, outcome)
    emitted: list[str] = []
    for directive in action_directives:
        target = directive.get("target")
        target = dict(target) if isinstance(target, Mapping) else {}
        rejection = _target_rejection(
            runtime,
            target,
            expected,
            action=action,
        ) or _directive_ref_rejection(directive, allowed_refs)
        if rejection:
            return _reject_semantic_directive(
                runtime,
                event,
                outcome,
                rejection,
                directive_id=str(directive.get("directive_id") or ""),
            )
        if action == "return_to_plan":
            return _apply_return_to_plan(
                runtime,
                event,
                decision=decision,
                outcome=outcome,
                directive=directive,
                delta_ref=delta_ref,
                expected=expected,
            )
        task_id = str(target["task_id"])
        request = runtime.event_writer.append(ZfEvent(
            type="orchestrator.semantic.rework.requested",
            actor="zf-cli",
            origin="kernel",
            task_id=task_id,
            payload={
                "schema_version": "orchestrator-semantic-rework-request.v1",
                "workflow_run_id": str(
                    decision.get("identity", {}).get("workflow_run_id") or ""
                ),
                "task_id": task_id,
                "target_stage_id": str(target["stage_id"]),
                "target_attempt_id": str(target["attempt_id"]),
                "target_role_instance": str(target["role_instance"]),
                "directive_id": str(directive.get("directive_id") or ""),
                "semantic_action": action,
                "failure_fingerprint": str(
                    expected.get("failure_fingerprint") or ""
                ),
                "reason": str(decision.get("required_followup") or ""),
                "required_actions": list(directive.get("required_actions") or []),
                "reuse_refs": list(directive.get("reuse_refs") or []),
                "invalidate_refs": list(directive.get("invalidate_refs") or []),
                "evidence_refs": list(directive.get("basis_refs") or []),
                "task_map_generation": str(
                    decision.get("identity", {}).get("task_map_generation") or ""
                ),
                "plan_artifact_package_ref": str(
                    decision.get("identity", {}).get("plan_artifact_package_ref") or ""
                ),
                "plan_artifact_package_digest": str(
                    decision.get("identity", {}).get("plan_artifact_package_digest") or ""
                ),
                "orchestrator_decision_ref": str(
                    (outcome.get("decision_ref") or {}).get("ref") or ""
                ),
                "orchestrator_decision_digest": str(
                    (outcome.get("decision_ref") or {}).get("sha256") or ""
                ),
                "orchestration_delta_ref": str(delta_ref.get("ref") or ""),
                "orchestration_delta_digest": str(delta_ref.get("sha256") or ""),
            },
            causation_id=event.id,
            correlation_id=event.correlation_id,
        ))
        emitted.append(request.id)
    return {**outcome, "semantic_rework_event_ids": emitted}


def _operation_checkpoint_context(
    runtime: Any,
    outcome: Mapping[str, Any],
) -> dict[str, Any]:
    operation = load_workflow_operation(
        runtime.event_log,
        str(outcome.get("operation_id") or ""),
    )
    request_ref = operation.get("request_ref") if isinstance(operation, Mapping) else None
    if not isinstance(request_ref, Mapping):
        return {}
    stored = hydrate_sidecar_ref(runtime.state_dir, dict(request_ref)).payload
    request = stored.get("request") if isinstance(stored, Mapping) else None
    input_ref = request.get("checkpoint_input_ref") if isinstance(request, Mapping) else None
    if not isinstance(input_ref, Mapping):
        return {}
    checkpoint_input = hydrate_sidecar_ref(runtime.state_dir, dict(input_ref)).payload
    context = (
        checkpoint_input.get("checkpoint_context")
        if isinstance(checkpoint_input, Mapping)
        else None
    )
    return dict(context) if isinstance(context, Mapping) else {}


def _operation_source_descriptors(
    runtime: Any,
    outcome: Mapping[str, Any],
) -> set[tuple[str, str]]:
    operation = load_workflow_operation(
        runtime.event_log,
        str(outcome.get("operation_id") or ""),
    )
    request_ref = operation.get("request_ref") if isinstance(operation, Mapping) else None
    if not isinstance(request_ref, Mapping):
        return set()
    stored = hydrate_sidecar_ref(runtime.state_dir, dict(request_ref)).payload
    request = stored.get("request") if isinstance(stored, Mapping) else None
    manifest_ref = request.get("source_manifest_ref") if isinstance(request, Mapping) else None
    if not isinstance(manifest_ref, Mapping):
        return set()
    manifest = hydrate_sidecar_ref(runtime.state_dir, dict(manifest_ref)).payload
    sources = manifest.get("sources") if isinstance(manifest, Mapping) else None
    return {
        (str(item.get("ref") or ""), str(item.get("sha256") or ""))
        for item in sources if isinstance(item, Mapping)
    } if isinstance(sources, list) else set()


def _directive_ref_rejection(
    directive: Mapping[str, Any],
    allowed_refs: set[tuple[str, str]],
) -> str:
    for field in ("basis_refs", "reuse_refs", "invalidate_refs"):
        values = directive.get(field)
        for descriptor in values if isinstance(values, list) else []:
            if not isinstance(descriptor, Mapping):
                continue
            key = (
                str(descriptor.get("ref") or ""),
                str(descriptor.get("sha256") or ""),
            )
            if key not in allowed_refs:
                return f"directive_ref_outside_operation:{field}:{key[0]}"
    return ""


def _apply_return_to_plan(
    runtime: Any,
    event: ZfEvent,
    *,
    decision: Mapping[str, Any],
    outcome: Mapping[str, Any],
    directive: Mapping[str, Any],
    delta_ref: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    if _semantic_replan_breaker_open(runtime, decision):
        runtime.event_writer.append(ZfEvent(
            type="human.escalate",
            actor="zf-cli",
            origin="kernel",
            payload={
                "reason": "OA semantic replan breaker opened",
                "orchestrator_operation_id": outcome.get("operation_id"),
                "orchestrator_decision_ref": outcome.get("decision_ref"),
            },
            causation_id=event.id,
            correlation_id=event.correlation_id,
        ))
        return {**dict(outcome), "status": "escalated", "applied": False}
    target = directive.get("target")
    target = dict(target) if isinstance(target, Mapping) else {}
    task_id = str(target.get("task_id") or "")
    task = runtime.task_store.get(task_id)
    if task is None:
        return _reject_semantic_directive(
            runtime,
            event,
            outcome,
            "target_task_not_current",
            directive_id=str(directive.get("directive_id") or ""),
        )
    identity = decision.get("identity")
    identity = identity if isinstance(identity, Mapping) else {}
    package_ref = str(identity.get("plan_artifact_package_ref") or "")
    package_digest = str(identity.get("plan_artifact_package_digest") or "")
    from zf.runtime.plan_artifact_package import hydrate_plan_artifact_package

    package = hydrate_plan_artifact_package(
        runtime.state_dir,
        {"ref": package_ref, "sha256": package_digest},
    )
    task_map_ref = next(
        (
            str(port.get("ref") or "")
            for port in [
                *list(package.get("produced") or []),
                *list(package.get("inherited") or []),
            ]
            if isinstance(port, Mapping)
            and str(port.get("logical_name") or "") in {
                "task_map", "task-map", "task_map_json",
            }
        ),
        "",
    )
    workflow_run_id = str(identity.get("workflow_run_id") or "")
    feature_id = str(getattr(getattr(task, "contract", None), "feature_id", "") or "")
    required_actions = [
        str(item) for item in directive.get("required_actions") or []
        if str(item).strip()
    ]
    payload = {
        "schema_version": "orchestrator-semantic-replan-request.v1",
        "workflow_run_id": workflow_run_id,
        "trace_id": workflow_run_id,
        "pdd_id": feature_id,
        "feature_id": feature_id,
        "flow_kind": str(package.get("flow_kind") or ""),
        "task_map_ref": task_map_ref,
        "task_map_generation": str(identity.get("task_map_generation") or ""),
        "plan_artifact_package_ref": package_ref,
        "plan_artifact_package_digest": package_digest,
        "target_ref": _latest_run_target_ref(runtime, workflow_run_id),
        "rework_of": str(outcome.get("source_event_id") or event.id),
        "rework_attempt": int(getattr(task, "retry_count", 0) or 0) + 1,
        "rework_source": "orchestrator.semantic.failure.requested",
        "classification": "oa_semantic_return_to_plan",
        "rework_feedback": required_actions,
        "rework_categories": list(decision.get("reason_codes") or []),
        "rework_summary": {
            "required_actions": required_actions,
            "failure_fingerprint": str(expected.get("failure_fingerprint") or ""),
        },
        "failed_task_ids": [task_id],
        "task_ids": [task_id],
        "resume_scope": "failed_children_only",
        "orchestrator_operation_id": str(outcome.get("operation_id") or ""),
        "orchestrator_decision_ref": dict(outcome.get("decision_ref") or {}),
        "orchestration_delta_ref": str(delta_ref.get("ref") or ""),
        "orchestration_delta_digest": str(delta_ref.get("sha256") or ""),
        "reuse_refs": list(directive.get("reuse_refs") or []),
        "invalidate_refs": list(directive.get("invalidate_refs") or []),
    }
    marker = runtime.event_writer.append(ZfEvent(
        type="orchestrator.replan_requested",
        actor="zf-cli",
        origin="kernel",
        task_id=task_id,
        payload=payload,
        causation_id=event.id,
        correlation_id=event.correlation_id or workflow_run_id,
    ))
    from zf.runtime import replan_resynth

    plan = SimpleNamespace(
        pdd_id=feature_id,
        trace_id=workflow_run_id,
        target_ref=payload["target_ref"],
        source_event_id=payload["rework_of"],
        attempt=payload["rework_attempt"],
        source_event_type=payload["rework_source"],
        feedback=tuple(required_actions),
        failure_categories=tuple(payload["rework_categories"]),
        rework_summary=dict(payload["rework_summary"]),
        classification=payload["classification"],
        failed_task_ids=(task_id,),
        task_ids=(task_id,),
        downstream_task_ids=(),
        resume_scope="failed_children_only",
        flow_kind=payload["flow_kind"],
        task_map_ref=task_map_ref,
        task_map_generation=payload["task_map_generation"],
        plan_artifact_package_ref=package_ref,
        plan_artifact_package_digest=package_digest,
        **replan_resynth.replan_semantic_context(payload),
    )
    resynth = replan_resynth.build_replan_resynth_event(
        plan=plan,
        events=runtime.event_log.read_all(),
        config=runtime.config,
    )
    emitted = [marker.id]
    if resynth is not None:
        emitted.append(replan_resynth.append_replan_resynth_once(runtime.event_writer, runtime.state_dir, resynth).id)
    return {**dict(outcome), "semantic_replan_event_ids": emitted}


def _latest_run_target_ref(runtime: Any, workflow_run_id: str) -> str:
    for event in reversed(runtime.event_log.read_all()):
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        event_run_id = str(
            payload.get("workflow_run_id")
            or payload.get("trace_id")
            or event.correlation_id
            or ""
        )
        if workflow_run_id and event_run_id and event_run_id != workflow_run_id:
            continue
        value = str(
            payload.get("candidate_ref")
            or payload.get("target_ref")
            or payload.get("candidate_head_commit")
            or ""
        )
        if value:
            return value
    git = getattr(getattr(runtime.config, "runtime", None), "git", None)
    return str(getattr(git, "candidate_base_ref", "") or "")


def _target_rejection(
    runtime: Any,
    target: Mapping[str, Any],
    expected: Mapping[str, Any],
    *,
    action: str = "rework",
) -> str:
    comparisons = {
        "task_id": "target_task_id",
        "stage_id": "target_stage_id",
        "attempt_id": "target_attempt_id",
        "role_instance": "target_role_instance",
    }
    for target_key, expected_key in comparisons.items():
        if str(target.get(target_key) or "") != str(expected.get(expected_key) or ""):
            return f"target_snapshot_mismatch:{target_key}"
    task_id = str(target.get("task_id") or "")
    task = runtime.task_store.get(task_id)
    if task is None or str(task.status) in {"done", "cancelled"}:
        return "target_task_not_current"
    active_dispatch_id = str(getattr(task, "active_dispatch_id", "") or "")
    if active_dispatch_id and active_dispatch_id != str(target.get("attempt_id") or ""):
        return "target_attempt_stale"
    role_id = str(target.get("role_instance") or "")
    role = next(
        (
            item for item in runtime.config.roles
            if str(item.instance_id or item.name) == role_id
        ),
        None,
    )
    if role is None:
        return "target_role_missing"
    kind = str(getattr(role, "role_kind", "auto") or "auto")
    if action in {"rework", "rebind", "invalidate", "return_to_plan"} and (
        kind == "reader" or (
        kind == "auto"
        and str(role.name) in {"review", "test", "judge", "verify", "critic"}
        )
    ):
        return "target_role_not_writer"
    if int(getattr(task, "retry_count", 0) or 0) > int(
        getattr(role, "max_rework_attempts", 0) or 0
    ):
        return "target_rework_budget_exhausted"
    return ""


def _semantic_replan_breaker_open(
    runtime: Any,
    decision: Mapping[str, Any],
) -> bool:
    policy = runtime.config.workflow.orchestration
    prior = [
        event
        for event in runtime.event_log.read_all()
        if event.type == "orchestrator.semantic.decision.applied"
        and isinstance(event.payload, dict)
        and event.payload.get("decision") in {"return_to_plan", "replan"}
    ]
    if len(prior) >= int(policy.max_plan_revisions):
        return True
    fingerprint = _decision_fingerprint(decision)
    repeated = sum(
        1
        for event in prior
        if str((event.payload or {}).get("decision_fingerprint") or "")
        == fingerprint
    )
    return repeated >= int(policy.no_progress_limit)


def _reject_semantic_directive(
    runtime: Any,
    event: ZfEvent,
    outcome: Mapping[str, Any],
    reason: str,
    *,
    directive_id: str = "",
) -> dict[str, Any]:
    payload = {
        **dict(outcome),
        "status": "rejected",
        "applied": False,
        "reason": reason,
        "directive_id": directive_id,
    }
    runtime.event_writer.append(ZfEvent(
        type="orchestrator.semantic.rework.rejected",
        actor="zf-cli",
        origin="kernel",
        payload=payload,
        causation_id=event.id,
        correlation_id=event.correlation_id,
    ))
    return payload


def _revision_breaker_open(runtime: Any, decision: Mapping[str, Any]) -> bool:
    policy = runtime.config.workflow.orchestration
    prior = [
        event
        for event in runtime.event_log.read_all()
        if event.type == "orchestrator.semantic.decision.applied"
        and isinstance(event.payload, dict)
        and event.payload.get("checkpoint") == "plan_candidate"
        and event.payload.get("decision") == "revise"
    ]
    if len(prior) >= int(policy.max_plan_revisions):
        return True
    fingerprint = _decision_fingerprint(decision)
    repeated = sum(
        1
        for event in prior
        if str((event.payload or {}).get("decision_fingerprint") or "")
        == fingerprint
    )
    return repeated >= int(policy.no_progress_limit)


def _decision_fingerprint(decision: Mapping[str, Any]) -> str:
    delta = decision.get("delta")
    directives = delta.get("directives") if isinstance(delta, Mapping) else []
    semantic_directives = []
    for directive in directives if isinstance(directives, list) else []:
        if not isinstance(directive, Mapping):
            continue
        target = directive.get("target")
        target = dict(target) if isinstance(target, Mapping) else {}
        semantic_directives.append({
            key: (
                target.get(key.removeprefix("target_"))
                if key.startswith("target_")
                else directive.get(key)
            )
            for key in (
                "action",
                "target_task_id",
                "target_stage_id",
                "target_role_instance",
                "required_actions",
                "reuse_refs",
                "invalidate_refs",
                "evidence_refs",
            )
            if (
                key in directive
                or key.removeprefix("target_") in target
            )
        })
    body = {
        "decision": decision.get("decision"),
        "reason_codes": decision.get("reason_codes") or [],
        "directives": semantic_directives,
    }
    return hashlib.sha256(json.dumps(
        body,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _existing_outcome(runtime: Any, decision_event_id: str) -> dict[str, Any]:
    for event in reversed(runtime.event_log.read_all()):
        if event.type not in {
            "orchestrator.semantic.decision.applied",
            "orchestrator.semantic.decision.shadowed",
            "orchestrator.semantic.decision.rejected",
        }:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if str(payload.get("decision_event_id") or payload.get("source_event_id") or "") == decision_event_id:
            return payload
    return {}


__all__ = ["apply_orchestrator_agent_decision"]
