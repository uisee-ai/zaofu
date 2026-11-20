"""Count fault dispatches without charging authenticated read continuations."""
from zf.core.events import ZfEvent


def reader_fault_dispatch_count(dispatches: list[ZfEvent], events: list[ZfEvent]) -> int:
    positions = {event.id: index for index, event in enumerate(events)}
    claims = {event.id: event for event in events
              if event.type == 'worker.continuation.claimed' and event.origin == 'kernel'
              and event.payload.get('recovery_action') == 'reader_progress_continuation'}
    previous = {}
    faults = 0
    for dispatch in dispatches:
        p = dispatch.payload
        nested = p.get('payload') or {}
        claim = claims.get(p.get('continuation_id'))
        predecessor = previous.get(p.get('run_id'))
        scope = ('operation_id', 'workflow_run_id', 'fanout_id', 'child_id')
        continuation = bool(
            claim and predecessor and dispatch.origin == 'kernel'
            and claim.id == p.get('recovery_decision_event_id') == dispatch.causation_id
            and claim.actor == p.get('role_instance') == predecessor.payload.get('role_instance')
            and positions.get(predecessor.id, -1) < positions.get(claim.id, -1)
            < positions.get(dispatch.id, -1)
            and claim.payload.get('dispatch_id') == p.get('run_id')
            and all(claim.payload.get(key) and claim.payload[key] == p.get(key)
                    == predecessor.payload.get(key) for key in scope)
            and claim.payload.get('attempt_id') and claim.payload.get('request_hash')
            and claim.payload.get('attempt_id')
            == (nested.get('attempt_id') or p.get('attempt_id'))
            and claim.payload.get('request_hash')
            == (nested.get('request_hash') or p.get('request_hash'))
        )
        faults += not continuation
        if p.get('run_id'):
            previous[p['run_id']] = dispatch
    return faults
