"""ZF-OBS-SPAN-001 — span-like trace projection (doc 39 §4.4).

Project events.jsonl into span records compatible with OpenTelemetry
shape, so Web UI / Agent View / external trace viewers can render
campaign / task / role / dispatch / fanout / candidate causality.

This is a **projection only**, not an OTel collector. Doc 39 §7
explicitly non-goals adding the OTel collector dependency at P0.
A future sprint can export these spans to OTel if needed.

Output paths::

    .zf/traces/spans.jsonl   (append-only span log, atomic_write per refresh)
    .zf/runs/<run_id>/trace.json   (per-campaign rollup)
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

from zf.core.state.atomic_io import atomic_write_text


@dataclass(frozen=True)
class Span:
    """One span record. Field names match OpenTelemetry conventions
    where reasonable so downstream exporters can adopt them with
    minimal renaming."""

    span_id: str
    trace_id: str
    parent_span_id: str = ""
    run_id: str = ""
    task_id: str = ""
    role: str = ""
    instance_id: str = ""
    dispatch_id: str = ""
    fanout_id: str = ""
    candidate_ref: str = ""
    event_type: str = ""
    status: str = "ok"  # ok | error | blocked
    started_at: str = ""
    ended_at: str = ""
    evidence_refs: tuple[str, ...] = ()


# Event type → status mapping for span synthesis.
_FAILURE_EVENTS: frozenset[str] = frozenset({
    "dev.blocked", "review.rejected", "test.failed",
    "judge.failed", "gate.failed", "static_gate.failed",
    "discriminator.failed",
})
_BLOCKED_EVENTS: frozenset[str] = frozenset({
    "task.done.blocked", "review.suspended", "test.suspended",
})


def _status_from_event_type(event_type: str) -> str:
    if event_type in _FAILURE_EVENTS:
        return "error"
    if event_type in _BLOCKED_EVENTS:
        return "blocked"
    return "ok"


def synthesize_span(event) -> Span | None:
    """Build a Span from one event. Returns None for events that
    don't represent a meaningful span boundary."""
    etype = getattr(event, "type", "")
    if not etype:
        return None
    # Only events with a task_id or dispatch_id are span-worthy.
    task_id = getattr(event, "task_id", "") or ""
    payload = getattr(event, "payload", {}) or {}
    dispatch_id = ""
    fanout_id = ""
    candidate_ref = ""
    role = ""
    instance_id = ""
    run_id = ""
    evidence_refs: list[str] = []
    if isinstance(payload, dict):
        dispatch_id = str(payload.get("dispatch_id", "") or "")
        fanout_id = str(payload.get("fanout_id", "") or "")
        candidate_ref = str(
            payload.get("candidate_ref", "")
            or payload.get("candidate_branch", "")
            or ""
        )
        role = str(payload.get("target_role", "") or "")
        instance_id = str(
            payload.get("target_instance", "")
            or payload.get("instance_id", "")
            or ""
        )
        run_id = str(payload.get("run_id", "") or "")
        ev_refs_raw = payload.get("evidence_refs") or []
        if isinstance(ev_refs_raw, list):
            for r in ev_refs_raw:
                if isinstance(r, dict):
                    path = str(r.get("path", "") or "")
                    if path:
                        evidence_refs.append(path)
                elif isinstance(r, str):
                    evidence_refs.append(r)
    actor = getattr(event, "actor", "") or ""
    if not instance_id and "-" in actor:
        instance_id = actor
    if not role and "-" in actor:
        role = actor.split("-", 1)[0]

    if not (task_id or dispatch_id or fanout_id):
        # No correlation hint → no span.
        return None
    return Span(
        span_id=getattr(event, "id", "") or "",
        trace_id=run_id or task_id or "",
        parent_span_id="",  # parent chain reconstruction is downstream
        run_id=run_id,
        task_id=task_id,
        role=role,
        instance_id=instance_id,
        dispatch_id=dispatch_id,
        fanout_id=fanout_id,
        candidate_ref=candidate_ref,
        event_type=etype,
        status=_status_from_event_type(etype),
        started_at=getattr(event, "ts", "") or "",
        ended_at=getattr(event, "ts", "") or "",
        evidence_refs=tuple(evidence_refs),
    )


def project_spans(events: Iterable) -> list[Span]:
    """Project a list of events into spans, filtering events that
    don't form meaningful span boundaries."""
    spans: list[Span] = []
    for ev in events:
        s = synthesize_span(ev)
        if s is not None:
            spans.append(s)
    return spans


def write_spans_jsonl(state_dir: Path, spans: Iterable[Span]) -> Path:
    """Write .zf/traces/spans.jsonl atomically (full rewrite — caller
    decides whether to incrementally rebuild)."""
    target_dir = state_dir / "traces"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "spans.jsonl"
    body = "\n".join(
        json.dumps(asdict(s), sort_keys=True, ensure_ascii=False)
        for s in spans
    )
    if body:
        body += "\n"
    atomic_write_text(target, body)
    return target


def write_run_trace(
    state_dir: Path,
    *,
    run_id: str,
    spans: Iterable[Span],
) -> Path:
    """Write per-run rollup at .zf/runs/<run_id>/trace.json."""
    if not run_id:
        raise ValueError("write_run_trace requires non-empty run_id")
    target_dir = state_dir / "runs" / run_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "trace.json"
    payload = {
        "run_id": run_id,
        "spans": [asdict(s) for s in spans if s.run_id == run_id or not s.run_id],
    }
    atomic_write_text(
        target,
        json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
    )
    return target
