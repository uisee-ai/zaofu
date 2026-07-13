"""Bounded task recovery context shared by semantic recovery advisors."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from zf.core.events.model import ZfEvent
from zf.core.security.redaction import redact_obj
from zf.core.task.store import TaskStore
from zf.runtime.sidecar_refs import write_sidecar_json


RECOVERY_CONTEXT_SCHEMA_VERSION = "task-recovery-context.v1"
_TASK_EVENT_LIMIT = 20
_ATTEMPT_EVENT_TYPES = {
    "task.rework.requested",
    "task.rework.capped",
    "run.manager.action.planned",
    "run.manager.action.applied",
    "run.manager.action.blocked",
    "run.manager.action.failed",
    "run.manager.action.verify.passed",
    "run.manager.action.verify.failed",
    "orchestrator.rework.triage.requested",
    "orchestrator.rework.triage.recorded",
}


def build_task_recovery_context(
    state_dir: Path,
    events: list[ZfEvent],
    *,
    task_id: str,
    failure_event_ids: list[str] | tuple[str, ...] = (),
    request_id: str = "",
) -> dict[str, Any]:
    """Build one bounded, redacted recovery view without rereading the log."""

    state_dir = Path(state_dir)
    task = TaskStore(state_dir / "kanban.json").get(task_id) if task_id else None
    task_events = [event for event in events if _event_task_id(event) == task_id]
    failures = set(str(value) for value in failure_event_ids if str(value).strip())
    failure_rows = [
        _event_row(event, include_findings=True)
        for event in task_events
        if event.id in failures
    ]
    attempted = [
        _event_row(event)
        for event in task_events
        if event.type in _ATTEMPT_EVENT_TYPES
    ][-_TASK_EVENT_LIMIT:]
    last = task_events[-1] if task_events else None
    now = datetime.now(timezone.utc)
    task_payload: dict[str, Any] = {}
    if task is not None:
        task_payload = {
            "task_id": task.id,
            "title": task.title,
            "status": task.status,
            "priority": task.priority,
            "assigned_to": task.assigned_to,
            "skills_required": list(task.skills_required),
            "blocked_by": list(task.blocked_by),
            "blocked_reason": task.blocked_reason,
            "retry_count": task.retry_count,
            "created_at": task.created_at,
            "dispatched_at": task.dispatched_at,
            "started_at": task.started_at,
            "active_dispatch_id": task.active_dispatch_id,
            "age_seconds": _age_seconds(task.created_at, now),
            "contract": asdict(task.contract),
            "evidence": asdict(task.evidence) if task.evidence is not None else None,
        }
    return redact_obj({
        "schema_version": RECOVERY_CONTEXT_SCHEMA_VERSION,
        "is_derived_projection": True,
        "request_id": request_id,
        "task": task_payload,
        "activity": {
            "last_event_id": last.id if last is not None else "",
            "last_event_type": last.type if last is not None else "",
            "last_event_at": last.ts if last is not None else "",
            "last_activity_age_seconds": _age_seconds(last.ts if last is not None else "", now),
            "recent_events": [_event_row(event) for event in task_events[-_TASK_EVENT_LIMIT:]],
        },
        "failure_ledger": {
            "failure_event_ids": sorted(failures),
            "failures": failure_rows,
            "failure_count": len(failure_rows),
        },
        "attempted_recovery_actions": attempted,
        "worker": _worker_context(state_dir, task.assigned_to if task is not None else ""),
        "truth_refs": {
            "events": "events.jsonl",
            "kanban": "kanban.json",
            "role_sessions": "role_sessions.yaml",
        },
    })


def write_task_recovery_context(
    state_dir: Path,
    events: list[ZfEvent],
    *,
    task_id: str,
    failure_event_ids: list[str] | tuple[str, ...] = (),
    request_id: str,
    source_event_id: str,
) -> dict[str, Any]:
    payload = build_task_recovery_context(
        state_dir,
        events,
        task_id=task_id,
        failure_event_ids=failure_event_ids,
        request_id=request_id,
    )
    return write_sidecar_json(
        Path(state_dir),
        f"diagnostics/recovery/{_safe_slug(request_id)}/context.json",
        payload,
        kind="recovery_context",
        schema_version=RECOVERY_CONTEXT_SCHEMA_VERSION,
        created_by="run-manager",
        source_event_id=source_event_id,
        access_scope={
            "visibility": "project",
            "actor": "run-manager",
            "purpose": "semantic-recovery",
        },
        retention={"class": "audit_required"},
        required=True,
        preview=f"recovery context for {task_id}",
    )


def _event_task_id(event: ZfEvent) -> str:
    payload = event.payload if isinstance(event.payload, dict) else {}
    return str(event.task_id or payload.get("task_id") or "")


def _event_row(event: ZfEvent, *, include_findings: bool = False) -> dict[str, Any]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    row: dict[str, Any] = {
        "event_id": event.id,
        "event_type": event.type,
        "actor": event.actor,
        "ts": event.ts,
        "causation_id": event.causation_id,
        "correlation_id": event.correlation_id,
    }
    if include_findings:
        row["evidence"] = {
            key: payload.get(key)
            for key in (
                "reason",
                "summary",
                "findings",
                "fix_items",
                "feedback",
                "gap_findings",
                "artifact_refs",
                "evidence_refs",
                "target_commit",
            )
            if payload.get(key) not in (None, "", [], {})
        }
    return row


def _worker_context(state_dir: Path, assignee: str | None) -> dict[str, Any]:
    assignee = str(assignee or "")
    path = Path(state_dir) / "role_sessions.yaml"
    if not assignee or not path.exists():
        return {"instance_id": assignee, "available": False}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {"instance_id": assignee, "available": False}
    meta = raw.get("instance_meta") if isinstance(raw, dict) else {}
    meta = meta if isinstance(meta, dict) else {}
    row = meta.get(assignee)
    if not isinstance(row, dict):
        return {"instance_id": assignee, "available": False}
    return redact_obj({
        "instance_id": assignee,
        "available": True,
        "backend": str(row.get("backend") or ""),
        "worker_state": str(row.get("worker_state") or row.get("state") or ""),
        "active_task_id": str(row.get("active_task_id") or row.get("task_id") or ""),
        "last_heartbeat_at": str(row.get("last_heartbeat_at") or ""),
        "last_activity_at": str(row.get("last_activity_at") or ""),
    })


def _age_seconds(value: str | None, now: datetime) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return max(0, int((now - parsed.astimezone(timezone.utc)).total_seconds()))


def _safe_slug(value: str) -> str:
    cleaned = "".join(
        char if char.isalnum() or char in {"-", "_", "."} else "-"
        for char in str(value or "")
    ).strip("-._")
    return cleaned[:96] or "unknown"


__all__ = [
    "RECOVERY_CONTEXT_SCHEMA_VERSION",
    "build_task_recovery_context",
    "write_task_recovery_context",
]
