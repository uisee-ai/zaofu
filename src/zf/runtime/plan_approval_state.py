"""Mechanical Plan approval waits shared by idle and recovery consumers."""

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.state.locks import locked_path


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
            if event.type == "plan.approved" and payload.get("semantic_control") and plan_id in pending:
                continue
            pending.pop(plan_id, None)
            continue
        pending[plan_id] = {
            str(payload.get("workflow_run_id") or ""),
            str(payload.get("run_id") or ""),
            str(payload.get("trace_id") or ""),
            str(event.correlation_id or ""),
        } - {""}
    return any(not workflow_run_id or workflow_run_id in scope for scope in pending.values())


def decide_pending_plan(
    writer: EventWriter,
    *,
    state_dir: Path,
    plan_id: str,
    event_type: str,
    payload: dict[str, Any],
    correlation_id: str | None = None,
    expected_request_id: str = "",
) -> tuple[ZfEvent | None, str]:
    """Serialize operator decisions and bind them to an open Plan request."""
    if event_type not in {"plan.approved", "plan.rejected"}:
        raise ValueError("invalid plan decision event")
    with locked_path(Path(state_dir) / "locks" / "plan-approval-decisions"):
        pending: ZfEvent | None = None
        for event in writer.event_log.read_all():
            body = event.payload if isinstance(event.payload, dict) else {}
            if str(body.get("plan_id") or "") != plan_id:
                continue
            if event.type == "plan.approval.requested":
                pending = event
            elif event.type in {"plan.approved", "plan.rejected"}:
                if event.type == "plan.approved" and body.get("semantic_control") and pending is not None:
                    continue
                pending = None
        if pending is None:
            return None, "plan is not awaiting review"
        if expected_request_id and pending.id != expected_request_id:
            return None, "plan approval request has changed"
        request = pending.payload if isinstance(pending.payload, dict) else {}
        bound = {
            key: request[key]
            for key in ("stage_id", "trace_id", "pdd_id", "feature_id", "task_map_ref",
                        "digest_ref", "task_count", "workflow_run_id", "task_map_generation")
            if request.get(key) not in (None, "")
        }
        return writer.emit(
            event_type,
            actor="operator",
            causation_id=plan_id,
            correlation_id=correlation_id,
            payload={**bound, **payload, "plan_id": plan_id, "request_event_id": pending.id},
        ), ""
