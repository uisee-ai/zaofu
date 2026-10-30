"""Replayable observability projection for Task Pipeline v4."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import asdict, is_dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable, Mapping

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.state.task_attempts import TaskAttemptStore
from zf.core.task.store import TaskStore
from zf.runtime.task_pipeline_contexts import task_pipeline_generation_contexts
from zf.runtime.task_pipeline_execution_status import (
    attach_execution_status, normalize_task_pipeline_operation_semantics,
)
from zf.runtime.task_pipeline_reconciler import (
    TaskPipelineReconciler,
    task_pipeline_policy_partitions,
)
from zf.runtime.workflow_operation import reduce_workflow_operations


TASK_PIPELINE_PROJECTION_SCHEMA = "task-pipeline-projection.v1"
_ACTIVE_OPERATION_STATUSES = frozenset({
    "requested", "reserved", "running", "suspended",
})
_ACTIVE_ATTEMPT_STATUSES = frozenset({"prepared", "delivering", "sent"})
_TERMINAL_TASK_STATUSES = frozenset({"done", "cancelled"})
_STAGE_ORDER = {"impl": 1, "verify": 2, "acceptance_review": 3, "integration": 4}


def read_task_pipeline_projection(
    state_dir: Path,
    *,
    project_root: Path,
    config: Any,
) -> dict[str, Any]:
    """Derive the current view exclusively from replayable runtime truth."""

    state_dir = Path(state_dir)
    events = EventLog(state_dir / "events.jsonl").read_all()
    task_store = TaskStore(state_dir / "kanban.json")
    attempts = TaskAttemptStore(state_dir / "task_attempts.json").current_rows()
    bindings = RoleSessionRegistry(
        state_dir / "role_sessions.yaml",
        project_root=str(project_root),
    ).task_stage_bindings()
    contexts = task_pipeline_generation_contexts(events)
    tasks = [
        task
        for task_id in sorted(contexts)
        if (task := task_store.get(task_id)) is not None
    ]
    partitions = task_pipeline_policy_partitions(
        config,
        contexts,
        task_ids=contexts,
    )
    policy_by_task = {
        task_id: partition["policy"]
        for partition in partitions
        for task_id in partition["task_ids"]
    }
    from zf.runtime.task_pipeline_entry import (
        task_pipeline_satisfied_external_gate_ids,
    )

    satisfied_external_gate_ids = task_pipeline_satisfied_external_gate_ids(
        events=events,
        tasks=tasks,
        generation_contexts=contexts,
        project_root=Path(project_root),
    )
    from zf.runtime.task_pipeline_orchestration import admitted_pipeline_delegations
    from zf.runtime.task_pipeline_affinity import task_pipeline_preferred_roles

    reader = SimpleNamespace(
        state_dir=state_dir, project_root=project_root, config=config,
        event_log=SimpleNamespace(read_all=lambda: events),
    )
    delegations = {}
    for partition in partitions:
        delegations.update(admitted_pipeline_delegations(
            reader,
            generation_contexts={key: contexts[key] for key in partition["task_ids"]},
            policy=partition["policy"],
        ))
    return build_task_pipeline_projection(
        policy=None,
        oa_delegations=delegations,
        preferred_role_instances=task_pipeline_preferred_roles(
            reader, generation_contexts=contexts,
        ),
        policy_by_task=policy_by_task,
        tasks=tasks,
        events=events,
        attempts=attempts,
        session_bindings=bindings,
        external_gate_satisfied_task_ids=satisfied_external_gate_ids,
        state_dir=state_dir,
    )


def build_task_pipeline_projection(
    *,
    policy: Mapping[str, Any] | None,
    policy_by_task: Mapping[str, Mapping[str, Any]] | None = None,
    tasks: Iterable[Any],
    events: Iterable[ZfEvent],
    attempts: Iterable[Mapping[str, Any]] = (),
    session_bindings: Mapping[str, Mapping[str, Any]]
    | Iterable[Mapping[str, Any]] = (),
    external_gate_satisfied_task_ids: Iterable[str] = (),
    oa_delegations: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None,
    preferred_role_instances: Mapping[str, Mapping[str, str]] | None = None,
    state_dir: Path | None = None,
) -> dict[str, Any]:
    """Build a deterministic projection suitable for live and archive replay."""

    event_rows = list(events)
    external_gate_satisfied_ids = {
        str(task_id).strip()
        for task_id in external_gate_satisfied_task_ids
        if str(task_id).strip()
    }
    contexts = task_pipeline_generation_contexts(event_rows)
    task_by_id = _task_by_id_prefer_active(tasks)
    managed_tasks = [
        task_by_id[task_id]
        for task_id in sorted(contexts)
        if task_id in task_by_id
    ]
    operation_rows = [
        dict(row)
        for row in reduce_workflow_operations(event_rows).values()
        if _matches_current_generation(row, contexts)
    ]
    if state_dir is not None:
        operation_rows = normalize_task_pipeline_operation_semantics(
            state_dir,
            operation_rows,
        )
    attempt_rows = [
        dict(row)
        for row in attempts
        if _attempt_matches_current_generation(row, contexts)
    ]
    binding_rows = _normalize_bindings(session_bindings)
    binding_rows = [
        row for row in binding_rows
        if _binding_matches_current_generation(row, contexts)
    ]

    compiled_policy = dict(policy or {})
    compiled_policy_by_task = {
        str(task_id): dict(task_policy)
        for task_id, task_policy in (policy_by_task or {}).items()
        if str(task_id).strip() and isinstance(task_policy, Mapping)
    }
    from zf.runtime.task_pipeline_rework import derive_impl_rework_requests

    impl_rework_requests = derive_impl_rework_requests(
        events=event_rows,
        generation_contexts=contexts,
        operation_rows=operation_rows,
    )
    policy_partitions: list[dict[str, Any]] = []
    if compiled_policy_by_task:
        scheduler, policy_partitions = _partitioned_scheduler_projection(
            policy_by_task=compiled_policy_by_task,
            tasks=managed_tasks,
            operations=operation_rows,
            attempts=attempt_rows,
            impl_rework_requests=impl_rework_requests,
            external_gate_satisfied_task_ids=external_gate_satisfied_ids,
            oa_delegations=oa_delegations,
            preferred_role_instances=preferred_role_instances,
        )
    else:
        scheduler = _scheduler_projection(
            policy=compiled_policy,
            tasks=managed_tasks,
            operations=operation_rows,
            attempts=attempt_rows,
            impl_rework_requests=impl_rework_requests,
            external_gate_satisfied_task_ids=external_gate_satisfied_ids,
            oa_delegations=oa_delegations,
            preferred_role_instances=preferred_role_instances,
        )
    selected_policies = [
        dict(row["policy"])
        for row in policy_partitions
        if isinstance(row.get("policy"), Mapping)
    ]
    projection_mode = str(compiled_policy.get("mode") or "disabled")
    projection_profile_id = str(compiled_policy.get("profile_id") or "")
    projection_profile_digest = str(compiled_policy.get("profile_digest") or "")
    if selected_policies:
        projection_mode = (
            str(selected_policies[0].get("mode") or "disabled")
            if len(selected_policies) == 1
            else "mixed"
        )
        projection_profile_id = (
            str(selected_policies[0].get("profile_id") or "")
            if len(selected_policies) == 1
            else "multiple"
        )
        projection_profile_digest = (
            str(selected_policies[0].get("profile_digest") or "")
            if len(selected_policies) == 1
            else ""
        )
    scheduler_views = {
        str(row.get("task_id") or ""): row
        for row in scheduler.get("tasks", [])
    }
    projected_tasks = [
        _project_task(
            task,
            context=contexts[task_id],
            scheduler_view=scheduler_views.get(task_id, {}),
            operations=operation_rows,
            attempts=attempt_rows,
            bindings=binding_rows,
        )
        for task_id in sorted(contexts)
        if (task := task_by_id.get(task_id)) is not None
    ]
    projected_operations = sorted(
        (_project_operation(row, attempt_rows) for row in operation_rows),
        key=_projected_operation_order,
    )
    attach_execution_status(projected_tasks, projected_operations, event_rows)
    projected_attempts = sorted(
        (_project_attempt(row) for row in attempt_rows),
        key=lambda row: str(row.get("attempt_id") or ""),
    )
    projected_bindings = sorted(
        (_project_binding(row) for row in binding_rows),
        key=lambda row: str(row.get("binding_key") or ""),
    )
    generations = _project_generations(
        contexts=contexts,
        events=event_rows,
        tasks=projected_tasks,
    )
    closure = _closure(
        projected_tasks,
        projected_operations,
        projected_attempts,
        projected_bindings,
        generations,
    )
    from zf.runtime.task_pipeline_recovery import (
        project_task_pipeline_recovery,
    )

    recovery = project_task_pipeline_recovery(
        events=event_rows,
        attempts=attempt_rows,
    )
    projection: dict[str, Any] = {
        "schema_version": TASK_PIPELINE_PROJECTION_SCHEMA,
        "is_derived_projection": True,
        "enabled": bool(compiled_policy or compiled_policy_by_task),
        "mode": projection_mode,
        "profile_id": projection_profile_id,
        "profile_digest": projection_profile_digest,
        "policy_partitions": [
            {
                key: value
                for key, value in row.items()
                if key != "policy"
            }
            for row in policy_partitions
        ],
        "authority": {
            "task": "TaskStore",
            "operation": "EventLog/workflow.operation.*",
            "attempt": "TaskAttemptStore",
            "worker": "WorkflowOperation.role_instance",
            "session": "RoleSessionRegistry.task_stage_bindings",
            "worker_inference_forbidden": ["Task.assigned_to", "lane_id"],
        },
        "summary": {
            "generation_count": len(generations),
            "task_count": len(projected_tasks),
            "task_statuses": _counts(projected_tasks, "task_status"),
            "operation_count": len(projected_operations),
            "operation_statuses": _counts(projected_operations, "status"),
            "active_worker_count": len({
                str(row.get("current_worker") or "")
                for row in projected_tasks
                if str(row.get("current_worker") or "")
            }),
            "session_statuses": _counts(projected_bindings, "status"),
            "terminal_convergence": str(closure["status"]),
        },
        "generations": generations,
        "tasks": projected_tasks,
        "operations": projected_operations,
        "attempts": projected_attempts,
        "sessions": projected_bindings,
        "queues": dict(scheduler.get("queues") or {}),
        "occupancy": dict(scheduler.get("occupancy") or {}),
        "capacity": dict(scheduler.get("capacity") or {}),
        "backpressure": dict(scheduler.get("backpressure") or {}),
        "dispatchable": dict(scheduler.get("dispatchable") or {}),
        "fairness": dict(scheduler.get("fairness") or {}),
        "closure": closure,
        "recovery": recovery,
        "compatibility": {
            "legacy_lane_view": "placement_history_only",
            "current_worker_source": "workflow_operation",
        },
    }
    projection["projection_digest"] = _digest(projection)
    return projection


def write_task_pipeline_projection(runtime: Any) -> Path | None:
    projection = read_task_pipeline_projection(
        Path(runtime.state_dir),
        project_root=Path(runtime.project_root),
        config=runtime.config,
    )
    if not projection.get("enabled") and not projection.get("tasks"):
        return None
    path = Path(runtime.state_dir) / "projections" / "task-pipeline.json"
    atomic_write_text(
        path,
        json.dumps(projection, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
    )
    return path


def _scheduler_projection(
    *,
    policy: Mapping[str, Any],
    tasks: list[Any],
    operations: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
    impl_rework_requests: Mapping[str, Mapping[str, Any]] | None = None,
    external_gate_satisfied_task_ids: Iterable[str] = (),
    oa_delegations: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None,
    preferred_role_instances: Mapping[str, Mapping[str, str]] | None = None,
) -> dict[str, Any]:
    if not policy:
        return {}
    terminal_ids = {
        str(getattr(task, "id", "") or _mapping_value(task, "id"))
        for task in tasks
        if str(getattr(task, "status", "") or _mapping_value(task, "status"))
        == "done"
    }
    return TaskPipelineReconciler().reconcile(
        policy=policy,
        tasks=tasks,
        operations=operations,
        attempts=attempts,
        terminal_task_ids=terminal_ids,
        impl_rework_requests=impl_rework_requests,
        external_gate_satisfied_task_ids=external_gate_satisfied_task_ids,
        oa_delegations=oa_delegations,
        preferred_role_instances=preferred_role_instances,
    )


def _task_by_id_prefer_active(tasks: Iterable[Any]) -> dict[str, Any]:
    """Keep a current non-terminal Task over an older archived duplicate."""

    rows: dict[str, Any] = {}
    for task in tasks:
        task_id = str(
            getattr(task, "id", "") or _mapping_value(task, "id")
        ).strip()
        if not task_id:
            continue
        existing = rows.get(task_id)
        if existing is None:
            rows[task_id] = task
            continue
        existing_status = str(
            getattr(existing, "status", "")
            or _mapping_value(existing, "status")
        )
        status = str(
            getattr(task, "status", "") or _mapping_value(task, "status")
        )
        if (
            existing_status in _TERMINAL_TASK_STATUSES
            and status not in _TERMINAL_TASK_STATUSES
        ):
            rows[task_id] = task
    return rows


def _partitioned_scheduler_projection(
    *,
    policy_by_task: Mapping[str, Mapping[str, Any]],
    tasks: list[Any],
    operations: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
    impl_rework_requests: Mapping[str, Mapping[str, Any]],
    external_gate_satisfied_task_ids: Iterable[str] = (),
    oa_delegations: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None,
    preferred_role_instances: Mapping[str, Mapping[str, str]] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    task_by_id = {
        str(getattr(task, "id", "") or _mapping_value(task, "id")): task
        for task in tasks
    }
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for task_id, task_policy in sorted(policy_by_task.items()):
        key = (
            str(task_policy.get("profile_id") or ""),
            str(task_policy.get("profile_digest") or ""),
            str(task_policy.get("mode") or ""),
        )
        group = grouped.setdefault(key, {
            "policy": dict(task_policy),
            "task_ids": [],
        })
        if task_id in task_by_id:
            group["task_ids"].append(task_id)

    partitions: list[dict[str, Any]] = []
    schedulers: list[dict[str, Any]] = []
    for key in sorted(grouped):
        group = grouped[key]
        task_ids = tuple(sorted(group["task_ids"]))
        if not task_ids:
            continue
        task_id_set = set(task_ids)
        task_policy = dict(group["policy"])
        scheduler = _scheduler_projection(
            policy=task_policy,
            oa_delegations=oa_delegations,
            preferred_role_instances=preferred_role_instances,
            tasks=[task_by_id[task_id] for task_id in task_ids],
            operations=operations,
            attempts=attempts,
            impl_rework_requests={
                task_id: request
                for task_id, request in impl_rework_requests.items()
                if task_id in task_id_set
            },
            external_gate_satisfied_task_ids=(
                task_id_set.intersection(external_gate_satisfied_task_ids)
            ),
        )
        schedulers.append(scheduler)
        partitions.append({
            "profile_id": key[0],
            "profile_digest": key[1],
            "mode": key[2],
            "task_ids": list(task_ids),
            "policy": task_policy,
            "queues": dict(scheduler.get("queues") or {}),
            "occupancy": dict(scheduler.get("occupancy") or {}),
            "capacity": dict(scheduler.get("capacity") or {}),
            "backpressure": dict(scheduler.get("backpressure") or {}),
            "dispatchable": dict(scheduler.get("dispatchable") or {}),
        })
    return _merge_scheduler_projections(schedulers), partitions


def _merge_scheduler_projections(
    schedulers: list[dict[str, Any]],
) -> dict[str, Any]:
    if not schedulers:
        return {}
    queue_names = sorted({
        name
        for scheduler in schedulers
        for name in (scheduler.get("queues") or {})
    })
    stage_names = sorted({
        name
        for scheduler in schedulers
        for name in (scheduler.get("dispatchable") or {})
    })
    task_rows = sorted(
        [
            dict(row)
            for scheduler in schedulers
            for row in scheduler.get("tasks", [])
            if isinstance(row, Mapping)
        ],
        key=lambda row: str(row.get("task_id") or ""),
    )
    active_task_ids = sorted({
        str(task_id)
        for scheduler in schedulers
        for task_id in (
            (scheduler.get("occupancy") or {}).get("active_task_ids") or []
        )
        if str(task_id)
    })
    occupancy_pools: dict[str, int] = {}
    capacity_pools: dict[str, dict[str, int]] = {}
    max_active = 0
    active_attempts = 0
    backpressure_counts = {
        "unverified_count": 0,
        "integration_queue_count": 0,
    }
    backpressure_limits: dict[str, int] = {}
    for scheduler in schedulers:
        occupancy = scheduler.get("occupancy") or {}
        capacity = scheduler.get("capacity") or {}
        max_active += int(capacity.get("max_active_task_pipelines") or 0)
        active_attempts += int(occupancy.get("active_attempts") or 0)
        for name, value in (occupancy.get("pools") or {}).items():
            occupancy_pools[str(name)] = occupancy_pools.get(str(name), 0) + int(value or 0)
        for name, value in (capacity.get("pools") or {}).items():
            if not isinstance(value, Mapping):
                continue
            entry = capacity_pools.setdefault(str(name), {"capacity": 0, "available": 0})
            entry["capacity"] += int(value.get("capacity") or 0)
            entry["available"] += int(value.get("available") or 0)
        backpressure = scheduler.get("backpressure") or {}
        for name in backpressure_counts:
            backpressure_counts[name] += int(backpressure.get(name) or 0)
        for name, value in backpressure.items():
            if name.endswith("limit"):
                backpressure_limits[name] = backpressure_limits.get(name, 0) + int(value or 0)
    for name, entry in capacity_pools.items():
        # Recalculate from merged occupancy so the explanation remains
        # truthful even if one partition omitted an available field.
        entry["available"] = max(0, entry["capacity"] - occupancy_pools.get(name, 0))
    return {
        "tasks": task_rows,
        "queues": {
            name: sorted({
                str(task_id)
                for scheduler in schedulers
                for task_id in (scheduler.get("queues") or {}).get(name, [])
                if str(task_id)
            })
            for name in queue_names
        },
        "dispatchable": {
            name: [
                dict(row)
                for scheduler in schedulers
                for row in (scheduler.get("dispatchable") or {}).get(name, [])
                if isinstance(row, Mapping)
            ]
            for name in stage_names
        },
        "occupancy": {
            "active_task_pipelines": len(active_task_ids),
            "active_task_ids": active_task_ids,
            "active_attempts": active_attempts,
            "pools": occupancy_pools,
            "policy_partitioned": True,
        },
        "capacity": {
            "max_active_task_pipelines": max_active,
            "available_task_pipelines": max(0, max_active - len(active_task_ids)),
            "pools": capacity_pools,
            "policy_partitioned": True,
        },
        "backpressure": {
            **backpressure_counts,
            **backpressure_limits,
            "unverified_limit_reached": any(
                bool((scheduler.get("backpressure") or {}).get("unverified_limit_reached"))
                for scheduler in schedulers
            ),
            "integration_limit_reached": any(
                bool((scheduler.get("backpressure") or {}).get("integration_limit_reached"))
                for scheduler in schedulers
            ),
            "policy_partitioned": True,
        },
        "fairness": {
            "ordering": "policy_partition,priority,created_at,task_id",
            "ordered_task_ids": [row["task_id"] for row in task_rows],
        },
    }


def _project_task(
    task: Any,
    *,
    context: Mapping[str, Any],
    scheduler_view: Mapping[str, Any],
    operations: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
    bindings: list[dict[str, Any]],
) -> dict[str, Any]:
    raw = asdict(task) if is_dataclass(task) else dict(task)
    task_id = str(raw.get("id") or "")
    task_operations = [
        row for row in operations if str(row.get("task_id") or "") == task_id
    ]
    active = [
        row for row in task_operations
        if str(row.get("status") or "") in _ACTIVE_OPERATION_STATUSES
    ]
    current = max(active, key=_operation_order, default=None)
    contract = raw.get("contract")
    if is_dataclass(contract):
        contract = asdict(contract)
    if not isinstance(contract, Mapping):
        contract = {}
    status = str(raw.get("status") or "")
    stage = str(scheduler_view.get("stage") or "")
    if status in _TERMINAL_TASK_STATUSES:
        stage = status
    task_attempts = [
        row for row in attempts if str(row.get("task_id") or "") == task_id
    ]
    task_bindings = [
        row for row in bindings if str(row.get("task_id") or "") == task_id
    ]
    return {
        "task_id": task_id,
        "title": str(raw.get("title") or ""),
        "task_status": status,
        "pipeline_stage": stage or "unknown",
        "workflow_run_id": str(context.get("workflow_run_id") or ""),
        "flow_kind": str(context.get("flow_kind") or ""),
        "task_map_generation": str(context.get("task_map_generation") or ""),
        "generation_id": str(context.get("generation_id") or ""),
        "blocked_by": sorted(str(item) for item in raw.get("blocked_by", [])),
        "blockers": list(scheduler_view.get("blockers") or []),
        "risk_class": str(contract.get("risk_class") or ""),
        "integration_admission_profile": str(
            contract.get("integration_admission_profile") or ""
        ),
        "current_operation_id": str(
            (current or {}).get("operation_id") or ""
        ),
        "current_stage": str(
            (current or {}).get("task_pipeline_stage") or ""
        ),
        "current_worker": str(
            (current or {}).get("role_instance") or ""
        ),
        "current_worker_source": "workflow_operation" if current else "",
        "active_operation_count": len(active),
        "active_attempt_count": sum(
            str(row.get("status") or "") in _ACTIVE_ATTEMPT_STATUSES
            for row in task_attempts
        ),
        "active_session_count": sum(
            str(row.get("status") or "") == "active"
            for row in task_bindings
        ),
        "operation_ids": sorted(
            str(row.get("operation_id") or "") for row in task_operations
        ),
        "session_binding_keys": sorted(
            str(row.get("binding_key") or "") for row in task_bindings
        ),
    }


def _project_operation(
    row: Mapping[str, Any],
    attempts: list[dict[str, Any]],
) -> dict[str, Any]:
    operation_id = str(row.get("operation_id") or "")
    active = str(row.get("status") or "") in _ACTIVE_OPERATION_STATUSES
    matching_attempts = [
        attempt for attempt in attempts
        if str(attempt.get("operation_id") or "") == operation_id
    ]
    return {
        "operation_id": operation_id,
        "workflow_run_id": str(row.get("workflow_run_id") or ""),
        "task_id": str(row.get("task_id") or ""),
        "stage": str(row.get("task_pipeline_stage") or ""),
        "operation_generation": int(row.get("operation_generation") or 0),
        "task_map_generation": str(row.get("task_map_generation") or ""),
        "workspace_generation": int(row.get("workspace_generation") or 0),
        "status": str(row.get("status") or ""),
        "semantic_verdict": str(row.get("semantic_verdict") or ""),
        "control_result_ref": dict(row.get("admitted_control_result_ref") or {}),
        "role_instance": str(row.get("role_instance") or ""),
        "current_worker": str(row.get("role_instance") or "") if active else "",
        "current_worker_source": "workflow_operation" if active else "",
        "placement_epoch": int(row.get("placement_epoch") or 0),
        "session_binding_key": str(
            row.get("task_stage_session_binding") or ""
        ),
        "active_attempt_id": str(row.get("active_attempt_id") or ""),
        "attempt_ids": sorted(
            str(attempt.get("attempt_id") or "")
            for attempt in matching_attempts
        ),
        "last_event_id": str(row.get("last_event_id") or ""),
        "last_event_at": str(row.get("last_event_at") or ""),
        "reason": str(row.get("reason") or ""),
    }


def _project_attempt(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: row.get(key)
        for key in (
            "attempt_id", "lease_id", "run_id", "task_id", "operation_id",
            "identity_version", "dispatch_id", "role", "instance_id",
            "placement_epoch", "ordinal", "series", "status", "updated_at",
            "lease_expires_at", "terminal_event_id", "failure_class",
            "failure_reason", "recovery_owner", "superseded_by",
        )
    }


def _project_binding(row: Mapping[str, Any]) -> dict[str, Any]:
    history = [
        {
            "placement_epoch": int(item.get("placement_epoch") or 0),
            "role_instance": str(item.get("role_instance") or ""),
            "workspace_generation": int(
                item.get("workspace_generation") or 0
            ),
            "bound_at": str(item.get("bound_at") or ""),
        }
        for item in row.get("placement_history", [])
        if isinstance(item, Mapping)
    ]
    return {
        "binding_key": str(row.get("binding_key") or ""),
        "workflow_run_id": str(row.get("workflow_run_id") or ""),
        "task_id": str(row.get("task_id") or ""),
        "stage": str(row.get("stage") or ""),
        "rework_affinity_id": str(row.get("rework_affinity_id") or ""),
        "session_id": str(row.get("session_id") or ""),
        "status": str(row.get("status") or ""),
        "current_role_instance": str(
            row.get("current_role_instance") or ""
        ),
        "current_placement_epoch": int(
            row.get("current_placement_epoch") or 0
        ),
        "workspace_generation": int(row.get("workspace_generation") or 0),
        "placement_history": history,
        "sealed_at": str(row.get("sealed_at") or ""),
        "archived_at": str(row.get("archived_at") or ""),
    }


def _project_generations(
    *,
    contexts: Mapping[str, Mapping[str, Any]],
    events: list[ZfEvent],
    tasks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for task_id, context in contexts.items():
        generation_id = str(context.get("generation_id") or "")
        row = grouped.setdefault(generation_id, {
            "generation_id": generation_id,
            "workflow_run_id": str(context.get("workflow_run_id") or ""),
            "task_map_generation": str(
                context.get("task_map_generation") or ""
            ),
            "dispatch_base_commit": str(
                context.get("dispatch_base_commit") or ""
            ),
            "task_ids": [],
            "candidate_freeze": {},
        })
        row["task_ids"].append(task_id)
    task_status = {row["task_id"]: row["task_status"] for row in tasks}
    for row in grouped.values():
        row["task_ids"] = sorted(set(row["task_ids"]))
        row["task_statuses"] = {
            task_id: task_status.get(task_id, "missing")
            for task_id in row["task_ids"]
        }
        for event in reversed(events):
            if event.type != "candidate.ready":
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            if (
                str(payload.get("workflow_run_id") or "")
                == row["workflow_run_id"]
                and str(payload.get("task_map_generation") or "")
                == row["task_map_generation"]
            ):
                row["candidate_freeze"] = {
                    "event_id": event.id,
                    "freeze_id": str(payload.get("freeze_id") or ""),
                    "candidate_generation": str(
                        payload.get("candidate_generation") or ""
                    ),
                    "candidate_head": str(
                        payload.get("candidate_head")
                        or payload.get("commit")
                        or ""
                    ),
                    "freeze_receipt_digest": str(
                        payload.get("freeze_receipt_digest") or ""
                    ),
                }
                break
    return [grouped[key] for key in sorted(grouped)]


def _closure(
    tasks: list[dict[str, Any]],
    operations: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
    bindings: list[dict[str, Any]],
    generations: list[dict[str, Any]],
) -> dict[str, Any]:
    active_operations = sorted(
        str(row.get("operation_id") or "")
        for row in operations
        if str(row.get("status") or "") in _ACTIVE_OPERATION_STATUSES
    )
    active_attempts = sorted(
        str(row.get("attempt_id") or "")
        for row in attempts
        if str(row.get("status") or "") in _ACTIVE_ATTEMPT_STATUSES
    )
    active_sessions = sorted(
        str(row.get("binding_key") or "")
        for row in bindings
        if str(row.get("status") or "") == "active"
    )
    terminal_expected = bool(tasks) and all(
        str(row.get("task_status") or "") in _TERMINAL_TASK_STATUSES
        for row in tasks
    )
    freeze_missing = [
        str(row.get("generation_id") or "")
        for row in generations
        if terminal_expected and not row.get("candidate_freeze")
    ]
    residuals = [
        *(f"active_operation:{item}" for item in active_operations),
        *(f"active_attempt:{item}" for item in active_attempts),
        *(f"active_session:{item}" for item in active_sessions),
        *(f"missing_candidate_freeze:{item}" for item in freeze_missing),
    ]
    if not terminal_expected:
        status = "running"
    elif residuals:
        status = "residual_active"
    else:
        status = "converged"
    return {
        "status": status,
        "terminal_expected": terminal_expected,
        "converged": status == "converged",
        "active_operation_ids": active_operations,
        "active_attempt_ids": active_attempts,
        "active_session_binding_keys": active_sessions,
        "missing_candidate_freeze_generation_ids": freeze_missing,
        "residuals": residuals,
    }


def _matches_current_generation(
    row: Mapping[str, Any],
    contexts: Mapping[str, Mapping[str, Any]],
) -> bool:
    task_id = str(row.get("task_id") or "")
    context = contexts.get(task_id)
    return bool(
        context
        and str(row.get("task_pipeline_stage") or "")
        and str(row.get("workflow_run_id") or "")
        == str(context.get("workflow_run_id") or "")
        and str(row.get("task_map_generation") or "")
        == str(context.get("task_map_generation") or "")
    )


def _attempt_matches_current_generation(
    row: Mapping[str, Any],
    contexts: Mapping[str, Mapping[str, Any]],
) -> bool:
    context = contexts.get(str(row.get("task_id") or ""))
    return bool(
        context
        and str(row.get("run_id") or "")
        == str(context.get("workflow_run_id") or "")
    )


def _binding_matches_current_generation(
    row: Mapping[str, Any],
    contexts: Mapping[str, Mapping[str, Any]],
) -> bool:
    context = contexts.get(str(row.get("task_id") or ""))
    if not context:
        return False
    generation = str(context.get("task_map_generation") or "")
    return (
        str(row.get("workflow_run_id") or "")
        == str(context.get("workflow_run_id") or "")
        and str(row.get("rework_affinity_id") or "").startswith(
            f"{generation}:"
        )
    )


def _normalize_bindings(
    value: Mapping[str, Mapping[str, Any]] | Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows = value.values() if isinstance(value, Mapping) else value
    return [dict(row) for row in rows if isinstance(row, Mapping)]


def _operation_order(row: Mapping[str, Any]) -> tuple[int, int, str, str]:
    return (
        int(row.get("operation_generation") or 0),
        _STAGE_ORDER.get(str(row.get("task_pipeline_stage") or ""), 0),
        str(row.get("last_event_at") or ""),
        str(row.get("operation_id") or ""),
    )


def _projected_operation_order(
    row: Mapping[str, Any],
) -> tuple[str, int, int, str]:
    return (
        str(row.get("task_id") or ""),
        int(row.get("operation_generation") or 0),
        _STAGE_ORDER.get(str(row.get("stage") or ""), 0),
        str(row.get("operation_id") or ""),
    )


def _counts(rows: Iterable[Mapping[str, Any]], key: str) -> dict[str, int]:
    counts = Counter(str(row.get(key) or "unknown") for row in rows)
    return dict(sorted(counts.items()))


def _mapping_value(value: Any, key: str) -> Any:
    return value.get(key) if isinstance(value, Mapping) else ""


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")).hexdigest()


__all__ = [
    "TASK_PIPELINE_PROJECTION_SCHEMA",
    "build_task_pipeline_projection",
    "normalize_task_pipeline_operation_semantics",
    "read_task_pipeline_projection",
    "write_task_pipeline_projection",
]
