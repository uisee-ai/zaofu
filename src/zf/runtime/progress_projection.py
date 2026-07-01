"""Structured worker progress and phase projection."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.security.redaction import redact_obj
from zf.core.state.atomic_io import atomic_write_text


PHASE_ORDER = [
    "intake",
    "design",
    "design_done",
    "design_critiqued",
    "implement",
    "build_done",
    "review",
    "review_approved",
    "test",
    "test_passed",
    "judge",
    "judge_passed",
    "done",
]

_PHASE_RANK = {phase: idx for idx, phase in enumerate(PHASE_ORDER)}
_EVENT_PHASE = {
    "arch.proposal.done": "design_done",
    "design.critique.done": "design_critiqued",
    "dev.build.done": "build_done",
    "review.approved": "review_approved",
    "test.passed": "test_passed",
    "judge.passed": "judge_passed",
    "task.done.accepted": "done",
    "task.done": "done",
}


def project_task_progress(
    state_dir: Path,
    task_id: str,
    *,
    events: list[ZfEvent] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    events = events if events is not None else EventLog(state_dir / "events.jsonl").read_all()
    now = now or datetime.now(timezone.utc)
    current_phase = ""
    latest_progress: dict[str, Any] | None = None
    timeline: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    last_progress_ts = ""
    for event in events:
        if event.task_id != task_id:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.type == "worker.progress":
            item = _progress_item(event, payload)
            latest_progress = item
            last_progress_ts = event.ts
            phase = _payload_str(payload, "phase")
            if current_phase and phase and _phase_rank(phase) < _phase_rank(current_phase):
                diagnostics.append({
                    "type": "phase.regression.ignored",
                    "event_id": event.id,
                    "from_phase": current_phase,
                    "attempted_phase": phase,
                    "message": "progress phase regression ignored in projection",
                })
                timeline.append({**item, "regression_ignored": True})
            else:
                timeline.append(item)
            if phase and _phase_rank(phase) >= _phase_rank(current_phase):
                current_phase = phase
            continue
        phase = _payload_str(payload, "phase") if event.type == "phase.progressed" else _EVENT_PHASE.get(event.type, "")
        if phase:
            item = _phase_item(event, payload, phase)
            if current_phase and _phase_rank(phase) < _phase_rank(current_phase):
                diagnostics.append({
                    "type": "phase.regression.ignored",
                    "event_id": event.id,
                    "from_phase": current_phase,
                    "attempted_phase": phase,
                    "message": "phase regression ignored in projection",
                })
                timeline.append({**item, "regression_ignored": True})
            else:
                current_phase = phase
                timeline.append(item)
            continue
        if event.type in {"phase.regression.ignored", "phase.regression.blocked"}:
            diagnostics.append({
                "type": event.type,
                "event_id": event.id,
                "from_phase": _payload_str(payload, "from_phase"),
                "attempted_phase": _payload_str(payload, "attempted_phase"),
                "message": _payload_str(payload, "message") or _payload_str(payload, "reason"),
            })
            timeline.append(_phase_item(event, payload, _payload_str(payload, "attempted_phase")))
    return {
        "schema_version": "task-progress.v1",
        "generated_at": now.isoformat(),
        "task_id": task_id,
        "current_phase": current_phase,
        "latest_progress": latest_progress,
        "timeline": redact_obj(timeline),
        "diagnostics": redact_obj(diagnostics),
        "freshness": {
            "last_progress_at": last_progress_ts,
            "last_progress_age_sec": _age_seconds(last_progress_ts, now=now),
        },
    }


def write_task_progress_projection(
    state_dir: Path,
    task_id: str,
    projection: dict[str, Any],
) -> Path:
    path = state_dir / "projections" / "tasks" / task_id / "progress.json"
    atomic_write_text(path, json.dumps(projection, ensure_ascii=False, indent=2) + "\n")
    return path


def phase_regression(
    events: list[ZfEvent],
    *,
    task_id: str,
    attempted_phase: str,
    source_event_id: str = "",
) -> tuple[bool, str]:
    current = ""
    for event in events:
        if source_event_id and event.id == source_event_id:
            continue
        if event.task_id != task_id:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        phase = _payload_str(payload, "phase") if event.type == "phase.progressed" else _EVENT_PHASE.get(event.type, "")
        if not phase:
            continue
        if _phase_rank(phase) >= _phase_rank(current):
            current = phase
    return bool(current and _phase_rank(attempted_phase) < _phase_rank(current)), current


def _progress_item(event: ZfEvent, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": event.id,
        "type": event.type,
        "ts": event.ts,
        "dispatch_id": _payload_str(payload, "dispatch_id"),
        "role": _payload_str(payload, "role"),
        "instance_id": _payload_str(payload, "instance_id") or event.actor or "",
        "phase": _payload_str(payload, "phase"),
        "message": _payload_str(payload, "message"),
        "current_subtask": _payload_str(payload, "current_subtask"),
        "percent": payload.get("percent"),
        "source": _payload_str(payload, "source"),
        "source_event_id": _payload_str(payload, "source_event_id"),
        "context_usage_ratio": payload.get("context_usage_ratio"),
    }


def _phase_item(event: ZfEvent, payload: dict[str, Any], phase: str) -> dict[str, Any]:
    return {
        "event_id": event.id,
        "type": event.type,
        "ts": event.ts,
        "dispatch_id": _payload_str(payload, "dispatch_id"),
        "role": _payload_str(payload, "role"),
        "instance_id": _payload_str(payload, "instance_id") or event.actor or "",
        "phase": phase,
        "message": _payload_str(payload, "message"),
        "source": _payload_str(payload, "source"),
        "source_event_id": _payload_str(payload, "source_event_id"),
    }


def _phase_rank(phase: str) -> int:
    if not phase:
        return -1
    return _PHASE_RANK.get(phase, len(_PHASE_RANK))


def _payload_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    return str(value).strip() if value is not None else ""


def _age_seconds(ts: str, *, now: datetime) -> float | None:
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, round((now - parsed).total_seconds(), 3))
