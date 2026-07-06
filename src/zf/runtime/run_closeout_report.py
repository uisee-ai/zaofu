"""Read-only run closeout report projection."""

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


def build_run_closeout_report(
    *,
    state_dir: Path,
    title: str = "Run Closeout",
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
    open_gaps = [event for event in events if _is_open_gap(event)]
    verification_refs = _collect_verification_refs(events)
    artifact_refs = _collect_artifact_refs(events)
    failure_to_eval_candidates = _failure_to_eval_candidates(events)

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
        f"- open_gaps: {len(open_gaps)}",
        f"- verification_ref_groups: {len(verification_refs)}",
        f"- artifact_refs: {len(artifact_refs)}",
        f"- failure_to_eval_candidates: {len(failure_to_eval_candidates)}",
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
        "## Open Gaps",
        "",
        *_event_bullets(open_gaps, limit=30),
        "",
        "## Verification Evidence",
        "",
        *_ref_bullets(verification_refs, limit=80),
        "",
        "## Artifact / Source Refs",
        "",
        *_ref_bullets(artifact_refs, limit=120),
        "",
        "## Recovery Signals",
        "",
        *_event_bullets(recoveries, limit=30),
        "",
        "## Operator / Manual Signals",
        "",
        *_event_bullets(operator_events, limit=30),
        "",
        "## Failure-to-Eval Candidates",
        "",
        *_failure_to_eval_bullets(failure_to_eval_candidates, limit=30),
        "",
        "## Important Timeline",
        "",
        *_event_bullets(important, limit=80),
        "",
    ]
    return "\n".join(lines)


def write_run_closeout_report(
    *,
    state_dir: Path,
    out: Path,
    title: str = "Run Closeout",
) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        build_run_closeout_report(state_dir=state_dir, title=title),
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


def _is_open_gap(event: ZfEvent) -> bool:
    if event.type in {
        "flow.goal.blocked",
        "goal.closure.blocked",
        "module.parity.blocked",
    }:
        return True
    payload = event.payload if isinstance(event.payload, dict) else {}
    try:
        return int(payload.get("open_p0_p1_gap_count") or 0) > 0
    except (TypeError, ValueError):
        return False


def _is_operator_visible(event: ZfEvent) -> bool:
    payload = event.payload if isinstance(event.payload, dict) else {}
    return (
        event.actor in {"operator", "human"}
        or event.type in {"human.escalate", "owner.visible_message.requested"}
        or "operator" in _payload_text(payload)
    )


def _failure_to_eval_candidates(events: list[ZfEvent]) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    for event in events:
        if not _is_manual_repair_signal(event):
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        covered_by = _failure_eval_coverage(payload)
        if covered_by:
            continue
        candidates.append({
            "event_id": event.id,
            "event_type": event.type,
            "task_id": event.task_id or str(payload.get("task_id") or ""),
            "reason": str(
                payload.get("reason")
                or payload.get("summary")
                or payload.get("status")
                or "manual repair/recovery has no regression coverage"
            ),
            "proposal": "create regression eval/backlog/skill proposal",
        })
    return candidates


def _is_manual_repair_signal(event: ZfEvent) -> bool:
    payload = event.payload if isinstance(event.payload, dict) else {}
    text = _payload_text(payload).lower()
    if event.actor in {"operator", "human"}:
        return True
    if event.type in {
        "workflow.resume.applied",
        "workflow.batch_resume.applied",
        "candidate.mechanical_fix.applied",
        "orchestrator.dispatch.retry_requested",
        "worker.respawn.requested",
    }:
        return any(marker in text for marker in ("operator", "manual", "human"))
    return False


def _failure_eval_coverage(payload: dict[str, Any]) -> str:
    for key in (
        "eval_case_ref",
        "regression_case_ref",
        "backlog_ref",
        "task_ref",
        "test_refs",
        "registry_key",
        "problem_key",
        "action_kind",
    ):
        value = payload.get(key)
        if value not in (None, "", [], {}):
            return key
    status = str(
        payload.get("coverage_status")
        or payload.get("failure_to_eval_status")
        or ""
    ).strip().lower()
    return status if status in {"covered", "verified"} else ""


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


def _collect_verification_refs(events: list[ZfEvent]) -> list[str]:
    keys = (
        "verify_commands",
        "test_refs",
        "demo_refs",
        "e2e_refs",
        "provider_refs",
        "parity_refs",
        "regression_refs",
        "repro_ref",
    )
    return _collect_payload_refs(events, keys)


def _collect_artifact_refs(events: list[ZfEvent]) -> list[str]:
    keys = (
        "artifact_refs",
        "evidence_refs",
        "source_refs",
        "gap_plan_ref",
        "task_map_ref",
        "new_task_map_ref",
        "replan_history_ref",
    )
    return _collect_payload_refs(events, keys)


def _collect_payload_refs(events: list[ZfEvent], keys: tuple[str, ...]) -> list[str]:
    refs: list[str] = []
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        for key in keys:
            refs.extend(_coerce_ref_values(payload.get(key), prefix=key))
        for nested_key in ("gap_tasks", "tasks"):
            nested = payload.get(nested_key)
            if not isinstance(nested, list):
                continue
            for item in nested:
                if not isinstance(item, dict):
                    continue
                for key in keys:
                    refs.extend(_coerce_ref_values(item.get(key), prefix=key))
    return list(dict.fromkeys(ref for ref in refs if ref))


def _coerce_ref_values(value: object, *, prefix: str) -> list[str]:
    if value in (None, "", [], {}):
        return []
    if isinstance(value, str):
        return [f"{prefix}: {value}"]
    if isinstance(value, (int, float, bool)):
        return [f"{prefix}: {value}"]
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_coerce_ref_values(item, prefix=prefix))
        return out
    if isinstance(value, dict):
        out = []
        for key, item in sorted(value.items()):
            for ref in _coerce_ref_values(item, prefix=f"{prefix}.{key}"):
                out.append(ref)
        return out
    return [f"{prefix}: {value}"]


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


def _ref_bullets(refs: list[str], *, limit: int) -> list[str]:
    if not refs:
        return ["- none"]
    lines = [f"- `{ref}`" for ref in refs[:limit]]
    if len(refs) > limit:
        lines.append(f"- ... {len(refs) - limit} more")
    return lines


def _failure_to_eval_bullets(candidates: list[dict[str, str]], *, limit: int) -> list[str]:
    if not candidates:
        return ["- none"]
    lines = []
    for item in candidates[:limit]:
        lines.append(
            f"- event=`{item['event_type']}` id=`{item['event_id']}` "
            f"task=`{item['task_id']}` reason=`{item['reason']}` "
            f"proposal=`{item['proposal']}`"
        )
    if len(candidates) > limit:
        lines.append(f"- ... {len(candidates) - limit} more")
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
