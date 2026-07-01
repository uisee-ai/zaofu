"""Log-row projection over events.jsonl (doc 82 §8.2).

Standardizes events into human-readable log rows for the Diagnostics Logs
view: timestamp / level / trace / task / role / source / message / attrs.
Read-only — rows are derived per request and never persisted.
"""

from __future__ import annotations

from typing import Any

from zf.core.security.redaction import redact_obj
from zf.runtime.delivery_projection_common import EventSlice

_LEVELS = {"INFO": 0, "WARN": 1, "ERROR": 2}

_ERROR_SUFFIXES = (".failed", ".rejected", ".timed_out", ".error", ".crashed")
_WARN_SUFFIXES = (".stuck", ".blocked", ".escalate", ".escalated", ".degraded",
                  ".stale_rejected", ".adoption_blocked", ".skipped", ".cancelled")
_WARN_TYPES = {"human.escalate", "supervisor.attention.raised"}

_MESSAGE_KEYS = ("message", "reason", "error", "summary", "detail", "status")
_ATTR_KEYS = ("status", "reason", "error", "exit_code", "stage_id", "fanout_id",
              "child_id", "feature_id", "decision", "failure_class", "policy")


def build_log_rows(
    events: EventSlice,
    *,
    limit: int = 200,
    level_min: str = "INFO",
    task_id: str = "",
    role: str = "",
    trace_id: str = "",
) -> list[dict[str, Any]]:
    """Newest-first log rows with level / task / role / trace filtering."""

    threshold = _LEVELS.get(str(level_min or "INFO").upper(), 0)
    rows: list[dict[str, Any]] = []
    for _seq, event in reversed(list(events)):
        if task_id and str(event.task_id or "") != task_id:
            continue
        if role and str(event.actor or "") != role:
            continue
        if trace_id and str(event.correlation_id or "") != trace_id:
            continue
        level = _level_for(event.type)
        if _LEVELS[level] < threshold:
            continue
        rows.append(_row(event, level))
        if len(rows) >= max(1, limit):
            break
    return redact_obj(rows)


def _level_for(event_type: str) -> str:
    lowered = str(event_type or "").lower()
    if lowered.endswith(_ERROR_SUFFIXES):
        return "ERROR"
    if lowered.endswith(_WARN_SUFFIXES) or lowered in _WARN_TYPES:
        return "WARN"
    return "INFO"


def _row(event: Any, level: str) -> dict[str, Any]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    return {
        "timestamp": str(event.ts or ""),
        "level": level,
        "trace_id": str(event.correlation_id or ""),
        "task_id": str(event.task_id or ""),
        "role": str(event.actor or ""),
        "source": str(event.type or ""),
        "message": _message(event.type, payload),
        "attrs": {key: payload[key] for key in _ATTR_KEYS if payload.get(key) not in (None, "")},
        "raw_event_ref": f"event:{event.id}",
    }


def _message(event_type: str, payload: dict[str, Any]) -> str:
    for key in _MESSAGE_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:300]
    return str(event_type or "")
