"""Deterministic Candidate integration bridge for Task Pipeline v4."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from zf.runtime.workflow_runtime_types import WorkflowRuntimeDecision


def reconcile_task_pipeline_integration(
    runtime: Any,
    *,
    projection: Mapping[str, Any],
    generation_contexts: Mapping[str, Mapping[str, Any]],
    operation_rows: list[dict[str, Any]],
) -> list[WorkflowRuntimeDecision]:
    """Drain at most one ready Task through the serial Candidate reducer."""

    ready = list(
        (projection.get("queues") or {}).get("integration_ready") or []
    )
    if not ready:
        return []
    task_id = str(ready[0] or "").strip()
    context = generation_contexts.get(task_id)
    task = runtime.task_store.get(task_id)
    if not task_id or context is None or task is None:
        return []
    source = _current_integration_source(
        runtime,
        operation_rows=operation_rows,
        task=task,
        context=context,
    )
    if source is None:
        from zf.runtime.task_pipeline_dispatch_events import emit_task_pipeline_waiting_once

        emit_task_pipeline_waiting_once(
            runtime, task_id=task_id, stage="integration",
            operation_generation=_integration_generation(projection, task_id=task_id, fallback=1),
            context=context, reason="waiting_for_current_verify_result",
            detail="integration requires the latest admitted Verify result bound to the current task contract and TaskRef",
        )
        return []
    generation = _integration_generation(
        projection,
        task_id=task_id,
        fallback=int(source.get("operation_generation") or 1),
    )
    from zf.runtime.candidates import CandidateRebuilder

    rebuilder = CandidateRebuilder(
        state_dir=Path(runtime.state_dir),
        project_root=Path(runtime.project_root),
        config=runtime.config,
        event_log=runtime.event_log,
    )
    result = rebuilder.integrate_task_pipeline_task(
        task_id=task_id,
        workflow_run_id=str(context.get("workflow_run_id") or ""),
        task_map_generation=str(context.get("task_map_generation") or ""),
        operation_generation=generation,
        pipeline_key=str(source.get("pipeline_key") or ""),
        dispatch_base_commit=str(context.get("dispatch_base_commit") or ""),
        contract_revision=_contract_revision(task),
        event_writer=runtime.event_writer,
        causation_id=str(
            source.get("call_result_admitted_event_id")
            or source.get("source_event_id")
            or context.get("generation_admitted_event_id")
            or ""
        ),
    )
    return [WorkflowRuntimeDecision(
        action=(
            "task_pipeline_integrated"
            if result.status == "integrated"
            else "task_pipeline_integration_blocked"
        ),
        task_id=task_id,
        role="candidate-integrator",
        reason=(
            "Task integration receipt admitted"
            if result.status == "integrated"
            else f"Task integration requires review: {result.status}"
        ),
    )]


def _current_verify_operation(
    operations: list[dict[str, Any]],
    task_id: str,
) -> dict[str, Any] | None:
    candidates = [
        row
        for row in operations
        if str(row.get("task_id") or "") == task_id
        and str(row.get("task_pipeline_stage") or "") == "verify"
    ]
    latest = max(
        candidates,
        key=lambda row: (
            int(row.get("operation_generation") or 1),
            str(row.get("operation_id") or ""),
        ),
        default=None,
    )
    if latest is None or latest.get("status") != "settled":
        return None
    if str(latest.get("semantic_verdict") or "").lower() not in {"passed", "pass"}:
        return None
    return latest


def _verify_result_is_current(
    runtime: Any, *, operation: Mapping[str, Any], task: Any,
    context: Mapping[str, Any],
) -> bool:
    """Consume admitted immutable evidence, not a transport settlement label."""
    from zf.runtime.sidecar_refs import SidecarRefError, hydrate_sidecar_ref

    descriptor = operation.get("admitted_control_result_ref")
    if not operation.get("call_result_admitted_event_id") or not isinstance(descriptor, Mapping):
        return False
    if not descriptor.get("ref") or not descriptor.get("sha256"):
        return False
    try:
        body = hydrate_sidecar_ref(Path(runtime.state_dir), dict(descriptor)).payload
    except (SidecarRefError, OSError, ValueError):
        return False
    if not isinstance(body, Mapping) or body.get("schema_version") != "verification-result.v1":
        return False
    if body.get("verdict") != "passed":
        return False
    expected = {
        "task_id": str(task.id),
        "workflow_run_id": str(context.get("workflow_run_id") or ""),
        "task_map_generation": str(context.get("task_map_generation") or ""),
        "contract_revision": _contract_revision(task),
    }
    if any(not value or str(body.get(key) or "") != value for key, value in expected.items()):
        return False
    if any(str(operation.get(key) or "") != expected[key] for key in (
        "task_id", "workflow_run_id", "task_map_generation",
    )):
        return False
    entry = runtime._task_ref_entry(str(task.id))
    return bool(entry and entry.get("source_commit") == body.get("target_commit"))


def _current_integration_source(
    runtime: Any,
    *,
    operation_rows: list[dict[str, Any]],
    task: Any,
    context: Mapping[str, Any],
) -> dict[str, Any] | None:
    verify = _current_verify_operation(operation_rows, str(task.id))
    if verify is not None and _verify_result_is_current(
        runtime, operation=verify, task=task, context=context,
    ):
        return verify
    from zf.runtime.task_pipeline_entry import task_pipeline_entry_mode

    if task_pipeline_entry_mode(task) != "external_gate":
        return None
    workflow_run_id = str(context.get("workflow_run_id") or "")
    task_map_generation = str(context.get("task_map_generation") or "")
    for event in reversed(runtime.event_log.read_all()):
        if event.type != "task.pipeline.external_gate.satisfied":
            continue
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        if (
            str(event.task_id or "") == str(task.id)
            and str(payload.get("workflow_run_id") or "") == workflow_run_id
            and str(payload.get("task_map_generation") or "")
            == task_map_generation
        ):
            return {
                **dict(payload),
                "source_event_id": event.id,
            }
    return None


def _contract_revision(task: Any) -> str:
    from zf.runtime.task_contract_snapshot import effective_contract_revision

    return effective_contract_revision(task)


def _integration_generation(
    projection: Mapping[str, Any],
    *,
    task_id: str,
    fallback: int,
) -> int:
    for raw in projection.get("tasks") or []:
        if not isinstance(raw, Mapping):
            continue
        if str(raw.get("task_id") or "") != task_id:
            continue
        try:
            generation = int(raw.get("next_operation_generation") or fallback)
        except (TypeError, ValueError):
            return fallback
        return generation if generation > 0 else fallback
    return fallback


__all__ = ["reconcile_task_pipeline_integration"]
