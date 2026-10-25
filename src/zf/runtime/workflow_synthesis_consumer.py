"""Durable consumer for queued Workflow Synthesis operations."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.state.locks import locked_path
from zf.runtime.workflow_operation import (
    WorkflowOperationService,
    reduce_workflow_operations,
)
from zf.runtime.workflow_requests import load_workflow_request


def consume_workflow_synthesis_operations(
    *,
    state_dir: Path,
    project_root: Path,
    config: Any,
    writer: EventWriter,
    agent: Any | None = None,
    limit: int = 1,
    request_id: str = "",
) -> int:
    """Consume durable synthesis operations with a process-safe lease."""

    from zf.runtime import workflow_synthesis as synthesis

    state_dir = Path(state_dir)
    consumed = 0
    lock = state_dir / "projections" / "workflow-synthesis-consumer.guard"
    service = WorkflowOperationService(
        state_dir=state_dir,
        event_log=writer.event_log,
        event_writer=writer,
    )
    with locked_path(lock, timeout_seconds=0.1):
        operations = reduce_workflow_operations(writer.event_log.read_all())
        candidates = [
            operation
            for operation in operations.values()
            if operation.get("operation_type")
            == synthesis.WORKFLOW_SYNTHESIS_OPERATION_TYPE
            and operation.get("status") in {"requested", "running", "settled"}
        ]
        candidates.sort(
            key=lambda item: str(item.get("last_event_at") or "")
        )
        if request_id:
            revision = load_workflow_request(state_dir, request_id).get("revision")
            candidates = [op for op in candidates if (
                (body := synthesis._hydrate_operation_request(state_dir, op)).get("request_id") == request_id
                and body.get("request_revision") == revision)]
        for operation in candidates[:max(1, int(limit or 1))]:
            try:
                request_body = synthesis._hydrate_operation_request(
                    state_dir,
                    operation,
                )
            except Exception as exc:
                _record_failure(
                    writer=writer,
                    service=service,
                    operation=operation,
                    request_id="",
                    phase="request_hydration",
                    error=exc,
                    retry_limit=synthesis.WORKFLOW_SYNTHESIS_PROPOSAL_RETRY_LIMIT,
                    terminalize=True,
                )
                consumed += 1
                continue
            request_id = str(request_body.get("request_id") or "")
            request_projection = load_workflow_request(state_dir, request_id)
            operation_revision = int(
                request_body.get("request_revision") or 0
            )
            current_revision = int(
                request_projection.get("revision") or 0
            )
            if current_revision != operation_revision:
                service.supersede(
                    operation_id=str(operation.get("operation_id") or ""),
                    request_hash=str(operation.get("request_hash") or ""),
                    workflow_run_id=str(
                        operation.get("workflow_run_id") or ""
                    ),
                    reason=(
                        "workflow request revision superseded "
                        f"{operation_revision} -> {current_revision}"
                    ),
                    causation_id=str(operation.get("last_event_id") or ""),
                    correlation_id=request_id,
                )
                consumed += 1
                continue
            if str(request_projection.get("status") or "") in {
                "proposed",
                "approved",
                "submitted",
                "running",
            }:
                continue
            if (
                operation.get("status") == "settled"
                and request_projection.get("synthesis_digest")
                and str(request_projection.get("status") or "")
                == "clarifying"
            ):
                continue
            actor = str(request_body.get("actor") or "workflow-synthesis")
            backend = str(request_body.get("backend") or "")
            operation_context = (
                request_body.get("operation_context")
                if isinstance(request_body.get("operation_context"), Mapping)
                else {}
            )
            try:
                outcome = synthesis.run_workflow_synthesis(
                    state_dir=state_dir,
                    project_root=project_root,
                    config=config,
                    writer=writer,
                    request_id=request_id,
                    actor=actor,
                    backend=backend,
                    agent=agent,
                    operation_context=operation_context,
                    causation_id=str(operation.get("last_event_id") or ""),
                    resume_running=operation.get("status") == "running",
                )
            except synthesis.WorkflowSynthesisError:
                consumed += 1
                continue
            if outcome.request_projection.get("status") == "ready":
                try:
                    synthesis._build_synthesis_proposal(
                        state_dir=state_dir,
                        outcome=outcome,
                        operation_context=operation_context,
                        actor=actor,
                    )
                except Exception as exc:
                    _record_failure(
                        writer=writer,
                        service=service,
                        operation=operation,
                        request_id=request_id,
                        phase="proposal_materialization",
                        error=exc,
                        retry_limit=(
                            synthesis.WORKFLOW_SYNTHESIS_PROPOSAL_RETRY_LIMIT
                        ),
                    )
            consumed += 1
    return consumed


def _record_failure(
    *,
    writer: EventWriter,
    service: WorkflowOperationService,
    operation: Mapping[str, Any],
    request_id: str,
    phase: str,
    error: Exception,
    retry_limit: int,
    terminalize: bool = False,
) -> None:
    operation_id = str(operation.get("operation_id") or "")
    request_hash = str(operation.get("request_hash") or "")
    workflow_run_id = str(operation.get("workflow_run_id") or "")
    event_type = (
        "workflow.synthesis.proposal.failed"
        if phase == "proposal_materialization"
        else "workflow.synthesis.failed"
    )
    previous_attempts = sum(
        1
        for event in writer.event_log.read_all()
        if event.type == event_type
        and str((event.payload or {}).get("operation_id") or "")
        == operation_id
        and str((event.payload or {}).get("phase") or "") == phase
    )
    attempt = previous_attempts + 1
    exhausted = (
        phase == "proposal_materialization"
        and attempt >= retry_limit
    )
    reason = str(error)[:512] or type(error).__name__
    writer.append(ZfEvent(
        type=event_type,
        actor="workflow-synthesis-consumer",
        causation_id=str(operation.get("last_event_id") or "") or None,
        correlation_id=request_id or workflow_run_id or None,
        payload={
            "request_id": request_id,
            "operation_id": operation_id,
            "request_hash": request_hash,
            "phase": phase,
            "attempt": attempt,
            "retry_limit": (
                retry_limit if phase == "proposal_materialization" else 0
            ),
            "reason": reason,
            "next_action": (
                "repair request sidecar and submit a new revision"
                if terminalize
                else (
                    "inspect proposal materialization failure"
                    if exhausted
                    else "retry proposal materialization"
                )
            ),
        },
    ))
    if not terminalize and not exhausted:
        return
    service.fail(
        operation_id=operation_id,
        request_hash=request_hash,
        workflow_run_id=workflow_run_id,
        reason=f"{phase}: {reason}",
        causation_id=str(operation.get("last_event_id") or ""),
        correlation_id=request_id or workflow_run_id,
    )


__all__ = ["consume_workflow_synthesis_operations"]
