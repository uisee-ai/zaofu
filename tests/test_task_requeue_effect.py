"""Requeue receipts never prove that the original task made progress."""
import pytest

from tests.test_run_manager_rework_triage import _config, _state
from zf.core.events import ZfEvent
from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.recovery_case import build_recovery_case_projection
from zf.runtime.run_manager import (
    _execute_operator_controlled_action, _settle_pending_action_effects,
)
from zf.runtime.run_manager_recovery_actions import recovery_action_contract


def requeue(tmp_path, *, workflow_run_id='run-1'):
    state, log, writer = _state(tmp_path)
    writer = EventWriter(log, default_origin='kernel')
    TaskStore(state / 'kanban.json').add(Task(id='T1', title='interrupted', status='in_progress'))
    action = dict(action='task-requeue', checkpoint_id='requeue-1',
                  recovery_case_id='case-1', task_id='T1', workflow_run_id=workflow_run_id)
    action.update(recovery_action_contract(action))
    assert _execute_operator_controlled_action(state_dir=state, writer=writer, config=_config(),
        project_root=tmp_path, action=action, causation_id='fault') == 'applied'
    return state, log, writer


def test_actual_requeue_receipt_waits_for_bound_work_and_survives_replay(tmp_path):
    state, log, writer = requeue(tmp_path)
    assert any(e.type == 'task.requeued' for e in log.read_all())
    assert build_recovery_case_projection(log.read_all())['cases'][0]['status'] == 'verifying'
    assert _settle_pending_action_effects(log.read_all(), writer) == 0
    writer.emit('task.done', task_id='T1', correlation_id='run-1',
                payload={'task_id': 'T1', 'workflow_run_id': 'run-1'})
    reloaded = EventLog(state / 'events.jsonl')
    assert _settle_pending_action_effects(reloaded.read_all(), EventWriter(reloaded)) == 1
    assert build_recovery_case_projection(reloaded.read_all())['cases'][0]['status'] == 'resolved'
    assert _settle_pending_action_effects(reloaded.read_all(), EventWriter(reloaded)) == 0


@pytest.mark.parametrize('changed', [
    {'task_id': 'T2'}, {'workflow_run_id': 'run-2'},
    {'semantic_verdict': 'failed'}, {'operation_id': ''},
])
def test_unrelated_or_unsuccessful_result_cannot_settle_requeue(tmp_path, changed):
    _, log, writer = requeue(tmp_path)
    body = dict(task_id='T1', workflow_run_id='run-1', operation_id='new-op', semantic_verdict='passed')
    writer.emit('workflow.call.result.admitted', task_id='T1', correlation_id='run-1',
                payload={**body, **changed})
    assert _settle_pending_action_effects(log.read_all(), writer) == 0
    writer.emit('workflow.call.result.admitted', task_id='T1', correlation_id='run-1', payload=body)
    assert _settle_pending_action_effects(log.read_all(), writer) == 1


def test_standalone_requeue_only_accepts_standalone_task_progress(tmp_path):
    _, log, writer = requeue(tmp_path, workflow_run_id='')
    writer.emit('task.done', task_id='T1', payload={'task_id': 'T1', 'workflow_run_id': 'foreign'})
    assert _settle_pending_action_effects(log.read_all(), writer) == 0
    writer.emit('task.done', task_id='T1', payload={'task_id': 'T1'})
    assert _settle_pending_action_effects(log.read_all(), writer) == 1


def test_fast_work_between_receipt_and_pending_is_not_lost(tmp_path, monkeypatch):
    import zf.runtime.run_manager as rm
    original = rm._post_verify_action
    def fast_result(writer, action, result, **kwargs):
        writer.emit('task.done', task_id='T1', payload={'task_id': 'T1', 'workflow_run_id': 'run-1'})
        return original(writer, action, result, **kwargs)
    monkeypatch.setattr(rm, '_post_verify_action', fast_result)
    _, log, writer = requeue(tmp_path)
    events = log.read_all()
    assert events.index(next(e for e in events if e.type == 'task.done')) < events.index(
        next(e for e in events if e.type == 'run.manager.action.effect.pending'))
    assert _settle_pending_action_effects(events, writer) == 1


@pytest.mark.parametrize('changed_identity', [False, True])
def test_owner_approved_requeue_keeps_original_case_and_run_until_effect(tmp_path, changed_identity):
    from zf.runtime.run_manager import _consume_human_decisions
    state, log, _ = _state(tmp_path)
    writer = EventWriter(log, default_origin='kernel')
    TaskStore(state / 'kanban.json').add(Task(id='T1', title='interrupted', status='in_progress'))
    writer.emit('human.escalate', payload={
        'decision_token': 'approval-1', 'action': 'task-requeue', 'task_id': 'T1',
        'workflow_run_id': 'run-1', 'recovery_case_id': 'case-1', 'checkpoint_id': 'original-checkpoint',
    })
    writer.emit('human.escalation.acknowledged', payload={
        'decision_token': 'approval-1', 'decision': 'approve_controlled_action',
        **({'task_id': 'T2', 'checkpoint_id': 'other', 'workflow_run_id': 'other',
            'recovery_case_id': 'other'} if changed_identity else {})})
    assert _consume_human_decisions(state_dir=state, writer=writer, config=_config(),
        project_root=tmp_path, events=log.read_all(), causation_id='tick') == (1, 0)
    pending = next(e for e in log.read_all() if e.type == 'run.manager.action.effect.pending')
    assert pending.payload['workflow_run_id'] == 'run-1'
    assert pending.payload['recovery_case_id'] == 'case-1'
    assert pending.payload['checkpoint_id'] == 'original-checkpoint'
    writer.emit('task.done', task_id='T1', payload={'task_id': 'T1', 'workflow_run_id': 'run-1'})
    assert _settle_pending_action_effects(log.read_all(), writer) == 1
    assert any(c['status'] == 'resolved' and c['recovery_case_id'] == 'case-1'
               for c in build_recovery_case_projection(log.read_all())['cases'])
