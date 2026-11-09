"""Validate and record an operator decision against a current escalation."""

from __future__ import annotations

from typing import Any

from zf.core.events import EventWriter, ZfEvent
from zf.core.state.locks import locked_path
from zf.runtime.control_actions_helpers import _task_id_from_payload
from zf.runtime.run_manager import (
    _human_decision_event_token, _human_escalations_by_token, _pending_human_decisions,
)


DECISIONS = {
    "human-decision-approve-controlled-action": "approve_controlled_action",
    "human-decision-request-autoresearch": "request_autoresearch",
    "human-decision-safe-halt": "safe_halt",
    "human-decision-dismiss": "dismissed",
}


def submit_human_decision(
    writer: EventWriter,
    *,
    requested: ZfEvent,
    action: str,
    requested_action: str,
    payload: dict[str, Any],
    project_id: str = "",
) -> dict[str, Any]:
    token = str(payload.get("decision_token") or payload.get("approval_ref") or "")
    token = token.removeprefix("human:").strip()
    with locked_path(writer.event_log.path.parent / "locks" / "human-decision"):
        events = writer.event_log.read_all()
        escalation = _human_escalations_by_token(events).get(token)
        if not escalation:
            return _conflict(action, requested_action, "human decision is unavailable")
        if not any(str(escalation.get(key) or "").strip() for key in ("checkpoint_id", "approval_command")):
            return _conflict(action, requested_action, "human decision needs contract verification")
        if token not in {row["decision_token"] for row in _pending_human_decisions(events)}:
            return _conflict(action, requested_action, "human decision is no longer current")
        for key in ("checkpoint_id", "fingerprint"):
            expected = str(payload.get(key) or "")
            if expected and expected != str(escalation.get(key) or ""):
                return _conflict(action, requested_action, f"{key} has changed")
        expected_event = str(payload.get("created_event_id") or "")
        source_ids = {str(escalation.get("event_id") or ""), *(str(x) for x in escalation.get("source_event_ids") or [])}
        if expected_event and expected_event not in source_ids:
            return _conflict(action, requested_action, "human decision has changed")
        for event in events:
            if event.type not in {"human.escalation.acknowledged", "run.manager.human_decision.applied", "run.manager.human_decision.rejected"}:
                continue
            body = event.payload if isinstance(event.payload, dict) else {}
            if _human_decision_event_token(event, body) == token:
                return _conflict(action, requested_action, "human decision was already submitted")
        decision = DECISIONS[action]
        acknowledged = writer.emit(
            "human.escalation.acknowledged",
            actor="web",
            task_id=_task_id_from_payload(payload),
            causation_id=requested.id,
            correlation_id=requested.correlation_id,
            payload={
                "schema_version": "human-escalation-acknowledged.v1",
                "decision_token": token,
                "decision": decision,
                "source": str(payload.get("source") or "operator-inbox"),
                "surface": "web",
                "project_id": project_id,
                "approval_ref": str(payload.get("approval_ref") or ""),
                "checkpoint_id": str(payload.get("checkpoint_id") or ""),
                "fingerprint": str(payload.get("fingerprint") or ""),
                "created_event_id": expected_event,
            },
        )
    completed = {
        "action": action, "requested_action": requested_action, "status": "acknowledged",
        "event_type": "human.escalation.acknowledged", "event_id": acknowledged.id,
        "decision_token": token, "decision": decision, "project_id": project_id,
    }
    for event_type in ("runtime.action.completed", "web.action.completed"):
        writer.emit(event_type, actor="web", task_id=_task_id_from_payload(payload), causation_id=acknowledged.id,
                    correlation_id=acknowledged.correlation_id, payload=completed)
    return {"_status_code": 202, "ok": True, **completed}


def _conflict(action: str, requested_action: str, reason: str) -> dict[str, Any]:
    return {"_status_code": 409, "ok": False, "status": "stale_decision",
            "action": action, "requested_action": requested_action, "reason": reason}
