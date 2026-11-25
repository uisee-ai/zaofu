"""Read only the ledger observations applicable to one bound member or Task."""

from collections import defaultdict


def scoped_team(team, members, *, task="", instance=""):
    """A relationship survives a filter only through a selected member's bound call."""
    if instance:
        members = [{**m, "messages": [r for r in m.get("messages", []) if instance in {r["sender"], r["recipient"]}]} for m in members]
    messages = {(m["source_event_id"], m["recipient"]): m for member in members for m in member.get("messages", [])}
    result = {**team, "members": members, "communications": list(messages.values()), "unbound_operation_ids": []}
    if task and "unreported_acceptance" in result:
        result["unreported_acceptance"] = [ac for ac in team["unreported_acceptance"] if ac["task_id"] == task]
    return result


def receipt_observations(events, operations, positions):
    """An instance or Task label alone cannot bind a heartbeat, wait, or lost transport."""
    records = defaultdict(list)
    rejected = {e.payload.get("source_event_id") for e in events
                if e.origin == "kernel" and e.type == "task.attempt.result_rejected"}
    for e in events:
        p = e.payload
        op = operations.get(p.get("operation_id"))
        if not op or e.id in rejected:
            continue
        run = p.get("workflow_run_id") or p.get("run_id") or e.correlation_id
        if run != op.get("workflow_run_id"):
            continue
        if e.type == "worker.heartbeat":
            if not (e.actor and e.actor == op.get("role_instance") and e.task_id == op.get("task_id")
                    and p.get("attempt_id") and p["attempt_id"] == op.get("active_attempt_id")
                    and all(not p.get(k) or p[k] == op.get(k) for k in ("dispatch_id", "lease_id"))):
                continue
            records[op["operation_id"]].append({"kind": "heartbeat", "event_type": e.type,
                "event_id": e.id, "seq": positions[e.id], "time": e.ts})
        elif (e.origin == "kernel" and e.type in {"workflow.operation.blocked","workflow.operation.interrupted"}
              and p.get("request_hash") and p["request_hash"] == op.get("request_hash")
              and (not p.get("source_attempt_id") or p["source_attempt_id"] == op.get("active_attempt_id"))):
            records[op["operation_id"]].append({"kind": "wait", "event_type": e.type, "event_id": e.id,
                "seq": positions[e.id], "time": e.ts, "reason": p.get("reason", ""),
                "owner": p.get("next_owner") or p.get("owner_route") or p.get("recovery_decision_owner") or "",
                "wake_event": p.get("wake_event") or "", "deadline": p.get("deadline") or ""})
    return records
