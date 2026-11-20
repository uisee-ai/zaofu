"""A request-bound reply settles diagnosis, never the product verdict."""
from dataclasses import replace

import pytest

from tests.test_run_manager import _config, _state
from zf.core.events import ZfEvent
from zf.runtime.recovery_case import build_recovery_case_projection
from zf.runtime.run_manager import (
    _effect_event_matches_scope, _settle_pending_action_effects,
    _unresolved_action_effects, run_manager_tick,
)


def effect():
    return {'action': 'resident-agent-reprompt', 'request_kind': 'recovery_diagnosis',
        'request_id': 'probe:request-1', 'instance_id': 'run-manager',
        'workflow_run_id': 'run-1', 'task_id': 'T1', 'task_map_generation': 'g1',
        'target_commit': 'a' * 40, 'active_attempt_id': 'attempt-1',
        'recovery_case_id': 'case-1', 'effect_id': 'effect-1',
        'expected_event_types': ['run.manager.agent.recommendation'],
        'effect_timeout_seconds': 1800}


def reply():
    return ZfEvent(type='run.manager.agent.recommendation', actor='run-manager',
        payload={'request_id': 'probe:request-1', 'task_id': 'T1',
                 'recommended_route': 'repair', 'proposal_only': True})


def test_native_reply_without_redundant_run_fields_matches_request():
    assert _effect_event_matches_scope(reply(), effect())


@pytest.mark.parametrize('field,value', [
    ('request_id', 'other'), ('workflow_run_id', 'other'), ('task_id', 'other'),
    ('task_map_generation', 'other'), ('target_commit', 'b' * 40),
    ('active_attempt_id', 'other'), ('recovery_case_id', 'other'),
])
def test_conflicting_reply_identity_is_rejected(field, value):
    r = reply()
    assert not _effect_event_matches_scope(replace(r, payload={**r.payload, field: value}), effect())


def test_other_responder_or_correlation_cannot_settle_request():
    assert not _effect_event_matches_scope(replace(reply(), actor='other'), effect())
    assert not _effect_event_matches_scope(replace(reply(), correlation_id='other-run'), effect())
    assert not _effect_event_matches_scope(replace(reply(), task_id='other-task'), effect())


def test_tick_corrects_false_timeout_once_and_keeps_failure_history(tmp_path, monkeypatch):
    state, log, writer = _state(tmp_path)
    writer.default_origin = 'kernel'
    writer.emit('run.manager.action.effect.pending', payload=effect())
    observed = writer.append(reply())
    failure = writer.emit('run.manager.action.effect.failed', payload={
        **effect(), 'status': 'failed', 'reason': 'recovery effect timed out'})
    # This test covers the tick caller without dispatching the semantic proposal.
    monkeypatch.setattr('zf.runtime.run_manager._consume_agent_recommendations', lambda **kw: (0,) * 7)
    run_manager_tick(state_dir=state, writer=writer, config=_config(),
                     project_root=tmp_path, auto_execute=False, spawn_repairs=False)
    passed = [e for e in log.read_all() if e.type == 'run.manager.action.effect.passed']
    assert len(passed) == 1 and passed[0].payload['observed_event_id'] == observed.id
    assert any(e.id == failure.id for e in log.read_all())
    assert _unresolved_action_effects(log.read_all()) == ([], [])
    assert build_recovery_case_projection(log.read_all())['cases'][0]['status'] != 'resolved'
    assert _settle_pending_action_effects(log.read_all(), writer) == 0


@pytest.mark.parametrize('reason', ['recovery effect timed out', 'recovery failed to make progress'])
def test_no_reply_or_real_failed_effect_is_not_overwritten(tmp_path, reason):
    _, log, writer = _state(tmp_path)
    writer.default_origin = 'kernel'
    writer.emit('run.manager.action.effect.pending', payload=effect())
    if reason != 'recovery effect timed out':
        writer.append(reply())
    writer.emit('run.manager.action.effect.failed', payload={**effect(), 'reason': reason})
    assert _settle_pending_action_effects(log.read_all(), writer) == 0
    assert _unresolved_action_effects(log.read_all()) == ([], ['effect-1'])


@pytest.mark.parametrize('invalid', ['pending_origin', 'failure_origin', 'pending_ref', 'request'])
def test_timeout_correction_requires_the_original_kernel_binding(tmp_path, invalid):
    _, log, writer = _state(tmp_path)
    pending = ZfEvent(type='run.manager.action.effect.pending', origin='kernel', payload=effect())
    failed = ZfEvent(type='run.manager.action.effect.failed', origin='kernel', payload={
        **effect(), 'reason': 'recovery effect timed out', 'effect_pending_event_id': pending.id})
    if invalid == 'pending_origin':
        pending = replace(pending, origin='worker')
    elif invalid == 'failure_origin':
        failed = replace(failed, origin='worker')
    else:
        key = 'effect_pending_event_id' if invalid == 'pending_ref' else 'request_id'
        failed = replace(failed, payload={**failed.payload, key: 'other'})
    assert _settle_pending_action_effects([pending, reply(), failed], writer) == 0
    assert not log.read_all()
