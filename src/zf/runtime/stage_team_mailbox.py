"""Join visible delivery receipts to the stage member at the same ledger cursor."""

from collections import defaultdict


def mailbox_index(events, run):
    addresses, executions = defaultdict(list), defaultdict(list)
    if run and any(e.type == "workflow.mailbox.accepted" for e in events):
        from zf.runtime.durable_mailbox_projection import project_mailbox

        for item in project_mailbox(events, run_id=run, limit=None)["items"]:
            for direction, prefix in (("received", ""), ("sent", "sender_")):
                version = item.get(prefix + "version")
                operation, attempt = item.get(prefix + "operation_id"), item.get(prefix + "attempt_id")
                if version:
                    addresses[version].append((direction, item))
                elif operation and attempt:
                    executions[operation, attempt].append((direction, item))
    return addresses, executions


def member_mailbox(member, index, proposal_digest, freeze_id):
    addresses, executions = index
    if not addresses and not executions:
        return None
    from zf.runtime.durable_mailbox_authority import member_address

    rows = []
    for task in member.get("task_ids") or [""]:
        address = member_address(member, task, proposal_digest=proposal_digest, freeze_id=freeze_id)
        rows.extend(addresses.get(address["version"], ()))
    for entry in member.get("history", []):
        rows.extend(executions.get((entry["operation_id"], entry.get("attempt_id")), ()))
    if not rows:
        return None
    incoming = [r for direction, r in rows if direction == "received"]
    sent = [r for direction, r in rows if direction == "sent" and r["kind"] == "question"
            and r.get("question_status") not in {"answered", "cancelled", "timed_out"}]
    pending = [r for r in incoming if r["status"] in {"pending", "pending_binding", "prepared"}
               or r.get("question_status") == "awaiting_claim"]
    claimed = [r for r in incoming if r.get("question_status") in {"claimed", "waiting", "handed_off"}]
    observed = [r for _, r in rows]
    unknown = [r for r in observed if "unknown" in {r.get("status"), r.get("notification_status"), r.get("delivery_status")}
               or r["status"] == "evidence_unavailable"]
    first = min(pending + claimed + sent + unknown, key=lambda r: r["posted_ts"], default={})
    return {"pending": len(pending), "unanswered": len(claimed), "sent_unanswered": len(sent), "unknown": len(unknown),
            "oldest_at": first.get("posted_ts", ""), "owner": first.get("owner", ""),
            "deadline": first.get("deadline", ""),
            "last_claimed_at": max((r.get("claimed_at") or "" for r in observed), default=""),
            "last_answered_at": max((r.get("answered_at") or "" for r in observed), default=""),
            "recovery_owner": next((r.get("recovery_owner") or r.get("owner") for r in unknown
                                    if r.get("recovery_owner") or r.get("owner")), "")}
