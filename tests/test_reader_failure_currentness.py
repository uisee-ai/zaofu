"""A successor Reader owns its failure; old shared-anchor attempts cannot shadow it."""
from dataclasses import replace

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.run_manager import _pending_semantic_event_actions


def request(oid, **overrides):
    return ZfEvent(type='workflow.operation.requested', origin='kernel', payload={
        'operation_id': oid, 'request_hash': f'hash-{oid}',
        'operation_type': 'fanout_reader_child', 'workflow_run_id': 'R1',
        'task_id': 'T1', 'parent_stage_id': 'verify', 'role_instance': 'verify-0',
        **overrides,
    })


def failure(oid):
    return ZfEvent(type='task.attempt.failed', origin='kernel', task_id='T1', payload={
        'workflow_run_id': 'R1', 'task_id': 'T1', 'operation_id': oid,
        'identity_version': 'role-v1', 'recovery_owner': 'workflow', 'retryable': False,
    })


@pytest.mark.parametrize('change', ['successor', 'other_role', 'other_run', 'other_task',
    'other_stage', 'worker_request', 'unknown_parent', 'missing_hash', 'late_failure',
    'failure_run', 'failure_task', 'failure_hash', 'worker_failure', 'conflicting_request',
    'replayed_old_request', 'malformed_request'])
def test_only_proven_same_owner_successor_retires_old_failure(change):
    old, new = request('old'), request('new')
    failed = failure('old')
    field = {'other_role': 'role_instance', 'other_run': 'workflow_run_id',
             'other_task': 'task_id', 'other_stage': 'parent_stage_id'}.get(change)
    if field:
        new = replace(new, payload={**new.payload, field: 'unrelated'})
    elif change == 'worker_request':
        new = replace(new, origin='worker')
    elif change == 'missing_hash':
        new = replace(new, payload={**new.payload, 'request_hash': ''})
    elif change == 'malformed_request':
        new = replace(new, payload={**new.payload, 'role_instance': ['verify-0']})
    elif change == 'failure_run':
        failed = replace(failed, payload={**failed.payload, 'workflow_run_id': 'other'})
    elif change == 'failure_task':
        failed = replace(failed, task_id='other')
    elif change == 'failure_hash':
        failed = replace(failed, payload={**failed.payload, 'request_hash': 'other'})
    elif change == 'worker_failure':
        failed = replace(failed, origin='worker')
    events = [old, failed, new]
    if change == 'unknown_parent':
        events.remove(old)
    elif change == 'late_failure':
        events = [old, new, failed]
    elif change == 'conflicting_request':
        events.append(replace(new, payload={**new.payload, 'request_hash': 'conflicting'}))
    elif change == 'replayed_old_request':
        events.append(old)
    actions = _pending_semantic_event_actions(events)
    assert any(failed.id in a['source_event_ids'] for a in actions) is (
        change not in {'successor', 'late_failure', 'replayed_old_request'})


def test_current_reader_failure_is_not_hidden_by_old_same_anchor_failure():
    old_failure, current_failure = failure('old'), failure('new')
    events = [request('old'), old_failure, request('new'), current_failure]
    actions = _pending_semantic_event_actions(events)
    assert len(actions) == 1
    assert actions[0]['source_event_ids'] == [current_failure.id]


def test_parallel_reader_failures_keep_distinct_diagnoses_on_shared_task():
    first, second = failure('reader-0'), failure('reader-1')
    events = [request('reader-0'), request('reader-1', role_instance='verify-1'), first, second]
    actions = _pending_semantic_event_actions(events)
    assert {tuple(a['source_event_ids']) for a in actions} == {(first.id,), (second.id,)}
