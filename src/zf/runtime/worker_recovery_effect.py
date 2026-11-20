"""Separate a worker restart receipt from progress of its bound work."""
from collections.abc import Callable
from zf.core.events import ZfEvent


def worker_recovery_effect_contract(action: dict) -> dict:
    result = {'effect_expected_events': ['worker.respawn.completed'],
              'effect_failure_events': ['worker.respawn.failed']}
    if action.get('task_id') and action.get('workflow_run_id'):
        progress = ['workflow.call.result.admitted', 'task.done', 'task.done.accepted']
        result.update(effect_expected_events=progress,
            effect_required_sequence=[['worker.respawn.completed'], progress])
    return result


def worker_recovery_progress_matches(event: ZfEvent, payload: dict, *, require_run: bool = True) -> bool:
    """Only kernel-admitted original work can complete the second effect step."""
    body = event.payload
    from zf.runtime.recovery_step import effect_bindings_match
    if not effect_bindings_match(event, payload):
        return False
    expected_run = payload.get('workflow_run_id') or ''
    actual_run = body.get('workflow_run_id', body.get('run_id')) or ''
    if not require_run:
        actual_run = actual_run or event.correlation_id or ''
    if (event.origin != 'kernel' or not payload.get('task_id')
            or require_run and not expected_run
            or (event.task_id or body.get('task_id')) != payload['task_id']
            or body.get('task_id') and body['task_id'] != payload['task_id']
            or actual_run != expected_run
            or event.correlation_id and event.correlation_id != expected_run):
        return False
    if any(payload.get(key) and body.get(key) != payload[key]
           for key in ('task_map_generation', 'contract_revision', 'target_commit')):
        return False
    if event.type == 'workflow.call.result.admitted':
        return bool(body.get('operation_id') and body.get('semantic_verdict') == 'passed')
    return event.type in {'task.done', 'task.done.accepted'}


def observe_worker_recovery_effect(events: list[ZfEvent], payload: dict,
                                  receipt_matches: Callable) -> ZfEvent | None:
    """A fast continuation may finish before its transport receipt is appended."""
    restarted = False
    progress = None
    for event in events:
        if event.type in {'worker.respawn.failed', 'worker.respawn.completed'}:
            if not receipt_matches(event, payload):
                continue
            if event.type == 'worker.respawn.failed':
                return event
            restarted = True
        elif worker_recovery_progress_matches(event, payload):
            progress = event
        if restarted and progress is not None:
            return progress
    return None
