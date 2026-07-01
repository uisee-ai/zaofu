"""Read-only operation timeline projection.

An operation is a dispatch-scoped slice of events. The projection is derived
from events.jsonl and TaskStore so it remains rebuildable runtime state, not a
second control plane.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.security.redaction import redact_obj
from zf.core.state.atomic_io import atomic_write_text
from zf.core.task.store import TERMINAL_STATES, TaskStore


def project_operation(
    state_dir: Path,
    dispatch_id: str,
    *,
    events: list[ZfEvent] | None = None,
) -> dict[str, Any]:
    events = events if events is not None else EventLog(state_dir / "events.jsonl").read_all()
    task_store = TaskStore(state_dir / "kanban.json")
    matched = [_event for _event in events if _event_dispatch_id(_event) == dispatch_id]
    task_id = _first_task_id(matched)
    task = task_store.get(task_id) if task_id else None
    role_session = _role_session_for_dispatch(state_dir, dispatch_id)
    timeline = [_timeline_item(event) for event in matched]
    last = matched[-1] if matched else None
    provider_events = [
        event for event in matched
        if event.type in {
            "agent.api_blocked",
            "agent.timeout",
            "provider.stop.recovery",
            "provider.health.changed",
            "provider.cooldown.started",
            "provider.account.exhausted",
        }
    ]
    provider_health = _provider_health(provider_events)
    evidence_refs = _evidence_refs(matched)
    resume_packet_path = _resume_packet_path(state_dir, task_id, dispatch_id)
    return {
        "schema_version": "operation.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "operation_id": f"op-{dispatch_id}" if dispatch_id else "",
        "dispatch_id": dispatch_id,
        "task_id": task_id,
        "task_title": task.title if task is not None else "",
        "task_status": task.status if task is not None else "",
        "state": _operation_state(task.status if task is not None else "", matched),
        "role": _first_payload_str(matched, "role") or _role_from_instance(_first_payload_str(matched, "instance_id")),
        "instance_id": _first_payload_str(matched, "instance_id") or _first_actor(matched),
        "backend": _first_payload_str(matched, "backend"),
        "provider_session_ref": (
            role_session.get("session_id")
            or role_session.get("provider_session_ref")
            or _first_payload_str(matched, "session_ref")
        ),
        "last_event_id": last.id if last else "",
        "last_event_type": last.type if last else "",
        "last_event_at": last.ts if last else "",
        "health": provider_health,
        "evidence_refs": evidence_refs,
        "resume_packet_path": resume_packet_path,
        "timeline": redact_obj(timeline),
        "freshness": {
            "last_event_at": last.ts if last else "",
            "event_count": len(matched),
        },
    }


def project_task_operations(
    state_dir: Path,
    task_id: str,
    *,
    events: list[ZfEvent] | None = None,
) -> dict[str, Any]:
    events = events if events is not None else EventLog(state_dir / "events.jsonl").read_all()
    dispatch_ids: list[str] = []
    for event in events:
        if event.task_id != task_id:
            continue
        dispatch_id = _event_dispatch_id(event)
        if dispatch_id and dispatch_id not in dispatch_ids:
            dispatch_ids.append(dispatch_id)
    return {
        "schema_version": "task-operations.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "task_id": task_id,
        "operations": [
            project_operation(state_dir, dispatch_id, events=events)
            for dispatch_id in dispatch_ids
        ],
    }


def write_operation_projection(
    state_dir: Path,
    dispatch_id: str,
    projection: dict[str, Any],
) -> Path:
    path = state_dir / "projections" / "operations" / f"{dispatch_id}.json"
    atomic_write_text(path, json.dumps(projection, ensure_ascii=False, indent=2) + "\n")
    return path


def _event_dispatch_id(event: ZfEvent) -> str:
    payload = event.payload if isinstance(event.payload, dict) else {}
    return _payload_str(payload, "dispatch_id") or _payload_str(payload, "active_dispatch_id")


def _first_task_id(events: list[ZfEvent]) -> str:
    for event in events:
        if event.task_id:
            return event.task_id
        payload = event.payload if isinstance(event.payload, dict) else {}
        task_id = _payload_str(payload, "task_id")
        if task_id:
            return task_id
    return ""


def _first_actor(events: list[ZfEvent]) -> str:
    return next((event.actor for event in events if event.actor), "")


def _first_payload_str(events: list[ZfEvent], key: str) -> str:
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        value = _payload_str(payload, key)
        if value:
            return value
    return ""


def _timeline_item(event: ZfEvent) -> dict[str, Any]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    return {
        "event_id": event.id,
        "type": event.type,
        "ts": event.ts,
        "actor": event.actor,
        "task_id": event.task_id,
        "dispatch_id": _event_dispatch_id(event),
        "role": _payload_str(payload, "role"),
        "instance_id": _payload_str(payload, "instance_id"),
        "phase": _payload_str(payload, "phase"),
        "reason": _payload_str(payload, "reason"),
        "message": _payload_str(payload, "message") or _payload_str(payload, "summary"),
        "status": _payload_str(payload, "status"),
    }


def _operation_state(task_status: str, events: list[ZfEvent]) -> str:
    if task_status in TERMINAL_STATES:
        return task_status
    event_types = {event.type for event in events}
    if event_types & {"agent.api_blocked", "provider.account.exhausted"}:
        return "blocked"
    if event_types & {"worker.context.critical", "completion_audit.routed"}:
        return "needs_recovery"
    if event_types & {"test.failed", "verify.failed", "review.rejected", "judge.failed", "gate.failed"}:
        return "rework"
    if event_types & {"worker.completed", "dev.build.done", "review.approved", "verify.passed", "test.passed", "judge.passed"}:
        return "progressed"
    return "in_progress" if events else "unknown"


def _provider_health(events: list[ZfEvent]) -> dict[str, Any]:
    if not events:
        return {"status": "unknown", "reason": "", "last_event_id": ""}
    event = events[-1]
    payload = event.payload if isinstance(event.payload, dict) else {}
    return {
        "status": _payload_str(payload, "status") or (
            "blocked" if event.type in {"agent.api_blocked", "provider.account.exhausted"} else "degraded"
        ),
        "reason": _payload_str(payload, "reason"),
        "action": _payload_str(payload, "action"),
        "last_event_id": event.id,
        "last_event_type": event.type,
        "requires_operator": bool(payload.get("requires_operator")),
    }


def _evidence_refs(events: list[ZfEvent]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        for key in ("artifact_refs", "evidence_refs", "files_touched", "changed_files"):
            raw = payload.get(key)
            values = raw if isinstance(raw, list) else [raw] if raw else []
            for value in values:
                refs.append({"event_id": event.id, "kind": key, "value": value})
    return refs


def _resume_packet_path(state_dir: Path, task_id: str, dispatch_id: str) -> str:
    if not task_id:
        return ""
    candidates = [
        state_dir / "resume_packets" / f"{task_id}.{dispatch_id}.json",
        state_dir / "resume_packets" / f"{task_id}.json",
    ]
    for path in candidates:
        if path.exists():
            return str(path)
    return ""


def _role_session_for_dispatch(state_dir: Path, dispatch_id: str) -> dict[str, str]:
    path = state_dir / "role_sessions.yaml"
    if not path.exists() or not dispatch_id:
        return {}
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    for value in data.values():
        if not isinstance(value, dict):
            continue
        if str(value.get("dispatch_id") or value.get("active_dispatch_id") or "") == dispatch_id:
            return {str(k): str(v) for k, v in value.items() if v is not None}
    return {}


def _role_from_instance(instance_id: str) -> str:
    return instance_id.split("-", 1)[0] if "-" in instance_id else instance_id


def _payload_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    return str(value).strip() if value is not None else ""
