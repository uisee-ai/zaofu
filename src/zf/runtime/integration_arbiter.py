"""Decision-only integration arbiter projection and audit events."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.dirty_tree_gate import classify_dirty_tree


INTEGRATION_ARBITER_SCHEMA_VERSION = "integration-arbiter.v1"
INTEGRATION_ARBITER_DECISION_EVENT = "integration.arbiter.decision"

_QUEUE_STATUS_QUEUED = "queued"
_QUEUE_STATUS_INTEGRATING = "integrating"
_QUEUE_STATUS_INTEGRATED = "integrated"
_QUEUE_STATUS_NEEDS_REVIEW = "needs_review"
_QUEUE_STATUS_DISCARDED = "discarded"

_ARBITER_INPUT_EVENT_TYPES = frozenset((
    "task.integration_enqueued", "integration.queue.integrating",
    "integration.queue.integrated", "integration.queue.needs_review",
    "integration.queue.retry_requested", "integration.queue.discarded",
    "candidate.integration.started", "candidate.integration.completed",
    "integration.failed", "fanout.started", "fanout.child.done",
    "fanout.completed", INTEGRATION_ARBITER_DECISION_EVENT,
))


@dataclass
class IntegrationArbiterDecision:
    id: str
    queue_entry_id: str
    queue_status: str
    decision: str
    status: str
    idempotency_key: str
    task_id: str = ""
    fanout_instance_id: str = ""
    target_event_type: str = ""
    reason: str = ""
    audit_event_id: str = ""
    action_options: list[dict[str, Any]] = field(default_factory=list)
    controlled_action: dict[str, Any] = field(default_factory=dict)
    dirty_guard: dict[str, Any] = field(default_factory=dict)
    merge_safety: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_integration_arbiter(
    queue_projection: dict[str, Any],
    events: Iterable[ZfEvent] = (),
    *,
    dirty_files: Iterable[str] = (),
    git_head: str | None = "",
    git_branch: str | None = "",
) -> dict[str, Any]:
    event_list = [
        event for event in events if event.type in _ARBITER_INPUT_EVENT_TYPES
    ]
    audit_events = _audit_events_by_idempotency_key(event_list)
    dirty_guard = _dirty_guard(dirty_files)
    merge_safety = _merge_safety(
        dirty_guard=dirty_guard,
        git_head=git_head or "",
        git_branch=git_branch or "",
    )

    decisions: list[dict[str, Any]] = []
    for raw_entry in queue_projection.get("entries", []) or []:
        if not isinstance(raw_entry, dict):
            continue
        decision = _decision_for_entry(
            raw_entry,
            audit_events=audit_events,
            dirty_guard=dirty_guard,
            merge_safety=merge_safety,
        )
        decisions.append(decision.to_dict())

    for raw_entry in queue_projection.get("stale_entries", []) or []:
        if not isinstance(raw_entry, dict):
            continue
        decision = _decision_for_stale_entry(
            raw_entry,
            audit_events=audit_events,
            dirty_guard=dirty_guard,
            merge_safety=merge_safety,
        )
        decisions.append(decision.to_dict())

    return {
        "schema_version": INTEGRATION_ARBITER_SCHEMA_VERSION,
        "is_derived_projection": True,
        "truth_sources": [
            "events.jsonl",
            "integration_queue_projection",
            "read_only_git_status",
        ],
        "summary": _summary(decisions),
        "input_event_types": sorted({event.type for event in event_list}),
        "dirty_guard": dirty_guard,
        "merge_safety": merge_safety,
        "decisions": decisions,
        "policy": {
            "direct_truth_mutation": False,
            "direct_git_merge": False,
            "controlled_action_required_for_writes": True,
            "operator_token_required_for_merge": True,
            "conflict_policy": "fail_closed",
            "concurrent_head_policy": "fail_closed",
        },
    }


def emit_integration_arbiter_decisions(
    writer: EventWriter,
    arbiter_projection: dict[str, Any],
) -> list[ZfEvent]:
    """Append audit-only decision events for decisions not already emitted."""

    emitted: list[ZfEvent] = []
    for raw_decision in arbiter_projection.get("decisions", []) or []:
        if not isinstance(raw_decision, dict):
            continue
        if raw_decision.get("audit_event_id"):
            continue
        idempotency_key = str(raw_decision.get("idempotency_key") or "")
        if not idempotency_key:
            continue
        event = writer.append(ZfEvent(
            type=INTEGRATION_ARBITER_DECISION_EVENT,
            actor="zf-cli",
            task_id=str(raw_decision.get("task_id") or "") or None,
            payload={
                "source": "integration_arbiter",
                "decision_id": str(raw_decision.get("id") or ""),
                "queue_entry_id": str(raw_decision.get("queue_entry_id") or ""),
                "queue_status": str(raw_decision.get("queue_status") or ""),
                "decision": str(raw_decision.get("decision") or ""),
                "status": str(raw_decision.get("status") or ""),
                "target_event_type": str(raw_decision.get("target_event_type") or ""),
                "idempotency_key": idempotency_key,
                "reason": str(raw_decision.get("reason") or ""),
                "controlled_action": raw_decision.get("controlled_action") or {},
                "dirty_guard": raw_decision.get("dirty_guard") or {},
                "merge_safety": raw_decision.get("merge_safety") or {},
                "action_options": raw_decision.get("action_options") or [],
            },
        ))
        emitted.append(event)
    return emitted


def _decision_for_entry(
    entry: dict[str, Any],
    *,
    audit_events: dict[str, str],
    dirty_guard: dict[str, Any],
    merge_safety: dict[str, Any],
) -> IntegrationArbiterDecision:
    entry_id = str(entry.get("id") or "")
    queue_status = str(entry.get("status") or "")
    updated_event_id = str(
        entry.get("updated_event_id") or entry.get("created_event_id") or ""
    )
    task_id = str(entry.get("task_id") or "")
    fanout_id = str(entry.get("fanout_instance_id") or "")

    if queue_status == _QUEUE_STATUS_QUEUED:
        decision = "start_controlled_integration"
        target_event_type = "integration.queue.integrating"
        idempotency_key = _idempotency_key(decision, entry_id, updated_event_id)
        blocked = not bool(merge_safety.get("merge_preflight_passed"))
        return _decision(
            entry_id=entry_id,
            queue_status=queue_status,
            decision=decision,
            status="blocked" if blocked else "pending",
            idempotency_key=idempotency_key,
            task_id=task_id,
            fanout_id=fanout_id,
            target_event_type=target_event_type,
            reason=(
                "dirty_or_unknown_head_blocks_integration"
                if blocked else
                "queued entry is ready for token-gated controlled integration"
            ),
            audit_event_id=audit_events.get(idempotency_key, ""),
            controlled_action=_controlled_merge_action(entry, idempotency_key),
            dirty_guard=dirty_guard,
            merge_safety=merge_safety,
        )

    if queue_status == _QUEUE_STATUS_INTEGRATING:
        decision = "monitor_controlled_integration"
        idempotency_key = _idempotency_key(decision, entry_id, updated_event_id)
        return _decision(
            entry_id=entry_id,
            queue_status=queue_status,
            decision=decision,
            status="monitoring",
            idempotency_key=idempotency_key,
            task_id=task_id,
            fanout_id=fanout_id,
            reason="integration is already in progress; arbiter only observes",
            audit_event_id=audit_events.get(idempotency_key, ""),
            dirty_guard=dirty_guard,
            merge_safety=merge_safety,
        )

    if queue_status == _QUEUE_STATUS_NEEDS_REVIEW:
        decision = "operator_review_retry_or_discard"
        idempotency_key = _idempotency_key(decision, entry_id, updated_event_id)
        return _decision(
            entry_id=entry_id,
            queue_status=queue_status,
            decision=decision,
            status="needs_review",
            idempotency_key=idempotency_key,
            task_id=task_id,
            fanout_id=fanout_id,
            reason=str(entry.get("reason") or "integration requires operator review"),
            audit_event_id=audit_events.get(idempotency_key, ""),
            action_options=_review_action_options(entry, updated_event_id),
            controlled_action={
                "required": True,
                "surface": "repair.action.requested",
                "allowed_kinds": [
                    "retry_integration_queue_entry",
                    "discard_integration_queue_entry",
                ],
                "operator_token_required": True,
            },
            dirty_guard=dirty_guard,
            merge_safety=merge_safety,
        )

    if queue_status in {_QUEUE_STATUS_INTEGRATED, _QUEUE_STATUS_DISCARDED}:
        decision = f"terminal_{queue_status}"
        idempotency_key = _idempotency_key(decision, entry_id, updated_event_id)
        return _decision(
            entry_id=entry_id,
            queue_status=queue_status,
            decision=decision,
            status="terminal",
            idempotency_key=idempotency_key,
            task_id=task_id,
            fanout_id=fanout_id,
            reason=f"integration queue entry is terminal: {queue_status}",
            audit_event_id=audit_events.get(idempotency_key, ""),
            dirty_guard=dirty_guard,
            merge_safety=merge_safety,
        )

    decision = "unknown_queue_status"
    idempotency_key = _idempotency_key(decision, entry_id, updated_event_id)
    return _decision(
        entry_id=entry_id,
        queue_status=queue_status,
        decision=decision,
        status="blocked",
        idempotency_key=idempotency_key,
        task_id=task_id,
        fanout_id=fanout_id,
        reason=f"unknown integration queue status: {queue_status or '(missing)'}",
        audit_event_id=audit_events.get(idempotency_key, ""),
        dirty_guard=dirty_guard,
        merge_safety=merge_safety,
    )


def _decision_for_stale_entry(
    entry: dict[str, Any],
    *,
    audit_events: dict[str, str],
    dirty_guard: dict[str, Any],
    merge_safety: dict[str, Any],
) -> IntegrationArbiterDecision:
    entry_id = str(entry.get("id") or "")
    updated_event_id = str(entry.get("event_id") or "")
    decision = "reject_stale_queue_event"
    idempotency_key = _idempotency_key(decision, entry_id, updated_event_id)
    return _decision(
        entry_id=entry_id,
        queue_status="stale_rejected",
        decision=decision,
        status="terminal",
        idempotency_key=idempotency_key,
        task_id=str(entry.get("task_id") or ""),
        fanout_id=str(entry.get("fanout_instance_id") or ""),
        reason=str(entry.get("stale_reason") or entry.get("reason") or "stale"),
        audit_event_id=audit_events.get(idempotency_key, ""),
        dirty_guard=dirty_guard,
        merge_safety=merge_safety,
    )


def _decision(
    *,
    entry_id: str,
    queue_status: str,
    decision: str,
    status: str,
    idempotency_key: str,
    task_id: str = "",
    fanout_id: str = "",
    target_event_type: str = "",
    reason: str = "",
    audit_event_id: str = "",
    action_options: list[dict[str, Any]] | None = None,
    controlled_action: dict[str, Any] | None = None,
    dirty_guard: dict[str, Any] | None = None,
    merge_safety: dict[str, Any] | None = None,
) -> IntegrationArbiterDecision:
    if audit_event_id and status not in {"terminal", "monitoring"}:
        status = "emitted"
    return IntegrationArbiterDecision(
        id=_decision_id(entry_id, decision, idempotency_key),
        queue_entry_id=entry_id,
        queue_status=queue_status,
        decision=decision,
        status=status,
        idempotency_key=idempotency_key,
        task_id=task_id,
        fanout_instance_id=fanout_id,
        target_event_type=target_event_type,
        reason=reason,
        audit_event_id=audit_event_id,
        action_options=action_options or [],
        controlled_action=controlled_action or {},
        dirty_guard=dirty_guard or {},
        merge_safety=merge_safety or {},
    )


def _controlled_merge_action(
    entry: dict[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    return {
        "required": True,
        "action": "integration-merge-candidate",
        "implemented": False,
        "operator_token_required": True,
        "dirty_guard_required": True,
        "head_recheck_required": True,
        "conflict_policy": "fail_closed",
        "payload": {
            "queue_entry_id": str(entry.get("id") or ""),
            "task_id": str(entry.get("task_id") or ""),
            "source_ref": str(entry.get("source_ref") or ""),
            "base_ref": str(entry.get("base_ref") or ""),
            "idempotency_key": idempotency_key,
        },
    }


def _review_action_options(
    entry: dict[str, Any],
    updated_event_id: str,
) -> list[dict[str, Any]]:
    entry_id = str(entry.get("id") or "")
    task_id = str(entry.get("task_id") or "")
    reason = str(entry.get("reason") or "")
    retry_key = _idempotency_key("retry_integration_queue_entry", entry_id, updated_event_id)
    discard_key = _idempotency_key(
        "discard_integration_queue_entry",
        entry_id,
        updated_event_id,
    )
    return [
        {
            "label": "retry",
            "surface": "repair.action.requested",
            "kind": "retry_integration_queue_entry",
            "queue_entry_id": entry_id,
            "task_id": task_id,
            "idempotency_key": retry_key,
            "payload": {
                "kind": "retry_integration_queue_entry",
                "queue_entry_id": entry_id,
                "task_id": task_id,
                "target_status": "queued",
                "idempotency_key": retry_key,
                "reason": reason,
            },
        },
        {
            "label": "discard",
            "surface": "repair.action.requested",
            "kind": "discard_integration_queue_entry",
            "queue_entry_id": entry_id,
            "task_id": task_id,
            "idempotency_key": discard_key,
            "payload": {
                "kind": "discard_integration_queue_entry",
                "queue_entry_id": entry_id,
                "task_id": task_id,
                "idempotency_key": discard_key,
                "reason": reason,
            },
        },
    ]


def _dirty_guard(dirty_files: Iterable[str]) -> dict[str, Any]:
    files = [str(path) for path in dirty_files if str(path).strip()]
    classification = classify_dirty_tree(changed_paths=files)
    return {
        "mode": "read_only_git_status",
        "dirty": bool(files),
        "dirty_files": files,
        "classification": classification.to_dict(),
        "fail_closed_on_dirty": True,
    }


def _merge_safety(
    *,
    dirty_guard: dict[str, Any],
    git_head: str,
    git_branch: str,
) -> dict[str, Any]:
    head_known = bool(git_head)
    dirty = bool(dirty_guard.get("dirty"))
    return {
        "git_head": git_head,
        "git_branch": git_branch,
        "head_known": head_known,
        "merge_preflight_passed": head_known and not dirty,
        "requires_head_recheck": True,
        "dirty_tree_policy": "fail_closed",
        "conflict_policy": "fail_closed",
        "concurrent_head_policy": "fail_closed",
    }


def _audit_events_by_idempotency_key(events: Iterable[ZfEvent]) -> dict[str, str]:
    seen: dict[str, str] = {}
    for event in events:
        if event.type != INTEGRATION_ARBITER_DECISION_EVENT:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        key = str(payload.get("idempotency_key") or "")
        if key and key not in seen:
            seen[key] = event.id
    return seen


def _idempotency_key(decision: str, entry_id: str, event_id: str) -> str:
    return f"integration-arbiter:{decision}:{entry_id}:{event_id or 'none'}"


def _decision_id(entry_id: str, decision: str, idempotency_key: str) -> str:
    raw = f"{entry_id}|{decision}|{idempotency_key}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"iad-{digest}"


def _summary(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for decision in decisions:
        status = str(decision.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return {
        "total": len(decisions),
        "counts": counts,
        "pending": counts.get("pending", 0),
        "blocked": counts.get("blocked", 0),
        "needs_review": counts.get("needs_review", 0),
        "monitoring": counts.get("monitoring", 0),
        "terminal": counts.get("terminal", 0),
        "emitted": counts.get("emitted", 0),
    }
