"""Independent diagnostics survive unrelated recovery candidates and replay."""
import json
from unittest.mock import patch

import pytest

from tests.test_run_manager import _config, _state
from zf.runtime.recovery_case import recovery_case_id_from_payload
from zf.runtime.run_manager import (
    _action_with_problem_envelope, _pending_workflow_task_actions,
    build_run_manager_projection,
)


def _attention(state, event):
    path = state / 'projections/supervisor/snapshot.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'attention_items': [{
        'attention_id': 'independent', 'fingerprint': 'repeated:projection',
        'workflow_run_id': 'R1', 'status': 'open',
        'action_policy': 'needs_diagnosis', 'suggested_route': 'run_manager_recovery',
        'source_event_ids': [event.id], 'summary': 'projection cannot be built',
    }]}))


@pytest.mark.parametrize('case_event', [
    'run.manager.action.effect.pending', 'run.manager.action.blocked',
    'run.manager.action.no_progress_break',
])
def test_pending_candidate_does_not_hide_independent_diagnosis(tmp_path, case_event):
    state, log, writer = _state(tmp_path)
    writer.emit('run.goal.started', correlation_id='R1', payload={'run_id': 'R1'})
    failure = writer.emit('kernel.housekeeping.failed', payload={
        'workflow_run_id': 'R1', 'step': 'task_pipeline_dispatch',
        'exc_type': 'ValueError', 'exc_repr': 'policy mismatch'})
    _attention(state, failure)
    resume = {'checkpoints': [{'idempotency_key': 'other-checkpoint',
        'task_id': 'T1', 'workflow_run_id': 'R1', 'safe_resume_action': 'reemit_trigger',
        'source_event_id': 'other-source'}], 'batch_checkpoints': []}
    old = _pending_workflow_task_actions(resume, log.read_all())[0]
    case_id = recovery_case_id_from_payload(_action_with_problem_envelope(old))
    writer.emit(case_event, payload={'recovery_case_id': case_id,
        'action': old['action'], 'source_event_ids': ['other-source']})
    with patch('zf.runtime.run_manager._workflow_resume_projection', return_value=resume), \
            patch('zf.runtime.run_manager._safe_runtime_pane_snapshot', return_value={}):
        projection = build_run_manager_projection(state, events=log.read_all(), config=_config())
    assert any(failure.id in a.get('source_event_ids', []) for a in projection['pending_actions'])
    assert not any(a['recovery_case_id'] == case_id for a in projection['pending_actions'])


def test_semantic_and_attention_for_same_failure_dispatch_only_once(tmp_path):
    state, log, writer = _state(tmp_path)
    failure = writer.emit('kernel.housekeeping.failed', payload={
        'step': 'task_pipeline_dispatch', 'exc_type': 'ValueError', 'exc_repr': 'policy'})
    _attention(state, failure)
    with patch('zf.runtime.run_manager._safe_runtime_pane_snapshot', return_value={}):
        projection = build_run_manager_projection(state, events=log.read_all(), config=_config())
    assert len([a for a in projection['pending_actions']
                if failure.id in a.get('source_event_ids', [])]) == 1


def test_dispatch_failure_uses_bounded_housekeeping_emitter(tmp_path):
    from types import SimpleNamespace
    from zf.runtime.orchestrator import Orchestrator
    from zf.runtime.task_pipeline_dispatch_events import emit_task_pipeline_dispatch_failure
    _state_dir, log, writer = _state(tmp_path)
    runtime = SimpleNamespace(event_writer=writer, _housekeeping_failure_last={},
                              _housekeeping_failure_dedup_seconds=60)
    runtime._emit_housekeeping_failure = lambda step, exc: Orchestrator._emit_housekeeping_failure(runtime, step, exc)
    for _ in range(20):
        emit_task_pipeline_dispatch_failure(runtime, ValueError('same failure'))
    events = [e for e in log.read_all() if e.type == 'kernel.housekeeping.failed']
    assert len(events) == 1
    assert events[0].payload['step'] == 'task_pipeline_dispatch'


def test_replayed_attention_keeps_exact_existing_case_and_independent_sources():
    from zf.core.events.model import ZfEvent
    from zf.runtime.run_manager_attention_frontier import independent_attention_actions
    from zf.runtime.recovery_case import converge_recovery_actions
    claim = ZfEvent(type='run.manager.action.effect.pending', payload={
        'source_event_ids': ['failure-one'], 'recovery_case_id': 'case-one',
        'action': 'diagnose-attention'})
    actions = independent_attention_actions([
        {'source_event_ids': ['failure-one'], 'fingerprint': 'different-observer'},
        {'source_event_ids': ['failure-two'], 'fingerprint': 'independent'},
    ], [], [claim])
    assert actions[0]['recovery_case_id'] == 'case-one'
    ready, _ = converge_recovery_actions([claim], actions)
    assert [a['source_event_ids'] for a in ready] == [['failure-two']]


def test_partial_source_overlap_does_not_hide_an_unowned_failure():
    from zf.runtime.run_manager_attention_frontier import independent_attention_actions
    attention = {'source_event_ids': ['owned', 'independent']}
    assert independent_attention_actions([attention], [{'source_event_ids': ['owned']}], []) == [attention]
