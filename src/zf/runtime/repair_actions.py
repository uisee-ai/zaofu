"""Derived repair action contract projection."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.store import TaskStore


REPAIR_ACTION_SCHEMA_VERSION = "repair-actions.v1"
REQUESTED = "repair.action.requested"
APPLIED = "repair.action.applied"
REJECTED = "repair.action.rejected"

REPAIR_ACTION_EVENT_TYPES = frozenset({REQUESTED, APPLIED, REJECTED})

ALLOWED_REPAIR_ACTION_KINDS = frozenset({
    "reemit_trigger",
    "requeue_task",
    "restart_worker",
    "cancel_worker",
    "rerun_fanout_child",
    "mark_stale_projection_for_rebuild",
    "retry_integration_queue_entry",
    "discard_integration_queue_entry",
})

REPAIR_ACTION_STATUSES = frozenset({
    "pending",
    "applied",
    "rejected",
    "invalid",
    "duplicate",
})


@dataclass
class RepairActionRecord:
    id: str
    kind: str
    status: str
    task_id: str = ""
    stage: str = ""
    fanout_id: str = ""
    fanout_child_id: str = ""
    queue_entry_id: str = ""
    worker_id: str = ""
    role: str = ""
    projection: str = ""
    attempt: int = 0
    idempotency_key: str = ""
    reason: str = ""
    requested_event_id: str = ""
    terminal_event_id: str = ""
    updated_at: str = ""
    evidence_refs: list[str] = field(default_factory=list)
    issues: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def read_repair_actions(state_dir: Path) -> dict[str, Any]:
    state_dir = Path(state_dir)
    events = EventLog(state_dir / "events.jsonl").read_all()
    return build_repair_action_projection(
        events,
        valid_task_ids=_read_task_ids(state_dir),
    )


def build_repair_action_projection(
    events: Iterable[ZfEvent],
    *,
    valid_task_ids: set[str] | None = None,
) -> dict[str, Any]:
    records: dict[str, RepairActionRecord] = {}
    idempotency_index: dict[str, str] = {}
    issues: list[dict[str, str]] = []

    for event in events:
        if event.type not in REPAIR_ACTION_EVENT_TYPES:
            continue
        payload = _payload(event)
        action_id = _action_id(event, payload)
        if not action_id:
            issue = _issue(event, "", "missing_action_identity")
            issues.append(issue)
            continue

        if event.type == REQUESTED:
            record = _request_record(event, payload, action_id)
            duplicate_of = idempotency_index.get(record.idempotency_key)
            if record.idempotency_key and duplicate_of:
                record.status = "duplicate"
                record.reason = f"duplicate idempotency key for {duplicate_of}"
                issue = _issue(event, action_id, "duplicate_idempotency_key")
                record.issues.append(issue)
                issues.append(issue)
            else:
                reason = _validation_reason(record, valid_task_ids)
                if reason:
                    record.status = "invalid"
                    record.reason = reason
                    issue = _issue(event, action_id, reason)
                    record.issues.append(issue)
                    issues.append(issue)
                if record.idempotency_key:
                    idempotency_index[record.idempotency_key] = action_id
            records[action_id] = record
            continue

        record = records.get(action_id)
        if record is None:
            record = _terminal_only_record(event, payload, action_id)
            issue = _issue(event, action_id, "terminal_without_request")
            record.issues.append(issue)
            issues.append(issue)
            records[action_id] = record
        _apply_terminal(record, event, payload)

    entries = sorted(
        (record.to_dict() for record in records.values()),
        key=lambda item: (str(item.get("updated_at") or ""), str(item.get("id") or "")),
    )
    return {
        "schema_version": REPAIR_ACTION_SCHEMA_VERSION,
        "is_derived_projection": True,
        "summary": _summary(entries, issues),
        "actions": entries,
        "issues": issues,
    }


def _request_record(
    event: ZfEvent,
    payload: dict[str, Any],
    action_id: str,
) -> RepairActionRecord:
    return RepairActionRecord(
        id=action_id,
        kind=_kind(payload),
        status="pending",
        task_id=_task_id(event, payload),
        stage=str(payload.get("stage") or ""),
        fanout_id=str(payload.get("fanout_id") or ""),
        fanout_child_id=str(payload.get("fanout_child_id") or payload.get("child_id") or ""),
        queue_entry_id=str(
            payload.get("queue_entry_id")
            or payload.get("integration_entry_id")
            or payload.get("entry_id")
            or "",
        ),
        worker_id=str(payload.get("worker_id") or payload.get("pane_id") or ""),
        role=str(payload.get("role") or payload.get("target_role") or ""),
        projection=str(
            payload.get("projection")
            or payload.get("projection_name")
            or payload.get("projection_id")
            or "",
        ),
        attempt=_int(payload.get("attempt")),
        idempotency_key=_idempotency_key(payload),
        reason=_reason(payload),
        requested_event_id=event.id,
        updated_at=event.ts,
        evidence_refs=_coerce_list(payload.get("evidence_refs") or payload.get("artifact_refs")),
    )


def _terminal_only_record(
    event: ZfEvent,
    payload: dict[str, Any],
    action_id: str,
) -> RepairActionRecord:
    return RepairActionRecord(
        id=action_id,
        kind=_kind(payload),
        status="pending",
        task_id=_task_id(event, payload),
        idempotency_key=_idempotency_key(payload),
        reason=_reason(payload),
        updated_at=event.ts,
    )


def _apply_terminal(
    record: RepairActionRecord,
    event: ZfEvent,
    payload: dict[str, Any],
) -> None:
    record.status = "applied" if event.type == APPLIED else "rejected"
    record.terminal_event_id = event.id
    record.updated_at = event.ts
    record.reason = _reason(payload) or record.reason
    record.evidence_refs = _unique(record.evidence_refs + _coerce_list(
        payload.get("evidence_refs") or payload.get("artifact_refs")
    ))


def _validation_reason(
    record: RepairActionRecord,
    valid_task_ids: set[str] | None,
) -> str:
    if not record.kind:
        return "missing_action_kind"
    if record.kind not in ALLOWED_REPAIR_ACTION_KINDS:
        return f"unknown_action_kind:{record.kind}"
    if not record.idempotency_key:
        return "missing_idempotency_key"
    if record.task_id and valid_task_ids is not None and record.task_id not in valid_task_ids:
        return f"unknown_task:{record.task_id}"
    if record.kind in {"reemit_trigger", "requeue_task"} and not record.task_id:
        return "missing_task_id"
    if record.kind == "rerun_fanout_child":
        if not record.fanout_id:
            return "missing_fanout_id"
        if not record.fanout_child_id:
            return "missing_fanout_child_id"
    if record.kind == "mark_stale_projection_for_rebuild" and not record.projection:
        return "missing_projection"
    if record.kind in {
        "retry_integration_queue_entry",
        "discard_integration_queue_entry",
    } and not record.queue_entry_id:
        return "missing_queue_entry_id"
    if record.kind in {"restart_worker", "cancel_worker"}:
        if not record.worker_id and not record.role:
            return "missing_worker_target"
    return ""


def _summary(
    entries: list[dict[str, Any]],
    issues: list[dict[str, str]],
) -> dict[str, Any]:
    counts = {status: 0 for status in sorted(REPAIR_ACTION_STATUSES)}
    for entry in entries:
        status = str(entry.get("status") or "")
        if status in counts:
            counts[status] += 1
    return {
        "total": len(entries),
        "counts": counts,
        "pending": counts["pending"],
        "applied": counts["applied"],
        "rejected": counts["rejected"],
        "invalid": counts["invalid"],
        "duplicate": counts["duplicate"],
        "issue_count": len(issues),
    }


def _read_task_ids(state_dir: Path) -> set[str] | None:
    path = state_dir / "kanban.json"
    if not path.exists():
        return None
    try:
        return {task.id for task in TaskStore(path).list_all_with_archive()}
    except Exception:
        return None


def _action_id(event: ZfEvent, payload: dict[str, Any]) -> str:
    for key in ("action_id", "repair_action_id", "id"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    key = _idempotency_key(payload)
    if key:
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
        return f"ra-{digest}"
    return event.id


def _kind(payload: dict[str, Any]) -> str:
    return str(payload.get("kind") or payload.get("action") or "").strip()


def _idempotency_key(payload: dict[str, Any]) -> str:
    return str(payload.get("idempotency_key") or "").strip()


def _payload(event: ZfEvent) -> dict[str, Any]:
    return event.payload if isinstance(event.payload, dict) else {}


def _task_id(event: ZfEvent, payload: dict[str, Any]) -> str:
    return str(event.task_id or payload.get("task_id") or "")


def _reason(payload: dict[str, Any]) -> str:
    for key in ("reason", "error", "message", "summary"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _issue(event: ZfEvent, action_id: str, reason: str) -> dict[str, str]:
    return {
        "event_id": event.id,
        "event_type": event.type,
        "action_id": action_id,
        "reason": reason,
    }


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _coerce_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list | tuple | set):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


__all__ = [
    "ALLOWED_REPAIR_ACTION_KINDS",
    "REPAIR_ACTION_EVENT_TYPES",
    "REPAIR_ACTION_SCHEMA_VERSION",
    "build_repair_action_projection",
    "read_repair_actions",
]
