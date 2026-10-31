"""Bind synthesis attempts and reuse admitted semantics after preparation repair."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from zf.core.events.writer import EventWriter
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import load_workflow_operation


def validate_recovery_binding(
    state_dir: Path, request: Mapping[str, Any], *, writer: EventWriter | None,
    operation_id: str, request_hash: str, retry_of_operation_id: str,
) -> None:
    from zf.runtime.workflow_request_concurrency import WorkflowRequestError
    error = "workflow request already has a different synthesis operation"
    if not writer or not retry_of_operation_id or retry_of_operation_id != request.get("synthesis_operation_id"):
        raise WorkflowRequestError(error)
    previous = load_workflow_operation(writer.event_log, retry_of_operation_id) or {}
    current = load_workflow_operation(writer.event_log, operation_id) or {}
    if (previous.get("status") not in {"failed", "blocked"}
            or previous.get("request_hash") != request.get("synthesis_request_hash")
            or current.get("parent_operation_id") != retry_of_operation_id
            or current.get("request_hash") != request_hash
            or current.get("status") != "requested"):
        raise WorkflowRequestError(error)
    for operation in (previous, current):
        body = hydrate_sidecar_ref(state_dir, operation["request_ref"]).payload["request"]
        if (body.get("request_id") != request["request_id"]
                or body.get("request_revision") != request["revision"]
                or body.get("requirement_digest") != request["requirement_spec_digest"]):
            raise WorkflowRequestError("preparation retry targets a different requirement")


def reusable_synthesis_candidate(
    state_dir: Path, context: Mapping[str, Any], *, request: Mapping[str, Any], writer: EventWriter,
) -> dict | None:
    descriptor = context.get("reusable_synthesis_result_ref")
    if not descriptor:
        return None
    from zf.runtime.workflow_synthesis import _RESULT_KEYS, _FLOW_SPEC_KEYS, WorkflowSynthesisError
    previous = load_workflow_operation(writer.event_log, str(context.get("retry_of_operation_id") or "")) or {}
    if (previous.get("status") not in {"failed", "blocked"}
            or previous.get("admitted_call_result_ref") != descriptor):
        raise WorkflowSynthesisError("preparation recovery does not match the failed admitted operation")
    body = hydrate_sidecar_ref(state_dir, descriptor).payload
    if (body.get("request_id") != request.get("request_id")
            or body.get("request_revision") != request.get("revision")
            or body.get("requirement_digest") != request.get("requirement_spec_digest")):
        raise WorkflowSynthesisError("preparation recovery requirement binding is stale")
    result = {k: v for k, v in body.items() if k in _RESULT_KEYS}
    short_spec = hydrate_sidecar_ref(state_dir, body["short_flow_spec_ref"]).payload
    result["short_flow_spec"] = {k: v for k, v in short_spec.items() if k in _FLOW_SPEC_KEYS}
    # The normal admission path rechecks today's controller/catalog digest and
    # capabilities, then binds the result to the new operation. No failed event
    # is erased, no old operation is reopened, and no provider turn is needed.
    return result


def bind_workflow_synthesis_operation(
    state_dir: Path,
    *,
    request_id: str,
    request_revision: int,
    operation_id: str,
    request_hash: str,
    actor: str,
    writer: EventWriter | None = None,
    retry_of_operation_id: str = "",
) -> dict[str, Any]:
    """Expose the durable synthesis owner on the request projection."""

    from zf.runtime.workflow_requests import load_workflow_request, _write_projection, _now_iso, _emit
    from zf.runtime.workflow_request_concurrency import WorkflowRequestError

    projection = load_workflow_request(state_dir, request_id)
    if not projection:
        raise WorkflowRequestError(f"workflow request not found: {request_id}")
    if int(projection.get("revision") or 0) != int(request_revision):
        raise WorkflowRequestError(
            "workflow synthesis operation targets a stale request revision"
        )
    existing_id = str(projection.get("synthesis_operation_id") or "")
    existing_hash = str(projection.get("synthesis_request_hash") or "")
    if existing_id and (
        existing_id != operation_id or existing_hash != request_hash
    ):
        validate_recovery_binding(state_dir, projection, writer=writer,
                                  operation_id=operation_id, request_hash=request_hash,
                                  retry_of_operation_id=retry_of_operation_id)
    if existing_id == operation_id and existing_hash == request_hash:
        return projection
    projection = dict(projection)
    projection.update({
        "synthesis_operation_id": str(operation_id),
        "synthesis_request_hash": str(request_hash),
        "synthesis_operation_revision": int(request_revision),
        "updated_at": _now_iso(),
    })
    _write_projection(state_dir, projection)
    _emit(
        writer,
        "workflow.synthesis.operation.bound",
        projection,
        actor=actor,
        extra={
            "operation_id": str(operation_id),
            "request_hash": str(request_hash),
            "request_revision": int(request_revision),
            "retry_of_operation_id": retry_of_operation_id,
        },
    )
    return projection
