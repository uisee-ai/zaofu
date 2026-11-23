"""A resolved resume checkpoint must not shadow a distinct new diagnosis."""
import json
from unittest.mock import patch

import pytest

from tests.test_run_manager import _config, _state
from zf.runtime.recovery_case import recovery_case_id_from_payload
from zf.runtime.run_manager import (
    _action_with_problem_envelope,
    _pending_workflow_batch_actions,
    _pending_workflow_task_actions,
    build_run_manager_projection,
)


@pytest.mark.parametrize('kind', ['task', 'batch'])
@pytest.mark.parametrize('case_state', ['resolved', 'verifying', 'waiting', 'blocked'])
def test_resolved_resume_does_not_starve_new_attention(tmp_path, kind, case_state):
    state, log, writer = _state(tmp_path)
    writer.emit('run.goal.started', correlation_id='R1', payload={
        'run_id': 'R1', 'objective': 'complete the current candidate'})
    failed = writer.emit('workflow.operation.failed', task_id='T1', payload={
        'workflow_run_id': 'R1', 'operation_id': 'reader-new',
        'reason': 'reader closed without a result'})
    resume = {
        'checkpoints': [{'idempotency_key': 'old-task-checkpoint',
            'task_id': 'T1', 'workflow_run_id': 'R1',
            'safe_resume_action': 'reemit_trigger', 'source_event_id': 'old-source'}],
        'batch_checkpoints': [{'checkpoint_id': 'old-batch-checkpoint',
            'trace_id': 'R1', 'fanout_id': 'old-fanout',
            'safe_resume_action': 'repair_failed_children', 'source_event_id': 'old-source'}],
    }
    resume['batch_checkpoints' if kind == 'task' else 'checkpoints'] = []
    producer = _pending_workflow_task_actions if kind == 'task' else _pending_workflow_batch_actions
    old_action = producer(resume, log.read_all())[0]
    case_id = recovery_case_id_from_payload(_action_with_problem_envelope(old_action))
    case_event = {'resolved': 'run.manager.action.verify.passed',
                  'verifying': 'run.manager.action.effect.pending',
                  'waiting': 'run.manager.action.blocked',
                  'blocked': 'run.manager.action.no_progress_break'}[case_state]
    writer.emit(case_event, payload={'recovery_case_id': case_id,
        'action': old_action['action'], 'blocker_kind': 'human_decision' if case_state == 'waiting' else ''})
    supervisor = state / 'projections/supervisor/snapshot.json'
    supervisor.parent.mkdir(parents=True, exist_ok=True)
    supervisor.write_text(json.dumps({'attention_items': [{
        'attention_id': 'reader-attention', 'fingerprint': 'new-reader-failure',
        'workflow_run_id': 'R1', 'task_id': 'T1', 'status': 'open',
        'action_policy': 'needs_diagnosis', 'suggested_route': 'run_manager_recovery',
        'source_event_ids': [failed.id], 'summary': 'current reader has no result',
    }]}))
    with patch('zf.runtime.run_manager._workflow_resume_projection', return_value=resume), \
            patch('zf.runtime.run_manager._safe_runtime_pane_snapshot', return_value={}):
        projection = build_run_manager_projection(state, events=log.read_all(), config=_config())
    diagnosis = [a for a in projection['pending_actions']
                 if a.get('fingerprint') == 'new-reader-failure']
    # A different fault remains eligible even while the older Case is active.
    assert diagnosis
    assert not any(a['recovery_case_id'] == case_id for a in projection['pending_actions'])
    # The old receipt remains visible; a pending effect is never bypassed.
    assert any(c['recovery_case_id'] == case_id for c in projection['recovery_cases']['cases'])
