"""A shared read projection; requirement readiness is not preparation success."""
from __future__ import annotations


def preparation_view(request: dict, operation: dict, *, git_blockers: list[dict] | None = None) -> dict:
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
    if op_status in {"failed", "blocked"}:
        message = str(operation.get("reason") or "Workflow preparation failed")
        return {"status": "preparation_failed", "can_retry": True,
                "blockers": [{"kind": "workflow_preparation_failed", "severity": "STOP",
                              "message": message, "fix_it": "修复原因后重试准备；保留既有 Task 与需求。"}]}
    if op_status in {"requested", "reserved", "queued", "running", "settled"}:
        return {**result, "status": "synthesis_queued" if op_status in {"requested", "reserved", "queued"}
                else "synthesis_running"}
    return result
