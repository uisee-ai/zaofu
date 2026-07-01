"""Project-scoped pause/resume lifecycle projection."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.security.redaction import redact_obj


PAUSE_EVENT_TYPES = {
    "loop.pause_requested",
    "runtime.maintenance.entered",
    "dispatch.paused",
}
RESUME_EVENT_TYPES = {
    "loop.resume_requested",
    "runtime.maintenance.exited",
    "dispatch.resumed",
}
HEADLESS_STOP_EVENT_TYPES = {
    "agent.session.run.cancelled",
    "operator.session.stopped",
    "run.cancelled",
}
CHECKPOINT_EVENT_TYPES = {"worker.checkpointed"}
RESUME_SWEEP_SIGNAL_TYPES = {
    "worker.probe.silent",
    "worker.stuck",
    "worker.context.warning",
    "worker.context.critical",
    "worker.drift.detected",
}


def project_pause_lifecycle(
    state_dir: Path,
    *,
    events: list[ZfEvent] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fold pause/resume intent, checkpoint, and recovery sweep evidence.

    The projection is read-only. Runtime truth remains in ``events.jsonl``;
    this function only gives Web/API and the dispatcher a shared way to
    interpret the latest pause lifecycle state.
    """

    state_dir = Path(state_dir)
    events = events if events is not None else _read_events(state_dir)
    now = now or datetime.now(timezone.utc)
    indexed = list(enumerate(events, start=1))
    latest_pause = _latest(indexed, PAUSE_EVENT_TYPES)
    latest_resume = _latest(indexed, RESUME_EVENT_TYPES)
    status, paused = _project_status(latest_pause, latest_resume)
    pause_seq = latest_pause[0] if latest_pause else 0
    resume_seq = latest_resume[0] if latest_resume else 0

    affected_sessions = [
        _affected_session(seq, event, pause_seq=pause_seq, resume_seq=resume_seq)
        for seq, event in indexed
        if event.type in HEADLESS_STOP_EVENT_TYPES
    ]
    checkpoints = [
        _checkpoint_ref(seq, event, pause_seq=pause_seq, resume_seq=resume_seq)
        for seq, event in indexed
        if event.type in CHECKPOINT_EVENT_TYPES
    ]
    resume_signals = [
        _resume_signal(seq, event)
        for seq, event in indexed
        if resume_seq and seq > resume_seq and event.type in RESUME_SWEEP_SIGNAL_TYPES
    ]
    lifecycle_events = [
        _event_ref(seq, event)
        for seq, event in indexed
        if event.type in (
            PAUSE_EVENT_TYPES
            | RESUME_EVENT_TYPES
            | HEADLESS_STOP_EVENT_TYPES
            | CHECKPOINT_EVENT_TYPES
        )
    ]

    projection = {
        "schema_version": "pause-lifecycle.v1",
        "generated_at": now.isoformat(),
        "state_dir": str(state_dir),
        "status": status,
        "paused": paused,
        "dispatch_allowed": not paused,
        "current": _current_state(status, paused, latest_pause, latest_resume),
        "summary": {
            "affected_sessions": len(affected_sessions),
            "checkpoints": len(checkpoints),
            "resume_signals": len(resume_signals),
            "dispatch_allowed": not paused,
        },
        "affected_sessions": list(reversed(affected_sessions))[:50],
        "checkpoints": list(reversed(checkpoints))[:50],
        "resume_sweep": {
            "last_resume_event_id": latest_resume[1].id if latest_resume else "",
            "last_resume_at": latest_resume[1].ts if latest_resume else "",
            "signals": list(reversed(resume_signals))[:50],
            "stale_workers": _stale_workers(resume_signals),
            "suggestions": [
                _resume_suggestion(signal) for signal in reversed(resume_signals)
            ][:30],
        },
        "events": list(reversed(lifecycle_events))[:100],
    }
    return redact_obj(projection)


def is_dispatch_paused(
    state_dir: Path,
    *,
    events: list[ZfEvent] | None = None,
) -> bool:
    projection = project_pause_lifecycle(state_dir, events=events)
    return bool(projection.get("paused"))


def _read_events(state_dir: Path) -> list[ZfEvent]:
    try:
        return EventLog(Path(state_dir) / "events.jsonl").read_all()
    except Exception:
        return []


def _latest(
    indexed: list[tuple[int, ZfEvent]],
    event_types: set[str],
) -> tuple[int, ZfEvent] | None:
    for seq, event in reversed(indexed):
        if event.type in event_types:
            return seq, event
    return None


def _project_status(
    latest_pause: tuple[int, ZfEvent] | None,
    latest_resume: tuple[int, ZfEvent] | None,
) -> tuple[str, bool]:
    pause_seq = latest_pause[0] if latest_pause else 0
    resume_seq = latest_resume[0] if latest_resume else 0
    if pause_seq > resume_seq:
        event = latest_pause[1]
        if event.type == "loop.pause_requested":
            return "pause_requested", True
        return "paused", True
    if resume_seq > pause_seq and latest_resume is not None:
        if latest_resume[1].type == "loop.resume_requested":
            return "resuming", False
    return "running", False


def _current_state(
    status: str,
    paused: bool,
    latest_pause: tuple[int, ZfEvent] | None,
    latest_resume: tuple[int, ZfEvent] | None,
) -> dict[str, Any]:
    pause_event = latest_pause[1] if latest_pause else None
    resume_event = latest_resume[1] if latest_resume else None
    source = pause_event if paused else resume_event
    source_payload = _payload(source)
    return {
        "status": status,
        "paused": paused,
        "reason": _reason(source),
        "actor": source.actor if source else "",
        "pause_event_id": pause_event.id if pause_event else "",
        "pause_event_type": pause_event.type if pause_event else "",
        "pause_at": pause_event.ts if pause_event else "",
        "resume_event_id": resume_event.id if resume_event else "",
        "resume_event_type": resume_event.type if resume_event else "",
        "resume_at": resume_event.ts if resume_event else "",
        "trigger_id": str(source_payload.get("trigger_id") or ""),
        "maintenance_current": str(source_payload.get("maintenance_current") or ""),
    }


def _affected_session(
    seq: int,
    event: ZfEvent,
    *,
    pause_seq: int,
    resume_seq: int,
) -> dict[str, Any]:
    payload = _payload(event)
    session = payload.get("session") if isinstance(payload.get("session"), dict) else {}
    return {
        "seq": seq,
        "event_id": event.id,
        "event_type": event.type,
        "task_id": event.task_id or str(payload.get("task_id") or ""),
        "actor": event.actor or "",
        "ts": event.ts,
        "session_id": str(payload.get("session_id") or session.get("session_id") or ""),
        "conversation_id": str(payload.get("conversation_id") or ""),
        "thread_id": str(payload.get("thread_id") or ""),
        "run_id": str(payload.get("run_id") or ""),
        "provider": str(payload.get("provider") or session.get("backend") or ""),
        "reason": _reason(event),
        "during_pause": _between(seq, pause_seq, resume_seq),
    }


def _checkpoint_ref(
    seq: int,
    event: ZfEvent,
    *,
    pause_seq: int,
    resume_seq: int,
) -> dict[str, Any]:
    payload = _payload(event)
    return {
        "seq": seq,
        "event_id": event.id,
        "task_id": event.task_id or str(payload.get("task_id") or ""),
        "instance_id": _event_instance(event),
        "role": str(payload.get("role") or ""),
        "checkpoint_id": str(payload.get("checkpoint_id") or ""),
        "resume_packet_path": str(payload.get("resume_packet_path") or ""),
        "dirty_diff_artifact": str(payload.get("dirty_diff_artifact") or ""),
        "transcript_path": str(payload.get("transcript_path") or ""),
        "reason": _reason(event),
        "ts": event.ts,
        "during_pause": _between(seq, pause_seq, resume_seq),
    }


def _resume_signal(seq: int, event: ZfEvent) -> dict[str, Any]:
    return {
        "seq": seq,
        "event_id": event.id,
        "event_type": event.type,
        "task_id": _event_task(event),
        "instance_id": _event_instance(event),
        "actor": event.actor or "",
        "ts": event.ts,
        "reason": _reason(event),
    }


def _resume_suggestion(signal: dict[str, Any]) -> dict[str, Any]:
    event_type = str(signal.get("event_type") or "")
    if event_type in {"worker.context.warning", "worker.context.critical"}:
        recovery = "checkpoint_then_context_recycle"
    elif event_type == "worker.drift.detected":
        recovery = "inspect_worktree_drift_before_dispatch"
    else:
        recovery = "probe_or_respawn_stale_worker"
    return {
        "suggestion_type": "resume_sweep",
        "recommended_recovery": recovery,
        "task_id": signal.get("task_id") or "",
        "instance_id": signal.get("instance_id") or "",
        "trigger_event_id": signal.get("event_id") or "",
        "reason": signal.get("reason") or "",
    }


def _stale_workers(signals: list[dict[str, Any]]) -> list[str]:
    workers = {
        str(signal.get("instance_id") or "")
        for signal in signals
        if str(signal.get("instance_id") or "")
    }
    return sorted(workers)


def _event_ref(seq: int, event: ZfEvent) -> dict[str, Any]:
    return {
        "seq": seq,
        "event_id": event.id,
        "type": event.type,
        "task_id": _event_task(event),
        "actor": event.actor or "",
        "ts": event.ts,
        "reason": _reason(event),
    }


def _between(seq: int, start_seq: int, end_seq: int) -> bool:
    if not start_seq:
        return False
    return seq >= start_seq and (not end_seq or seq <= end_seq)


def _payload(event: ZfEvent | None) -> dict[str, Any]:
    if event is None or not isinstance(event.payload, dict):
        return {}
    return event.payload


def _reason(event: ZfEvent | None) -> str:
    payload = _payload(event)
    for key in ("reason", "validation_summary", "message", "summary"):
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _event_task(event: ZfEvent | None) -> str:
    payload = _payload(event)
    return str((event.task_id if event else "") or payload.get("task_id") or "").strip()


def _event_instance(event: ZfEvent | None) -> str:
    payload = _payload(event)
    for key in ("instance_id", "assigned_worker", "worker", "assignee", "role"):
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return str(event.actor or "").strip() if event else ""


__all__ = [
    "project_pause_lifecycle",
    "is_dispatch_paused",
]
