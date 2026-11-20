"""Observe work after the exact requeue receipt, including fast continuations."""
from zf.core.events import ZfEvent
from zf.runtime.worker_recovery_effect import worker_recovery_progress_matches


def observe_task_requeue_effect(events: list[ZfEvent], payload: dict) -> ZfEvent | None:
    receipt_id = payload.get('effect_request_event_id')
    for index, event in enumerate(events):
        if (event.id == receipt_id and event.type == 'task.requeued' and event.origin == 'kernel'
                and event.task_id == payload.get('task_id')
                and event.payload.get('task_id') == payload.get('task_id')):
            return next((e for e in events[index + 1:]
                         if worker_recovery_progress_matches(e, payload, require_run=False)), None)
    return None
