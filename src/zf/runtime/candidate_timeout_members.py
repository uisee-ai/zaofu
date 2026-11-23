"""Retain kernel timeout facts without manufacturing a Reader control result."""
from pathlib import Path

from zf.core.events import ZfEvent
from zf.runtime.reader_respawn_continuation import reader_dispatch_call_identity
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import operation_request_hash


def candidate_failed_members(events: list[ZfEvent], aggregate: ZfEvent) -> dict[str, ZfEvent]:
    """Old timeout aggregates listed only pending children; recover the ledger set."""
    expected = aggregate.payload.get('failed_children')
    if not isinstance(expected, list) or not expected or len(set(expected)) != len(expected):
        raise ValueError('candidate aggregate failed members missing')
    history = events[:events.index(aggregate)]
    fanout = aggregate.payload.get('fanout_id')
    current = {e.payload.get('child_id'): e for e in history
               if e.origin == 'kernel' and e.type in {'fanout.child.failed',
                   'fanout.child.completed', 'fanout.child.cancelled', 'fanout.child.dispatched'}
               and e.payload.get('fanout_id') == fanout}
    children = {key: event for key, event in current.items() if event.type == 'fanout.child.failed'}
    if any(e.origin == 'kernel' and e.payload.get('fanout_id') == fanout
           and e.type in {'fanout.started', 'fanout.cancelled', 'fanout.child.dispatched',
               'fanout.child.failed', 'fanout.child.completed', 'fanout.child.cancelled',
               'fanout.aggregate.started', 'fanout.aggregate.completed'}
           for e in events[events.index(aggregate) + 1:]):
        raise ValueError('candidate aggregate member authority advanced')
    if aggregate.payload.get('reason') == 'timeout':
        starts = [e for e in history if e.origin == 'kernel' and e.type == 'fanout.started'
                  and e.payload.get('fanout_id') == fanout]
        start = starts[0].payload if len(starts) == 1 else {}
        roster = start.get('expected_children') or []
        expected_roles = {row.get('child_id'): row.get('role_instance')
                          for row in roster if isinstance(row, dict)}
        if (not roster or len(expected_roles) != len(roster)
                or not all(expected_roles) or not all(expected_roles.values())
                or set(current) != set(expected_roles) or not start.get('stage_id')
                or any(e.type not in {'fanout.child.failed', 'fanout.child.completed'}
                    or e.payload.get('role_instance') != expected_roles[key]
                    or e.payload.get('stage_id') != start['stage_id']
                    for key, e in current.items())):
            raise ValueError('candidate timeout expected member roster incomplete or different')
    if set(children) == set(expected):
        return children
    timeout = next((e for e in history if e.id == aggregate.causation_id), None)
    pending = aggregate.payload.get('pending_children')
    if (aggregate.actor != 'zf-cli' or aggregate.origin != 'kernel'
            or aggregate.payload.get('reason') != 'timeout'
            or not timeout or timeout.origin != 'kernel' or timeout.actor != 'zf-cli'
            or timeout.type != 'fanout.timed_out' or timeout.payload.get('fanout_id') != fanout
            or not pending or set(expected) != set(pending)
            or set(pending) != set(timeout.payload.get('pending_children') or [])
            or not set(expected) <= set(children)):
        raise ValueError('candidate aggregate failed members differ')
    return children


def candidate_unresolved_timeout(state_dir: Path, events: list[ZfEvent],
                                 child: ZfEvent, aggregate: ZfEvent,
                                 operations: dict, *, run_id: str, task_id: str) -> tuple[dict, dict]:
    from zf.runtime.candidate_plan_recovery import candidate_plan_scope

    p = child.payload
    history = events[:events.index(aggregate)]
    dispatches = [e for e in history[:history.index(child)]
                 if e.origin == 'kernel' and e.actor == 'zf-cli'
                 and e.type == 'fanout.child.dispatched'
                 and all(p.get(k) and e.payload.get(k) == p[k] for k in (
                     'fanout_id', 'child_id', 'role_instance', 'task_id', 'run_id', 'stage_id'))]
    if len(dispatches) != 1:
        raise ValueError('candidate timeout has no unique dispatch lineage')
    dispatch = dispatches[0]
    call = reader_dispatch_call_identity(dispatch.payload)
    op = operations.get(call.get('operation_id'), {})
    failed = next((e for e in history if e.id == op.get('last_event_id')), None)
    cause = next((e for e in history if failed and e.id == failed.causation_id), None)
    if (child.actor != 'zf-cli' or p.get('reason') not in {'idle', 'timeout'}
            or p.get('failure_kind') != 'infra' or p.get('control_result_ref')
            or op.get('status') != 'failed' or op.get('operation_type') != 'fanout_reader_child'
            or op.get('workflow_run_id') != run_id or op.get('task_id') != task_id
            or op.get('role_instance') != p['role_instance']
            or call.get('workflow_run_id') != run_id
            or op.get('request_hash') != call.get('request_hash')
            or op.get('active_attempt_id') != call.get('attempt_id')
            or op.get('admitted_control_result_ref') or op.get('call_result_admitted_event_id')
            or not failed or failed.origin != 'kernel' or failed.type != 'workflow.operation.failed'
            or failed.payload.get('reason') not in {'fanout_timeout', 'reader_idle_timeout'}
            or not cause or cause.origin != 'kernel'
            or not ((cause.id == child.id) or (cause.type == 'fanout.timed_out'
                and cause.payload.get('fanout_id') == p['fanout_id']
                and p['child_id'] in (cause.payload.get('pending_children') or [])))):
        raise ValueError('candidate timeout operation binding changed or outcome unavailable')
    if any(p.get(k) and p[k] != call.get(k) for k in (
            'operation_id', 'request_hash', 'attempt_id', 'workflow_run_id')):
        raise ValueError('candidate timeout identity conflicts with dispatch')
    envelope = hydrate_sidecar_ref(state_dir, op['request_ref']).payload
    request = envelope.get('request') or {}
    result_identity = request.get('result_identity') or {}
    if (operation_request_hash(envelope) != op['request_hash']
            or any(envelope.get(k) != op.get(k) for k in (
                'operation_id', 'workflow_run_id', 'task_id', 'role_instance', 'active_attempt_id'))
            or request.get('fanout_id') != p['fanout_id'] or request.get('child_id') != p['child_id']
            or envelope.get('parent_stage_id') != p['stage_id']
            or request.get('stage_id') != p['stage_id'] or result_identity.get('stage_id') != p['stage_id']
            or request.get('role_instance') != p['role_instance']
            or result_identity.get('role_instance') != p['role_instance']):
        raise ValueError('candidate timeout immutable request differs')
    scope = candidate_plan_scope(state_dir, events, result_identity)
    started = next((e for e in reversed(history) if e.type == 'fanout.started'
                    and e.origin == 'kernel' and e.payload.get('fanout_id') == p['fanout_id']), None)
    if (scope['anchor_task_id'] != task_id or not started
            or started.payload.get('trigger_event_id') != scope['trigger_event_id']
            or started.payload.get('workflow_run_id') != run_id):
        raise ValueError('candidate timeout target superseded')
    return ({**call, 'role_instance': p['role_instance'], 'child_id': p['child_id'],
             'task_id': task_id, 'stage_id': p['stage_id'], 'outcome': 'unknown',
             'reason': p['reason'], 'transport_run_id': p['run_id'],
             'dispatch_event_id': dispatch.id, 'child_event_id': child.id,
             'operation_failure_event_id': failed.id, 'request_ref': op['request_ref']}, scope)
