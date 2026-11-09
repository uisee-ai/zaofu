"""Bounded operator redrive after an OA checkpoint bound the wrong role."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.core.state.locks import locked_path
from zf.runtime.orchestrator_agent_execution_review import pending_execution_reviews
from zf.runtime.run_contract import stable_json_sha256
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import reduce_workflow_operations


def request_review_retry(
    *, state_dir: Path, event_log: EventLog, event_writer: EventWriter,
    config: Any, escalation_id: str, reason: str, apply: bool = False,
) -> dict[str, Any]:
    """Re-review a verified role-binding defect, without issuing a rework decision."""
    if not escalation_id.strip() or not reason.strip():
        raise ValueError("escalation id and operator reason are required")
    request_id = "oa-operator-role-review-" + stable_json_sha256({
        "escalation_id": escalation_id,
    })[:24]
    with locked_path(state_dir / "locks" / "oa-operator-review-retry.lock"):
        events = event_log.read_all()
        by_id = {event.id: event for event in events}
        escalation = by_id.get(escalation_id)
        if not escalation or escalation.type != "human.escalate":
            raise ValueError("OA escalation not found")
        op_id = str(escalation.payload.get("orchestrator_operation_id") or "")
        submitted = by_id.get(str(escalation.causation_id or ""))
        checkpoint = next((e for e in events if e.type == "orchestrator.semantic.checkpoint.requested"
                           and e.payload.get("operation_id") == op_id), None)
        if (not op_id or not submitted or submitted.type != "orchestrator.semantic.decision.submitted"
                or submitted.payload.get("operation_id") != op_id or not checkpoint
                or checkpoint.payload.get("checkpoint") != "semantic_failure"):
            raise ValueError("escalation is not bound to a semantic checkpoint decision")
        decision_ref = escalation.payload.get("orchestrator_decision_ref")
        if (not isinstance(decision_ref, dict) or decision_ref != submitted.payload.get("control_result_ref")
                or not any(e.type == "workflow.call.result.admitted"
                           and e.payload.get("operation_id") == op_id
                           and e.payload.get("source_event_id") == submitted.id
                           and e.payload.get("control_result_ref") == decision_ref for e in events)):
            raise ValueError("escalation lacks the admitted decision")
        decision = hydrate_sidecar_ref(state_dir, decision_ref).payload
        if decision.get("decision") != "escalate":
            raise ValueError("decision is not an escalation")
        source_id = str(checkpoint.payload.get("source_event_id") or checkpoint.causation_id or "")
        original = by_id.get(source_id)
        if not original or original.type != "orchestrator.semantic.failure.requested":
            raise ValueError("checkpoint source request is missing")
        target_op_id = str(original.payload.get("target_operation_id") or "")
        target_op = reduce_workflow_operations(events).get(target_op_id, {})
        input_ref = checkpoint.payload.get("checkpoint_input_ref")
        if not isinstance(input_ref, dict):
            raise ValueError("checkpoint input is missing")
        body = hydrate_sidecar_ref(state_dir, input_ref).payload
        target = body.get("checkpoint_context") or {}
        actual_role = str(target_op.get("role_instance") or "")
        attempt = str(target_op.get("active_attempt_id") or target_op.get("dispatch_id") or "")
        if (not actual_role or not attempt or not target_op_id
                or target_op.get("status") != "settled"
                or target_op.get("task_id") != original.task_id
                or target.get("target_task_id") != original.task_id
                or target.get("target_attempt_id") != attempt
                or target.get("target_stage_id") != "impl"
                or not target.get("target_role_instance")
                or target.get("target_role_instance") == actual_role):
            raise ValueError("checkpoint does not prove a current settled role mismatch")
        previous = next((e for e in events if e.type == "orchestrator.semantic.failure.requested"
                         and e.payload.get("request_id") == request_id), None)
        if previous:
            return {"status": "already_requested", "event_id": previous.id, "request_id": request_id}
        # Re-evaluate all currentness and policy checks while disregarding only
        # the exact escalated request; any later request still fences replay.
        actions = pending_execution_reviews(state_dir=state_dir, config=config,
            events=[event for event in events if event.id != original.id])
        action = next((a for a in actions if a.get("task_id") == original.task_id
                       and a.get("target_operation_id") == target_op_id), None)
        if action is None:
            raise ValueError("task or operation is no longer eligible for review")
        action = {**action, "request_id": request_id, "checkpoint_id": request_id,
                  "fingerprint": request_id,
                  "summary": f"Operator re-review after {escalation_id}: {reason.strip()}"}
        from zf.runtime.run_manager_rework_triage import triage_action_preflight
        preflight = triage_action_preflight(action["action"], action)
        if preflight["status"] != "passed":
            raise ValueError("semantic review preflight failed: " + ", ".join(preflight["failures"]))
        result = {"status": "preview", "request_id": request_id, "task_id": original.task_id,
                  "target_operation_id": target_op_id, "target_attempt_id": attempt,
                  "target_role_instance": actual_role, "escalation_event_id": escalation_id,
                  "reason": reason.strip()}
        if apply:
            from zf.runtime.run_manager import _execute_orchestrator_triage_request
            outcome = _execute_orchestrator_triage_request(writer=event_writer, action=action,
                causation_id=escalation_id, state_dir=state_dir, config=config)
            if outcome != "applied":
                raise ValueError("semantic review request was blocked")
            requested = next(e for e in event_log.read_all()
                             if e.type == "orchestrator.semantic.failure.requested"
                             and e.payload.get("request_id") == request_id)
            result.update(status="requested", event_id=requested.id)
        return result
