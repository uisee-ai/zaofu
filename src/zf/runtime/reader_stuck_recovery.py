"""Recover silence against a Reader operation, never its shared task's last lane."""
from typing import Any

from zf.core.events import ZfEvent
from zf.runtime.workflow_operation import reduce_workflow_operations


def reader_stuck_dispatch(runtime: Any, role: Any) -> ZfEvent | None:
    if role.role_kind != 'reader':
        return None
    child = runtime._active_fanout_child_for_instance(role.instance_id)
    if not child or not child.get('operation_id'):
        return None
    events = runtime.event_log.read_all()
    dispatch = next((e for e in reversed(events) if e.id == child.get('event_id')), None)
    if (dispatch is None or dispatch.origin != 'kernel' or dispatch.actor != 'zf-cli'
            or dispatch.type not in {'fanout.child.dispatched','fanout.synth.dispatched'}
            or any(dispatch.payload.get(k) != child.get(k)
                   for k in ('operation_id','request_hash','run_id','role_instance'))):
        return None
    return dispatch


def reader_stuck_block_reason(runtime: Any, role: Any) -> str:
    """Recheck under the existing respawn lease before touching the provider."""
    from zf.runtime.run_admission import run_dispatch_block_reason

    dispatch = reader_stuck_dispatch(runtime, role)
    if dispatch is None:
        return 'reader dispatch no longer owned'
    events = runtime.event_log.read_all()
    p = dispatch.payload
    manifest = runtime._fanout_manifest(str(p.get('fanout_id') or ''))
    terminal = {'completed', 'failed', 'timed_out', 'cancelled'}
    if (not manifest or manifest.get('status') in terminal
            or (manifest.get('aggregate') or {}).get('status') in terminal):
        return 'reader fanout is missing or terminal'
    operation = reduce_workflow_operations(events).get(p.get('operation_id'), {})
    if (operation.get('status') != 'running'
            or operation.get('operation_type') not in {'fanout_reader_child','fanout_synth'}
            or operation.get('role_instance') != role.instance_id
            or operation.get('request_hash') != p.get('request_hash')
            or operation.get('active_attempt_id') != (p.get('attempt_id') or p.get('run_id'))
            or operation.get('workflow_run_id') != p.get('workflow_run_id')
            or runtime._fanout_dispatch_has_authoritative_result(events, dispatch)):
        return 'reader operation binding changed or already settled'
    return run_dispatch_block_reason(runtime, event=dispatch,
                                     run_id=str(operation['workflow_run_id']))
