"""Typed Candidate execution failures retain a diagnosis owner."""
from zf.core.events import ZfEvent
from zf.runtime.canonical_recovery import classify_recovery_scope


def candidate_infrastructure_failure(event: ZfEvent) -> bool:
    return (event.type in {"test.failed", "verify.failed", "review.rejected"}
        and event.payload.get("failure_kind") == "infra"
        and bool(event.payload.get("fanout_id"))
        and classify_recovery_scope(event) == "candidate")


def pending_candidate_infrastructure_diagnoses(events: list[ZfEvent]) -> list[dict]:
    """A blocked batch projection must not hide its own diagnosis owner."""
    from zf.runtime.run_manager import _pending_semantic_event_actions

    actions = _pending_semantic_event_actions(events, candidate_infra_only=True)
    for action in actions:
        action.update(
            source_event_id=action['source_event_ids'][0],
            suggested_route='run_manager_resident_agent',
            expected_output=['run.manager.agent.recommendation'],
            continuation_expected_events=['run.manager.agent.recommendation'],
        )
    return actions


def candidate_diagnosis_needs_tick(events, *, config, run_id, now_epoch) -> bool:
    """Keep a bounded resident obligation reachable despite a probe allowance hold."""
    from datetime import datetime, timezone
    from zf.runtime.run_manager import (
        build_resident_agent_projection, _pending_resident_agent_actions, _pending_human_decisions,
    )
    from zf.runtime.run_continuation import human_decisions_block_action

    gates = _pending_human_decisions(events)
    if any(action.get('workflow_run_id') == run_id
           and not human_decisions_block_action(gates, action, events=events)
           for action in pending_candidate_infrastructure_diagnoses(events)):
        return True
    failures = {event.id for event in events if candidate_infrastructure_failure(event)
                and (event.payload.get('workflow_run_id') or event.correlation_id) == run_id}
    checkpoints = {event.payload.get('checkpoint_id') for event in events
        if event.type == 'run.manager.action.planned' and event.origin == 'kernel'
        and failures.intersection(event.payload.get('source_event_ids') or [])}
    request_ids = {event.payload.get('request_id') for event in events
        if event.type == 'run.manager.resident.prompted' and event.origin == 'kernel'
        and event.payload.get('request_id')
        and event.payload.get('source_action_checkpoint_id') in checkpoints}
    consumed = {event.payload.get('source_event_id') for event in events
                if event.type == 'run.manager.agent.recommendation.consumed'}
    if any(event.type == 'run.manager.agent.recommendation' and event.actor == 'run-manager'
           and event.payload.get('request_id') in request_ids and event.id not in consumed
           for event in events):
        return True
    resident = build_resident_agent_projection(events, config=config,
        now=datetime.fromtimestamp(now_epoch, tz=timezone.utc))
    requests = [row for row in resident.get('requests', [])
                if row.get('source_action_checkpoint_id') in checkpoints]
    if any(row.get('status') == 'pending' for row in requests):
        return True
    # Existing request deadlines and action receipts bound timeout handling.
    # No retry clock or diagnosis state machine is introduced here.
    return bool(requests and _pending_resident_agent_actions(resident, events))
