"""Truthful, bounded lifecycle spans for one selected Trace.

These are ZaoFu lifecycle spans reconstructed only from explicit start and
terminal EventLog occurrences.  They are not provider-native/OTel spans and
never infer timing from arbitrary Trace events or display stages.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import sqlite3
from pathlib import Path
from typing import Any

from zf.core.config.schema import ZfConfig
from zf.web.projections.events import _events_with_seq
from zf.web.projections.trace_pages import (
    _bounded_limit,
    _decode_cursor,
    _encode_cursor,
    _resolve_trace_id,
    _scope_digest,
    _trace_events,
    _validate_cursor_window,
)


_CURSOR_KIND = "trace-spans"
_MAX_LIMIT = 100
_MAX_TEXT_CHARS = 120
_MAX_DIAGNOSTICS = 40
_START_TYPES = {
    "agent.session.run.started": "agent.session.run",
    "runtime.action.attempt.started": "runtime.action.attempt",
    "task.attempt.started": "task.attempt",
}
_TERMINAL_TYPES = {
    "agent.session.run.completed": ("agent.session.run", "completed"),
    "agent.session.run.failed": ("agent.session.run", "failed"),
    "agent.session.run.cancelled": ("agent.session.run", "cancelled"),
    "runtime.action.attempt.completed": ("runtime.action.attempt", "completed"),
    "runtime.action.attempt.failed": ("runtime.action.attempt", "failed"),
    "task.attempt.succeeded": ("task.attempt", "completed"),
    "task.attempt.failed": ("task.attempt", "failed"),
    "task.attempt.superseded": ("task.attempt", "cancelled"),
    "task.attempt.deadlettered": ("task.attempt", "failed"),
}


def trace_span_page(
    state_dir: Path,
    trace_id: str,
    *,
    limit: int = 50,
    cursor: str = "",
    focus_span_id: str = "",
    config: ZfConfig | None = None,
) -> dict[str, Any]:
    """Return a stable page of provable ZaoFu lifecycle spans."""

    bounded_limit = _bounded_limit(limit, default=50, maximum=_MAX_LIMIT)
    from zf.web.projections import metric_traces
    events = None
    try:
        current_seq = metric_traces.current_seq(state_dir, config)
    except sqlite3.DatabaseError:
        events = _events_with_seq(state_dir, config=config)
        current_seq = int(events[-1][0]) if events else 0
    scope = _scope_digest(state_dir)
    as_of_seq = current_seq
    before_seq: int | None = None
    if cursor:
        cursor_data = _decode_cursor(
            cursor,
            expected_kind=_CURSOR_KIND,
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

    if events is None:
        try:
            events = metric_traces.trace_events(state_dir, trace_id, as_of_seq=as_of_seq, config=config)
        except (sqlite3.DatabaseError, OSError, ValueError):
            events = _events_with_seq(state_dir, config=config)

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
    folded = _fold_lifecycle_spans(trace_events, trace_ref=trace_id)
    spans = folded["spans"]
    bounded_focus = focus_span_id if len(focus_span_id) <= _MAX_TEXT_CHARS else ""
    focused_item = next(
        (
            item
            for _, item in spans
            if bounded_focus and item["span_id"] == bounded_focus
        ),
        None,
    )
    eligible = spans
    if before_seq is not None:
        eligible = [item for item in eligible if item[0] < before_seq]
    has_more = len(eligible) > bounded_limit
    page = eligible[-bounded_limit:]
    next_cursor = None
    if has_more and page:
        next_cursor = _encode_cursor(
            kind=_CURSOR_KIND,
            scope=scope,
            as_of_seq=as_of_seq,
            before_seq=page[0][0],
            trace_id=trace_id,
        )

    coverage_status = "available"
    coverage_reason = "Paired allowlisted ZaoFu lifecycle events."
    if folded["degraded_span_count"] or folded["diagnostic_count"]:
        coverage_status = "degraded"
        coverage_reason = "Lifecycle evidence is partial or has degraded timing."
    elif not spans:
        coverage_status = "empty"
        coverage_reason = "No allowlisted lifecycle events were observed."

    return {
        "schema_version": "trace-spans.v1",
        "trace_id": trace_id,
        "items": [item[1] for item in page],
        "focused_item": focused_item,
        "span_count": len(spans),
        "limit": bounded_limit,
        "has_more": has_more,
        "next_cursor": next_cursor,
        "as_of_seq": as_of_seq,
        "coverage": {
            "status": coverage_status,
            "reason": coverage_reason,
            "collector": "unobserved",
            "projection": "zaofu.lifecycle-pairs.v1",
            "ledger": "complete",
            "source": "events.jsonl",
            "observed_allowlisted_event_count": folded[
                "observed_allowlisted_event_count"
            ],
            "eligible_event_count": folded["eligible_event_count"],
            "paired_span_count": len(spans),
            "degraded_span_count": folded["degraded_span_count"],
            "unpaired_start_count": folded["unpaired_start_count"],
            "unpaired_terminal_count": folded["unpaired_terminal_count"],
            "malformed_event_count": folded["malformed_event_count"],
            "untrusted_event_count": folded["untrusted_event_count"],
        },
        "diagnostics": folded["diagnostics"],
        "diagnostics_truncated": folded["diagnostic_count"] > len(
            folded["diagnostics"]
        ),
        "empty": not spans,
    }


def _fold_lifecycle_spans(
    trace_events: list[tuple[int, object]],
    *,
    trace_ref: str,
) -> dict[str, Any]:
    open_starts: dict[tuple[str, tuple[str, ...]], tuple[int, object, dict[str, Any]]] = {}
    spans: list[tuple[int, dict[str, Any]]] = []
    diagnostics: list[dict[str, Any]] = []
    observed_allowlisted_event_count = 0
    eligible_event_count = 0
    malformed_event_count = 0
    unpaired_terminal_count = 0
    degraded_span_count = 0
    untrusted_event_count = 0

    def diagnose(code: str, event: object) -> None:
        if len(diagnostics) >= _MAX_DIAGNOSTICS:
            return
        diagnostics.append({
            "kind": "lifecycle_evidence",
            "message": code.replace("_", " "),
            "code": code,
            "event_type": _bounded(getattr(event, "type", "")),
            "event_id": _event_ref(getattr(event, "id", "")),
        })

    for seq, event in trace_events:
        event_type = str(getattr(event, "type", "") or "")
        kind = _START_TYPES.get(event_type)
        terminal = _TERMINAL_TYPES.get(event_type)
        if kind is None and terminal is None:
            continue
        observed_allowlisted_event_count += 1
        origin = str(getattr(event, "origin", "") or "").strip()
        if origin not in {"", "kernel"}:
            untrusted_event_count += 1
            diagnose("untrusted_lifecycle_origin", event)
            continue
        eligible_event_count += 1
        payload = getattr(event, "payload", {}) or {}
        if not isinstance(payload, dict):
            malformed_event_count += 1
            diagnose("malformed_payload", event)
            continue

        if kind is not None:
            identity = _identity(kind, payload)
            if identity is None:
                malformed_event_count += 1
                diagnose("missing_lifecycle_identity", event)
                continue
            key = (kind, identity)
            if key in open_starts:
                malformed_event_count += 1
                diagnose("duplicate_lifecycle_start", event)
                continue
            open_starts[key] = (seq, event, payload)
            continue

        assert terminal is not None
        kind, status = terminal
        identity = _identity(kind, payload)
        if identity is None:
            malformed_event_count += 1
            diagnose("missing_lifecycle_identity", event)
            continue
        key = (kind, identity)
        started = open_starts.get(key)
        if started is None:
            unpaired_terminal_count += 1
            diagnose("terminal_without_start", event)
            continue
        start_seq, start_event, start_payload = started
        if not _pair_is_proven(kind, start_event, start_payload, event, payload):
            unpaired_terminal_count += 1
            diagnose("lifecycle_pair_mismatch", event)
            continue
        del open_starts[key]
        span = _span_from_pair(
            trace_ref=trace_ref,
            kind=kind,
            identity=identity,
            start_event=start_event,
            start_payload=start_payload,
            terminal_event=event,
            terminal_payload=payload,
            status=status,
        )
        if span["degraded"]:
            degraded_span_count += 1
        spans.append((seq, span))

    for _, start_event, _ in open_starts.values():
        diagnose("start_without_terminal", start_event)
    spans.sort(key=lambda item: item[0])
    diagnostic_count = (
        malformed_event_count
        + untrusted_event_count
        + unpaired_terminal_count
        + len(open_starts)
    )
    return {
        "spans": spans,
        "observed_allowlisted_event_count": observed_allowlisted_event_count,
        "eligible_event_count": eligible_event_count,
        "malformed_event_count": malformed_event_count,
        "untrusted_event_count": untrusted_event_count,
        "unpaired_start_count": len(open_starts),
        "unpaired_terminal_count": unpaired_terminal_count,
        "degraded_span_count": degraded_span_count,
        "diagnostics": diagnostics,
        "diagnostic_count": diagnostic_count,
    }


def _identity(kind: str, payload: dict[str, Any]) -> tuple[str, ...] | None:
    if kind == "agent.session.run":
        values = tuple(
            str(payload.get(key) or "").strip()
            for key in ("run_id", "thread_id", "source")
        )
        return values if all(values) else None
    attempt_id = str(payload.get("attempt_id") or "").strip()
    return (attempt_id,) if attempt_id else None


def _pair_is_proven(
    kind: str,
    start_event: object,
    start_payload: dict[str, Any],
    terminal_event: object,
    terminal_payload: dict[str, Any],
) -> bool:
    if kind in {"agent.session.run", "runtime.action.attempt"}:
        if getattr(terminal_event, "causation_id", None) != getattr(
            start_event, "id", None
        ):
            return False
    if kind == "task.attempt":
        for field in (
            "workflow_run_id",
            "operation_id",
            "lease_id",
            "dispatch_id",
        ):
            started = str(start_payload.get(field) or "").strip()
            ended = str(terminal_payload.get(field) or "").strip()
            if started and ended and started != ended:
                return False
    start_task = str(getattr(start_event, "task_id", "") or "")
    terminal_task = str(getattr(terminal_event, "task_id", "") or "")
    return not (start_task and terminal_task and start_task != terminal_task)


def _span_from_pair(
    *,
    trace_ref: str,
    kind: str,
    identity: tuple[str, ...],
    start_event: object,
    start_payload: dict[str, Any],
    terminal_event: object,
    terminal_payload: dict[str, Any],
    status: str,
) -> dict[str, Any]:
    recovered = kind == "task.attempt" and bool(start_payload.get("recovered"))
    start_raw = str(getattr(start_event, "ts", "") or "")
    end_raw = str(getattr(terminal_event, "ts", "") or "")
    start_dt = _timestamp(start_raw)
    end_dt = _timestamp(end_raw)
    degradation_reason = ""
    duration: float | None = None
    started_at: str | None = start_raw if start_dt is not None else None
    ended_at: str | None = end_raw if end_dt is not None else None
    if recovered:
        started_at = None
        degradation_reason = "recovered_start_has_no_original_timestamp"
    elif start_dt is None or end_dt is None:
        degradation_reason = "invalid_lifecycle_timestamp"
    else:
        try:
            elapsed = (end_dt - start_dt).total_seconds()
        except TypeError:
            degradation_reason = "incompatible_lifecycle_timestamps"
        else:
            if elapsed < 0:
                degradation_reason = "terminal_precedes_start"
            else:
                duration = elapsed

    digest_basis = "\0".join((kind, *identity))
    digest = hashlib.sha256(digest_basis.encode("utf-8")).hexdigest()[:32]
    action = _bounded(start_payload.get("action"))
    name = {
        "agent.session.run": "Agent session run",
        "runtime.action.attempt": f"Runtime action: {action}" if action else "Runtime action",
        "task.attempt": "Task attempt",
    }[kind]
    task_id = _bounded(getattr(start_event, "task_id", ""))
    actor = _bounded(getattr(start_event, "actor", ""))
    backend = _bounded(
        start_payload.get("backend") or terminal_payload.get("backend")
    )
    source_ids = [
        _event_ref(getattr(start_event, "id", "")),
        _event_ref(getattr(terminal_event, "id", "")),
    ]
    origins = {
        str(getattr(event, "origin", "") or "").strip()
        for event in (start_event, terminal_event)
    }
    origin_provenance = (
        "kernel" if origins == {"kernel"} else "legacy_unattributed"
    )
    result: dict[str, Any] = {
        "trace_id": trace_ref,
        "span_id": f"zf-span:{kind}:{digest}",
        "parent_span_id": None,
        "name": name,
        "kind": kind,
        "status": status,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_seconds": duration,
        "source": "events.jsonl:lifecycle_pair",
        "truth_class": "paired_lifecycle",
        "degraded": bool(degradation_reason),
        "degradation_reason": degradation_reason or None,
        "source_event_ids": source_ids,
        "provenance": {
            "projection": "zaofu.lifecycle-pairs.v1",
            "identity": "allowlisted_payload_identity",
            "timing": "explicit_start_and_terminal_events",
            "parent": "unobserved",
            "origin": origin_provenance,
        },
    }
    if task_id:
        result["task_id"] = task_id
    if actor:
        result["actor"] = actor
    if backend:
        result["backend"] = backend
    return result


def _timestamp(value: str) -> datetime | None:
    if not value or len(value) > _MAX_TEXT_CHARS:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (OverflowError, TypeError, ValueError):
        return None


def _event_ref(value: object) -> str:
    text = str(value or "")
    if len(text) <= _MAX_TEXT_CHARS:
        return text
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"event-ref:sha256:{digest}"


def _bounded(value: object) -> str:
    return str(value or "").strip()[:_MAX_TEXT_CHARS]


__all__ = ["trace_span_page"]
