"""Project exact exhausted semantic-submit failures into Reader lifecycle facts."""
from types import SimpleNamespace

from zf.runtime.pause_lifecycle import is_dispatch_paused
from zf.runtime.reader_handoff_runtime import handle_non_admitted_reader_result
from zf.runtime.run_admission import build_run_admission_projection
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import reduce_workflow_operations


def reconcile_protocol_failed_readers(runtime, events) -> bool:
    """An invalid submit has no semantic event, but must not strand its child."""
    failures = {e.id: e for e in events if e.type == 'workflow.operation.failed'
        and e.origin == 'kernel' and e.payload.get('reason') == 'output_repair_exhausted'}
    if not failures or is_dispatch_paused(runtime.state_dir, events=events):
        return False
    by_id = {e.id: e for e in events}
    positions = {e.id: i for i, e in enumerate(events)}
    latest = {}
    dispatches = {}
    for e in events:
        if e.type in {'fanout.child.dispatched', 'fanout.child.completed', 'fanout.child.failed'}:
            latest[(e.payload.get('fanout_id'), e.payload.get('child_id'))] = e
        if e.type == 'fanout.child.dispatched':
            dispatches[(e.payload.get('fanout_id'), e.payload.get('child_id'))] = e
    runs = build_run_admission_projection(events).runs
    for op in reduce_workflow_operations(events).values():
        failed = failures.get(op.get('last_event_id'))
        if not failed or op.get('status') != 'failed' or op.get('operation_type') != 'fanout_reader_child':
            continue
        run = runs.get(op.get('workflow_run_id'))
        if run is None or run.status != 'running':
            continue
        invalid = by_id.get(failed.causation_id)
        # Older CLI submit receipts omitted origin. Their exact causal kernel
        # failure and immutable attempt envelope are both required below.
        if (invalid is None or invalid.type != 'workflow.call.result.invalid'
                or invalid.origin not in {'', 'kernel'} or invalid.actor != 'zf-cli'
                or invalid.payload.get('reason') != 'output_repair_exhausted'
                or positions[invalid.id] >= positions[failed.id]
                or any(invalid.payload.get(k) != op.get(k) or failed.payload.get(k) != op.get(k)
                       for k in ('workflow_run_id', 'operation_id', 'request_hash'))):
            continue
        try:
            request = hydrate_sidecar_ref(runtime.state_dir, op['request_ref']).payload['request']
            identity = hydrate_sidecar_ref(runtime.state_dir, invalid.payload['envelope_ref']).payload['identity']
            if not isinstance(request, dict) or not isinstance(identity, dict):
                continue
            if (any(identity.get(k) != op.get(k) for k in ('workflow_run_id', 'operation_id', 'request_hash'))
                    or identity.get('attempt_id') != op.get('active_attempt_id')
                    or identity.get('producer_role') != op.get('role_instance')):
                continue
            key = (request['fanout_id'], request['child_id'])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        dispatch = dispatches.get(key)
        terminal = latest.get(key)
        if dispatch is None or dispatch.origin != 'kernel':
            continue
        body = dispatch.payload
        call = body.get('payload') or {}
        if (positions[dispatch.id] >= positions[invalid.id]
                or body.get('operation_id') != op['operation_id']
                or body.get('workflow_run_id') != op['workflow_run_id']
                or body.get('role_instance') != op.get('role_instance')
                or call.get('request_hash') != op['request_hash']
                or call.get('attempt_id') != op.get('active_attempt_id')
                or not body.get('run_id')):
            continue
        fanout_id, child_id = key
        manifest = runtime._fanout_manifest(fanout_id)
        if (not manifest or manifest.get('topology') != 'fanout_reader'
                or runtime._fanout_identity_stale_reason(fanout_id)[0]
                or manifest.get('status') in {'completed', 'failed', 'timed_out', 'cancelled'}
                or (manifest.get('aggregate') or {}).get('status') in {'completed', 'failed', 'timed_out', 'cancelled'}):
            continue
        if terminal != dispatch:
            if (terminal is None or terminal.type != 'fanout.child.failed' or terminal.origin != 'kernel'
                    or terminal.causation_id != failed.id
                    or terminal.payload.get('protocol_invalid_event_id') != invalid.id
                    or terminal.payload.get('run_id') != body['run_id']
                    or any(terminal.payload.get(k) != op.get(k) for k in (
                        'workflow_run_id', 'operation_id', 'request_hash', 'role_instance'))
                    or terminal.payload.get('attempt_id') != op.get('active_attempt_id')):
                continue
            child = next((c for c in manifest.get('children', []) if c.get('child_id') == child_id), {})
            if child.get('status') == 'failed':
                continue  # Existing incomplete-aggregate recovery owns this boundary.
            from zf.runtime.fanout import FanoutManifestProjector

            FanoutManifestProjector(runtime.state_dir).write_manifest(fanout_id, events)
            runtime._release_fanout_worker_if_terminal(role_instance=op['role_instance'],
                fanout_id=fanout_id, child_id=child_id, run_id=body['run_id'], task_id=op.get('task_id', ''))
            runtime._evaluate_reader_fanout(fanout_id)
            return True
        return handle_non_admitted_reader_result(runtime, fanout_id=fanout_id, child_id=child_id,
            run_id=body['run_id'], manifest=manifest, event=failed, call_mode='blocking',
            call_outcome=SimpleNamespace(status='invalid', admitted=False,
                issues=invalid.payload.get('issues') or []), base_payload={
                'fanout_id': fanout_id, 'child_id': child_id, 'run_id': body['run_id'],
                'role_instance': op['role_instance'], 'workflow_run_id': op['workflow_run_id'],
                'trace_id': op['workflow_run_id'], 'stage_id': manifest.get('stage_id', ''),
                'task_id': op.get('task_id', ''), 'operation_id': op['operation_id'],
                'request_hash': op['request_hash'], 'attempt_id': op['active_attempt_id'],
                'target_ref': manifest.get('target_ref', ''),
                'protocol_invalid_event_id': invalid.id,
            })
    return False
