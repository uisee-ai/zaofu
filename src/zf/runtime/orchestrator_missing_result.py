"""Bounded continuation after a positively closed OA turn without its result."""
from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.orchestrator_agent_operations import CHECKPOINT_REQUESTED, workflow_operation_service
from zf.runtime.orchestrator_agent_role import orchestrator_instance_id
from zf.runtime.pause_lifecycle import is_dispatch_paused
from zf.runtime.provider_turn_liveness import active_codex_turn_from_events


def reconcile_closed_checkpoint_turn(
    runtime: Any, events: list[ZfEvent], operations: dict[str, dict[str, Any]],
) -> None:
    role = orchestrator_instance_id(runtime)
    if is_dispatch_paused(runtime.state_dir, events=events) or active_codex_turn_from_events(events, role):
        return
    running = [o for o in operations.values() if o.get('operation_type') == 'orchestrator_agent_semantic'
               and o.get('role_instance') == role and o.get('status') == 'running']
    if len(running) != 1:
        return  # Ambiguous ownership is not authority to interrupt a provider.
    operation = running[0]
    for event in events:
        p = event.payload
        if (event.type == 'workflow.call.result.admitted'
                and p.get('operation_id') == operation['operation_id']
                and p.get('request_hash') == operation['request_hash']):
            return  # Admission can precede its operation-state projection.
        if (event.type in {'run.goal.completed', 'run.cancelled'}
                and (p.get('workflow_run_id') or p.get('run_id') or event.correlation_id)
                == operation['workflow_run_id']):
            return
    starts = [i for i, e in enumerate(events) if e.type in {'workflow.operation.started', 'workflow.operation.retry_started'}
              and e.payload.get('operation_id') == operation['operation_id']]
    if not starts:
        return
    turn = None
    closed = None
    for event in events[starts[-1] + 1:]:
        if event.actor != role:
            continue
        p = event.payload
        if event.type == 'codex.hook.user_prompt_submit' and p.get('session_id') and p.get('turn_id'):
            turn = (p['session_id'], p['turn_id'])
            closed = None
        elif turn and p.get('turn_id') == turn[1] and (
            (event.type == 'codex.hook.stop' and p.get('session_id') == turn[0])
            or (event.type == 'provider.turn.closed' and p.get('backend') == 'codex')
        ):
            closed = event
    if closed is None:
        return
    checkpoint = next((e for e in events if e.id == operation.get('dispatch_id')
                       and e.type == CHECKPOINT_REQUESTED), None)
    if checkpoint is None:
        return
    service = workflow_operation_service(runtime)
    identity = {k: operation[k] for k in ('operation_id', 'request_hash', 'workflow_run_id')}
    if int(operation.get('retry_count') or 0) >= 1:
        service.block(**identity, reason='orchestrator closed turn without result; bounded continuation exhausted',
                      causation_id=closed.id, correlation_id=operation['workflow_run_id'])
        return
    service.interrupt(**identity, reason='provider_turn_closed_without_result',
                      causation_id=closed.id, correlation_id=operation['workflow_run_id'])
    runtime.event_writer.append(ZfEvent(type=CHECKPOINT_REQUESTED, actor='zf-cli', origin='kernel',
        payload={**checkpoint.payload, 'original_checkpoint_event_id': checkpoint.id,
                 'missing_result_turn_event_id': closed.id,
                 'checkpoint_dispatch_retry_attempt': 1, 'checkpoint_dispatch_retry_max_attempts': 1},
        causation_id=closed.id, correlation_id=operation['workflow_run_id']))
