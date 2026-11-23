"""Reconcile Supervisor observations with exact existing recovery ownership."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from zf.core.events.model import ZfEvent


def _sources(action: Mapping[str, Any]) -> set[str]:
    values = action.get('source_event_ids') or []
    return {str(value) for value in values if value} | {
        str(action[key]) for key in ('source_event_id',) if action.get(key)
    }


def independent_attention_actions(
    attention: Sequence[dict[str, Any]],
    existing: Sequence[dict[str, Any]],
    events: Sequence[ZfEvent],
) -> list[dict[str, Any]]:
    """Task identity or a nonempty queue does not establish fault ownership.

    Suppress only observations wholly covered by one selected source owner.
    If that owner already emitted an effect, bind its Case and let ordinary
    recovery admission decide whether it is active, resolved or retryable.
    """
    selected = [_sources(action) for action in existing]
    claims = [event.payload for event in events
              if (event.type.startswith('run.manager.action.')
                  or event.type == 'run.manager.autoresearch.requested')
              and event.payload.get('recovery_case_id')]
    out = []
    for action in attention:
        sources = _sources(action)
        if sources and any(sources <= owned for owned in selected):
            continue
        owners = {str(claim['recovery_case_id']) for claim in claims
                  if sources and sources <= _sources(claim)}
        if len(owners) == 1:
            action = {**action, 'recovery_case_id': owners.pop()}
        out.append(action)
        selected.append(sources)
    return out
