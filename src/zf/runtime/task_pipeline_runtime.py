"""Blocking runtime wiring for the v4 Task Pipeline controller.

The Task Pipeline owns scheduling only after the existing plan/task-map gates
have admitted one immutable generation.  Stage artifacts and semantic result
profiles remain the existing Contract Snapshot, implementation-result.v1 and
verification-result.v1 contracts.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from zf.core.config.schema import RoleConfig
from zf.core.events.model import ZfEvent
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.workflow_runtime_types import WorkflowRuntimeDecision
from zf.runtime.task_pipeline_contexts import (
    TASK_PIPELINE_GENERATION_ADMITTED,
    TASK_PIPELINE_GENERATION_SCHEMA,
    task_pipeline_generation_contexts,
    task_pipeline_managed_task_ids,
)
from zf.runtime.task_pipeline_reconciler import (
    TaskPipelineReconciler,
    task_pipeline_any_blocking,
    task_pipeline_policy,
    task_pipeline_policy_for_contexts,
    task_pipeline_policy_partitions,
)
from zf.runtime.task_pipeline_affinity import task_pipeline_preferred_roles
from zf.runtime.task_pipeline_dispatch_events import (
    emit_task_pipeline_dispatch_deferred_once,
    emit_task_pipeline_waiting_once,
)
from zf.runtime.task_pipeline_runtime_selection import (
    operation_matches_generation,
    terminal_dependency_ids,
)


class TaskPipelineRuntimeError(RuntimeError):
    """A blocking Task Pipeline invariant could not be proven."""


class TaskPipelineWaiting(TaskPipelineRuntimeError):
    """A level-triggered prerequisite is not current yet."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail or reason


@dataclass(frozen=True)
class TaskPipelineGenerationPreflight:
    """Resolved immutable inputs for one generation admission."""

    task_ids: tuple[str, ...]
    workflow_run_id: str
    task_map_generation: str
    plan_artifact_package_id: str
    plan_artifact_package_ref: str
    plan_artifact_package_digest: str
    dispatch_base_commit: str
    generation_id: str
    profile_id: str
    profile_digest: str


def preflight_task_pipeline_generation(
    runtime: Any,
    *,
    trigger_event: ZfEvent,
    trace_id: str,
    loaded: Any,
    task_items: Iterable[Mapping[str, Any]],
) -> TaskPipelineGenerationPreflight | None:
    """Resolve a blocking generation before canonical Task side effects."""

    trigger_payload = (
        trigger_event.payload if isinstance(trigger_event.payload, dict) else {}
    )
    flow_kind = str(
        getattr(loaded, "flow_kind", "")
        or trigger_payload.get("flow_kind")
        or trigger_payload.get("request_kind")
        or ""
    ).strip()
    policy = task_pipeline_policy(runtime.config, flow_kind=flow_kind)
    if not policy or str(policy.get("mode") or "") != "blocking":
        return None
    task_ids = tuple(sorted({
        str(item.get("task_id") or "").strip()
        for item in task_items
        if str(item.get("task_id") or "").strip()
    }))
    if not task_ids:
        raise TaskPipelineRuntimeError(
            "blocking Task Pipeline generation has no admitted tasks"
        )
    workflow_run_id = str(
        getattr(loaded, "workflow_run_id", "")
        or trigger_payload.get("workflow_run_id")
        or trigger_payload.get("run_id")
        or trace_id
        or trigger_event.correlation_id
        or ""
    ).strip()
    if not workflow_run_id:
        raise TaskPipelineRuntimeError(
            "blocking Task Pipeline generation requires workflow_run_id"
        )
    task_map_generation = str(
        getattr(loaded, "task_map_generation", "")
        or trigger_payload.get("task_map_generation")
        or trigger_payload.get("task_map_digest")
        or _file_sha256(getattr(loaded, "task_map_path", None))
        or ""
    ).strip()
    if not task_map_generation:
        raise TaskPipelineRuntimeError(
            "blocking Task Pipeline generation requires task_map_generation"
        )
    package_identity = {
        "plan_artifact_package_id": str(
            getattr(loaded, "plan_artifact_package_id", "")
            or trigger_payload.get("plan_artifact_package_id")
            or ""
        ).strip(),
        "plan_artifact_package_ref": str(
            getattr(loaded, "plan_artifact_package_ref", "")
            or trigger_payload.get("plan_artifact_package_ref")
            or ""
        ).strip(),
        "plan_artifact_package_digest": str(
            getattr(loaded, "plan_artifact_package_digest", "")
            or trigger_payload.get("plan_artifact_package_digest")
            or ""
        ).strip(),
    }
    missing_package_identity = [
        key for key, value in package_identity.items() if not value
    ]
    if missing_package_identity:
        raise TaskPipelineRuntimeError(
            "blocking Task Pipeline generation requires Plan Artifact Package "
            "identity: " + ", ".join(missing_package_identity)
        )
    base_ref = str(
        getattr(loaded, "dispatch_base_commit", "")
        or trigger_payload.get("dispatch_base_commit")
        or trigger_payload.get("base_commit")
        or runtime.config.runtime.git.candidate_base_ref
        or ""
    ).strip()
    dispatch_base_commit = _resolve_commit(runtime.project_root, base_ref)
    profile_digest = str(policy.get("profile_digest") or "")
    return TaskPipelineGenerationPreflight(
        task_ids=task_ids,
        workflow_run_id=workflow_run_id,
        task_map_generation=task_map_generation,
        **package_identity,
        dispatch_base_commit=dispatch_base_commit,
        generation_id=_generation_id(
            workflow_run_id=workflow_run_id,
            task_map_generation=task_map_generation,
            plan_artifact_package_id=package_identity[
                "plan_artifact_package_id"
            ],
            task_ids=list(task_ids),
            profile_digest=profile_digest,
        ),
        profile_id=str(policy.get("profile_id") or ""),
        profile_digest=profile_digest,
    )


def admit_task_pipeline_generation(
    runtime: Any,
    *,
    trigger_event: ZfEvent,
    task_map_admitted_event: ZfEvent,
    stage_id: str,
    trace_id: str,
    loaded: Any,
    task_items: Iterable[Mapping[str, Any]],
    preflight: TaskPipelineGenerationPreflight | None = None,
) -> ZfEvent | None:
    """Append one replay-stable v4 generation fact after plan admission."""

    prepared = preflight or preflight_task_pipeline_generation(
        runtime,
        trigger_event=trigger_event,
        trace_id=trace_id,
        loaded=loaded,
        task_items=task_items,
    )
    if prepared is None:
        return None
    admitted_payload = (
        task_map_admitted_event.payload
        if isinstance(task_map_admitted_event.payload, dict)
        else {}
    )
    trigger_payload = (
        trigger_event.payload
        if isinstance(trigger_event.payload, dict)
        else {}
    )
    flow_kind = str(
        getattr(loaded, "flow_kind", "")
        or trigger_payload.get("flow_kind")
        or trigger_payload.get("request_kind")
        or ""
    ).strip()
    policy = task_pipeline_policy(runtime.config, flow_kind=flow_kind) or {}
    pdd_id = str(
        trigger_payload.get("pdd_id")
        or trigger_payload.get("feature_id")
        or ""
    ).strip()
    feature_id = str(
        trigger_payload.get("feature_id")
        or trigger_payload.get("pdd_id")
        or ""
    ).strip()
    for existing in reversed(runtime.event_log.read_all()):
        if existing.type != TASK_PIPELINE_GENERATION_ADMITTED:
            continue
        payload = existing.payload if isinstance(existing.payload, dict) else {}
        if str(payload.get("generation_id") or "") == prepared.generation_id:
            return existing

    event = ZfEvent(
        type=TASK_PIPELINE_GENERATION_ADMITTED,
        actor="zf-cli",
        origin="kernel",
        payload={
            "schema_version": TASK_PIPELINE_GENERATION_SCHEMA,
            "generation_id": prepared.generation_id,
            "workflow_run_id": prepared.workflow_run_id,
            "flow_kind": flow_kind,
            "request_kind": str(
                trigger_payload.get("request_kind") or flow_kind
            ).strip(),
            "pdd_id": pdd_id,
            "feature_id": feature_id,
            "stage_id": stage_id,
            "trigger_event_id": trigger_event.id,
            "fanout_id": str(
                trigger_payload.get("fanout_id")
                or trigger_payload.get("upstream_fanout_id")
                or f"task-pipeline-{prepared.generation_id}"
            ),
            "task_map_admitted_event_id": task_map_admitted_event.id,
            "task_map_generation": prepared.task_map_generation,
            "plan_artifact_package_id": prepared.plan_artifact_package_id,
            "plan_artifact_package_ref": prepared.plan_artifact_package_ref,
            "plan_artifact_package_digest": (
                prepared.plan_artifact_package_digest
            ),
            "task_map_ref": str(getattr(loaded, "task_map_ref", "") or ""),
            "task_map_digest": str(admitted_payload.get("task_map_digest") or ""),
            "source_index_ref": str(
                getattr(loaded, "source_index_ref", "") or ""
            ),
            "dispatch_base_commit": prepared.dispatch_base_commit,
            "profile_id": prepared.profile_id,
            "profile_digest": prepared.profile_digest,
            "task_ids": list(prepared.task_ids),
        },
        causation_id=task_map_admitted_event.id,
        correlation_id=prepared.workflow_run_id,
    )
    return runtime.event_writer.append(event)


def prepare_task_pipeline_dispatch(
    runtime: Any,
    *,
    candidate_task_ids: Iterable[str],
) -> tuple[list[WorkflowRuntimeDecision], set[str]]:
    """Run the configured v4 controller inside the canonical dispatch path."""

    candidate_ids = {
        str(task_id).strip()
        for task_id in candidate_task_ids
        if str(task_id).strip()
    }
    managed: set[str] = set()
    try:
        event_log = getattr(runtime, "event_log", None)
        contexts = task_pipeline_generation_contexts(
            event_log.read_all() if event_log is not None else []
        )
        managed = task_pipeline_managed_task_ids(runtime)
        active_task_ids = {
            task_id
            for task_id in contexts
            if (
                (task := runtime.task_store.get(task_id)) is not None
                and str(task.status) not in {"done", "cancelled"}
            )
        }
        partitions = (
            task_pipeline_policy_partitions(
                runtime.config,
                contexts,
                task_ids=active_task_ids,
            )
            if active_task_ids
            else []
        )
        partitioned_task_ids = {
            task_id
            for partition in partitions
            for task_id in partition["task_ids"]
        }
        if active_task_ids and partitioned_task_ids != active_task_ids:
            raise TaskPipelineRuntimeError(
                "active Task Pipeline generations have unresolved Flow policies"
            )
        policies = [partition["policy"] for partition in partitions]
        if not policies:
            policy = task_pipeline_policy(runtime.config)
            policies = [policy] if policy else []
        if not policies:
            if active_task_ids:
                raise TaskPipelineRuntimeError(
                    "active Task Pipeline generations have unresolved Flow policies"
                )
            if task_pipeline_any_blocking(runtime.config):
                managed.update(candidate_ids)
            return [], managed
        blocking = any(
            str(policy.get("mode") or "shadow") == "blocking"
            for policy in policies
        )
        if blocking:
            # Blocking means the controller owns the dispatch boundary, not
            # only Tasks that already reached generation admission.  A parent
            # Task can exist while its Workflow Plan still awaits approval;
            # fencing every ready candidate prevents the legacy dispatcher
            # from turning Task creation into an execution side effect.
            managed.update(candidate_ids)
            if runtime._dispatch_globally_paused():
                return [], managed
            return reconcile_task_pipeline_runtime(runtime), managed
        from zf.runtime.task_pipeline_projection import (
            write_task_pipeline_projection,
        )

        write_task_pipeline_projection(runtime)
        return [], set()
    except Exception as exc:
        runtime.event_writer.append(ZfEvent(
            type="kernel.housekeeping.failed",
            actor="orchestrator",
            origin="kernel",
            payload={
                "step": "task_pipeline_dispatch",
                "exc_type": type(exc).__name__,
                "exc_repr": repr(exc)[:500],
            },
        ))
        if not managed:
            managed = candidate_ids
        return [], managed


def reconcile_task_pipeline_runtime(
    runtime: Any,
) -> list[WorkflowRuntimeDecision]:
    """Reconcile and dispatch one level-triggered blocking v4 ready-set."""

    events = runtime.event_log.read_all()
    contexts = task_pipeline_generation_contexts(events)
    if not contexts:
        return []
    decisions: list[WorkflowRuntimeDecision] = []
    from zf.runtime.task_pipeline_terminal import (
        reconcile_task_pipeline_freeze,
        reconcile_task_pipeline_terminals,
    )
    decisions.extend(
        reconcile_task_pipeline_terminals(runtime, generation_contexts=contexts)
    )
    decisions.extend(
        reconcile_task_pipeline_freeze(runtime, generation_contexts=contexts)
    )
    active_task_ids = {
        task_id
        for task_id in contexts
        if (
            (task := runtime.task_store.get(task_id)) is not None
            and str(task.status) not in {"done", "cancelled"}
        )
    }
    if not active_task_ids:
        from zf.runtime.task_pipeline_projection import (
            write_task_pipeline_projection,
        )

        write_task_pipeline_projection(runtime)
        return decisions
    partitions = task_pipeline_policy_partitions(
        runtime.config,
        contexts,
        task_ids=active_task_ids,
    )
    partitioned_task_ids = {
        task_id
        for partition in partitions
        for task_id in partition["task_ids"]
    }
    if partitioned_task_ids != active_task_ids:
        raise TaskPipelineRuntimeError(
            "active Task Pipeline generations have unresolved Flow policies"
        )
    blocking_partitions = [
        partition
        for partition in partitions
        if str(partition["policy"].get("mode") or "") == "blocking"
    ]
    if not blocking_partitions:
        return []
    blocking_task_ids = {
        task_id
        for partition in blocking_partitions
        for task_id in partition["task_ids"]
    }
    blocking_contexts = {
        task_id: contexts[task_id]
        for task_id in sorted(blocking_task_ids)
    }
    from zf.runtime.task_pipeline_entry import reconcile_task_pipeline_entries

    entry_decisions, external_gate_satisfied_ids = (
        reconcile_task_pipeline_entries(
            runtime,
            generation_contexts=blocking_contexts,
        )
    )
    decisions.extend(entry_decisions)
    from zf.runtime.task_pipeline_recovery import (
        reconcile_task_pipeline_redrives,
    )

    decisions.extend(reconcile_task_pipeline_redrives(
        runtime,
        generation_contexts=blocking_contexts,
    ))
    from zf.runtime.task_attempt_runtime import task_attempt_store
    from zf.runtime.workflow_operation import reduce_workflow_operations
    from zf.runtime.task_pipeline_rework import (
        derive_impl_rework_requests,
        reconcile_task_ref_repair_replays,
    )
    from zf.runtime.task_pipeline_acceptance import (
        reconcile_task_pipeline_acceptance_routes,
    )
    from zf.runtime.task_pipeline_integration import (
        reconcile_task_pipeline_integration,
    )
    from zf.runtime.task_pipeline_dispatch import dispatch_task_pipeline_stage
    from zf.runtime.task_pipeline_projection import (
        normalize_task_pipeline_operation_semantics,
    )

    reconcile_task_ref_repair_replays(
        runtime,
        events=runtime.event_log.read_all(),
        generation_contexts=contexts,
    )

    for partition in blocking_partitions:
        policy = partition["policy"]
        group_contexts = {
            task_id: contexts[task_id]
            for task_id in partition["task_ids"]
        }
        events = runtime.event_log.read_all()
        state_dir = getattr(runtime, "state_dir", None)
        all_operations = normalize_task_pipeline_operation_semantics(
            state_dir,
            [row for row in reduce_workflow_operations(events).values()
             if operation_matches_generation(row, contexts)],
        ) if state_dir is not None else [
            row for row in reduce_workflow_operations(events).values()
            if operation_matches_generation(row, contexts)
        ]
        group_operations = [
            row
            for row in all_operations
            if operation_matches_generation(row, group_contexts)
        ]
        decisions.extend(reconcile_task_pipeline_acceptance_routes(
            runtime,
            generation_contexts=group_contexts,
            operation_rows=group_operations,
        ))
        events = runtime.event_log.read_all()
        all_operations = normalize_task_pipeline_operation_semantics(
            state_dir,
            [row for row in reduce_workflow_operations(events).values()
             if operation_matches_generation(row, contexts)],
        ) if state_dir is not None else [
            row for row in reduce_workflow_operations(events).values()
            if operation_matches_generation(row, contexts)
        ]
        group_operations = [
            row
            for row in all_operations
            if operation_matches_generation(row, group_contexts)
        ]
        tasks = [
            task
            for task_id in sorted(group_contexts)
            if (task := runtime.task_store.get(task_id)) is not None
        ]
        attempts = list(task_attempt_store(runtime).current_rows())
        impl_rework_requests = derive_impl_rework_requests(
            events=events,
            generation_contexts=group_contexts,
            operation_rows=group_operations,
        )
        projection = TaskPipelineReconciler().reconcile(
            policy=policy,
            tasks=tasks,
            operations=all_operations,
            attempts=attempts,
            terminal_task_ids=terminal_dependency_ids(runtime, tasks),
            external_gate_satisfied_task_ids=(
                set(group_contexts).intersection(
                    external_gate_satisfied_ids
                )
            ),
            impl_rework_requests=impl_rework_requests,
            preferred_role_instances=task_pipeline_preferred_roles(
                runtime,
                generation_contexts=group_contexts,
            ),
        )
        from zf.runtime.task_pipeline_semantic_exhaustion import (
            reconcile_task_pipeline_semantic_exhaustion,
        )

        reconcile_task_pipeline_semantic_exhaustion(
            runtime,
            projection=projection,
            generation_contexts=group_contexts,
        )
        decisions.extend(reconcile_task_pipeline_integration(
            runtime,
            projection=projection,
            generation_contexts=group_contexts,
            operation_rows=group_operations,
        ))
        decisions.extend(reconcile_task_pipeline_terminals(
            runtime,
            generation_contexts=group_contexts,
        ))
        for stage in ("verify", "acceptance_review", "impl"):
            for assignment in projection.get("dispatchable", {}).get(stage, []):
                task_id = str(assignment.get("task_id") or "")
                task = runtime.task_store.get(task_id)
                if task is None:
                    continue
                cooldown_active = getattr(
                    runtime,
                    "_dispatch_recent_failure_cooldown_active",
                    None,
                )
                if callable(cooldown_active) and cooldown_active(task):
                    emit_task_pipeline_waiting_once(
                        runtime,
                        task_id=task_id,
                        stage=stage,
                        operation_generation=int(
                            assignment.get("operation_generation") or 1
                        ),
                        context=group_contexts[task_id],
                        reason="dispatch_failure_cooldown",
                        detail=(
                            "Task Pipeline dispatch is cooling down after "
                            "repeated deterministic activation failures"
                        ),
                    )
                    continue
                try:
                    decision = dispatch_task_pipeline_stage(
                        runtime,
                        policy=policy,
                        task=task,
                        assignment=dict(assignment),
                        generation_context=group_contexts[task_id],
                        operation_rows=group_operations,
                        attempt_rows=attempts,
                    )
                except TaskPipelineWaiting as exc:
                    emit_task_pipeline_waiting_once(
                        runtime,
                        task_id=task_id,
                        stage=stage,
                        operation_generation=int(
                            assignment.get("operation_generation") or 1
                        ),
                        context=group_contexts[task_id],
                        reason=exc.reason,
                        detail=exc.detail,
                    )
                    continue
                except Exception as exc:
                    record_failure = getattr(
                        runtime,
                        "_record_dispatch_failure",
                        None,
                    )
                    if callable(record_failure):
                        record_failure(task_id)
                    emit_task_pipeline_dispatch_deferred_once(
                        runtime,
                        task_id=task_id,
                        stage=stage,
                        operation_generation=int(
                            assignment.get("operation_generation") or 1
                        ),
                        context=group_contexts[task_id],
                        reason=f"{type(exc).__name__}: {exc}"[:500],
                    )
                    continue
                clear_failure = getattr(
                    runtime,
                    "_clear_dispatch_failure",
                    None,
                )
                if callable(clear_failure):
                    clear_failure(task_id)
                if decision is not None:
                    decisions.append(decision)
    decisions.extend(
        reconcile_task_pipeline_freeze(runtime, generation_contexts=contexts)
    )
    from zf.runtime.task_pipeline_projection import (
        write_task_pipeline_projection,
    )

    write_task_pipeline_projection(runtime)
    return decisions


def _effective_pipeline_role(
    role: RoleConfig,
    *,
    policy: Mapping[str, Any],
    stage: str,
    task: Any,
) -> RoleConfig:
    pool = dict((policy.get("pools") or {}).get(stage) or {})
    profile = next(
        (
            item
            for item in pool.get("worker_profiles") or []
            if isinstance(item, Mapping)
            and str(item.get("role") or "")
            in {role.instance_id, role.name}
        ),
        {},
    )
    skills = list(dict.fromkeys([
        *list(role.skills),
        *[str(item) for item in pool.get("skills") or [] if str(item)],
        *[str(item) for item in profile.get("skills") or [] if str(item)],
        *[
            str(item)
            for item in getattr(task, "skills_required", ()) or ()
            if str(item)
        ],
    ]))
    return replace(role, skills=skills)


def _role_config_digest(runtime: Any, role: RoleConfig, task: Any) -> str:
    from zf.runtime.execution_profiles import resolve_execution_profile

    execution = resolve_execution_profile(
        runtime.config,
        role_instance=role.instance_id,
        contract=getattr(task, "contract", None),
    )
    stable = {
        "backend": role.backend,
        "model": role.model,
        "model_reasoning_effort": role.model_reasoning_effort,
        "permission_mode": role.permission_mode,
        "allowed_tools": list(role.allowed_tools),
        "skills": list(role.skills),
        "constraints": asdict(role.constraints),
        "execution_profile_id": execution.profile_id,
        "execution_profile_digest": execution.profile_digest,
    }
    return hashlib.sha256(
        json.dumps(stable, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _activate_task_stage_binding(
    runtime: Any,
    *,
    role: RoleConfig,
    task: Any,
    workflow_run_id: str,
    task_map_generation: str,
    stage: str,
    workspace_generation: int,
    placement_epoch: int,
    role_config_digest: str,
    project_path: Path,
    workspace_root: Path,
) -> dict[str, Any]:
    registry = RoleSessionRegistry(
        Path(runtime.state_dir) / "role_sessions.yaml",
        project_root=str(runtime.project_root),
    )
    affinity = f"{task_map_generation}:{stage}"
    existing = registry.task_stage_binding(
        workflow_run_id=workflow_run_id,
        task_id=str(task.id),
        stage=stage,
        rework_affinity_id=affinity,
    )
    if (
        existing is not None
        and str(existing.get("role_config_digest") or "")
        != role_config_digest
    ):
        raise TaskPipelineWaiting(
            "waiting_for_compatible_role",
            "idle role does not match the frozen Task-stage execution profile",
        )

    role_meta = registry.instance_meta().get(role.instance_id, {})
    previous_binding_key = str(
        role_meta.get("active_task_stage_binding_key") or ""
    )
    next_binding_key = str(existing.get("binding_key") or "") if existing else ""
    alive = False
    try:
        alive = bool(runtime.transport.is_alive(role.instance_id))
    except Exception:
        alive = False
    current_path = ""
    if alive:
        try:
            snapshot = runtime.transport.lifecycle_snapshot(role.instance_id)
            current_path = str(snapshot.current_path or "")
        except Exception:
            current_path = ""
    path_mismatch = bool(
        current_path
        and Path(current_path).resolve(strict=False)
        != project_path.resolve(strict=False)
    )
    binding_switch = bool(
        alive
        and (
            not next_binding_key
            or previous_binding_key != next_binding_key
            or path_mismatch
        )
    )
    if binding_switch:
        from zf.runtime.task_attempt_runtime import (
            active_task_attempt_identities_for_role,
        )

        active_attempts = active_task_attempt_identities_for_role(
            runtime,
            role_name=role.name,
            instance_id=role.instance_id,
        )
        if active_attempts:
            raise TaskPipelineWaiting(
                "waiting_for_slot_settlement",
                "physical slot still owns an active TaskAttempt",
            )
        from zf.runtime.workflow_operation import reduce_workflow_operations

        active_operations = [
            row
            for row in reduce_workflow_operations(
                runtime.event_log.read_all()
            ).values()
            if str(row.get("role_instance") or "") == role.instance_id
            and str(row.get("status") or "")
            in {"requested", "reserved", "running", "suspended"}
        ]
        if active_operations:
            raise TaskPipelineWaiting(
                "waiting_for_slot_settlement",
                "physical slot still owns an active WorkflowOperation",
            )
        runtime.transport.terminate(role.instance_id)
        now = _now_iso()
        registry.update_instance_meta(
            role.instance_id,
            lifecycle_state="suspended",
            lifecycle_transition_at=now,
            lifecycle_suspended_at=now,
            lifecycle_last_error="",
        )
        runtime._set_worker_state(
            role.instance_id,
            "suspended",
            reason="Task Pipeline slot rebound to another Task-stage session",
            force=True,
        )

    binding = registry.bind_task_stage_session(
        workflow_run_id=workflow_run_id,
        task_id=str(task.id),
        stage=stage,
        rework_affinity_id=affinity,
        role_instance=role.instance_id,
        role_config_digest=role_config_digest,
        workspace_generation=workspace_generation,
        placement_epoch=placement_epoch,
        backend=role.backend,
    )
    registry.activate_task_stage_session(
        binding_key=str(binding.get("binding_key") or ""),
        role_instance=role.instance_id,
    )
    runtime._ensure_role_active(
        role,
        task_id=str(task.id),
        spawn_cwd=project_path,
        skill_runtime_root=workspace_root,
    )
    try:
        snapshot = runtime.transport.lifecycle_snapshot(role.instance_id)
        observed_path = str(snapshot.current_path or "")
    except Exception:
        observed_path = ""
    if observed_path and Path(observed_path).resolve() != project_path.resolve():
        raise TaskPipelineRuntimeError(
            "activated Task Pipeline slot cwd does not match Task Workspace"
        )
    return binding


def _verify_rework_feedback(
    runtime: Any,
    *,
    operation_rows: Iterable[Mapping[str, Any]],
    task_id: str,
    operation_generation: int,
) -> list[dict[str, Any]]:
    operation = next(
        (
            row
            for row in operation_rows
            if str(row.get("task_id") or "") == task_id
            and str(row.get("task_pipeline_stage") or "")
            == "acceptance_review"
            and int(row.get("operation_generation") or 0)
            == operation_generation
            and str(row.get("semantic_verdict") or "") == "revise"
        ),
        None,
    )
    if operation is None:
        operation = next(
            (
                row
                for row in operation_rows
                if str(row.get("task_id") or "") == task_id
                and str(row.get("task_pipeline_stage") or "") == "verify"
                and int(row.get("operation_generation") or 0)
                == operation_generation
            ),
            None,
        )
    if operation is None:
        return []
    descriptor = operation.get("admitted_control_result_ref")
    if not isinstance(descriptor, Mapping):
        return []
    try:
        from zf.runtime.sidecar_refs import hydrate_sidecar_ref

        body = hydrate_sidecar_ref(
            Path(runtime.state_dir), dict(descriptor)
        ).payload
    except Exception:
        return []
    if not isinstance(body, Mapping):
        return []
    rows = (
        body.get("feedback")
        or body.get("rework_items")
        or body.get("findings")
        or []
    )
    return [dict(item) for item in rows if isinstance(item, Mapping)]


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise TaskPipelineRuntimeError(
            f"git {' '.join(args)} failed in {cwd}: {detail}"
        )
    return result.stdout.strip()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _generation_id(
    *,
    workflow_run_id: str,
    task_map_generation: str,
    plan_artifact_package_id: str,
    task_ids: list[str],
    profile_digest: str,
) -> str:
    digest = hashlib.sha256(
        "\x1f".join((
            workflow_run_id,
            task_map_generation,
            plan_artifact_package_id,
            ",".join(task_ids),
            profile_digest,
        )).encode("utf-8")
    ).hexdigest()[:20]
    return f"tpg-{digest}"


def _resolve_commit(project_root: Path, ref: str) -> str:
    if not ref:
        raise TaskPipelineRuntimeError(
            "blocking Task Pipeline generation requires an exact git base"
        )
    candidates = [ref]
    # Match CandidateRebuilder's established compatibility rule: `main` is the
    # portable default, while newly initialized repositories may still use a
    # different branch name. The admitted event freezes the fallback as a SHA.
    if ref == "main":
        candidates.append("HEAD")
    failures: list[str] = []
    for candidate in candidates:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", f"{candidate}^{{commit}}"],
            cwd=project_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        failures.append(result.stderr.strip() or result.stdout.strip())
    detail = "; ".join(value for value in failures if value)
    raise TaskPipelineRuntimeError(
        f"Task Pipeline dispatch base {ref!r} is not resolvable: {detail}"
    )


def _file_sha256(path: object) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except (OSError, TypeError):
        return ""


__all__ = [
    "TASK_PIPELINE_GENERATION_ADMITTED", "TASK_PIPELINE_GENERATION_SCHEMA",
    "TaskPipelineGenerationPreflight", "TaskPipelineRuntimeError",
    "admit_task_pipeline_generation", "preflight_task_pipeline_generation",
    "prepare_task_pipeline_dispatch", "task_pipeline_generation_contexts",
    "task_pipeline_managed_task_ids",
]
