"""Match a reader operation to the final occurrence of its exact carrier."""
from __future__ import annotations

from typing import Any, Mapping

from zf.core.events import ZfEvent


def terminal_reader_event(operation: Mapping[str, Any], events: list[ZfEvent]) -> str:
    if operation.get('operation_type') != 'fanout_reader_child':
        return ''
    operation_id = str(operation.get('operation_id') or '')
    workflow_run_id = str(operation.get('workflow_run_id') or '')
    carrier: tuple[str, str, str] | None = None
    terminal = ''
    for event in events:
        p = event.payload
        if event.type == 'fanout.child.dispatched' and p.get('operation_id') == operation_id:
            # A later dispatch invalidates the previous terminal occurrence.
            terminal = ''
            carrier = None
            if p.get('workflow_run_id') == workflow_run_id:
                key = tuple(str(p.get(k) or '') for k in ('fanout_id', 'child_id', 'run_id'))
                if all(key):
                    carrier = key
        elif carrier and event.type in {'fanout.child.failed', 'fanout.child.completed'}:
            if (
                tuple(str(p.get(k) or '') for k in ('fanout_id', 'child_id', 'run_id')) == carrier
                and str(p.get('workflow_run_id') or p.get('trace_id') or '') == workflow_run_id
            ):
                terminal = event.id
    return terminal
