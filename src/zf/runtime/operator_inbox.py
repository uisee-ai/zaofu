"""Operator inbox projection.

This is a read-only view over ``events.jsonl``. Controlled actions still own
approve/reject mutations.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from zf.runtime.operator_plan_preview import (
    PLAN_APPROVAL_REQUESTED,
    PLAN_APPROVED,
    PLAN_REJECTED,
    plan_preview_available,
)

GENERIC_APPROVAL_REQUESTED = "approval.requested"
GENERIC_APPROVAL_RESOLVED = "approval.resolved"
GENERIC_APPROVAL_EXPIRED = "approval.expired"
GENERIC_APPROVAL_POLICY_REJECTED = "approval.rejected_by_policy"
HUMAN_ESCALATION_REQUESTED = "human.escalate"
HUMAN_ESCALATION_SENT = "human.escalation.sent"
HUMAN_ESCALATION_ACKNOWLEDGED = "human.escalation.acknowledged"
RUN_MANAGER_HUMAN_DECISION_APPLIED = "run.manager.human_decision.applied"
RUN_MANAGER_HUMAN_DECISION_REJECTED = "run.manager.human_decision.rejected"

OPERATOR_INBOX_SCHEMA_VERSION = "operator-inbox.v1"
_HIDDEN_VISIBLE_STATUSES = {"acknowledged"}


def build_operator_inbox(
    state_dir: Path,
    events: Iterable[Any],
    *,
    project_root: Path | None = None,
) -> dict[str, Any]:
    """Build an operator inbox from event truth.

    The inbox intentionally exposes actions as names only. Executing those
    names remains the Web controlled-action path's responsibility.
    """

    items: dict[str, dict[str, Any]] = {}
    attention_aliases: dict[str, str] = {}
    suppressed_acknowledged = 0
    for event in events:
        etype = _etype(event)
        payload = _payload(event)
        event_id = _event_id(event)
        ts = _event_ts(event)

        if etype == PLAN_APPROVAL_REQUESTED:
            plan_id = str(payload.get("plan_id") or "")
            if not plan_id:
                continue
            items[_plan_item_id(plan_id)] = _plan_approval_item(
                state_dir,
                event=event,
                payload=payload,
                project_root=project_root,
                status="pending",
                resolved_event_id="",
                resolved_ts="",
                reject_reason="",
            )
            continue

        if etype in {PLAN_APPROVED, PLAN_REJECTED}:
            plan_id = str(payload.get("plan_id") or "")
            if not plan_id:
                continue
            key = _plan_item_id(plan_id)
            existing = items.get(key)
            if existing is None:
                existing = _resolved_plan_placeholder(
                    event=event,
                    payload=payload,
                    status="approved" if etype == PLAN_APPROVED else "rejected",
                )
                items[key] = existing
            existing["status"] = "approved" if etype == PLAN_APPROVED else "rejected"
            existing["resolved_event_id"] = event_id
            existing["resolved_ts"] = ts
            if etype == PLAN_REJECTED:
                existing["reject_reason"] = str(payload.get("reason") or "")
            continue

        if etype == GENERIC_APPROVAL_REQUESTED:
            approval_ref = _approval_ref(payload, event_id)
            items[_generic_item_id(approval_ref)] = _generic_approval_item(
                event=event,
                payload=payload,
                approval_ref=approval_ref,
                status="pending",
            )
            continue

        if etype in {
            GENERIC_APPROVAL_RESOLVED,
            GENERIC_APPROVAL_EXPIRED,
            GENERIC_APPROVAL_POLICY_REJECTED,
        }:
            approval_ref = _approval_ref(payload, "")
            if not approval_ref:
                continue
            key = _generic_item_id(approval_ref)
            existing = items.get(key)
            if existing is None:
                existing = _generic_approval_item(
                    event=event,
                    payload=payload,
                    approval_ref=approval_ref,
                    status="resolved",
                )
                items[key] = existing
            existing["status"] = _generic_resolution_status(etype, payload)
            existing["resolved_event_id"] = event_id
            existing["resolved_ts"] = ts
            existing["resolution"] = str(payload.get("resolution") or payload.get("reason") or "")
            continue

        if etype in {HUMAN_ESCALATION_REQUESTED, HUMAN_ESCALATION_SENT}:
            token = _human_decision_token(payload, event_id)
            if not token:
                continue
            items[_human_item_id(token)] = _human_decision_item(
                event=event,
                payload=payload,
                decision_token=token,
                status="pending",
            )
            continue

        if etype in {
            HUMAN_ESCALATION_ACKNOWLEDGED,
            RUN_MANAGER_HUMAN_DECISION_APPLIED,
            RUN_MANAGER_HUMAN_DECISION_REJECTED,
        }:
            token = _human_decision_token(payload, event_id)
            if not token:
                continue
            key = _human_item_id(token)
            existing = items.get(key)
            if existing is None and etype == HUMAN_ESCALATION_ACKNOWLEDGED:
                suppressed_acknowledged += 1
                continue
            if existing is None:
                existing = _human_decision_item(
                    event=event,
                    payload=payload,
                    decision_token=token,
                    status="resolved",
                )
                items[key] = existing
            existing["status"] = _human_resolution_status(etype)
            existing["resolved_event_id"] = event_id
            existing["resolved_ts"] = ts
            existing["resolution"] = str(payload.get("decision") or payload.get("next_route") or "")
            continue

        if etype.startswith("runtime.attention."):
            attention_id = str(payload.get("attention_id") or payload.get("id") or event_id)
            key = _attention_item_id(payload, event_id, attention_aliases)
            terminal_status = _attention_terminal_status(etype)
            if terminal_status:
                existing = items.get(key)
                if existing is not None:
                    existing["status"] = terminal_status
                    existing["resolved_event_id"] = event_id
                    existing["resolved_ts"] = ts
                    existing["resolution"] = str(payload.get("reason") or terminal_status)
                elif terminal_status == "acknowledged":
                    suppressed_acknowledged += 1
                continue
            if etype not in {"runtime.attention.needed", "runtime.attention.unacknowledged", "runtime.attention.escalated"}:
                continue
            item = _attention_item(event=event, payload=payload, attention_id=attention_id)
            item["id"] = key
            if etype == "runtime.attention.escalated":
                item["status"] = "escalated"
            items[key] = item

    ordered_all = sorted(
        items.values(),
        key=lambda item: (item.get("status") != "pending", item.get("created_ts") or ""),
    )
    hidden = [item for item in ordered_all if _hide_from_inbox(item)]
    ordered = [item for item in ordered_all if not _hide_from_inbox(item)]
    pending = [item for item in ordered if item.get("status") == "pending"]
    return {
        "schema_version": OPERATOR_INBOX_SCHEMA_VERSION,
        "is_derived_projection": True,
        "summary": {
            "total": len(ordered),
            "pending": len(pending),
            "plan_approvals": sum(1 for item in ordered if item.get("kind") == "plan_approval"),
            "attention": sum(1 for item in ordered if item.get("kind") == "runtime_attention"),
            "human_decisions": sum(1 for item in ordered if item.get("kind") == "human_decision"),
            "suppressed_acknowledged": suppressed_acknowledged + len(hidden),
        },
        "items": ordered,
        "pending": pending,
        "policy": {
            "truth_source": "events.jsonl",
            "mutation_path": "controlled-action",
            "agent_can_propose_plan_approve": False,
            "agent_can_propose_plan_reject": True,
        },
    }


def _hide_from_inbox(item: dict[str, Any]) -> bool:
    return str(item.get("status") or "") in _HIDDEN_VISIBLE_STATUSES


def _plan_approval_item(
    state_dir: Path,
    *,
    event: Any,
    payload: dict[str, Any],
    project_root: Path | None,
    status: str,
    resolved_event_id: str,
    resolved_ts: str,
    reject_reason: str,
) -> dict[str, Any]:
    plan_id = str(payload.get("plan_id") or "")
    digest_ref = str(payload.get("digest_ref") or "")
    task_map_ref = str(payload.get("task_map_ref") or "")
    return {
        "id": _plan_item_id(plan_id),
        "kind": "plan_approval",
        "status": status,
        "title": "Plan Ready",
        "summary": _plan_summary(payload),
        "created_event_id": _event_id(event),
        "created_ts": _event_ts(event),
        "resolved_event_id": resolved_event_id,
        "resolved_ts": resolved_ts,
        "approval_ref": f"plan:{plan_id}",
        "plan_id": plan_id,
        "stage_id": str(payload.get("stage_id") or ""),
        "trace_id": str(payload.get("trace_id") or _correlation_id(event) or ""),
        "pdd_id": str(payload.get("pdd_id") or ""),
        "task_count": _int_or_none(payload.get("task_count")),
        "refs": {
            "digest_ref": digest_ref,
            "task_map_ref": task_map_ref,
        },
        "preview": {
            "available": plan_preview_available(
                state_dir,
                project_root=project_root,
                refs=[digest_ref, task_map_ref],
            ),
            "api_path": f"/plans/{plan_id}/preview",
            "fullscreen": True,
            "scroll": True,
        },
        "actions": [
            {"action": "plan-approve", "label": "Approve", "requires_token": True},
            {"action": "plan-reject", "label": "Reject", "requires_token": True, "requires_reason": True},
            {"action": "chat-orchestrator", "label": "Repair Chat", "requires_token": True},
        ],
        "reject_reason": reject_reason,
        "policy": {
            "agent_can_propose_plan_approve": False,
            "repair_owner": "orchestrator",
        },
    }


def _resolved_plan_placeholder(
    *,
    event: Any,
    payload: dict[str, Any],
    status: str,
) -> dict[str, Any]:
    plan_id = str(payload.get("plan_id") or "")
    return {
        "id": _plan_item_id(plan_id),
        "kind": "plan_approval",
        "status": status,
        "title": "Plan Ready",
        "summary": f"plan {plan_id}",
        "created_event_id": "",
        "created_ts": "",
        "resolved_event_id": _event_id(event),
        "resolved_ts": _event_ts(event),
        "approval_ref": f"plan:{plan_id}",
        "plan_id": plan_id,
        "stage_id": str(payload.get("stage_id") or ""),
        "trace_id": str(payload.get("trace_id") or _correlation_id(event) or ""),
        "pdd_id": str(payload.get("pdd_id") or ""),
        "task_count": _int_or_none(payload.get("task_count")),
        "refs": {},
        "preview": {"available": False, "api_path": f"/plans/{plan_id}/preview"},
        "actions": [],
        "reject_reason": str(payload.get("reason") or ""),
        "policy": {"agent_can_propose_plan_approve": False},
    }


def _generic_approval_item(
    *,
    event: Any,
    payload: dict[str, Any],
    approval_ref: str,
    status: str,
) -> dict[str, Any]:
    return {
        "id": _generic_item_id(approval_ref),
        "kind": "approval",
        "status": status,
        "title": str(payload.get("title") or "Approval requested"),
        "summary": str(payload.get("summary") or payload.get("reason") or approval_ref),
        "created_event_id": _event_id(event),
        "created_ts": _event_ts(event),
        "resolved_event_id": "",
        "resolved_ts": "",
        "approval_ref": approval_ref,
        "actions": [
            {"action": str(payload.get("approve_action") or ""), "label": "Approve", "requires_token": True},
            {"action": str(payload.get("reject_action") or ""), "label": "Reject", "requires_token": True},
        ],
    }


def _attention_item(
    *,
    event: Any,
    payload: dict[str, Any],
    attention_id: str,
) -> dict[str, Any]:
    return {
        "id": f"attention:{attention_id}",
        "kind": "runtime_attention",
        "status": "pending",
        "title": str(payload.get("title") or "Runtime attention"),
        "summary": str(payload.get("summary") or payload.get("reason") or _etype(event)),
        "created_event_id": _event_id(event),
        "created_ts": _event_ts(event),
        "resolved_event_id": "",
        "resolved_ts": "",
        "attention_id": attention_id,
        "actions": [{"action": "attention-ack", "label": "Ack", "requires_token": True}],
    }


def _attention_terminal_status(etype: str) -> str:
    suffix = etype.rsplit(".", 1)[-1]
    if suffix in {"acknowledged", "resolved", "dismissed", "cleared", "snoozed"}:
        return suffix
    return ""


def _attention_item_id(
    payload: dict[str, Any],
    event_id: str,
    aliases: dict[str, str],
) -> str:
    keys = _attention_match_keys(payload, event_id)
    for key in keys:
        existing = aliases.get(key)
        if existing:
            for alias in keys:
                aliases[alias] = existing
            return existing
    primary = keys[0] if keys else f"attention:{event_id}"
    for key in keys:
        aliases[key] = primary
    return primary


def _attention_match_keys(payload: dict[str, Any], event_id: str) -> list[str]:
    keys: list[str] = []
    attention_id = str(payload.get("attention_id") or payload.get("id") or "").strip()
    fingerprint = str(payload.get("fingerprint") or "").strip()
    source_event_id = str(payload.get("source_event_id") or "").strip()
    if attention_id:
        keys.append(f"attention:{attention_id}")
    if fingerprint:
        keys.append(f"attention:fingerprint:{fingerprint}")
    if source_event_id:
        keys.append(f"attention:source:{source_event_id}")
    if not keys and event_id:
        keys.append(f"attention:{event_id}")
    return keys


def _human_decision_item(
    *,
    event: Any,
    payload: dict[str, Any],
    decision_token: str,
    status: str,
) -> dict[str, Any]:
    return {
        "id": _human_item_id(decision_token),
        "kind": "human_decision",
        "status": status,
        "title": "Run Manager Decision",
        "summary": str(payload.get("reason") or payload.get("question") or "Run Manager needs an operator decision"),
        "created_event_id": _event_id(event),
        "created_ts": _event_ts(event),
        "resolved_event_id": "",
        "resolved_ts": "",
        "decision_token": decision_token,
        "approval_ref": f"human:{decision_token}",
        "checkpoint_id": str(payload.get("checkpoint_id") or ""),
        "fingerprint": str(payload.get("fingerprint") or ""),
        "actions": [
            {"action": "human-decision-approve-controlled-action", "label": "Approve", "requires_token": True},
            {"action": "human-decision-request-autoresearch", "label": "Diagnose", "requires_token": True},
            {"action": "human-decision-safe-halt", "label": "Halt", "requires_token": True},
        ],
        "policy": {
            "mutation_path": "human.escalation.acknowledged -> run_manager_tick",
            "agent_can_approve": False,
        },
    }


def _plan_summary(payload: dict[str, Any]) -> str:
    stage = str(payload.get("stage_id") or "plan")
    task_count = payload.get("task_count")
    pdd_id = str(payload.get("pdd_id") or "")
    count = f"{task_count} tasks" if task_count not in (None, "") else "task map ready"
    return " / ".join(part for part in (stage, count, pdd_id) if part)


def _generic_resolution_status(etype: str, payload: dict[str, Any]) -> str:
    if etype == GENERIC_APPROVAL_EXPIRED:
        return "expired"
    if etype == GENERIC_APPROVAL_POLICY_REJECTED:
        return "rejected_by_policy"
    return str(payload.get("status") or payload.get("resolution") or "resolved")


def _approval_ref(payload: dict[str, Any], fallback: str) -> str:
    return str(payload.get("approval_ref") or payload.get("approval_id") or fallback)


def _human_decision_token(payload: dict[str, Any], fallback: str) -> str:
    raw = str(
        payload.get("decision_token")
        or payload.get("response_token")
        or payload.get("approval_ref")
        or payload.get("source_message_id")
        or payload.get("escalation_event_id")
        or fallback
    )
    if raw.startswith("human:"):
        raw = raw.removeprefix("human:")
    return raw


def _human_resolution_status(etype: str) -> str:
    if etype == RUN_MANAGER_HUMAN_DECISION_REJECTED:
        return "rejected"
    if etype == RUN_MANAGER_HUMAN_DECISION_APPLIED:
        return "applied"
    return "acknowledged"


def _plan_item_id(plan_id: str) -> str:
    return f"plan:{plan_id}"


def _generic_item_id(approval_ref: str) -> str:
    return f"approval:{approval_ref}"


def _human_item_id(decision_token: str) -> str:
    return f"human:{decision_token}"


def _etype(event: Any) -> str:
    if isinstance(event, dict):
        return str(event.get("type") or "")
    return str(getattr(event, "type", "") or "")


def _payload(event: Any) -> dict[str, Any]:
    payload = event.get("payload") if isinstance(event, dict) else getattr(event, "payload", None)
    return payload if isinstance(payload, dict) else {}


def _event_id(event: Any) -> str:
    if event is None:
        return ""
    if isinstance(event, dict):
        return str(event.get("id") or "")
    return str(getattr(event, "id", "") or "")


def _event_ts(event: Any) -> str:
    if event is None:
        return ""
    if isinstance(event, dict):
        return str(event.get("ts") or "")
    return str(getattr(event, "ts", "") or "")


def _correlation_id(event: Any) -> str:
    if isinstance(event, dict):
        return str(event.get("correlation_id") or "")
    return str(getattr(event, "correlation_id", "") or "")


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


__all__ = [
    "OPERATOR_INBOX_SCHEMA_VERSION",
    "build_operator_inbox",
]
