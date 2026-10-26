"""Bounded, cursor-paged Trace list and detail projections.

The legacy Trace projections remain in ``events.py``.  This module provides
the explicitly versioned Web contract used by the dedicated Traces page while
preserving EventLog-backed grouping and project isolation.
"""

from __future__ import annotations

import base64
import binascii
from datetime import datetime
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from zf.core.config.schema import ZfConfig
from zf.core.security.redaction import redact_event, redact_obj
from zf.runtime.execution_route import (
    _STAGE_LABELS,
    _STAGE_ORDER,
    _actor_for_event,
    _combine_statuses,
    _event_status as _route_event_status,
    _route_status,
    _stage_for_event,
    _summary_label,
)
from zf.web.projections.events import _event_signal_summary, _events_with_seq
from zf.web.projections.trace_identity import (
    event_trace_id as _event_trace_id,
    resolve_trace_id as _resolve_trace_id,
    trace_events as _trace_events,
    wire_trace_id as _wire_trace_id,
)


_CURSOR_VERSION = 1
_LIST_CURSOR_KIND = "trace-list"
_DETAIL_CURSOR_KIND = "trace-detail"
_MAX_CURSOR_BYTES = 2048
_MAX_LIST_LIMIT = 100
_MAX_DETAIL_LIMIT = 200
_MAX_LIST_METADATA_VALUES = 4
_MAX_DETAIL_METADATA_VALUES = 20
_MAX_ROUTE_METADATA_VALUES = 8
_MAX_METADATA_VALUE_CHARS = 120
_MAX_TIMELINE_SCALAR_CHARS = 120


class TraceCursorError(ValueError):
    """Raised when a Trace page cursor cannot safely be resumed."""


def trace_list_page(
    state_dir: Path,
    *,
    limit: int = 50,
    cursor: str = "",
    config: ZfConfig | None = None,
) -> dict[str, Any]:
    """Return one stable newest-first page of EventLog-derived traces."""

    bounded_limit = _bounded_limit(limit, default=50, maximum=_MAX_LIST_LIMIT)
    from zf.web.projections import metric_traces
    fallback = None
    try:
        current_seq = metric_traces.current_seq(state_dir, config)
    except sqlite3.DatabaseError:
        fallback = _events_with_seq(state_dir, config=config)
        current_seq = int(fallback[-1][0]) if fallback else 0
    scope = _scope_digest(state_dir)
    before_seq: int | None = None
    as_of_seq = current_seq
    if cursor:
        cursor_data = _decode_cursor(
            cursor,
            expected_kind=_LIST_CURSOR_KIND,
            expected_scope=scope,
        )
        as_of_seq = cursor_data["as_of_seq"]
        before_seq = cursor_data["before_seq"]
        _validate_cursor_window(
            as_of_seq=as_of_seq,
            before_seq=before_seq,
            current_seq=current_seq,
        )

    try:
        summaries = (_trace_summaries(fallback, as_of_seq=as_of_seq) if fallback is not None
                     else metric_traces.summaries(state_dir, as_of_seq=as_of_seq,
                                                 before_seq=before_seq, limit=bounded_limit + 1))
    except sqlite3.DatabaseError:
        summaries = _trace_summaries(_events_with_seq(state_dir, config=config), as_of_seq=as_of_seq)
    if before_seq is not None:
        summaries = [
            item for item in summaries
            if int(item.get("last_seq") or 0) < before_seq
        ]
    has_more = len(summaries) > bounded_limit
    items = summaries[:bounded_limit]
    next_cursor = ""
    if has_more and items:
        next_cursor = _encode_cursor(
            kind=_LIST_CURSOR_KIND,
            scope=scope,
            as_of_seq=as_of_seq,
            before_seq=int(items[-1]["last_seq"]),
        )
    return {
        "schema_version": "trace-list.v2",
        "items": items,
        "limit": bounded_limit,
        "has_more": has_more,
        "next_cursor": next_cursor or None,
        "as_of_seq": as_of_seq,
        "is_derived_projection": True,
    }


def trace_detail_page(
    state_dir: Path,
    trace_id: str,
    *,
    limit: int = 80,
    cursor: str = "",
    config: ZfConfig | None = None,
) -> dict[str, Any]:
    """Return a bounded oldest-to-newest window from one Trace timeline."""

    bounded_limit = _bounded_limit(limit, default=80, maximum=_MAX_DETAIL_LIMIT)
    events = _events_with_seq(state_dir, config=config)
    current_seq = int(events[-1][0]) if events else 0
    scope = _scope_digest(state_dir)
    before_seq: int | None = None
    as_of_seq = current_seq
    if cursor:
        cursor_data = _decode_cursor(
            cursor,
            expected_kind=_DETAIL_CURSOR_KIND,
            expected_scope=scope,
            expected_trace_id=trace_id,
        )
        as_of_seq = cursor_data["as_of_seq"]
        before_seq = cursor_data["before_seq"]
        _validate_cursor_window(
            as_of_seq=as_of_seq,
            before_seq=before_seq,
            current_seq=current_seq,
        )

    resolved_trace_id = _resolve_trace_id(
        events,
        trace_ref=trace_id,
        as_of_seq=as_of_seq,
    )
    trace_events = _trace_events(
        events,
        trace_id=resolved_trace_id,
        as_of_seq=as_of_seq,
    )
    eligible = trace_events
    if before_seq is not None:
        eligible = [(seq, event) for seq, event in eligible if seq < before_seq]
    has_more = len(eligible) > bounded_limit
    page = eligible[-bounded_limit:]
    next_cursor = ""
    if has_more and page:
        next_cursor = _encode_cursor(
            kind=_DETAIL_CURSOR_KIND,
            scope=scope,
            as_of_seq=as_of_seq,
            before_seq=int(page[0][0]),
            trace_id=trace_id,
        )

    first_seq = int(trace_events[0][0]) if trace_events else 0
    last_seq = int(trace_events[-1][0]) if trace_events else 0
    first_ts = str(getattr(trace_events[0][1], "ts", "") or "") if trace_events else ""
    last_ts = str(getattr(trace_events[-1][1], "ts", "") or "") if trace_events else ""
    latest_event = trace_events[-1][1] if trace_events else None
    tasks, tasks_truncated = _bounded_event_attribute_values(
        trace_events,
        attribute="task_id",
        limit=_MAX_DETAIL_METADATA_VALUES,
    )
    actors, actors_truncated = _bounded_event_attribute_values(
        trace_events,
        attribute="actor",
        limit=_MAX_DETAIL_METADATA_VALUES,
    )
    return {
        "schema_version": "trace-detail.v2",
        "trace_id": trace_id,
        "trace_id_opaque": resolved_trace_id != trace_id,
        "event_count": len(trace_events),
        "first_seq": first_seq,
        "last_seq": last_seq,
        "first_ts": _bounded_metadata_value(first_ts),
        "last_ts": _bounded_metadata_value(last_ts),
        "duration_seconds": _duration_seconds(first_ts, last_ts),
        "status": _event_status(latest_event),
        "tasks": tasks,
        "tasks_truncated": tasks_truncated,
        "actors": actors,
        "actors_truncated": actors_truncated,
        "timeline": [_event_summary(seq, event) for seq, event in page],
        "execution_route": _bounded_execution_route(
            trace_events,
            trace_id=trace_id,
        ),
        "limit": bounded_limit,
        "truncated": len(trace_events) > len(page),
        "has_more": has_more,
        "next_cursor": next_cursor or None,
        "as_of_seq": as_of_seq,
        "empty": not trace_events,
    }


def _trace_summaries(
    events: list[tuple[int, object]],
    *,
    as_of_seq: int,
) -> list[dict[str, Any]]:
    """Group exactly like the legacy ``_traces`` projection, without its cap."""

    grouped: dict[str, dict[str, Any]] = {}
    for seq, event in events:
        if seq > as_of_seq:
            break
        _accumulate_trace_summary(grouped, seq, event)
    return _finalize_trace_summaries(grouped)


def _accumulate_trace_summary(grouped: dict, seq: int, event: object, *, trace_key: str | None = None) -> None:
    trace_key = trace_key if trace_key is not None else _event_trace_id(event)
    if not trace_key:
        return
    item = grouped.setdefault(trace_key, {
        "trace_id": trace_key,
        "first_seq": seq,
        "last_seq": seq,
        "first_ts": str(getattr(event, "ts", "") or ""),
        "last_ts": str(getattr(event, "ts", "") or ""),
        "duration_seconds": None,
        "event_count": 0,
        "task_ids": set(),
        "task_ids_truncated": False,
        "actors": set(),
        "actors_truncated": False,
        "backends": set(),
        "backends_truncated": False,
        "last_type": "",
        "status": "observed",
        "source": (
            "event_trace"
            if not trace_key.startswith("task:")
            else "task_event_fallback"
        ),
    })
    item["event_count"] += 1
    item["last_seq"] = seq
    item["last_ts"] = str(getattr(event, "ts", "") or "")
    item["last_type"] = _bounded_metadata_value(
        getattr(event, "type", ""),
    )
    item["status"] = _event_status(event)
    if getattr(event, "task_id", None):
        _add_bounded_set_value(item, "task_ids", event.task_id)
    if getattr(event, "actor", None):
        _add_bounded_set_value(item, "actors", event.actor)
    payload = getattr(event, "payload", {}) or {}
    if isinstance(payload, dict):
        backend = payload.get("backend")
        if isinstance(backend, str) and backend.strip():
            _add_bounded_set_value(item, "backends", backend)


def _finalize_trace_summaries(grouped: dict) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in grouped.values():
        trace_ref, trace_id_opaque = _wire_trace_id(item["trace_id"])
        out.append({
            **item,
            "trace_id": trace_ref,
            "trace_id_opaque": trace_id_opaque,
            "duration_seconds": _duration_seconds(item["first_ts"], item["last_ts"]),
            "first_ts": _bounded_metadata_value(item["first_ts"]),
            "last_ts": _bounded_metadata_value(item["last_ts"]),
            "task_ids": sorted(item["task_ids"]),
            "actors": sorted(item["actors"]),
            "backends": sorted(item["backends"]),
        })
    out.sort(key=lambda item: int(item["last_seq"]), reverse=True)
    return out


def _event_summary(seq: int, event: object) -> dict[str, Any]:
    safe_event = redact_event(event)  # type: ignore[arg-type]
    payload = getattr(safe_event, "payload", {}) or {}
    if not isinstance(payload, dict):
        payload = {}
    truncated_fields: list[str] = []

    def bounded(field: str, value: object, *, optional: bool = False) -> str | None:
        if optional and value is None:
            return None
        text = str(value or "")
        if len(text) > _MAX_TIMELINE_SCALAR_CHARS:
            truncated_fields.append(field)
            return text[:_MAX_TIMELINE_SCALAR_CHARS]
        return text

    event_id = bounded("id", getattr(safe_event, "id", "")) or ""
    return {
        "seq": seq,
        "id": event_id,
        "ts": bounded("ts", getattr(safe_event, "ts", "")),
        "type": bounded("type", getattr(safe_event, "type", "")),
        "actor": bounded(
            "actor",
            getattr(safe_event, "actor", None),
            optional=True,
        ),
        "task_id": bounded(
            "task_id",
            getattr(safe_event, "task_id", None),
            optional=True,
        ),
        "causation_id": bounded(
            "causation_id",
            getattr(safe_event, "causation_id", None),
            optional=True,
        ),
        "correlation_id": bounded(
            "correlation_id",
            getattr(safe_event, "correlation_id", None),
            optional=True,
        ),
        "status": _event_status(safe_event),
        "summary": _event_signal_summary(
            str(getattr(safe_event, "type", "") or ""),
            payload,
        ),
        "span_id": bounded(
            "span_id",
            payload.get("span_id"),
            optional=True,
        ),
        "parent_span_id": bounded(
            "parent_span_id",
            payload.get("parent_span_id"),
            optional=True,
        ),
        # Raw hydrates the complete redacted event record, not just its payload.
        # An empty-payload event is therefore still a valid Raw target.
        "has_raw": bool(getattr(event, "id", None)) and "id" not in truncated_fields,
        "metadata_truncated": bool(truncated_fields),
        "truncated_fields": truncated_fields,
        "payload_slim": True,
    }


def _bounded_execution_route(
    trace_events: list[tuple[int, object]],
    *,
    trace_id: str,
) -> dict[str, Any]:
    """Return an exact stage fold with bounded descriptive metadata.

    The legacy route includes actor nodes and adjacent-stage Cartesian edges.
    That remains available to v1 callers but cannot be part of a bounded v2
    response.  This fold reads every as-of event, keeps exact stage facts, caps
    only label arrays, and emits no DAG, swimlane, or source-event duplicate.
    """

    stages: dict[str, dict[str, Any]] = {}
    staged_event_count = 0
    for seq, event in trace_events:
        payload = getattr(event, "payload", {}) or {}
        if not isinstance(payload, dict):
            payload = {}
        stage = _stage_for_event(event, payload)  # type: ignore[arg-type]
        if not stage:
            continue
        staged_event_count += 1
        actor = _actor_for_event(event, payload, stage)  # type: ignore[arg-type]
        status = _route_event_status(event, payload)  # type: ignore[arg-type]
        item = stages.setdefault(stage, {
            "stage": stage,
            "first_seq": seq,
            "last_seq": seq,
            "first_ts": str(getattr(event, "ts", "") or ""),
            "last_ts": str(getattr(event, "ts", "") or ""),
            "event_count": 0,
            "failed_count": 0,
            "actor_statuses": {},
            "actors": [],
            "actors_truncated": False,
            "event_types": [],
            "event_types_truncated": False,
            "task_ids": [],
            "task_ids_truncated": False,
        })
        item["last_seq"] = seq
        item["last_ts"] = str(getattr(event, "ts", "") or "")
        item["event_count"] += 1
        item["failed_count"] += int(status == "failed")
        item["actor_statuses"][actor or "system"] = status
        _add_bounded_list_value(item, "actors", actor)
        _add_bounded_list_value(
            item,
            "event_types",
            getattr(event, "type", ""),
        )
        _add_bounded_list_value(
            item,
            "task_ids",
            getattr(event, "task_id", ""),
        )

    linear: list[dict[str, Any]] = []
    for stage in _STAGE_ORDER:
        item = stages.get(stage)
        if item is None:
            continue
        actors = item["actors"]
        values_truncated = any(
            item[f"{key}_truncated"]
            for key in ("actors", "event_types", "task_ids")
        )
        linear.append({
            "stage": stage,
            "label": (
                "Dev Fanout"
                if stage == "dev"
                and (len(actors) > 1 or item["actors_truncated"])
                else _STAGE_LABELS[stage]
            ),
            "status": _combine_statuses(list(item["actor_statuses"].values())),
            "parallel": len(item["actor_statuses"]) > 1,
            "actors": actors,
            "first_seq": item["first_seq"],
            "last_seq": item["last_seq"],
            "first_ts": _bounded_metadata_value(item["first_ts"]),
            "last_ts": _bounded_metadata_value(item["last_ts"]),
            "event_count": item["event_count"],
            "event_types": item["event_types"],
            "task_ids": item["task_ids"],
            "failed_count": item["failed_count"],
            "values_truncated": values_truncated,
        })

    current = linear[-1] if linear else {}
    result = {
        "schema_version": "execution-route-summary.v2",
        "scope": {"task_id": "", "trace_id": trace_id},
        "status": _route_status(linear),
        "current_stage": current.get("stage", ""),
        "current_stage_label": current.get("label", ""),
        "summary": " -> ".join(
            label for step in linear if (label := _summary_label(step))
        ),
        "step_count": len(linear),
        "parallel": any(bool(step["parallel"]) for step in linear),
        "linear": linear,
        "trace_event_count": len(trace_events),
        "source_event_count": staged_event_count,
        "metadata_truncated": any(
            step["values_truncated"] for step in linear
        ),
        "empty": not linear,
    }
    return redact_obj(result)


def _bounded_event_attribute_values(
    events: list[tuple[int, object]],
    *,
    attribute: str,
    limit: int,
) -> tuple[list[str], bool]:
    values: list[str] = []
    truncated = False
    for _, event in events:
        value = _bounded_metadata_value(getattr(event, attribute, ""))
        if not value or value in values:
            continue
        if len(values) < limit:
            values.append(value)
        else:
            truncated = True
    return sorted(values), truncated


def _add_bounded_set_value(
    item: dict[str, Any],
    key: str,
    value: object,
) -> None:
    text = _bounded_metadata_value(value)
    if not text or text in item[key]:
        return
    if len(item[key]) < _MAX_LIST_METADATA_VALUES:
        item[key].add(text)
    else:
        item[f"{key}_truncated"] = True


def _add_bounded_list_value(
    item: dict[str, Any],
    key: str,
    value: object,
) -> None:
    text = _bounded_metadata_value(value)
    if not text or text in item[key]:
        return
    if len(item[key]) < _MAX_ROUTE_METADATA_VALUES:
        item[key].append(text)
    else:
        item[f"{key}_truncated"] = True


def _bounded_metadata_value(value: object) -> str:
    return str(value or "").strip()[:_MAX_METADATA_VALUE_CHARS]


def _event_status(event: object | None) -> str:
    if event is None:
        return "observed"
    payload = getattr(event, "payload", {}) or {}
    if not isinstance(payload, dict):
        payload = {}
    explicit = str(payload.get("status") or payload.get("state") or "").strip().lower()
    if explicit:
        if explicit in {"blocked", "timed_out", "waiting_for_input"}:
            return "blocked"
        if explicit in {"failed", "error", "rejected", "cancelled"}:
            return "failed"
        if explicit in {"completed", "done", "passed", "approved", "accepted", "ready"}:
            return "completed"
        if explicit in {"running", "started", "in_progress", "dispatched"}:
            return "running"
    event_type = str(getattr(event, "type", "") or "").lower()
    if "blocked" in event_type or "timed_out" in event_type:
        return "blocked"
    if any(token in event_type for token in ("failed", "error", "rejected")):
        return "failed"
    if any(token in event_type for token in ("completed", "done", "passed", "approved", "accepted")):
        return "completed"
    if any(token in event_type for token in ("running", "started", "progress", "in_progress", "dispatched")):
        return "running"
    return "observed"


def _duration_seconds(first_ts: str, last_ts: str) -> int | None:
    if not first_ts or not last_ts:
        return None
    try:
        first = datetime.fromisoformat(first_ts.replace("Z", "+00:00"))
        last = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
        return max(0, int((last - first).total_seconds()))
    except (OverflowError, TypeError, ValueError):
        return None


def _bounded_limit(value: int, *, default: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, maximum))


def _scope_digest(state_dir: Path) -> str:
    return hashlib.sha256(str(Path(state_dir).resolve()).encode("utf-8")).hexdigest()[:24]


def _encode_cursor(
    *,
    kind: str,
    scope: str,
    as_of_seq: int,
    before_seq: int,
    trace_id: str = "",
) -> str:
    body: dict[str, Any] = {
        "v": _CURSOR_VERSION,
        "kind": kind,
        "scope": scope,
        "as_of_seq": as_of_seq,
        "before_seq": before_seq,
    }
    if trace_id:
        body["trace_id"] = trace_id
    encoded = json.dumps(body, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return base64.urlsafe_b64encode(encoded.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_cursor(
    cursor: str,
    *,
    expected_kind: str,
    expected_scope: str,
    expected_trace_id: str = "",
) -> dict[str, Any]:
    token = str(cursor or "").strip()
    if not token or len(token) > _MAX_CURSOR_BYTES:
        raise TraceCursorError("invalid trace cursor")
    try:
        padding = "=" * (-len(token) % 4)
        raw = base64.b64decode(
            (token + padding).encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError, json.JSONDecodeError, binascii.Error):
        raise TraceCursorError("invalid trace cursor") from None
    if not isinstance(data, dict):
        raise TraceCursorError("invalid trace cursor")
    if data.get("v") != _CURSOR_VERSION or data.get("kind") != expected_kind:
        raise TraceCursorError("trace cursor contract mismatch")
    if data.get("scope") != expected_scope:
        raise TraceCursorError("trace cursor project mismatch")
    if expected_trace_id and data.get("trace_id") != expected_trace_id:
        raise TraceCursorError("trace cursor trace mismatch")
    as_of_seq = data.get("as_of_seq")
    before_seq = data.get("before_seq")
    if type(as_of_seq) is not int or type(before_seq) is not int:
        raise TraceCursorError("invalid trace cursor position")
    if as_of_seq < 0 or before_seq < 0:
        raise TraceCursorError("invalid trace cursor position")
    return {
        "as_of_seq": as_of_seq,
        "before_seq": before_seq,
    }


def _validate_cursor_window(
    *,
    as_of_seq: int,
    before_seq: int,
    current_seq: int,
) -> None:
    if as_of_seq > current_seq:
        raise TraceCursorError("trace cursor is outside the current event window")
    if before_seq > as_of_seq:
        raise TraceCursorError("invalid trace cursor position")


__all__ = [
    "TraceCursorError",
    "trace_detail_page",
    "trace_list_page",
]
