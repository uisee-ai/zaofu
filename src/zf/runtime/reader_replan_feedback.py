"""Validate and carry operator-authored feedback on authorized reader retries."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from zf.core.events.model import ZfEvent


def operator_feedback(payload: Mapping[str, Any]) -> list[str]:
    if 'operator_feedback' not in payload:
        return []
    value = payload['operator_feedback']
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError('operator_feedback must be a list of non-empty strings')
    return [item.strip() for item in value]


def bind_operator_feedback(trigger: ZfEvent, feedback: list[str], *, request_id: str) -> None:
    if not feedback:
        trigger.payload.pop('operator_feedback', None)
        return
    trigger.payload['operator_feedback'] = list(feedback)
    trigger.payload['rework_feedback'] = [
        *trigger.payload.get('rework_feedback', []),
        *({'message': text, 'source': 'operator', 'source_event_id': request_id}
          for text in feedback),
    ]
