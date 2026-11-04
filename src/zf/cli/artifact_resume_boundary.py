"""Conservative context-boundary checks for a resumed Codex read."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any

from zf.core.events.log import EventLog
from zf.runtime.artifact_context_events import artifact_context_events


_COMPACT_TYPES = {"compacted", "context_compacted", "context.compacted", "worker.context.compacted"}


def _epoch(value: Any) -> float:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("context boundary timestamp lacks timezone")
    return parsed.timestamp()


def continuation_boundary(*, event_log: EventLog | None, transcript: Path | None,
                          actor: str, session_id: str) -> float | None:
    """Return the latest known compaction or None when session evidence is unsafe.

    A matching transcript does not prove model comprehension or guarantee that
    an unobserved provider compaction cannot occur. This only rejects known
    boundaries and incomplete session evidence.
    """
    if event_log is None or transcript is None or not session_id:
        return None
    boundary = 0.0
    seen_session = False
    try:
        with transcript.open(encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                entry = json.loads(line)
                if not isinstance(entry, dict):
                    return None
                payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
                if entry.get("type") == "session_meta":
                    if str(payload.get("id") or "") != session_id:
                        return None
                    seen_session = True
                if str(entry.get("type") or "") in _COMPACT_TYPES or str(payload.get("type") or "") in _COMPACT_TYPES:
                    if not seen_session:
                        return None
                    boundary = max(boundary, _epoch(entry.get("timestamp")))
        if not seen_session:
            return None
        for event in artifact_context_events(event_log):
            payload = event.payload if isinstance(event.payload, dict) else {}
            if event.type not in {"worker.context.compacted", "worker.context.compact.requested"}:
                continue
            if event.actor != actor and str(payload.get("instance_id") or "") != actor:
                continue
            bound_session = str(payload.get("session_id") or "")
            if bound_session and bound_session != session_id:
                continue
            boundary = max(boundary, _epoch(event.ts))
        return boundary
    except (OSError, ValueError, TypeError, KeyError):
        return None
