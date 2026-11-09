"""Currentness guard for owner decisions submitted from the Inbox."""

from __future__ import annotations

from typing import Any, Callable

from zf.core.events import ZfEvent
from zf.core.state.locks import locked_path


def execute_inbox_replan(
    service: Any,
    *,
    requested: ZfEvent,
    action: str,
    requested_action: str,
    payload: dict[str, Any],
    handler: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    inbox_source = str(payload.get("source") or service.source) == "operator-inbox"
    approval_ref = str(payload.get("approval_ref") or "").strip()
    with locked_path(service.state_dir / "locks" / "inbox-replan-decisions"):
        pending = None
        had_request = False
        events = service.writer.event_log.read_all()
        for event in events:
            body = event.payload if isinstance(event.payload, dict) else {}
            if str(body.get("approval_ref") or "") != approval_ref:
                continue
            if event.type == "approval.requested":
                had_request = True
                pending = event
            elif event.type in {"approval.resolved", "approval.expired", "approval.rejected_by_policy"}:
                pending = None
        if not inbox_source and not had_request:
            return handler(requested=requested, action=action, requested_action=requested_action, payload=payload)
        if not approval_ref or pending is None:
            return _conflict(action, "approval is not awaiting an owner decision")
        from zf.runtime.operator_inbox import _owner_preview_payload

        body = _owner_preview_payload(
            pending.payload if isinstance(pending.payload, dict) else {},
            events_by_id={event.id: event for event in events},
            state_dir=service.state_dir,
            project_root=service.project_root,
        )
        expected_event = str(payload.get("created_event_id") or "")
        if expected_event and expected_event != pending.id:
            return _conflict(action, "approval request has changed")
        guarded_payload = dict(payload)
        for key in ("checkpoint_id", "workflow_run_id", "fanout_id", "source_event_id", "proposal_ref", "eval_ref", "candidate_task_map_ref"):
            if key in payload and str(payload.get(key) or "") != str(body.get(key) or ""):
                return _conflict(action, f"approval {key} has changed")
            if key not in guarded_payload and body.get(key):
                guarded_payload[key] = body[key]
        return handler(requested=requested, action=action, requested_action=requested_action, payload=guarded_payload)


def _conflict(action: str, reason: str) -> dict[str, Any]:
    return {"_status_code": 409, "ok": False, "status": "stale_decision", "action": action, "reason": reason}
