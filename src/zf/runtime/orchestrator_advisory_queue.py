"""One advisory owner: durable FIFO with a bounded guidance deadline priority."""

from datetime import datetime, timezone

from zf.runtime.pause_lifecycle import active_request_age_seconds
from zf.runtime.provider_turn_liveness import active_codex_turn_from_events
from zf.runtime.workflow_operation import reduce_workflow_operations


def guidance_inflight(events, owner: str) -> bool:
    closed = {e.payload.get("request_id") for e in events if e.type == "worker.assistance.closed"}
    return bool(active_codex_turn_from_events(events, owner)) or any(
        e.type == "worker.assistance.dispatching"
        and e.payload.get("owner_instance") == owner
        and e.payload.get("request_id") not in closed
        for e in events
    )


def next_advisory_event(events, owner: str):
    """An old checkpoint cannot indefinitely hide time-sensitive worker guidance."""
    handled = {e.payload.get("request_id") for e in events
               if e.type in {"worker.assistance.closed", "worker.assistance.dispatching"}}
    operations = reduce_workflow_operations(events)
    queued = []
    urgent = []
    now = datetime.now(timezone.utc)
    for event in events:
        p = event.payload
        if (event.type == "orchestrator.semantic.checkpoint.requested"
                and operations.get(str(p.get("operation_id") or ""), {}).get("status") == "requested"
                and operations[p["operation_id"]].get("role_instance") == owner):
            queued.append(event)
        elif (event.type == "orchestrator.rework.triage.requested"
              and p.get("request_kind") == "worker_guidance"
              and p.get("owner_instance") == owner and p.get("request_id") not in handled):
            queued.append(event)
            timeout = int(p.get("timeout_seconds") or 0)
            age = active_request_age_seconds(events, created_at=datetime.fromisoformat(event.ts), now=now)
            if timeout > 0 and age >= timeout / 2:
                urgent.append(event)
    return next(iter(urgent or queued), None)
