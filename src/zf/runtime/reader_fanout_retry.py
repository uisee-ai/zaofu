"""Fresh durable reader calls for explicitly controlled child retries."""
from __future__ import annotations

from typing import Any

from zf.core.config.schema import RoleConfig
from zf.core.events.model import ZfEvent
from zf.runtime.fanout import FanoutChild, FanoutContext
from zf.runtime.writer_fanout_retry import _WRITER_RETRY_CALL_IDENTITY_KEYS


_READER_RETRY_CALL_IDENTITY_KEYS = _WRITER_RETRY_CALL_IDENTITY_KEYS - {
    # A reader consumes the writer's evidence and pinned verification target.
    # They are immutable inputs, not outputs owned by this reader attempt.
    'impl_self_check_ref', 'impl_self_check_digest',
    'target_snapshot_ref', 'target_snapshot_digest',
}


def prepare_fresh_reader_retry(
    runtime: Any, *, manifest: dict, child: dict, role: RoleConfig,
    attempt: int, previous_dispatch: ZfEvent,
) -> Any:
    """Use the normal reader admission path with a new immutable identity."""
    payload = dict(child.get('payload') or {})
    old_operation_id = str(
        payload.get('operation_id') or previous_dispatch.payload.get('operation_id') or ''
    )
    # A failed initial pin has an obligation but no call; legacy shadow readers
    # still use their existing path without inventing a preceding operation.
    if not old_operation_id:
        from zf.runtime.reader_rebuild_admission import reader_rebuild_error
        error = reader_rebuild_error(runtime.state_dir, runtime.event_log.read_all(), {
            "fanout_id": manifest["fanout_id"], "fanout_child_id": child["child_id"],
            "workflow_run_id": manifest.get("trace_id"),
            "active_attempt_id": previous_dispatch.payload.get("run_id"),
            "source_event_id": previous_dispatch.id})
        if error:
            return None
    for key in _READER_RETRY_CALL_IDENTITY_KEYS:
        payload.pop(key, None)
        child.pop(key, None)
    payload.update({
        'retry_of_run_id': str(previous_dispatch.payload.get('run_id') or ''),
        'retry_attempt': attempt,
    })
    if old_operation_id:
        payload.update(parent_operation_id=old_operation_id, retry_of_operation_id=old_operation_id)
    context = FanoutContext(
        fanout_id=str(manifest['fanout_id']),
        stage_id=str(manifest['stage_id']), topology='fanout_reader',
        trace_id=str(manifest.get('trace_id') or ''),
        trigger_event_id=str(manifest.get('trigger_event_id') or ''),
        target_ref=str(manifest.get('target_ref') or ''), expected_children=[],
    )
    retry_child = FanoutChild(
        child_id=str(child['child_id']), role_instance=role.instance_id,
        target_ref=str(child.get('target_ref') or context.target_ref), payload=payload,
    )
    prepared = runtime._prepare_reader_fanout_child_operation(
        context=context, child=retry_child, role=role,
        causation_id=previous_dispatch.id,
        aggregate=manifest.get('aggregate_config') or {}, retry_attempt=attempt,
    )
    if prepared.get('skip'):
        raise ValueError('reader retry target could not be pinned')
    child.update(payload)
    child['payload'] = payload
    return prepared.get('prepared_call')


def blocked_reader_aggregate_recoverable(manifest: dict) -> bool:
    """Caller must fence terminal Runs and superseded generations first."""
    aggregate = manifest.get('aggregate') or {}
    children = manifest.get('children') or []
    return (
        aggregate.get('status') == 'cancelled'
        and aggregate.get('reason') == 'workflow_run_terminal:run.goal.blocked'
        and bool(children)
        and all(child.get('status') in {'completed', 'failed'} for child in children)
    )
