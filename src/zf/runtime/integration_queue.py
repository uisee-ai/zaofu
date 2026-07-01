"""Derived integration queue projection for isolated loop handoff."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.integration_queue_helpers import (
    coerce_list as _coerce_list,
    event_issue as _issue,
    fanout_status_by_id as _fanout_status_by_id,
    payload_dict as _payload,
    queue_reason as _reason,
    stale_reason as _stale_reason,
    superseded_by as _superseded_by,
    task_id_for_event as _task_id,
    unique_strings as _unique,
)


INTEGRATION_QUEUE_SCHEMA_VERSION = "integration-queue.v1"

STATUS_QUEUED = "queued"
STATUS_INTEGRATING = "integrating"
STATUS_INTEGRATED = "integrated"
STATUS_NEEDS_REVIEW = "needs_review"
STATUS_DISCARDED = "discarded"

QUEUE_STATUSES = frozenset({
    STATUS_QUEUED,
    STATUS_INTEGRATING,
    STATUS_INTEGRATED,
    STATUS_NEEDS_REVIEW,
    STATUS_DISCARDED,
})

QUEUE_EVENT_TYPES = frozenset({
    "task.integration_enqueued",
    "integration.queue.integrating",
    "integration.queue.integrated",
    "integration.queue.needs_review",
    "integration.queue.retry_requested",
    "integration.queue.discarded",
    "candidate.integration.started",
    "candidate.integration.completed",
    "integration.failed",
})

_QUEUE_SPECIFIC_EVENT_TYPES = frozenset({
    "integration.queue.integrating",
    "integration.queue.integrated",
    "integration.queue.needs_review",
    "integration.queue.retry_requested",
    "integration.queue.discarded",
})

_VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    STATUS_QUEUED: frozenset({
        STATUS_INTEGRATING,
        STATUS_NEEDS_REVIEW,
        STATUS_DISCARDED,
    }),
    STATUS_INTEGRATING: frozenset({
        STATUS_INTEGRATED,
        STATUS_NEEDS_REVIEW,
        STATUS_DISCARDED,
    }),
    STATUS_NEEDS_REVIEW: frozenset({
        STATUS_QUEUED,
        STATUS_INTEGRATING,
        STATUS_DISCARDED,
    }),
    STATUS_INTEGRATED: frozenset(),
    STATUS_DISCARDED: frozenset(),
}


@dataclass
class IntegrationQueueEntry:
    id: str
    status: str = STATUS_QUEUED
    task_id: str = ""
    fanout_instance_id: str = ""
    source_ref: str = ""
    base_ref: str = ""
    handoff_ref: str = ""
    artifact_refs: list[str] = field(default_factory=list)
    verification_refs: list[str] = field(default_factory=list)
    reason: str = ""
    retry_count: int = 0
    created_event_id: str = ""
    updated_event_id: str = ""
    updated_at: str = ""
    event_refs: list[dict[str, str]] = field(default_factory=list)
    issues: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def read_integration_queue(
    state_dir: Path,
    *,
    project_root: Path | None = None,
) -> dict[str, Any]:
    """Read events from the configured state dir and derive the queue."""

    events = EventLog(Path(state_dir) / "events.jsonl").read_all()
    git_state = None
    if project_root is not None:
        from zf.runtime.git_capture import capture_git_state

        git_state = capture_git_state(project_root)
    return build_integration_queue(
        events,
        dirty_files=git_state.dirty_files if git_state is not None else (),
        git_head=git_state.head if git_state is not None else "",
        git_branch=git_state.branch if git_state is not None else "",
    )


def build_integration_queue(
    events: Iterable[ZfEvent],
    *,
    dirty_files: Iterable[str] = (),
    git_head: str | None = "",
    git_branch: str | None = "",
) -> dict[str, Any]:
    event_list = list(events)
    fanout_status = _fanout_status_by_id(event_list)
    entries: dict[str, IntegrationQueueEntry] = {}
    issues: list[dict[str, str]] = []
    stale_entries: list[dict[str, Any]] = []
    seen_event_ids: set[str] = set()
    seen_operation_keys: set[tuple[str, str, str]] = set()

    for event in event_list:
        if event.type not in QUEUE_EVENT_TYPES:
            continue
        if event.id in seen_event_ids:
            continue
        seen_event_ids.add(event.id)
        payload = _payload(event)
        if not _queue_relevant(event, payload):
            continue
        entry_id = _entry_id_for_event(event, payload)
        if not entry_id:
            issue = _issue(event, "", "missing_queue_identity")
            issues.append(issue)
            continue
        stale_reason = _stale_reason(payload, fanout_status)
        if stale_reason:
            stale = _entry_stub(event, payload, entry_id)
            stale["status"] = "stale_rejected"
            stale["reason"] = _reason(payload) or stale_reason
            stale["stale_reason"] = stale_reason
            superseded_by = _superseded_by(payload, fanout_status)
            if superseded_by:
                stale["superseded_by"] = superseded_by
            stale_entries.append(stale)
            issues.append(_issue(event, entry_id, "stale_queue_event_rejected"))
            continue

        action = _action_for_event(event, payload)
        if not action:
            continue

        operation_key = _operation_key(event, payload, entry_id, action)
        if operation_key in seen_operation_keys:
            continue
        seen_operation_keys.add(operation_key)

        if action == "enqueue":
            entry = entries.get(entry_id)
            if entry is None:
                entry = _new_entry(event, payload, entry_id)
                entries[entry_id] = entry
            else:
                _merge_payload(entry, event, payload)
            _record_event(entry, event)
            continue

        entry = entries.get(entry_id)
        if entry is None:
            if action == STATUS_NEEDS_REVIEW:
                entry = _new_entry(event, payload, entry_id)
                entries[entry_id] = entry
            else:
                issue = _issue(event, entry_id, "missing_queued_entry")
                issues.append(issue)
                continue

        target_status = _target_status_for_action(action, payload)
        _merge_payload(entry, event, payload)
        if target_status == STATUS_QUEUED and entry.status == STATUS_NEEDS_REVIEW:
            entry.retry_count += 1
        if _apply_transition(entry, target_status, event):
            _record_event(entry, event)
        else:
            issue = _issue(
                event,
                entry_id,
                f"illegal_transition:{entry.status}->{target_status}",
            )
            entry.issues.append(issue)
            issues.append(issue)
            _record_event(entry, event)

    entry_list = sorted(
        (entry.to_dict() for entry in entries.values()),
        key=lambda item: (str(item.get("updated_at") or ""), str(item.get("id") or "")),
    )
    projection = {
        "schema_version": INTEGRATION_QUEUE_SCHEMA_VERSION,
        "is_derived_projection": True,
        "summary": _summary(entry_list, stale_entries, issues),
        "entries": entry_list,
        "stale_entries": stale_entries,
        "issues": issues,
    }
    from zf.runtime.integration_arbiter import build_integration_arbiter

    projection["arbiter"] = build_integration_arbiter(
        projection,
        event_list,
        dirty_files=dirty_files,
        git_head=git_head,
        git_branch=git_branch,
    )
    return projection


def _queue_relevant(event: ZfEvent, payload: dict[str, Any]) -> bool:
    if event.type in _QUEUE_SPECIFIC_EVENT_TYPES:
        return True
    if event.type == "task.integration_enqueued":
        return True
    return bool(_explicit_entry_id(payload) or _identity_parts(event, payload))


def _action_for_event(event: ZfEvent, payload: dict[str, Any]) -> str:
    if event.type == "task.integration_enqueued":
        return "enqueue"
    if event.type in {"integration.queue.integrating", "candidate.integration.started"}:
        return STATUS_INTEGRATING
    if event.type in {"integration.queue.integrated", "candidate.integration.completed"}:
        return STATUS_INTEGRATED
    if event.type in {"integration.queue.needs_review", "integration.failed"}:
        return STATUS_NEEDS_REVIEW
    if event.type == "integration.queue.retry_requested":
        return "retry"
    if event.type == "integration.queue.discarded":
        return STATUS_DISCARDED
    return ""


def _target_status_for_action(action: str, payload: dict[str, Any]) -> str:
    if action == "retry":
        requested = str(payload.get("target_status") or "").strip()
        if requested in {STATUS_QUEUED, STATUS_INTEGRATING}:
            return requested
        return STATUS_QUEUED
    return action


def _apply_transition(
    entry: IntegrationQueueEntry,
    target_status: str,
    event: ZfEvent,
) -> bool:
    if target_status == entry.status:
        entry.updated_event_id = event.id
        entry.updated_at = event.ts
        return True
    if target_status not in _VALID_TRANSITIONS.get(entry.status, frozenset()):
        return False
    entry.status = target_status
    entry.updated_event_id = event.id
    entry.updated_at = event.ts
    return True


def _new_entry(
    event: ZfEvent,
    payload: dict[str, Any],
    entry_id: str,
) -> IntegrationQueueEntry:
    entry = IntegrationQueueEntry(
        id=entry_id,
        status=STATUS_QUEUED,
        task_id=_task_id(event, payload),
        fanout_instance_id=str(payload.get("fanout_instance_id") or ""),
        source_ref=str(payload.get("source_ref") or payload.get("target_ref") or ""),
        base_ref=str(payload.get("base_ref") or ""),
        handoff_ref=str(payload.get("handoff_ref") or ""),
        artifact_refs=_coerce_list(payload.get("artifact_refs") or payload.get("artifacts")),
        verification_refs=_coerce_list(payload.get("verification_refs")),
        reason=_reason(payload),
        created_event_id=event.id,
        updated_event_id=event.id,
        updated_at=event.ts,
    )
    return entry


def _merge_payload(
    entry: IntegrationQueueEntry,
    event: ZfEvent,
    payload: dict[str, Any],
) -> None:
    entry.task_id = entry.task_id or _task_id(event, payload)
    entry.fanout_instance_id = entry.fanout_instance_id or str(
        payload.get("fanout_instance_id") or ""
    )
    entry.source_ref = entry.source_ref or str(
        payload.get("source_ref") or payload.get("target_ref") or ""
    )
    entry.base_ref = entry.base_ref or str(payload.get("base_ref") or "")
    entry.handoff_ref = entry.handoff_ref or str(payload.get("handoff_ref") or "")
    entry.artifact_refs = _unique(entry.artifact_refs + _coerce_list(
        payload.get("artifact_refs") or payload.get("artifacts")
    ))
    entry.verification_refs = _unique(entry.verification_refs + _coerce_list(
        payload.get("verification_refs")
    ))
    reason = _reason(payload)
    if reason:
        entry.reason = reason
    entry.updated_event_id = event.id
    entry.updated_at = event.ts


def _record_event(entry: IntegrationQueueEntry, event: ZfEvent) -> None:
    entry.event_refs.append({
        "id": event.id,
        "type": event.type,
        "task_id": event.task_id or "",
        "ts": event.ts,
    })


def _entry_id_for_event(event: ZfEvent, payload: dict[str, Any]) -> str:
    explicit = _explicit_entry_id(payload)
    if explicit:
        return explicit
    parts = _identity_parts(event, payload)
    if not parts:
        return ""
    raw = "|".join(parts)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"iq-{digest}"


def _explicit_entry_id(payload: dict[str, Any]) -> str:
    for key in (
        "queue_entry_id",
        "integration_entry_id",
        "entry_id",
        "integration_id",
        "id",
    ):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _identity_parts(event: ZfEvent, payload: dict[str, Any]) -> list[str]:
    parts = [
        _task_id(event, payload),
        str(payload.get("fanout_instance_id") or ""),
        str(payload.get("source_ref") or payload.get("target_ref") or ""),
        str(payload.get("base_ref") or ""),
        str(payload.get("handoff_ref") or ""),
        str(payload.get("pdd_id") or payload.get("feature_id") or ""),
    ]
    return [part for part in parts if part]


def _operation_key(
    event: ZfEvent,
    payload: dict[str, Any],
    entry_id: str,
    action: str,
) -> tuple[str, str, str]:
    key = str(payload.get("idempotency_key") or payload.get("operation_id") or event.id)
    return (entry_id, action, key)


def _entry_stub(
    event: ZfEvent,
    payload: dict[str, Any],
    entry_id: str,
) -> dict[str, Any]:
    return {
        "id": entry_id,
        "event_id": event.id,
        "event_type": event.type,
        "task_id": _task_id(event, payload),
        "fanout_instance_id": str(payload.get("fanout_instance_id") or ""),
        "source_ref": str(payload.get("source_ref") or payload.get("target_ref") or ""),
        "handoff_ref": str(payload.get("handoff_ref") or ""),
        "artifact_refs": _coerce_list(payload.get("artifact_refs") or payload.get("artifacts")),
        "verification_refs": _coerce_list(payload.get("verification_refs")),
        "ts": event.ts,
    }


def _summary(
    entries: list[dict[str, Any]],
    stale_entries: list[dict[str, Any]],
    issues: list[dict[str, str]],
) -> dict[str, Any]:
    counts = {status: 0 for status in sorted(QUEUE_STATUSES)}
    for entry in entries:
        status = str(entry.get("status") or "")
        if status in counts:
            counts[status] += 1
    return {
        "total": len(entries),
        "counts": counts,
        "queued": counts[STATUS_QUEUED],
        "integrating": counts[STATUS_INTEGRATING],
        "needs_review": counts[STATUS_NEEDS_REVIEW],
        "integrated": counts[STATUS_INTEGRATED],
        "discarded": counts[STATUS_DISCARDED],
        "stale_rejected": len(stale_entries),
        "issue_count": len(issues),
    }


__all__ = [
    "INTEGRATION_QUEUE_SCHEMA_VERSION",
    "QUEUE_EVENT_TYPES",
    "QUEUE_STATUSES",
    "build_integration_queue",
    "read_integration_queue",
]
