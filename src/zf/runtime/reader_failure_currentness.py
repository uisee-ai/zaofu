"""Retire old Reader diagnostic inputs only on a bound kernel successor request."""
from collections.abc import Sequence

from zf.core.events.model import ZfEvent

_OWNER_FIELDS = ('workflow_run_id', 'task_id', 'parent_stage_id', 'role_instance')
_FAILURES = {'task.attempt.failed', 'workflow.operation.failed', 'workflow.operation.blocked'}


def superseded_reader_failure_ids(events: Sequence[ZfEvent]) -> set[str]:
    requests = {}
    latest = {}
    ambiguous = set()
    for event in events:
        p = event.payload or {}
        if (event.type != 'workflow.operation.requested' or event.origin != 'kernel'
                or p.get('operation_type') != 'fanout_reader_child'):
            continue
        oid = p.get('operation_id')
        owner = tuple(p.get(key) for key in _OWNER_FIELDS)
        if not all(isinstance(v, str) and v for v in (oid, p.get('request_hash'), *owner)):
            continue
        identity = (owner, p['request_hash'])
        if oid in requests:
            if requests[oid] != identity:
                ambiguous.add(oid)
            continue
        requests[oid] = identity
        latest[owner] = oid
    obsolete = set()
    for event in events:
        p = event.payload or {}
        oid = p.get('operation_id')
        if (event.type not in _FAILURES or event.origin != 'kernel'
                or not isinstance(oid, str) or oid not in requests):
            continue
        owner, digest = requests[oid]
        successor = latest.get(owner)
        if oid in ambiguous or successor in ambiguous or successor == oid:
            continue
        # Missing or contradictory failure scope remains visible for diagnosis.
        if (p.get('workflow_run_id') != owner[0]
                or (event.task_id or p.get('task_id')) != owner[1]
                or (p.get('task_id') and p['task_id'] != owner[1])
                or (p.get('request_hash') and p['request_hash'] != digest)
                or (event.correlation_id and event.correlation_id != owner[0])):
            continue
        obsolete.add(event.id)
    return obsolete
