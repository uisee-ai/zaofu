"""Exact contract, target, and self-check admission for Task Pipeline stages."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from zf.core.events.model import ZfEvent


def prepare_contract_snapshot(
    runtime: Any,
    *,
    task: Any = None,
    generation_context: Mapping[str, Any] | None = None,
    workspace: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    from zf.runtime.task_contract_snapshot import (
        build_task_contract_snapshot,
        write_task_contract_snapshot,
    )

    snapshot = build_task_contract_snapshot(
        task,
        workflow_run_id=str(generation_context.get("workflow_run_id") or ""),
        task_map_generation_id=str(
            generation_context.get("task_map_generation") or ""
        ),
        base_commit=str(workspace.base_commit),
        task_ref=f"{runtime.config.runtime.git.task_ref_prefix}/{task.id}",
    )
    descriptor = write_task_contract_snapshot(
        Path(runtime.state_dir),
        snapshot,
        source_event_id=str(
            generation_context.get("generation_admitted_event_id") or ""
        ),
    )
    return snapshot, descriptor


def prepare_verify_target(
    runtime: Any,
    *,
    task_id: str,
    workflow_run_id: str,
    task_map_generation: str,
    operation_generation: int,
    workspace: Any,
    contract_snapshot: Mapping[str, Any],
    contract_descriptor: Mapping[str, Any],
    task: Any = None,
    generation_context: Mapping[str, Any] | None = None,
    entry_mode: str = "standard",
) -> dict[str, Any]:
    from zf.runtime.task_pipeline_runtime import (
        TaskPipelineRuntimeError,
        TaskPipelineWaiting,
        _git,
    )

    expected_workdir = Path(workspace.project_path).resolve()
    if entry_mode == "verify_only":
        if task is None or generation_context is None:
            raise TaskPipelineRuntimeError(
                "verify-only target requires Task and generation context"
            )
        from zf.runtime.task_pipeline_entry import (
            admit_task_pipeline_read_only_ref,
            task_pipeline_entry_target,
        )

        target_commit = task_pipeline_entry_target(
            task,
            generation_context,
            project_root=Path(runtime.project_root),
        )
        workspace_head = _git(expected_workdir, "rev-parse", "HEAD")
        if workspace_head != target_commit:
            raise TaskPipelineRuntimeError(
                "verify-only Task Workspace is not at the immutable target"
            )
        entry = admit_task_pipeline_read_only_ref(
            runtime,
            task=task,
            context=generation_context,
            target_commit=target_commit,
            causation_id=str(
                generation_context.get("generation_admitted_event_id") or ""
            ),
            workdir=str(expected_workdir),
        )
    else:
        entry = runtime._task_ref_entry(task_id)
    source_commit = str(entry.get("source_commit") or "").strip()
    if not source_commit:
        raise TaskPipelineWaiting(
            "waiting_for_task_ref",
            "implementation settled but exact TaskRef is not admitted yet",
        )
    actual_workdir_raw = str(entry.get("workdir") or "").strip()
    if not actual_workdir_raw:
        raise TaskPipelineWaiting(
            "waiting_for_task_ref_currentness",
            "TaskRef has no Task Workspace binding",
        )
    actual_workdir = Path(actual_workdir_raw)
    if not actual_workdir.is_absolute():
        actual_workdir = Path(runtime.project_root) / actual_workdir
    if actual_workdir.resolve() != expected_workdir:
        raise TaskPipelineRuntimeError(
            "TaskRef workdir does not match the admitted Task Workspace"
        )
    workspace_head = _git(expected_workdir, "rev-parse", "HEAD")
    if workspace_head != source_commit:
        raise TaskPipelineWaiting(
            "waiting_for_task_ref_currentness",
            "TaskRef commit is not the current Task Workspace HEAD",
        )
    task_ref = str(entry.get("task_ref") or "").strip()
    expected_task_ref = str(contract_snapshot.get("task_ref") or "")
    if task_ref != expected_task_ref:
        raise TaskPipelineRuntimeError(
            "TaskRef identity differs from the Contract Snapshot"
        )

    from zf.runtime.task_contract_snapshot import (
        build_target_snapshot,
        target_payload_fields,
        write_target_snapshot,
    )

    target = build_target_snapshot(
        contract_descriptor,
        target_commit=source_commit,
        contract_snapshot=contract_snapshot,
    )
    target_descriptor = write_target_snapshot(
        Path(runtime.state_dir),
        target,
        source_event_id=str(entry.get("trigger_event_id") or ""),
    )
    fields = {
        "target_commit": source_commit,
        "target_snapshot_ref": str(target_descriptor.get("ref") or ""),
        "target_snapshot_digest": str(
            target_descriptor.get("sha256") or ""
        ),
        **target_payload_fields(target_descriptor),
    }
    if entry_mode == "verify_only":
        return fields
    from zf.runtime.task_pipeline_verify_retry import verify_impl_generation

    self_check_descriptor = admit_impl_self_check(
        runtime,
        task_id=task_id,
        workflow_run_id=workflow_run_id,
        task_map_generation=task_map_generation,
        operation_generation=verify_impl_generation(
            runtime.event_log.read_all(), task_id=task_id,
            workflow_run_id=workflow_run_id, task_map_generation=task_map_generation,
            operation_generation=operation_generation,
        ),
        source_commit=source_commit,
        contract_snapshot=contract_snapshot,
        target_snapshot=target,
    )
    from zf.runtime.impl_self_check import self_check_payload_fields

    return {**fields, **self_check_payload_fields(self_check_descriptor)}


def admit_impl_self_check(
    runtime: Any,
    *,
    task_id: str,
    workflow_run_id: str,
    task_map_generation: str,
    operation_generation: int,
    source_commit: str,
    contract_snapshot: Mapping[str, Any],
    target_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    from zf.runtime.impl_self_check import (
        descriptor_from_payload,
        hydrate_impl_self_check,
        normalize_impl_self_check,
        self_check_payload_fields,
        write_impl_self_check,
    )
    from zf.runtime.task_pipeline_runtime import TaskPipelineWaiting

    events = runtime.event_log.read_all()
    for event in reversed(events):
        if event.type != "impl.self_check.completed" or event.task_id != task_id:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if (
            str(payload.get("workflow_run_id") or "") == workflow_run_id
            and str(payload.get("task_map_generation") or "")
            == task_map_generation
            and str(payload.get("target_commit") or "") == source_commit
            and int(payload.get("operation_generation") or 0)
            == operation_generation
        ):
            descriptor = descriptor_from_payload(payload)
            hydrate_impl_self_check(
                Path(runtime.state_dir),
                descriptor,
                contract_snapshot=contract_snapshot,
                target_snapshot=target_snapshot,
            )
            return descriptor

    result_event = next(
        (
            event
            for event in reversed(events)
            if event.type == "dev.build.done"
            and event.task_id == task_id
            and isinstance(event.payload, dict)
            and str(event.payload.get("workflow_run_id") or "")
            == workflow_run_id
            and str(event.payload.get("task_map_generation") or "")
            == task_map_generation
            and int(event.payload.get("operation_generation") or 0)
            == operation_generation
            and str(event.payload.get("source_commit") or "")
            == source_commit
        ),
        None,
    )
    if result_event is None:
        raise TaskPipelineWaiting(
            "waiting_for_impl_result",
            "TaskRef exists but the matching typed implementation result is absent",
        )
    result_payload = result_event.payload
    if (
        result_payload.get("impl_self_check_ref")
        and result_payload.get("impl_self_check_digest")
    ):
        descriptor = descriptor_from_payload(result_payload)
        hydrate_impl_self_check(
            Path(runtime.state_dir),
            descriptor,
            contract_snapshot=contract_snapshot,
            target_snapshot=target_snapshot,
        )
        return descriptor
    if not isinstance(result_payload.get("impl_self_check"), Mapping):
        raise TaskPipelineWaiting(
            "waiting_for_impl_self_check",
            "typed implementation result has no admitted impl self-check",
        )
    attempt_id = str(
        result_payload.get("attempt_id")
        or result_payload.get("dispatch_id")
        or ""
    )
    body = normalize_impl_self_check(
        result_payload,
        contract_snapshot=contract_snapshot,
        target_snapshot=target_snapshot,
        expected_attempt_id=attempt_id,
        strict=True,
    )
    descriptor = write_impl_self_check(
        Path(runtime.state_dir),
        body,
        source_event_id=result_event.id,
        created_by=str(result_event.actor or "worker"),
    )
    fields = self_check_payload_fields(descriptor)
    runtime.event_writer.append(ZfEvent(
        type="impl.self_check.completed",
        actor="orchestrator",
        origin="kernel",
        task_id=task_id,
        payload={
            **fields,
            "workflow_run_id": workflow_run_id,
            "task_map_generation": task_map_generation,
            "operation_generation": operation_generation,
            "contract_revision": str(
                contract_snapshot.get("contract_revision") or ""
            ),
            "target_commit": source_commit,
            "attempt_id": str(body.get("attempt_id") or ""),
        },
        causation_id=result_event.id,
        correlation_id=workflow_run_id,
    ))
    return descriptor


__all__ = [
    "admit_impl_self_check",
    "prepare_contract_snapshot",
    "prepare_verify_target",
]
