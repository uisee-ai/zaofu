"""Owner-selected future dispatch scope; never authorization to skip Plan review."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from zf.core.task.store import TaskStore


def dispatch_task_ids(value: Any) -> list[str]:
    if (not isinstance(value, list) or not 1 <= len(value) <= 128
            or any(not isinstance(item, str) or not item.strip() or len(item) > 256
                   or any(ch in item for ch in '\r\n\0') for item in value)):
        raise ValueError('dispatch_task_ids requires 1..128 nonempty bounded task ids')
    return list(dict.fromkeys(item.strip() for item in value))


def validate_owner_dispatch_scope(state_dir: Path, ids: list[str], *, request: Mapping,
                                  events: Sequence) -> None:
    from zf.runtime.task_contract_authority import task_execution_binding
    from zf.runtime.workflow_operation import reduce_workflow_operations

    active = {str(op.get('task_id') or '') for op in reduce_workflow_operations(events).values()
              if op.get('status') in {'reserved', 'running', 'suspended'}}
    store = TaskStore(state_dir / 'kanban.json')
    run = str(request.get('workflow_run_id') or request.get('trace_id') or '')
    feature = str(request.get('feature_id') or request.get('pdd_id') or '')
    for task_id in ids:
        current = store.get(task_id)
        if task_id in active or (current is not None and (
                current.status != 'backlog' or current.active_dispatch_id)):
            raise ValueError(f'dispatch scope cannot rebind active or non-backlog task {task_id}')
        if current is None:
            continue  # New ids remain proposals until a reviewed Plan materializes them.
        binding = task_execution_binding(current)
        if ((binding.workflow_run_id and binding.workflow_run_id != run)
                or (current.contract.feature_id and current.contract.feature_id != feature)):
            raise ValueError(f'dispatch scope task {task_id} belongs to another workflow')


def bind_owner_dispatch_scope(payload: dict, *, source: Any, events: Sequence) -> None:
    def field(item, name, default=None):
        return item.get(name, default) if isinstance(item, Mapping) else getattr(item, name, default)

    body = field(source, 'payload', {}) or {}
    if 'dispatch_task_ids' not in body:
        return
    request = next((e for e in events if field(e, 'id') == body.get('request_event_id')
                    and field(e, 'type') == 'plan.approval.requested'), None)
    request_body = field(request, 'payload', {}) or {}
    if (field(source, 'type') != 'plan.rejected' or field(source, 'actor') != 'operator'
            or request is None or request_body.get('plan_id') != body.get('plan_id')
            or request_body.get('workflow_run_id') != body.get('workflow_run_id')):
        raise ValueError('dispatch scope requires a bound operator Plan rejection')
    payload['task_ids'] = dispatch_task_ids(body['dispatch_task_ids'])
    payload['resume_scope'] = 'gap_tasks_only'
    payload['dispatch_scope_source_event_id'] = field(source, 'id')
