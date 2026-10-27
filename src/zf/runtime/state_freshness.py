"""Latest freshness facts without matching every historical payload to a task."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from zf.core.events.model import ZfEvent


def latest_state_freshness(
    events: list[ZfEvent], *, task_id: str, actor: str, now: datetime, index=None,
) -> dict[str, Any]:
    from zf.runtime.long_horizon import _age_seconds, _event_task_match

    categories = {
        "progress": {"worker.progress", "phase.progressed"},
        "file_change": {"task.files_touched", "task.ref.updated", "dev.build.done"},
        "test": {"test.passed", "test.failed", "static_gate.passed", "static_gate.failed"},
        "evidence": {"task.done.evidence", "task.evidence_linked", "task.artifact_refs.updated"},
    }
    found: dict[str, ZfEvent] = {}
    context_ratio = None
    indexed = index is not None and events is index.events
    for i in range(len(events) - 1, -1, -1):
        event = events[i]
        if "heartbeat" not in found and event.type == "worker.heartbeat" and (not actor or event.actor == actor):
            found["heartbeat"] = event
        if context_ratio is None and (not actor or event.actor in {actor, None}):
            payload = event.payload if isinstance(event.payload, dict) else {}
            for key in ("context_usage_ratio", "usage_ratio", "context_ratio", "ratio"):
                value = payload.get(key)
                if isinstance(value, int | float):
                    context_ratio = float(value)
                    break

        needed = [key for key, kinds in categories.items() if key not in found and event.type in kinds]
        if "event" not in found or needed:
            # Actor equality is sufficient and avoids walking unrelated bodies.
            matches = bool(actor and event.actor == actor)
            if not matches and task_id:
                matches = index.event_matches_task(i, task_id) if indexed else _event_task_match(event, task_id)
            if matches:
                found.setdefault("event", event)
                for key in needed:
                    found[key] = event
        if len(found) == 6 and context_ratio is not None:
            break

    result: dict[str, Any] = {}
    for key in ("event", "heartbeat", "progress", "file_change", "test", "evidence"):
        event = found.get(key)
        result[f"last_{key}_at"] = event.ts if event else ""
        result[f"last_{key}_age_sec"] = _age_seconds(event.ts, now=now) if event else None
    result["context_usage_ratio"] = context_ratio
    result["idle_age_sec"] = result["last_event_age_sec"]
    return result
