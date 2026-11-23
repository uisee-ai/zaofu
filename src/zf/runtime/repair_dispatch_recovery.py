"""Return a failed repair dispatch to the existing durable diagnosis owner."""
from typing import Any


def pending_dispatch_diagnoses(events: list) -> list[dict[str, Any]]:
    requests, blocked = {}, {}
    for event in events:
        p = event.payload
        key = (p.get("fingerprint"), p.get("attempt"), p.get("candidate_id"))
        if event.type in {"autoresearch.repair.dispatch_requested", "run.manager.repair.accepted"}:
            requests[key] = event
            # A new admitted request is a new attempt; historical failures remain.
            blocked.pop(key, None)
        elif event.type == "autoresearch.repair.dispatched" and event.origin == "kernel":
            requests.pop(key, None)
            blocked.pop(key, None)
        elif event.type == "autoresearch.repair.dispatch_blocked" and event.origin == "kernel":
            request = requests.get(key)
            if (request and p.get("source_request_id", request.id) == request.id
                    and p.get("reason") in {"worktree_add_failed", "repair_contract_freeze_failed"}):
                blocked.setdefault(key, event)  # Existing repeated ticks are one incident.
    result = []
    for key, event in blocked.items():
        request = requests[key]
        continuation = (request.payload.get("repair_task_payload") or {}).get("continuation") or {}
        result.append({
            "schema_version": "run-manager.pending-action.v1", "action": "diagnose-attention",
            "checkpoint_id": "repair-dispatch-diagnosis-" + request.id,
            "fingerprint": "repair-dispatch:" + request.id,
            "safe_resume_action": "diagnose_attention", "failure_class": "repair_dispatch_failed",
            "workflow_run_id": continuation.get("workflow_run_id", ""),
            "task_id": continuation.get("task_id", ""), "source_event_ids": [request.id, event.id],
            "candidate_id": key[2], "attempt": key[1], "continuation": continuation,
            "worktree_path": event.payload.get("worktree", ""),
            "owner_route": "run_manager", "suggested_route": "run_manager_resident_agent",
            "action_policy": "needs_diagnosis", "intervention_class": "diagnose",
            "expected_output": ["run.manager.agent.recommendation"],
            "expected_downstream_events": ["run.manager.resident.prompted", "run.manager.autoresearch.requested"],
            "reason": "Repair dispatch failed: " + event.payload["reason"] +
                "; inspect the retained worktree and request ownership before choosing a controlled "
                "recovery. Do not delete or adopt existing work, repeat the same dispatch, or expand authorization.",
        })
    return result
