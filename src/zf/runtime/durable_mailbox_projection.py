"""Cursor-bound metadata projection. Private participants, bodies and locators never enter this view."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from zf.runtime.call_result_envelope import canonical_json_sha256 as digest
from zf.runtime.durable_mailbox_authority import PREFIX, record
from zf.runtime.sidecar_refs import SidecarRefError, verify_sidecar_ref


def _address(addr):
    fields = (
        "mode",
        "stage",
        "obligation_id",
        "work_unit_id",
        "task_id",
        "role",
        "version",
        "contract_revision",
        "pipeline_stage",
        "operation_id",
        "attempt_id",
    )
    result = {k: addr[k] for k in fields if k in addr}
    result["owner"] = addr.get("owner") or addr.get("role") or addr.get("actor", "")
    return result


def project_mailbox(events, *, run_id, cursor=None, stage="", obligation_id="", limit=100, state_dir=None):
    """Fold only authorized operator metadata at the supplied historical ledger position."""
    if limit is not None and (type(limit) is not int or limit < 1 or limit > 1000):
        raise ValueError("invalid mailbox projection limit")
    if cursor is not None and (type(cursor) is not int or cursor < 0):
        raise ValueError("invalid mailbox ledger cursor")
    rows = defaultdict(dict)
    refs = defaultdict(list)
    visible_seq = 0
    response_windows = {}
    for i, entry in enumerate(events, 1):
        seq, e = entry if isinstance(entry, tuple) else (i, entry)
        if cursor is not None and seq > cursor:
            break
        p = e.payload
        if p.get("workflow_run_id") != run_id:
            continue
        if e.type == "worker.assistance.requested" and e.origin == "kernel" and p.get("durable_mailbox") is True:
            seconds = p.get("timeout_seconds")
            if type(seconds) in (float, int) and seconds > 0:
                response_windows[(p.get("request_id"), e.actor, p.get("operation_id"))] = {
                    "response_started_at": e.ts, "response_timeout_seconds": seconds,
                }
            continue
        if not e.type.startswith(PREFIX):
            continue
        kind = e.type[len(PREFIX) :]
        if not record(e, kind):
            continue
        key = p.get("message_key")
        if kind == "accepted":
            if p.get("visibility") != "operator_metadata" or key in rows:
                continue
            for addr in p["recipients"]:
                if (stage and addr.get("stage") != stage) or (
                    obligation_id and addr.get("obligation_id") != obligation_id
                ):
                    continue
                pending = (
                    "pending_binding" if addr.get("version") in p.get("unbound_recipients", ()) else "pending"
                )
                rows[key][digest(addr)] = {
                    **_address(addr),
                    **response_windows.get((p.get("question_id"), p.get("sender", {}).get("actor"),
                                            p.get("sender", {}).get("operation_id")), {}),
                    "status": pending,
                    "message_key": key,
                    "sender_version": p.get("sender_address", {}).get("version", ""),
                    "sender_operation_id": p.get("sender", {}).get("operation_id", ""),
                    "sender_attempt_id": p.get("sender", {}).get("attempt_id", ""),
                    "posted_ts": e.ts,
                    "source_event_id": e.id,
                    "kind": p["kind"],
                    "deadline": p.get("deadline", ""),
                    "delivery_status": "pending",
                    "question_status": "awaiting_claim" if p["kind"] == "question" else "",
                    "notification_status": "",
                    "claimed_at": "",
                    "answered_at": "",
                    "recovery_owner": "",
                }
            if key in rows:
                refs[key].append(p["body_ref"])
                visible_seq += 1
        elif kind in {"prepared", "acknowledged"}:
            visible = False
            for item in p.get("items", []):
                row = rows.get(item["message_key"], {}).get(digest(item["recipient"]))
                if row is None:
                    continue
                visible = True
                if row["status"] in {
                    "pending_binding",
                    "pending",
                    "prepared",
                    "acknowledged",
                    "evidence_unavailable",
                }:
                    row.update(status=kind, recovery_owner="")
                row.update(delivery_status=kind, source_event_id=e.id)
                consumer = p.get("consumer", {})
                row.update(
                    consumer=consumer.get("actor", ""), consumer_attempt_id=consumer.get("attempt_id", "")
                )
            visible_seq += int(visible)
        elif key in rows and kind in {"question", "notification", "disposition", "binding"}:
            visible_seq += 1
            for rid, row in list(rows[key].items()):
                if kind in {"disposition", "binding"} and p.get("recipient_digest") != rid:
                    continue
                row["source_event_id"] = e.id
                if kind == "binding":
                    row.update(
                        status="pending",
                        delivery_status="pending",
                        consumer=p["consumer"]["actor"],
                        consumer_attempt_id=p["consumer"]["attempt_id"],
                        recovery_owner="", cause="",
                    )
                    if row.get("question_status") in {"claimed", "waiting"}:
                        row["status"] = row["question_status"]
                    continue
                if kind == "notification":
                    row["notification_status"] = p.get("status", "")
                    if p.get("status") == "unknown":
                        row["recovery_owner"] = "run_manager"
                    continue
                if p.get("status") == "diagnosis_required":
                    row.update(status="diagnosis_required", recovery_owner=p["recovery_owner"], cause=p["reason"])
                    continue
                if p.get("status") == "evidence_restored":
                    row.update(
                        status=row.get("question_status")
                        if row.get("question_status") in {"claimed", "waiting", "answered"}
                        else row.get("delivery_status", "pending"),
                        recovery_owner="",
                        cause="",
                    )
                    continue
                if p.get("status") == "evidence_unavailable":
                    row.update(status="evidence_unavailable", recovery_owner="run_manager", cause=p["reason"])
                    continue
                row.update(
                    status=p["status"],
                    question_status=p["status"] if row["kind"] == "question" else "",
                    deadline=p.get("deadline") or row["deadline"],
                    cause=p.get("reason", ""),
                )
                if p.get("consumer"):
                    row["owner"] = p["consumer"].get("actor") or row["owner"]
                if kind == "question":
                    row["recovery_owner"] = ""
                if p.get("action") == "claim":
                    row["claimed_at"] = e.ts
                if p.get("action") == "answer":
                    row["answered_at"] = e.ts
                if p.get("target") and p.get("action") == "handoff":
                    row.update(
                        **_address(p["target"]),
                        delivery_status="pending",
                        consumer="",
                        consumer_attempt_id="",
                    )
                    rows[key].pop(rid)
                    rows[key][digest(p["target"])] = row
            if kind == "question" and p.get("answer_ref"):
                refs[key].append(p["answer_ref"])
    result = []
    for key, recipients in rows.items():
        if state_dir is not None and cursor is None:
            try:
                for ref in refs[key]:
                    verify_sidecar_ref(Path(state_dir), ref)
            except (OSError, SidecarRefError):
                for row in recipients.values():
                    row.update(status="evidence_unavailable", recovery_owner="run_manager")
        result.extend(recipients.values())
    return {
        "items": result if limit is None else result[:limit],
        "total": len(result),
        "source_seq": visible_seq,
    }
