"""Dispatch one admitted Task Pipeline stage operation."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.core.state.task_attempts import task_attempt_id, task_attempt_lease_id
from zf.runtime.task_contract_snapshot import (
    contract_snapshot_identity_fields,
    snapshot_payload_fields,
)
from zf.runtime.transport import DispatchContext
from zf.runtime.workflow_runtime_types import WorkflowRuntimeDecision


def dispatch_task_pipeline_stage(
    runtime: Any,
    *,
    policy: Mapping[str, Any],
    task: Any,
    assignment: Mapping[str, Any],
    generation_context: Mapping[str, Any],
    operation_rows: list[dict[str, Any]],
    attempt_rows: list[dict[str, Any]],
) -> WorkflowRuntimeDecision | None:
    from zf.runtime.task_pipeline_runtime import (
        TaskPipelineRuntimeError,
        _activate_task_stage_binding,
        _effective_pipeline_role,
        _now_iso,
        _role_config_digest,
        _verify_rework_feedback,
    )
    from zf.runtime.task_pipeline_targets import (
        prepare_contract_snapshot,
        prepare_verify_target,
    )

    stage = str(assignment.get("stage") or "").strip()
    task_id = str(task.id)
    workflow_run_id = str(generation_context.get("workflow_run_id") or "")
    task_map_generation = str(
        generation_context.get("task_map_generation") or ""
    )
    operation_generation = int(assignment.get("operation_generation") or 1)
    from zf.runtime.task_pipeline_entry import (
        task_pipeline_entry_mode,
        task_pipeline_entry_target,
        task_pipeline_external_evidence_bindings,
    )

    entry_mode = task_pipeline_entry_mode(task)
    role = runtime._find_role_by_instance(
        str(assignment.get("role_instance") or "")
    )
    if role is None:
        from zf.runtime.task_pipeline_runtime import TaskPipelineWaiting

        raise TaskPipelineWaiting(
            "waiting_for_capability",
            "selected Task Pipeline role is not present in runtime topology",
        )
    role = _effective_pipeline_role(
        role,
        policy=policy,
        stage=stage,
        task=task,
    )
    if stage == "acceptance_review" and (
        str(getattr(role, "role_kind", "") or "") != "reader"
        or "zf-integration-acceptance-review" not in set(role.skills)
    ):
        raise TaskPipelineRuntimeError(
            "risk acceptance review requires a dedicated reader role and skill"
        )
    if str(getattr(role.lifecycle, "mode", "") or "") != "on_demand":
        raise TaskPipelineRuntimeError(
            f"Task Pipeline role {role.instance_id} must be on_demand"
        )

    workspace_generation = 1
    from zf.runtime.task_workspaces import TaskWorkspaceManager

    workspace_manager = TaskWorkspaceManager(
        state_dir=Path(runtime.state_dir),
        project_root=Path(runtime.project_root),
        config=runtime.config,
    )
    from zf.runtime.task_pipeline_terminal import task_pipeline_workspace_base

    workspace_base = (
        task_pipeline_workspace_base(
            runtime,
            task=task,
            generation_context=generation_context,
        )
        if entry_mode == "standard"
        else task_pipeline_entry_target(
            task,
            generation_context,
            project_root=Path(runtime.project_root),
        )
    )
    workspace = workspace_manager.prepare(
        role=role,
        workflow_run_id=workflow_run_id,
        task_id=task_id,
        task_map_generation=task_map_generation,
        workspace_generation=workspace_generation,
        base_ref=workspace_base,
    )
    if not workspace.enabled or workspace.mode != "worktree":
        raise TaskPipelineRuntimeError(
            "blocking Task Pipeline requires runtime.workdirs worktree mode"
        )
    project_path = Path(workspace.project_path).resolve()
    contract_snapshot, contract_descriptor = prepare_contract_snapshot(
        runtime,
        task=task,
        generation_context=generation_context,
        workspace=workspace,
    )
    package_identity: dict[str, str] = {}
    for key in (
        "plan_artifact_package_id",
        "plan_artifact_package_ref",
        "plan_artifact_package_digest",
    ):
        admitted_value = str(generation_context.get(key) or "").strip()
        task_value = str(contract_snapshot.get(key) or "").strip()
        if not admitted_value or task_value != admitted_value:
            raise TaskPipelineRuntimeError(
                f"Task Contract Snapshot {key} does not match the admitted "
                "Task Pipeline generation"
            )
        package_identity[key] = admitted_value

    target_fields: dict[str, Any] = {}
    if stage == "verify":
        target_fields = prepare_verify_target(
            runtime,
            task_id=task_id,
            workflow_run_id=workflow_run_id,
            task_map_generation=task_map_generation,
            operation_generation=operation_generation,
            workspace=workspace,
            task=task,
            generation_context=generation_context,
            entry_mode=entry_mode,
            contract_snapshot=contract_snapshot,
            contract_descriptor=contract_descriptor,
        )
    elif stage == "acceptance_review":
        target_fields = _prepare_acceptance_review_target(
            runtime,
            task=task,
            operation_rows=operation_rows,
            operation_generation=operation_generation,
            workspace=workspace,
            contract_snapshot=contract_snapshot,
            contract_descriptor=contract_descriptor,
            policy=policy,
        )

    from zf.runtime.task_pipeline_identity import task_pipeline_operation_identity

    operation_identity = task_pipeline_operation_identity(
        workflow_run_id=workflow_run_id,
        task_id=task_id,
        task_map_generation=task_map_generation,
        stage=stage,
        stage_revision=(
            "implementation-result.v1"
            if stage == "impl"
            else (
                "verification-result.v1"
                if stage == "verify"
                else "task-integration-acceptance-result.v1"
            )
        ),
        operation_generation=operation_generation,
    )
    stage_operation_ids = {
        str(row.get("operation_id") or "")
        for row in operation_rows
        if str(row.get("task_id") or "") == task_id
        and str(row.get("task_pipeline_stage") or "") == stage
    }
    placement_epoch = _next_task_stage_placement_epoch(
        runtime,
        workflow_run_id=workflow_run_id,
        task_id=task_id,
        task_map_generation=task_map_generation,
        stage=stage,
        operation_ids=stage_operation_ids | {operation_identity.operation_id},
        attempt_rows=attempt_rows,
    )
    role_config_digest = _role_config_digest(runtime, role, task)
    binding = _activate_task_stage_binding(
        runtime,
        role=role,
        task=task,
        workflow_run_id=workflow_run_id,
        task_map_generation=task_map_generation,
        stage=stage,
        workspace_generation=workspace_generation,
        placement_epoch=placement_epoch,
        role_config_digest=role_config_digest,
        project_path=project_path,
        workspace_root=Path(workspace.workdir),
    )
    dispatch_id = f"tpd-{operation_identity.operation_id[-20:]}-p{placement_epoch}"
    scheduler_attempt_id = task_attempt_id(
        workflow_run_id,
        task_id,
        dispatch_id,
    )
    lease_id = task_attempt_lease_id(scheduler_attempt_id)
    output_profile_id = {
        "impl": "implementation",
        "verify": "task-verify",
        "acceptance_review": "integration-acceptance-review",
    }[stage]
    external_evidence_bindings = (
        task_pipeline_external_evidence_bindings(
            runtime,
            task=task,
            context=generation_context,
        )
        if stage == "verify"
        else []
    )
    operation_payload: dict[str, Any] = {
        "workflow_run_id": workflow_run_id,
        "trace_id": workflow_run_id,
        "task_id": task_id,
        "stage_id": stage,
        "task_pipeline_stage": stage,
        "task_pipeline_entry_mode": entry_mode,
        "operation_generation": operation_generation,
        "task_map_generation": task_map_generation,
        "workspace_generation": workspace_generation,
        "placement_epoch": placement_epoch,
        "pipeline_key": operation_identity.pipeline_key,
        "task_stage_session_binding": str(binding.get("binding_key") or ""),
        "role_instance": role.instance_id,
        "attempt_id": scheduler_attempt_id,
        "lease_id": lease_id,
        "dispatch_id": dispatch_id,
        "output_profile_id": output_profile_id,
        "output_profile_revision": "1",
        "canonical_success_event": (
            "dev.build.done"
            if stage == "impl"
            else (
                "task.pipeline.verify.completed"
                if stage == "verify"
                else "task.pipeline.acceptance.completed"
            )
        ),
        "canonical_failure_event": (
            "dev.blocked"
            if stage == "impl"
            else (
                "task.pipeline.verify.failed"
                if stage == "verify"
                else "task.pipeline.acceptance.failed"
            )
        ),
        "task_map_ref": str(generation_context.get("task_map_ref") or ""),
        "source_index_ref": str(generation_context.get("source_index_ref") or ""),
        "base_commit": str(workspace.base_commit),
        "target_ref": str(workspace.base_commit),
        "task_ref": str(contract_snapshot.get("task_ref") or ""),
        "source_branch": str(workspace.branch),
        "workdir": str(project_path),
        "instruction": _task_instruction(task),
        "verification_owner": str(
            contract_snapshot.get("verification_owner") or "task_verify"
        ),
        "verification_tier": str(
            contract_snapshot.get("verification_tier") or "runtime"
        ),
        "external_evidence_bindings": external_evidence_bindings,
        "skills": list(role.skills),
        **snapshot_payload_fields(contract_descriptor),
        **contract_snapshot_identity_fields(contract_snapshot),
        **package_identity,
        **target_fields,
    }
    if operation_generation > 1:
        rework_feedback = _verify_rework_feedback(
            runtime,
            operation_rows=operation_rows,
            task_id=task_id,
            operation_generation=operation_generation - 1,
        )
        from zf.runtime.task_pipeline_rework import impl_rework_feedback

        rework_feedback.extend(impl_rework_feedback(
            assignment.get("impl_rework_request")
            if isinstance(assignment.get("impl_rework_request"), Mapping)
            else None
        ))
        operation_payload["rework_feedback"] = rework_feedback

    from zf.runtime.call_result_runtime import prepare_call_operation
    from zf.runtime.task_pipeline_capsule import publish_pipeline_capsule

    publish_pipeline_capsule(
        runtime,
        task_id=task_id,
        snapshot=contract_snapshot,
        dispatch_id=dispatch_id,
        generation_event_id=str(generation_context.get("generation_admitted_event_id") or ""),
    )

    prepared = prepare_call_operation(
        runtime,
        payload=operation_payload,
        operation_type="task-stage",
        operation_key=operation_identity.operation_key,
        stage_id=stage,
        task_id=task_id,
        dispatch_id=dispatch_id,
        causation_id=str(
            generation_context.get("generation_admitted_event_id") or ""
        ),
        correlation_id=workflow_run_id,
        workdir_write_scopes=(
            list(contract_snapshot.get("allowed_paths") or [])
            if stage == "impl"
            else []
        ),
        scheduler_attempt_id=scheduler_attempt_id,
    )
    if prepared.operation_id != operation_identity.operation_id:
        raise TaskPipelineRuntimeError(
            "Task Pipeline operation identity diverged during call preparation"
        )
    if not prepared.should_dispatch:
        return None

    from zf.runtime.task_pipeline_briefing import write_task_pipeline_briefing

    briefing_path = write_task_pipeline_briefing(
        runtime,
        role=role,
        task=task,
        stage=stage,
        workspace=workspace,
        contract_snapshot=contract_snapshot,
        payload=operation_payload,
        prepared=prepared,
    )
    from zf.runtime.injection import build_task_prompt

    prompt = build_task_prompt(
        role.instance_id,
        briefing_path,
        prompt_kind="task_pipeline_stage",
    )
    context = DispatchContext(
        trace_id=workflow_run_id,
        run_id=workflow_run_id,
        task_id=task_id,
        role_name=role.name,
        instance_id=role.instance_id,
        backend=role.backend,
        briefing_path=briefing_path,
        dispatch_id=dispatch_id,
        operation_id=operation_identity.operation_id,
        attempt_id=scheduler_attempt_id,
        lease_id=lease_id,
        task_pipeline_stage=stage,
        operation_generation=operation_generation,
        task_map_generation=task_map_generation,
        workspace_generation=workspace_generation,
        placement_epoch=placement_epoch,
        task_stage_session_binding=str(binding.get("binding_key") or ""),
    )
    delivered = runtime._send_transport_task(
        role.instance_id,
        briefing_path,
        prompt,
        context,
    ) or context
    from zf.runtime.call_result_runtime import mark_call_operation_started

    mark_call_operation_started(
        runtime,
        prepared,
        task_id=task_id,
        dispatch_id=dispatch_id,
        causation_id=str(
            generation_context.get("generation_admitted_event_id") or ""
        ),
        correlation_id=workflow_run_id,
    )
    runtime.task_store.update(
        task_id,
        status="in_progress",
        assigned_to=role.instance_id,
        active_dispatch_id=dispatch_id,
        dispatched_at=_now_iso(),
        started_at=str(getattr(task, "started_at", "") or _now_iso()),
    )
    from zf.runtime.task_attempt_runtime import dispatch_attempt_payload

    occurrence_payload = {
        **operation_payload,
        **dispatch_attempt_payload(delivered),
        "operation_id": operation_identity.operation_id,
        "operation_key": operation_identity.operation_key,
        "briefing": str(briefing_path),
        "assignee": role.instance_id,
        "role": role.name,
    }
    runtime.event_writer.append(ZfEvent(
        type="task.dispatched",
        actor="orchestrator",
        origin="kernel",
        task_id=task_id,
        payload=occurrence_payload,
        causation_id=str(
            generation_context.get("generation_admitted_event_id") or ""
        ),
        correlation_id=workflow_run_id,
    ))
    runtime.event_writer.append(ZfEvent(
        type="task.pipeline.stage.dispatched",
        actor="orchestrator",
        origin="kernel",
        task_id=task_id,
        payload=occurrence_payload,
        causation_id=str(
            generation_context.get("generation_admitted_event_id") or ""
        ),
        correlation_id=workflow_run_id,
    ))
    runtime._set_worker_state(
        role.instance_id,
        "busy",
        reason=f"Task Pipeline {stage} dispatched",
        task_id=task_id,
        force=True,
        extra_payload={
            "operation_id": operation_identity.operation_id,
            "placement_epoch": placement_epoch,
        },
    )
    return WorkflowRuntimeDecision(
        action="task_pipeline_dispatch",
        task_id=task_id,
        role=role.instance_id,
        reason=(
            f"Task Pipeline {stage} generation {operation_generation} "
            f"dispatched to {role.instance_id}"
        ),
    )


def _next_task_stage_placement_epoch(
    runtime: Any,
    *,
    workflow_run_id: str,
    task_id: str,
    task_map_generation: str,
    stage: str,
    operation_ids: set[str],
    attempt_rows: list[dict[str, Any]],
) -> int:
    """Advance past durable attempts and pre-attempt session bindings."""

    highest_epoch = max(
        (
            int(row.get("placement_epoch") or 0)
            for row in attempt_rows
            if str(row.get("operation_id") or "") in operation_ids
        ),
        default=0,
    )
    from zf.core.state.role_sessions import RoleSessionRegistry

    registry = RoleSessionRegistry(
        Path(runtime.state_dir) / "role_sessions.yaml",
        project_root=str(runtime.project_root),
    )
    binding = registry.task_stage_binding(
        workflow_run_id=workflow_run_id,
        task_id=task_id,
        stage=stage,
        rework_affinity_id=f"{task_map_generation}:{stage}",
    )
    if binding is not None:
        highest_epoch = max(
            highest_epoch,
            int(binding.get("current_placement_epoch") or 0),
        )
    return highest_epoch + 1


def _task_instruction(task: Any) -> str:
    contract = getattr(task, "contract", None)
    return str(
        getattr(contract, "behavior", "")
        or getattr(task, "title", "")
        or f"Complete Task {task.id}"
    ).strip()


def _prepare_acceptance_review_target(
    runtime: Any,
    *,
    task: Any,
    operation_rows: list[dict[str, Any]],
    operation_generation: int,
    workspace: Any,
    contract_snapshot: Mapping[str, Any],
    contract_descriptor: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref
    from zf.runtime.task_pipeline_runtime import (
        TaskPipelineRuntimeError,
        TaskPipelineWaiting,
        _git,
    )
    from zf.runtime.verification_result import validate_verification_result

    task_id = str(task.id)
    verify = next(
        (
            row for row in operation_rows
            if str(row.get("task_id") or "") == task_id
            and str(row.get("task_pipeline_stage") or "") == "verify"
            and int(row.get("operation_generation") or 0)
            == operation_generation
            and str(row.get("status") or "") == "settled"
        ),
        None,
    )
    descriptor = (
        verify.get("admitted_control_result_ref")
        if isinstance(verify, Mapping)
        else None
    )
    if not isinstance(descriptor, Mapping):
        raise TaskPipelineWaiting(
            "waiting_for_verification_result",
            "risk review requires the admitted Task Verify control result",
        )
    try:
        hydrated = hydrate_sidecar_ref(
            Path(runtime.state_dir),
            dict(descriptor),
            purpose="integration_acceptance_dispatch",
            actor="orchestrator",
        )
        verification = (
            hydrated.payload
            if isinstance(hydrated.payload, Mapping)
            else {}
        )
        validate_verification_result(verification, strict=True)
    except Exception as exc:
        raise TaskPipelineWaiting(
            "waiting_for_current_verification_result",
            str(exc),
        ) from exc
    target_commit = str(verification.get("target_commit") or "").strip()
    expected = {
        "workflow_run_id": str(
            contract_snapshot.get("workflow_run_id") or ""
        ),
        "task_id": task_id,
        "contract_revision": str(
            contract_snapshot.get("contract_revision") or ""
        ),
        "task_map_generation": str(
            contract_snapshot.get("task_map_generation") or ""
        ),
        "contract_snapshot_ref": str(contract_descriptor.get("ref") or ""),
        "contract_snapshot_digest": str(
            contract_descriptor.get("sha256") or ""
        ),
    }
    mismatches = [
        key for key, value in expected.items()
        if str(verification.get(key) or "") != value
    ]
    if mismatches or str(verification.get("verdict") or "") != "passed":
        raise TaskPipelineRuntimeError(
            "risk review verification input is stale or not passed: "
            + ", ".join(mismatches)
        )
    workspace_head = _git(Path(workspace.project_path), "rev-parse", "HEAD")
    if workspace_head != target_commit:
        raise TaskPipelineWaiting(
            "waiting_for_task_workspace_target",
            "risk review Task Workspace is not at the verified target commit",
        )
    contract = getattr(task, "contract", None)
    risk_class = str(getattr(contract, "risk_class", "") or "")
    admission_profile = str(
        getattr(contract, "integration_admission_profile", "") or ""
    )
    risk_policy = dict(
        (policy.get("integration_admission") or {}).get("risk_review") or {}
    )
    if (
        admission_profile != "risk_review"
        or not bool(risk_policy.get("enabled"))
        or risk_class not in set(risk_policy.get("for_risks") or [])
    ):
        raise TaskPipelineRuntimeError(
            "Task Contract risk review is not admitted by the frozen profile"
        )
    verification_ref = str(descriptor.get("ref") or "")
    verification_digest = str(descriptor.get("sha256") or "")
    return {
        "target_commit": target_commit,
        "exact_task_target_commit": target_commit,
        "target_snapshot_ref": str(
            verification.get("target_snapshot_ref") or ""
        ),
        "target_snapshot_digest": str(
            verification.get("target_snapshot_digest") or ""
        ),
        "verification_result_ref": verification_ref,
        "verification_result_digest": verification_digest,
        "risk_class": risk_class,
        "integration_admission_profile": admission_profile,
        "risk_review_timeout_seconds": int(
            risk_policy.get("timeout_seconds") or 0
        ),
        "risk_review_max_turns": int(risk_policy.get("max_turns") or 0),
        "risk_review_budget_usd": float(
            risk_policy.get("budget_usd") or 0.0
        ),
        "operation_limits": {
            "timeout_seconds": int(risk_policy.get("timeout_seconds") or 0),
            "cost_budget_usd": float(risk_policy.get("budget_usd") or 0.0),
        },
        "input_refs": [{
            **dict(descriptor),
            "source_id": "task-verification-result",
            "kind": "verification_result",
            "artifact_id": Path(verification_ref).name,
            "allowed_paths": ["$"],
        }],
    }


__all__ = ["dispatch_task_pipeline_stage"]
