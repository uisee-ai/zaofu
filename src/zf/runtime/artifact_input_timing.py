"""Observed attempt timing, without attributing wall time to model reasoning."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping


def attempt_timing(events: list, operation: Mapping[str, Any], attempt: str) -> dict:
    result = {'queue_ms': None, 'execution_ms': None, 'provider_startup_ms': None,
              'inter_stage_wait_ms': None, 'model_reasoning_ms': None}
    if not operation:
        return result
    relevant, active = [], ''
    for event in events:
        if (not event.type.startswith('workflow.operation.')
                or event.payload.get('operation_id') != operation.get('operation_id')):
            continue
        identity = event.payload.get('active_attempt_id') or event.payload.get('attempt_id')
        if identity:
            active = identity
        if active == attempt:
            relevant.append(event)

    def elapsed(first: str, last: str) -> float | None:
        starts = [event for event in relevant if event.type == first]
        ends = [event for event in relevant if event.type == last]
        if len(starts) != 1 or len(ends) != 1:
            return None
        try:
            value = (datetime.fromisoformat(ends[0].ts.replace('Z', '+00:00'))
                     - datetime.fromisoformat(starts[0].ts.replace('Z', '+00:00'))).total_seconds() * 1000
            return round(value, 3) if value >= 0 else None
        except (ValueError, TypeError):
            return None

    result['queue_ms'] = elapsed('workflow.operation.requested', 'workflow.operation.started')
    result['execution_ms'] = elapsed('workflow.operation.started', 'workflow.operation.settled')
    return result
