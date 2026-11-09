"""An approved immutable plan does not need another approval on trigger replay."""
from copy import deepcopy

import pytest

from tests.test_plan_approval_identity import _check, _trigger
from tests.test_writer_fanout_runtime import _approval_orch
from zf.cli.plan_approval import _pending
from zf.core.events import EventWriter, ZfEvent
from zf.runtime.plan_approval_state import has_pending_plan_approval


def test_approved_trigger_replay_does_not_mint_or_execute_another_plan(tmp_path):
    _, log, orch = _approval_orch(tmp_path, enabled=True)
    first, second = _trigger('a' * 64), _trigger('a' * 64)
    assert not _check(orch, first)
    assert not _check(orch, second)
    EventWriter(log).emit('plan.approved', actor='operator', payload={'plan_id': first.id})
    assert _check(orch, first)
    for _ in range(3):
        assert not _check(orch, second)
    events = log.read_all()
    assert len([e for e in events if e.type == 'plan.approval.requested']) == 1
    assert len([e for e in events if e.type == 'plan.approved']) == 1
    assert not any(e.type == 'fanout.started' for e in events)


def _events(fault='', *, late=False):
    body = {'plan_id': 'first', 'workflow_run_id': 'run-1', 'trace_id': 'run-1',
            'stage_id': 'impl', 'pdd_id': 'GOAL-1',
            'plan_fingerprint': 'plan-approval.v2:' + 'a' * 64}
    request = ZfEvent(type='plan.approval.requested', payload=body)
    clone = deepcopy(body); clone['plan_id'] = 'duplicate'
    decision = ZfEvent(type='plan.approved', actor='operator', payload={'plan_id': 'first'})
    if fault in ('workflow_run_id', 'trace_id', 'stage_id', 'pdd_id', 'plan_fingerprint'):
        clone[fault] = 'changed'
    elif fault == 'semantic': decision.payload['semantic_control'] = True
    elif fault == 'auto': decision.payload['auto'] = True
    elif fault == 'worker': decision.actor = 'worker'
    elif fault == 'reject': decision.type = 'plan.rejected'
    elif fault == 'legacy': body['plan_fingerprint'] = clone['plan_fingerprint'] = 'old-task-hash'
    elif fault == 'missing_run':
        for value in (body, clone):
            value.pop('workflow_run_id'); value.pop('trace_id')
    elif fault == 'missing_request': decision.payload['plan_id'] = 'unbound'
    duplicate = ZfEvent(type='plan.approval.requested', payload=clone)
    return [request, decision, duplicate] if late else [request, duplicate, decision]


@pytest.mark.parametrize('late', [False, True])
def test_existing_duplicate_request_does_not_block_idle_or_cli(late):
    events = _events(late=late)
    assert not has_pending_plan_approval(events, workflow_run_id='run-1')
    assert _pending(events, workflow_run_id='run-1') == []
    assert len(events) == 3  # Projection never writes a replacement decision.


@pytest.mark.parametrize('fault', ['workflow_run_id', 'trace_id', 'stage_id', 'pdd_id',
    'plan_fingerprint', 'semantic', 'auto', 'worker', 'reject', 'legacy', 'missing_run', 'missing_request'])
def test_unproven_equivalence_cannot_retire_pending_review(fault):
    events = _events(fault)
    assert has_pending_plan_approval(events)
    assert any(p['plan_id'] == 'duplicate' for p in _pending(events))


def test_later_rejection_revokes_equivalence():
    events = _events()
    events.append(ZfEvent(type='plan.rejected', actor='operator', payload={'plan_id': 'first'}))
    assert has_pending_plan_approval(events)
    assert [p['plan_id'] for p in _pending(events)] == ['duplicate']
