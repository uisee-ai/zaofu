"""Run-scoped no-progress windows, rebuilt from immutable recovery evidence."""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import Any

NO_PROGRESS_EVENT_TYPES = frozenset({
    "task.dispatch_context.bound",
    "fanout.child.stale_completion",
    "run.manager.action.blocked",
    "run.manager.action.failed",
    "run.manager.action.verify.failed",
    "run.manager.autoresearch.consumed",
    "run.manager.human_decision.rejected",
    "human.escalate",
    "integration.failed",
    "candidate.quality.failed",
})


def build_no_progress_projection(
    events: list[Any],
    *,
    threshold: int = 3,
    fingerprint_for: Callable[..., str],
) -> dict[str, Any]:
    from zf.runtime.run_scope import event_run_id, run_aliases
    from zf.runtime.stage_replan_generation import completed_generation_source

    aliases = run_aliases(events)
    dismissed: set[str] = set()
    seen: dict[str, Any] = {}
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        source = seen.get(str(payload.get("escalation_event_id") or ""))
        source_run = (event_run_id(source, aliases=aliases) or source.correlation_id) if source else ""
        ack_run = event_run_id(event, aliases=aliases) or event.correlation_id
        if (event.type == "human.escalation.acknowledged"
                and event.origin == "kernel" and payload.get("status") == "dismissed"
                and source is not None and source.type == "human.escalate"
                and source_run and ack_run == source_run):
            dismissed.add(source.id)
        seen[event.id] = event
    counts: Counter[tuple[str, str]] = Counter()
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    prior: dict[str, Any] = {}
    for event in events:
        source = completed_generation_source(event, prior, aliases)
        prior[event.id] = event
        if source is not None:
            key = (event_run_id(source, aliases=aliases), fingerprint_for(
                source.type, source.payload, fallback=source.id,
            ))
            counts.pop(key, None)
            latest.pop(key, None)
        etype = str(getattr(event, "type", "") or "")
        if etype not in NO_PROGRESS_EVENT_TYPES or event.id in dismissed:
            continue
        payload = getattr(event, "payload", None)
        payload = payload if isinstance(payload, dict) else {}
        status = str(payload.get("status") or payload.get("next_route") or "")
        if etype == "run.manager.autoresearch.consumed" and status not in {"failed", "human_escalate"}:
            continue
        fp = fingerprint_for(
            etype,
            payload,
            fallback=str(getattr(event, "id", "") or ""),
        )
        key = (event_run_id(event, aliases=aliases), fp)
        counts[key] += 1
        latest[key] = {
            "fingerprint": fp,
            "count": counts[key],
            "event_id": str(getattr(event, "id", "") or ""),
            "event_type": etype,
            "checkpoint_id": str(payload.get("checkpoint_id") or ""),
            "safe_resume_action": str(payload.get("safe_resume_action") or ""),
            "action_policy": str(payload.get("action_policy") or ""),
            "owner_route": str(payload.get("owner_route") or ""),
            "failure_class": str(payload.get("failure_class") or ""),
            "verify_condition": str(payload.get("verify_condition") or ""),
            "reason": str(payload.get("reason") or payload.get("decision") or status),
        }
    tripped = [
        row for row in latest.values()
        if int(row.get("count") or 0) >= threshold
    ]
    tripped.sort(key=lambda item: (-int(item.get("count") or 0), str(item.get("event_id") or "")))
    return {
        "schema_version": "run-manager.no-progress.v1",
        "is_derived_projection": True,
        "threshold": threshold,
        "status": "tripped" if tripped else "clear",
        "summary": {
            "fingerprints": len(counts),
            "tripped": len(tripped),
        },
        "items": tripped,
    }
