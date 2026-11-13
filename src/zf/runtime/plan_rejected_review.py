"""Carry exact rejected review facts, without inheriting approval or read waivers."""
from __future__ import annotations

from pathlib import Path

from zf.core.events import EventLog
from zf.runtime.plan_local_review_policy import reviewed_critic_manifest


def rejected_review_source(state_dir: Path, *, trigger_id: str, run: str, rework_of: str) -> dict | None:
    if not all((trigger_id, run, rework_of)):
        return None
    events = EventLog(state_dir / 'events.jsonl').read_all()
    by_id = {event.id: event for event in events}
    trigger = by_id.get(trigger_id)
    if (trigger is None or trigger.origin != 'kernel' or trigger.payload.get('workflow_run_id') != run
            or trigger.payload.get('rework_of') != rework_of):
        return None
    current = by_id.get(rework_of)
    seen = set()
    for _ in range(8):
        if current is None or current.id in seen or current.payload.get('workflow_run_id') != run:
            return None
        seen.add(current.id)
        if current.type == 'fanout.synth.completed':
            break
        if current.origin != 'kernel':
            return None
        current = by_id.get(current.causation_id)
    else:
        return None
    report = current.payload.get('report') or {}
    if (not isinstance(report, dict)
            or any(value not in (None, '', 'needs_rework') for value in
                   (current.payload.get('recommendation'), report.get('recommendation')))):
        return None
    try:
        prior = reviewed_critic_manifest(state_dir, events, run, current, expected_verdict='rejected')
    except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError, StopIteration):
        return None
    return {**prior['_critic_control_ref'], 'source_id': 'plan-rejected-review',
            'artifact_id': 'rejected-plan-review.json', 'kind': 'rejected_plan_review_facts',
            'allowed_paths': ['$']}
