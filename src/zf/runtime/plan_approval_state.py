"""Mechanical Plan approval waits shared by idle and recovery consumers."""

from collections.abc import Iterable

from zf.core.events.model import ZfEvent


def has_pending_plan_approval(
    events: Iterable[ZfEvent], *, workflow_run_id: str = "",
) -> bool:
    pending: dict[str, set[str]] = {}
    for event in events:
        if event.type not in {"plan.approval.requested", "plan.approved", "plan.rejected"}:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        plan_id = str(payload.get("plan_id") or "")
        if not plan_id:
            continue
        if event.type != "plan.approval.requested":
            pending.pop(plan_id, None)
            continue
        pending[plan_id] = {
            str(payload.get("workflow_run_id") or ""),
            str(payload.get("run_id") or ""),
            str(payload.get("trace_id") or ""),
            str(event.correlation_id or ""),
        } - {""}
    return any(not workflow_run_id or workflow_run_id in scope for scope in pending.values())
