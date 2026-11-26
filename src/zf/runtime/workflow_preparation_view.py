"""A shared read projection; requirement readiness is not preparation success."""

from __future__ import annotations

from collections.abc import Sequence

from zf.core.events.model import ZfEvent


def proposal_failure(events: Sequence[ZfEvent], request: dict, operation: dict) -> dict:
    """Find only the current request's admitted operation and exact failure receipt."""
    if (
        operation.get("status") != "settled"
        or not request.get("request_id")
        or not operation.get("operation_id")
        or not operation.get("request_hash")
        or request.get("synthesis_operation_id") != operation["operation_id"]
        or request.get("synthesis_operation_revision") != request.get("revision")
        or request.get("proposal_ref")
    ):
        return {}
    for event in reversed(events):
        payload = event.payload
        if (
            event.type == "workflow.synthesis.proposal.failed"
            and event.actor == "workflow-synthesis-consumer"
            and event.origin in {"kernel", ""}  # Older consumer receipts have no origin.
            and payload.get("phase") == "proposal_materialization"
            and payload.get("request_id") == request["request_id"]
            and payload.get("operation_id") == operation["operation_id"]
            and payload.get("request_hash") == operation["request_hash"]
        ):
            return {**payload, "event_id": event.id}
    return {}


def _materialization_failure(operation: dict, failure: dict) -> dict:
    attempt, limit = failure.get("attempt"), failure.get("retry_limit")
    reason = failure.get("reason")
    known_reason = isinstance(reason, str) and bool(reason.strip())
    known_attempt = type(attempt) is int and attempt > 0
    known_limit = type(limit) is int and limit >= 0
    retry = (
        bool(operation.get("admitted_call_result_ref"))
        and failure.get("requires_resynthesis") is False
        and known_reason
        and known_attempt
        and known_limit
        and attempt < limit
    )
    blockers = failure.get("blockers")
    if not isinstance(blockers, list) or not all(isinstance(row, dict) for row in blockers):
        blockers = []
    action = failure.get("next_action")
    return {
        "status": "preparation_failed",
        "phase": "proposal_materialization",
        "attempt": attempt if known_attempt else None,
        "retry_limit": limit if known_limit else None,
        "source_event_id": failure.get("event_id", ""),
        "can_retry": retry,
        "blockers": blockers
        or [
            {
                "kind": "proposal_materialization_failed",
                "severity": "STOP",
                "message": reason
                if known_reason
                else "Workflow proposal materialization failed; cause unavailable",
                "fix_it": action if isinstance(action, str) else "",
            }
        ],
    }


def preparation_view(
    request: dict,
    operation: dict,
    *,
    git_blockers: list[dict] | None = None,
    failure: dict | None = None,
) -> dict:
    status = str(request.get("status") or "")
    result = {"status": status, "blockers": [], "can_retry": False}
    if request.get("run_id") or status not in {"ready", "draft", "clarifying"}:
        return result
    op_status = str(operation.get("queue_status") or operation.get("status") or "")
    if request.get("synthesis_operation_revision") not in (None, request.get("revision")):
        op_status = ""  # A prior revision cannot override the current requirement.
    if op_status == "cancelled":
        return {**result, "status": "cancelled"}
    if status != "ready":
        return result
    if git_blockers:
        return {"status": "preparation_blocked", "blockers": git_blockers, "can_retry": True}
    if op_status == "settled" and failure:
        return _materialization_failure(operation, failure)
    if op_status in {"failed", "blocked"}:
        message = str(operation.get("reason") or "Workflow preparation failed")
        return {"status": "preparation_failed", "can_retry": True,
                "blockers": [{"kind": "workflow_preparation_failed", "severity": "STOP",
                              "message": message, "fix_it": "修复原因后重试准备；保留既有 Task 与需求。"}]}
    if op_status in {"requested", "reserved", "queued", "running", "settled"}:
        return {**result, "status": "synthesis_queued" if op_status in {"requested", "reserved", "queued"}
                else "synthesis_running"}
    return result
