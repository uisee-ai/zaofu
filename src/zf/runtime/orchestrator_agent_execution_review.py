"""Run Manager intents for opt-in, current, settled Impl semantic blockers."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from zf.core.task.store import TaskStore
from zf.runtime.orchestrator_agent_execution_policy import operation_bindings
from zf.runtime.orchestrator_agent_policy import _effective_policy, checkpoint_policy
from zf.runtime.run_contract import stable_json_sha256
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_pipeline_contexts import task_pipeline_generation_contexts
from zf.runtime.task_pipeline_execution_status import normalize_task_pipeline_operation_semantics
from zf.runtime.workflow_operation import reduce_workflow_operations


def pending_execution_reviews(
    *, state_dir: Path, config: Any, events: list,
    excluded_task_ids: set[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Observe a failure, never fabricate an exhaustion or dispatch a worker."""
    from zf.runtime.run_manager import build_run_goal_projection

    policies = [config.workflow.orchestration, *config.workflow.orchestration.flow_policies.values()]
    if not any(getattr(policy, "review_blocked_tasks", False) for policy in policies):
        return []
    if not any(role.name == "orchestrator" for role in config.roles):
        return []
    contexts = task_pipeline_generation_contexts(events)
    operations = normalize_task_pipeline_operation_semantics(
        state_dir, reduce_workflow_operations(events).values(),
    )
    store = TaskStore(state_dir / "kanban.json")
    actions = []
    goals = {}
    latest_by_run = {}
    by_id = {event.id: event for event in events}
    for task_id, context in contexts.items():
        if task_id in excluded_task_ids:
            continue
        flow = str(context.get("flow_kind") or "")
        policy = _effective_policy(config, flow)
        if (not getattr(policy, "review_blocked_tasks", False)
                or getattr(policy, "autonomous_actions", None) is None
                or not checkpoint_policy(config, "semantic_failure", flow_kind=flow)):
            continue
        run_id = str(context.get("workflow_run_id") or "")
        if run_id not in goals:
            goals[run_id] = build_run_goal_projection(events, run_id=run_id)
            latest_by_run[run_id] = operation_bindings(events, run_id, set(contexts))
        goal = goals[run_id]
        if goal.get("status") not in {"active", "running"}:
            continue
        task = store.get(task_id)
        if task is None or task.status in {"done", "cancelled"}:
            continue
        rows = [row for row in operations if row.get("task_id") == task_id
                and row.get("workflow_run_id") == run_id
                and row.get("task_map_generation") == context.get("task_map_generation")]
        if any(row.get("status") in {"requested", "reserved", "running", "suspended"} for row in rows):
            continue
        impls = [row for row in rows if row.get("task_pipeline_stage") == "impl"]
        impl = max(impls, key=lambda row: int(row.get("operation_generation") or 0), default={})
        if impl.get("status") != "settled" or impl.get("semantic_verdict") not in {"failed", "blocked", "rejected"}:
            continue
        latest = latest_by_run[run_id].get(task_id, {})
        if latest.get("operation_id") != impl.get("operation_id"):
            continue
        result_ref = impl.get("admitted_control_result_ref")
        if not isinstance(result_ref, dict):
            continue
        try:
            result = hydrate_sidecar_ref(state_dir, result_ref).payload
        except (OSError, ValueError):
            continue
        # Environment/provider/transport faults stay with their existing recovery owner.
        if str(result.get("failure_class") or "none") not in {"none", "semantic", "product"}:
            continue
        source_id = str(impl.get("call_result_admitted_event_id") or "")
        if source_id not in by_id:
            continue
        request_id = "oa-execution-review-" + stable_json_sha256({
            "run": run_id, "generation": context.get("task_map_generation"),
            "operation": impl["operation_id"], "result": result_ref,
        })[:24]
        if any(
            event.type in {"orchestrator.semantic.failure.requested", "orchestrator.rework.triage.requested"}
            and (event.payload.get("request_id") == request_id or (
                event.payload.get("workflow_run_id") == run_id
                and source_id in event.payload.get("failure_event_ids", [])
            )) for event in events
        ):
            continue
        actions.append({
            "schema_version": "run-manager.pending-action.v1",
            "action": "orchestrator-rework-triage",
            "checkpoint_id": request_id, "request_id": request_id,
            "safe_resume_action": "request_orchestrator_triage",
            "task_id": task_id, "workflow_run_id": run_id, "flow_kind": flow,
            "stage_id": "impl", "target_stage_id": "impl",
            "target_operation_id": impl["operation_id"],
            "task_map_generation": context.get("task_map_generation", ""),
            "role": impl.get("role_instance", ""),
            "fingerprint": request_id, "failure_count": 1,
            "failure_class": "settled_impl_requires_semantic_review",
            "source_event_id": source_id,
            "source_event_type": by_id[source_id].type,
            "source_event_ids": [source_id], "failure_event_ids": [source_id],
            "owner_route": "run_manager", "action_policy": "auto_decide",
            "intervention_class": "semantic_replan", "recovery_scope": "task",
            "summary": "Current Impl settled without passing; classify exact failure before retry or local replan.",
            "expected_downstream_events": ["orchestrator.semantic.failure.requested"],
            "verify_condition": "expected_downstream_event:orchestrator.semantic.failure.requested",
        })
    return actions
