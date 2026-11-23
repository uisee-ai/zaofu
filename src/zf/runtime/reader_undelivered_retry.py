"""Validate first delivery after an exhausted Reader environment wait."""
from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.call_result_runtime import workflow_operation_service
from zf.runtime.pause_lifecycle import is_dispatch_paused
from zf.runtime.run_admission import build_run_admission_projection
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import reduce_workflow_operations


def undelivered_retry_anchor(
    runtime: Any, *, manifest: dict, child: dict, events: list[ZfEvent],
) -> ZfEvent | None:
    """Use the real failed obligation as causation, never invent a dispatch."""
    run_id = str(manifest.get('trace_id') or '')
    run = build_run_admission_projection(events).runs.get(run_id)
    if (manifest.get('topology') != 'fanout_reader' or child.get('status') != 'failed'
            or run is None or run.status != 'running'
            or is_dispatch_paused(runtime.state_dir, events=events)):
        return None
    failed = next((e for e in reversed(events) if e.id == child.get('last_event_id')), None)
    if (failed is None or failed.type != 'fanout.child.failed' or failed.origin != 'kernel'
            or failed.payload.get('failure_class') != 'reader_environment_wait_exhausted'):
        return None
    p = failed.payload
    if any(p.get(key) != value or not value for key, value in (
        ('workflow_run_id', run_id), ('fanout_id', manifest.get('fanout_id')),
        ('stage_id', manifest.get('stage_id')), ('child_id', child.get('child_id')),
        ('role_instance', child.get('role_instance')), ('run_id', child.get('run_id')),
    )):
        return None
    candidates = [op for op in reduce_workflow_operations(events).values()
        if op.get('operation_type') == 'fanout_reader_child'
        and op.get('status') in {'requested', 'suspended', 'failed'}
        and op.get('workflow_run_id') == run_id
        and op.get('parent_stage_id') == p['stage_id']
        and op.get('role_instance') == p['role_instance']
        and op.get('active_attempt_id') == p['run_id']]
    if not candidates:
        from zf.runtime.reader_unprepared_wait import unprepared_wait_error
        error = unprepared_wait_error(events, {"fanout_id": p["fanout_id"],
            "fanout_child_id": p["child_id"], "workflow_run_id": run_id,
            "active_attempt_id": p["run_id"], "source_event_id": failed.id})
        return None if error else failed
    if len(candidates) != 1:
        return None
    op = candidates[0]
    positions = {e.id: i for i, e in enumerate(events)}
    last = next((e for e in events if e.id == op.get('last_event_id')), None)
    if op['status'] == 'failed':
        if (last is None or last.type != 'workflow.operation.failed'
                or last.causation_id != failed.id):
            return None
    elif positions.get(op.get('last_event_id'), len(events)) > positions[failed.id]:
        return None
    if (op.get('started_event_id') or op.get('call_result_admitted_event_id')
            or any(e.type in {'workflow.operation.started', 'workflow.operation.retry_started',
                             'fanout.child.dispatched'}
                   and (e.payload.get('operation_id') == op['operation_id']
                        or (e.payload.get('role_instance') == p['role_instance']
                            and e.payload.get('run_id') == p['run_id'])) for e in events)):
        return None
    try:
        body = hydrate_sidecar_ref(runtime.state_dir, op['request_ref']).payload
    except (ValueError, KeyError, OSError):
        return None
    if (any(body.get(key) != op.get(key) for key in
            ('operation_id', 'workflow_run_id', 'role_instance', 'active_attempt_id'))
            or any(body.get('request', {}).get(key) != p[key]
                   for key in ('fanout_id', 'child_id', 'stage_id'))):
        return None
    if op['status'] != 'failed':
        workflow_operation_service(runtime).fail(
            operation_id=op['operation_id'], request_hash=op['request_hash'],
            workflow_run_id=run_id, task_id=op.get('task_id', ''),
            reason=p['reason'], causation_id=failed.id, correlation_id=run_id,
            details={'failure_class': p['failure_class'], 'fanout_id': p['fanout_id'],
                     'child_id': p['child_id']})
    # The expected child was persisted before its request was prepared, so it
    # may not yet carry the durable identity. This local copy feeds admission.
    child['payload'] = {**(child.get('payload') or {}), 'operation_id': op['operation_id']}
    return failed
