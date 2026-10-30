"""Bound execution-only OA decisions to existing configuration and Task contracts."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from zf.core.task.store import TaskStore
from zf.runtime.orchestrator_agent_policy import _effective_policy
from zf.runtime.run_contract import stable_json_sha256
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_pipeline_contexts import task_pipeline_generation_contexts
from zf.runtime.workflow_operation import reduce_workflow_operations


EXECUTION_CHECKPOINTS = frozenset({"pre_impl", "semantic_failure"})


def bounded_actions(config: Any, flow_kind: str) -> list[str] | None:
    return getattr(_effective_policy(config, flow_kind), "autonomous_actions", None)


def task_execution_binding(task: Any) -> dict[str, Any]:
    """Heartbeat, cost and timestamps are deliberately outside the authority hash."""
    return {
        "contract_digest": stable_json_sha256(asdict(task.contract)),
        "blocked_by": sorted(task.blocked_by),
        "status": task.status,
        "active_dispatch_id": task.active_dispatch_id or "",
    }


def operation_bindings(events, workflow_run_id: str, selected: set[str]) -> dict[str, Any]:
    stages = {"impl": 1, "verify": 2, "acceptance_review": 3, "integration": 4}
    contexts = task_pipeline_generation_contexts(events)
    rows = [row for row in reduce_workflow_operations(events).values()
            if row.get("workflow_run_id") == workflow_run_id
            and row.get("task_id") in selected and row.get("task_pipeline_stage") in stages
            and (row["task_id"] not in contexts or all(
                row.get(key) == contexts[row["task_id"]].get(key)
                for key in ("workflow_run_id", "task_map_generation")
            ))]
    rows.sort(key=lambda row: (int(row.get("operation_generation") or 0),
                              stages[row["task_pipeline_stage"]]))
    return {row["task_id"]: {key: row.get(key) for key in (
        "operation_id", "task_map_generation", "task_pipeline_stage", "status",
        "active_attempt_id", "operation_generation", "admitted_control_result_ref",
    )} for row in rows}


def build_execution_context(
    runtime: Any, *, workflow_run_id: str, payload: Mapping[str, Any],
) -> dict[str, Any]:
    from zf.runtime.task_pipeline_dispatch_summary import build_dispatch_summary
    from zf.runtime.task_pipeline_projection import read_task_pipeline_projection

    package = hydrate_sidecar_ref(runtime.state_dir, {
        "ref": payload["plan_artifact_package_ref"],
        "sha256": payload["plan_artifact_package_digest"],
    }).payload
    ports = [*package.get("produced", []), *package.get("inherited", [])]
    graph = []
    for port in ports:
        if port.get("logical_name") != "task_map":
            continue
        task_map = hydrate_sidecar_ref(runtime.state_dir, {
            "ref": port["ref"], "sha256": port["sha256"],
        }).payload
        graph = [{key: task.get(key) for key in (
            "task_id", "title", "blocked_by", "owner_role", "allowed_paths",
            "goal_claim_ids",
        )} for task in task_map.get("tasks", [])]
        break
    target = str(payload.get("target_task_id") or "")
    selected = {target} if target else {row["task_id"] for row in graph}
    store = TaskStore(Path(runtime.state_dir) / "kanban.json")
    bindings = {
        task_id: task_execution_binding(task)
        for task_id in sorted(selected)
        if (task := store.get(task_id)) is not None
    }
    projection = read_task_pipeline_projection(
        runtime.state_dir, project_root=runtime.project_root, config=runtime.config,
    )
    return {
        "schema_version": "orchestrator-execution-context.v1",
        "workflow_run_id": workflow_run_id,
        "flow_kind": str(payload.get("flow_kind") or ""),
        "effective_config_ref": dict(payload.get("effective_config_ref") or {}),
        "effective_config_digest": str(
            (payload.get("effective_config_ref") or {}).get("sha256") or ""
            if isinstance(payload.get("effective_config_ref"), Mapping)
            else payload.get("effective_config_digest") or ""
        ),
        "autonomous_actions": bounded_actions(runtime.config, str(payload.get("flow_kind") or "")),
        "task_map_generation": payload.get("task_map_generation", ""),
        "plan_artifact_package_ref": payload["plan_artifact_package_ref"],
        "plan_artifact_package_digest": payload["plan_artifact_package_digest"],
        "task_bindings": bindings,
        "operation_bindings": operation_bindings(runtime.event_log.read_all(), workflow_run_id, selected),
        "plan_tasks": graph,
        "dispatch_summary": build_dispatch_summary(projection, workflow_run_id=workflow_run_id),
        "constraints": {
            "contract_changes_require_replan": True,
            "verify_required": True,
            "dependency_override": False,
            "capacity_is_live_not_reserved": True,
        },
    }


def execution_decision_rejection(
    runtime: Any, *, decision: Mapping[str, Any], request: Mapping[str, Any],
) -> str:
    """Check an opt-in authority snapshot; semantic reasons remain agent-owned."""
    checkpoint = str(request.get("checkpoint") or "")
    if checkpoint not in EXECUTION_CHECKPOINTS:
        return ""
    input_body = hydrate_sidecar_ref(runtime.state_dir, request["checkpoint_input_ref"]).payload
    descriptor = input_body.get("execution_context_ref")
    flow = str(input_body.get("flow_kind") or "")
    configured = bounded_actions(runtime.config, flow)
    if descriptor is None:
        return "execution_authority_missing" if configured is not None else ""
    context = hydrate_sidecar_ref(runtime.state_dir, descriptor).payload
    expected_config_ref = request.get("effective_config_ref")
    expected_config_ref = (
        expected_config_ref if isinstance(expected_config_ref, Mapping) else {}
    )
    context_config_ref = context.get("effective_config_ref")
    context_config_ref = (
        context_config_ref if isinstance(context_config_ref, Mapping) else {}
    )
    if (
        str(context_config_ref.get("ref") or "")
        != str(expected_config_ref.get("ref") or "")
        or str(context_config_ref.get("sha256") or "")
        != str(expected_config_ref.get("sha256") or "")
        or str(context.get("effective_config_digest") or "")
        != str(request.get("effective_config_digest") or "")
    ):
        return "execution_authority_changed"
    if context.get("autonomous_actions") != configured:
        return "execution_authority_changed"
    action = str(decision.get("decision") or "")
    # These routes do not grant direct execution or weaken an approval gate.
    if action in {"return_to_plan", "escalate", "halt", "clarify", "block", "revise"}:
        return ""
    permission = "delegate" if checkpoint == "pre_impl" and action == "adopt" else action
    if permission not in (configured or []):
        return "execution_action_not_authorized:" + permission
    if checkpoint == "pre_impl":
        ids = {row["task_id"] for row in context.get("plan_tasks", [])}
        if not ids:
            return "execution_task_map_missing"
        units = (decision.get("run_plan") or {}).get("graph", {}).get("work_units", [])
        selected: set[str] = set()
        for unit in units:
            unit_ids = set(unit.get("task_ids") or [])
            if not unit_ids and not unit.get("stage_ids"):
                unit_ids = {str(unit.get("work_unit_id") or "")}
            if unit_ids - ids:
                return "execution_task_outside_plan"
            selected.update(unit_ids or ids)
    else:
        selected = set(context.get("task_bindings", {}))
        if not selected:
            return "execution_target_missing"
        for directive in (decision.get("delta") or {}).get("directives", []):
            if directive.get("action") != action:
                return "execution_mixed_actions"
            if str(directive.get("target", {}).get("task_id") or "") not in selected:
                return "execution_target_outside_snapshot"
            if directive.get("invalidate_refs"):
                return "execution_invalidation_requires_replan"
    if checkpoint == "semantic_failure":
        expected_ops = context.get("operation_bindings", {})
        current_ops = operation_bindings(runtime.event_log.read_all(), context["workflow_run_id"], selected)
        if current_ops != expected_ops:
            return "execution_operation_changed"
        if any(row.get("status") in {"requested", "reserved", "running", "suspended"}
               for row in current_ops.values()):
            return "execution_operation_active"
    store = TaskStore(Path(runtime.state_dir) / "kanban.json")
    for task_id in selected:
        expected = context.get("task_bindings", {}).get(task_id)
        if expected is None:  # pre_impl can precede canonical Task materialization.
            continue
        task = store.get(task_id)
        if task is None or task_execution_binding(task) != expected:
            return "execution_target_changed:" + task_id
    return ""
