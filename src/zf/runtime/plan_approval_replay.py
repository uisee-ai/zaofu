"""Read-only retirement of duplicate requests for an owner-approved plan."""
from collections.abc import Iterable, Mapping
import re
from typing import Any

from zf.core.events.model import ZfEvent


def _key(payload: Mapping[str, Any], correlation_id: str = '') -> tuple[str, ...] | None:
    fingerprint = str(payload.get('plan_fingerprint') or '')
    if not re.fullmatch(r'plan-approval\.v2:[0-9a-f]{64}', fingerprint):
        return None
    scopes = {str(payload.get(name) or '') for name in ('workflow_run_id', 'run_id', 'trace_id')}
    scopes.discard('')
    if not scopes and correlation_id:
        scopes.add(correlation_id)
    stage, pdd = str(payload.get('stage_id') or ''), str(payload.get('pdd_id') or '')
    if len(scopes) != 1 or not stage or not pdd:
        return None
    return (scopes.pop(), stage, pdd, fingerprint, *(
        str(payload.get(name) or '') for name in
        ('plan_artifact_package_digest', 'task_map_generation', 'plan_artifact_digest')
    ))


def _state(events: Iterable[ZfEvent]) -> tuple[dict[str, ZfEvent], dict[str, ZfEvent]]:
    requests: dict[str, ZfEvent] = {}
    pending: dict[str, ZfEvent] = {}
    approved: set[str] = set()
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        plan_id = str(payload.get('plan_id') or '')
        if not plan_id:
            continue
        if event.type == 'plan.approval.requested':
            requests[plan_id] = pending[plan_id] = event
            approved.discard(plan_id)
        elif event.type in {'plan.approved', 'plan.rejected'}:
            if event.type == 'plan.approved' and payload.get('semantic_control'):
                continue
            pending.pop(plan_id, None)
            approved.discard(plan_id)
            if (event.type == 'plan.approved' and event.actor == 'operator'
                    and not payload.get('auto') and plan_id in requests):
                approved.add(plan_id)
    return pending, {pid: requests[pid] for pid in approved}


def approved_equivalent_plan_id(events: Iterable[ZfEvent], payload: Mapping[str, Any]) -> str:
    key = _key(payload)
    if key is None:
        return ''
    _, approved = _state(events)
    return next((pid for pid, event in approved.items()
                 if pid != payload.get('plan_id') and _key(event.payload, event.correlation_id or '') == key), '')


def pending_plan_requests(events: Iterable[ZfEvent]) -> list[ZfEvent]:
    pending, approved = _state(events)
    keys = {_key(event.payload, event.correlation_id or '') for event in approved.values()}
    keys.discard(None)
    return [event for event in pending.values()
            if _key(event.payload, event.correlation_id or '') not in keys]
