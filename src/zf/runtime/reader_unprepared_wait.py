"""Bind recovery to a Reader obligation that failed before call preparation."""
from zf.core.events import ZfEvent
from zf.runtime.workflow_operation import reduce_workflow_operations


def unprepared_wait_error(events: list[ZfEvent], payload: dict) -> str:
    """An absent operation is valid only with exact Kernel pre-delivery evidence."""
    by_id = {e.id: e for e in events}
    failed = by_id.get(payload.get('source_event_id'))
    if (failed is None or failed.origin != 'kernel' or failed.type != 'fanout.child.failed'
            or failed.payload.get('failure_class') != 'reader_environment_wait_exhausted'):
        return 'unprepared Reader requires a Kernel environment-wait failure'
    p = failed.payload
    if any(not p.get(key) or p[key] != value for key, value in (
        ('fanout_id', payload.get('fanout_id')), ('child_id', payload.get('fanout_child_id')),
        ('workflow_run_id', payload.get('workflow_run_id')), ('run_id', payload.get('active_attempt_id')),
    )):
        return 'unprepared Reader failure binding mismatch'
    started = next((e for e in reversed(events) if e.type == 'fanout.started'
                    and e.payload.get('fanout_id') == p['fanout_id']), None)
    if (started is None or started.origin != 'kernel' or started.payload.get('topology') != 'fanout_reader'
            or started.payload.get('trace_id') != p['workflow_run_id']
            or started.payload.get('stage_id') != p.get('stage_id')):
        return 'unprepared Reader has no bound start obligation'
    rows = [c for c in started.payload.get('expected_children', []) if c.get('child_id') == p['child_id']]
    if (len(rows) != 1 or not p.get('role_instance') or rows[0].get('role_instance') != p['role_instance']
            or (rows[0].get('target_ref') or started.payload.get('target_ref')) != p.get('target_ref')
            or p['run_id'] != f"run-{p['fanout_id']}-{p['child_id']}"):
        return 'unprepared Reader child obligation mismatch'
    wait = by_id.get(p.get('first_wait_event_id'))
    if (wait is None or wait.origin != 'kernel' or wait.type != 'fanout.child.dispatch_deferred'
            or wait.payload.get('reason') != 'reader_environment_busy'
            or any(wait.payload.get(k) != p.get(k) for k in
                   ('fanout_id', 'child_id', 'stage_id', 'role_instance', 'run_id'))
            or events.index(wait) >= events.index(failed)):
        return 'unprepared Reader lacks its original wait evidence'
    child_events = [e for e in events if e.type in {
        'fanout.child.dispatched', 'fanout.child.completed', 'fanout.child.failed'}
        and e.payload.get('fanout_id') == p['fanout_id'] and e.payload.get('child_id') == p['child_id']]
    if (not child_events or child_events[-1].id != failed.id
            or any(e.type != 'fanout.child.failed' for e in child_events)):
        return 'unprepared Reader was delivered or its failure is no longer current'
    historical = [e.payload for e in events if e.type == "workflow.operation.requested"]
    if any(op.get('workflow_run_id') == p['workflow_run_id']
           and op.get('parent_stage_id') == p['stage_id']
           and op.get('role_instance') == p['role_instance']
           and op.get('active_attempt_id') == p['run_id']
           for op in [*historical, *reduce_workflow_operations(events).values()]):
        return 'prepared Reader requires its exact operation identity'
    return ''
