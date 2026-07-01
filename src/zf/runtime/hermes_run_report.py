"""Read-only Hermes refactor run closeout report projection."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent


_IMPORTANT_EVENTS = {
    "candidate.ready",
    "candidate.updated",
    "candidate.conflict",
    "candidate.quality.failed",
    "candidate.mechanical_fix.applied",
    "integration.failed",
    "verify.passed",
    "verify.failed",
    "judge.passed",
    "judge.failed",
    "fanout.cancelled",
    "fanout.child.failed",
    "human.escalate",
    "owner.visible_message.requested",
    "workflow.resume.applied",
    "workflow.resume.rejected",
    "workflow.batch_resume.applied",
    "workflow.batch_resume.rejected",
    "worker.respawn.requested",
    "orchestrator.dispatch.retry_requested",
    "orchestrator.dispatch_failed",
    "orchestrator.replan_requested",
}


def build_hermes_run_report(
    *,
    state_dir: Path,
    title: str = "Hermes Refactor Run Closeout",
) -> str:
    """Render a markdown report from runtime events without mutating state."""
    state_dir = Path(state_dir)
    events = EventLog(state_dir / "events.jsonl").read_all()
    counts = Counter(event.type for event in events)
    important = [event for event in events if _is_important(event)]
    candidate = _latest_candidate(events)
    failures = [event for event in events if _is_failure(event)]
    recoveries = [event for event in events if _is_recovery(event)]
    operator_events = [event for event in events if _is_operator_visible(event)]

    lines = [
        f"# {title}",
        "",
        f"> generated_at: `{datetime.now(timezone.utc).isoformat()}`",
        f"> state_dir: `{state_dir}`",
        "",
        "## Summary",
        "",
        f"- total_events: {len(events)}",
        f"- important_events: {len(important)}",
        f"- failures: {len(failures)}",
        f"- recoveries: {len(recoveries)}",
        f"- operator_events: {len(operator_events)}",
        f"- final_event: `{events[-1].type if events else ''}`",
        "",
        "## Candidate",
        "",
        f"- candidate_ref: `{candidate.get('candidate_ref', '')}`",
        f"- candidate_head_commit: `{candidate.get('candidate_head_commit', '')}`",
        f"- candidate_base_commit: `{candidate.get('candidate_base_commit', '')}`",
        f"- fanout_id: `{candidate.get('fanout_id', '')}`",
        "",
        "## Event Counts",
        "",
        *_bullet_counts(counts),
        "",
        "## Failure Signals",
        "",
        *_event_bullets(failures, limit=30),
        "",
        "## Recovery Signals",
        "",
        *_event_bullets(recoveries, limit=30),
        "",
        "## Operator / Manual Signals",
        "",
        *_event_bullets(operator_events, limit=30),
        "",
        "## Important Timeline",
        "",
        *_event_bullets(important, limit=80),
        "",
    ]
    return "\n".join(lines)


def write_hermes_run_report(
    *,
    state_dir: Path,
    out: Path,
    title: str = "Hermes Refactor Run Closeout",
) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        build_hermes_run_report(state_dir=state_dir, title=title),
        encoding="utf-8",
    )
    return out


def _is_important(event: ZfEvent) -> bool:
    if event.type in _IMPORTANT_EVENTS:
        return True
    payload = event.payload if isinstance(event.payload, dict) else {}
    text = _payload_text(payload)
    return any(
        marker in text
        for marker in (
            "missing_upstream_affinity_fanout",
            "candidate-quality-failed",
            "candidate_quality_failed",
            "pane_dead",
            "operator",
        )
    )


def _is_failure(event: ZfEvent) -> bool:
    if event.type.endswith(".failed") or event.type in {
        "candidate.conflict",
        "candidate.quality.failed",
        "fanout.cancelled",
        "human.escalate",
    }:
        return True
    payload = event.payload if isinstance(event.payload, dict) else {}
    return any(
        marker in _payload_text(payload)
        for marker in (
            "missing_upstream_affinity_fanout",
            "candidate-quality-failed",
            "candidate_quality_failed",
        )
    )


def _is_recovery(event: ZfEvent) -> bool:
    return event.type in {
        "candidate.mechanical_fix.applied",
        "workflow.resume.applied",
        "workflow.batch_resume.applied",
        "worker.respawn.requested",
        "orchestrator.dispatch.retry_requested",
        "orchestrator.replan_requested",
    }


def _is_operator_visible(event: ZfEvent) -> bool:
    payload = event.payload if isinstance(event.payload, dict) else {}
    return (
        event.actor in {"operator", "human"}
        or event.type in {"human.escalate", "owner.visible_message.requested"}
        or "operator" in _payload_text(payload)
    )


def _latest_candidate(events: list[ZfEvent]) -> dict[str, str]:
    for event in reversed(events):
        if event.type not in {"candidate.ready", "candidate.updated"}:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        return {
            "candidate_ref": str(
                payload.get("candidate_ref") or payload.get("branch") or ""
            ),
            "candidate_head_commit": str(
                payload.get("candidate_head_commit") or payload.get("commit") or ""
            ),
            "candidate_base_commit": str(payload.get("candidate_base_commit") or ""),
            "fanout_id": str(payload.get("fanout_id") or ""),
        }
    return {}


def _bullet_counts(counts: Counter[str]) -> list[str]:
    if not counts:
        return ["- none"]
    return [
        f"- `{event_type}`: {count}"
        for event_type, count in sorted(counts.items())
        if event_type in _IMPORTANT_EVENTS or event_type.endswith(".failed")
    ] or ["- none"]


def _event_bullets(events: list[ZfEvent], *, limit: int) -> list[str]:
    if not events:
        return ["- none"]
    lines = []
    for event in events[:limit]:
        payload = event.payload if isinstance(event.payload, dict) else {}
        summary = _event_summary(payload)
        lines.append(
            f"- `{event.ts}` `{event.type}` id=`{event.id}` "
            f"task=`{event.task_id or ''}` {summary}".rstrip()
        )
    if len(events) > limit:
        lines.append(f"- ... {len(events) - limit} more")
    return lines


def _event_summary(payload: dict[str, Any]) -> str:
    keys = (
        "reason",
        "status",
        "candidate_ref",
        "candidate_head_commit",
        "fanout_id",
        "child_id",
        "classification",
        "source",
    )
    parts = [
        f"{key}={payload[key]!r}"
        for key in keys
        if payload.get(key) not in (None, "")
    ]
    if not parts:
        text = _payload_text(payload)
        return text[:180] if text else ""
    return " ".join(parts)


def _payload_text(value: object) -> str:
    if isinstance(value, dict):
        return " ".join(_payload_text(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return " ".join(_payload_text(item) for item in value)
    return str(value or "")
