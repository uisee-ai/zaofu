"""Read-only retirement of duplicate approvals and superseded Plan packages."""
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


def _package_key(payload: Mapping[str, Any], prefix: str) -> tuple[str, str, str] | None:
    run = str(payload.get('workflow_run_id') or '')
    ref = str(payload.get(prefix + 'ref') or '')
    digest = str(payload.get(prefix + 'digest') or '')
    if not run or not ref or not re.fullmatch(r'[0-9a-f]{64}', digest):
        return None
    return run, ref, digest


def _superseded_packages(events: Iterable[ZfEvent]) -> set[tuple[str, str, str]]:
    retired: set[tuple[str, str, str]] = set()
    admitted: set[tuple[str, ...]] = set()
    for event in events:
        if event.origin != 'kernel':
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        key = _package_key(payload, 'package_')
        slot = str(payload.get('package_slot') or '')
        cause = str(event.causation_id or '')
        if key is None or not slot or not cause:
            continue
        if event.type == 'plan.artifact_package.admitted' and payload.get('status') == 'admitted':
            admitted.add((*key, slot, cause))
            retired.discard(key)
        elif event.type == 'plan.artifact_package.superseded' and payload.get('status') == 'superseded':
            successor = _package_key(payload, 'superseded_by_package_')
            if successor and successor != key and (*successor, slot, cause) in admitted:
                retired.add(key)
    return retired


def _state(events: Iterable[ZfEvent]) -> tuple[dict[str, ZfEvent], dict[str, ZfEvent]]:
    events = list(events)
    retired = _superseded_packages(events)
    # A plan_id identifies the exact ready event. Readmission needs a new
    # trigger; approval cannot revive a package quarantined by that trigger.
    aborted = {
        str(event.payload.get('trigger_event_id') or '')
        for event in events
        if event.type == 'fanout.cancelled' and event.origin == 'kernel'
        and event.payload.get('failure_scope') == 'plan_admission'
        and event.payload.get('reason') != 'terminal_tasks'
        and event.causation_id
        and event.causation_id == event.payload.get('trigger_event_id')
    }
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
    pending = {pid: event for pid, event in pending.items()
               if pid not in aborted
               and _package_key(event.payload, 'plan_artifact_package_') not in retired}
    return pending, {pid: requests[pid] for pid in approved
                     if pid not in aborted
                     and _package_key(requests[pid].payload, 'plan_artifact_package_') not in retired}


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
