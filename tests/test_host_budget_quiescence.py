"""A retained budget alert must not suppress the authorized recovery tick."""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from tests.test_host_verification import policy
from tests.test_tick_services import _FakeOrchestrator, _state
from zf.core.config.schema import ZfConfig
from zf.core.events.model import ZfEvent
from zf.runtime.quiescent import quiescent_now
from zf.runtime.tick_services import TickServiceState, run_standard_tick_services


def checkpoint():
    cfg = ZfConfig()
    cfg.goal.enabled = cfg.goal.quiescent_after_escalate = True
    cfg.runtime.host_verification = policy()
    cfg.runtime.host_verification.max_executions_per_run = 1
    started = ZfEvent(type='runtime.action.attempt.started', origin='kernel', payload={
        'workflow_run_id': 'run-1', 'approval_ref': 'owner:test', 'manifest_digest': 'previous',
        'operation_id': 'op-1'})
    completed = ZfEvent(type='runtime.action.attempt.completed', origin='kernel', payload=dict(started.payload))
    alert = ZfEvent(type='human.escalate', actor='run-manager', origin='kernel', correlation_id='run-1',
        ts=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), payload={
            'reason': 'preauthorized_probe_budget_exhausted', 'host_manifest_digest': 'a' * 64,
            'blocking_scope': 'verification', 'owner_route': 'run_manager'})
    return cfg, [started, completed, alert]


def test_owner_budget_increase_reopens_the_actual_tick_after_grace(tmp_path):
    cfg, events = checkpoint()
    orch = _FakeOrchestrator(_state(tmp_path), cfg)
    for event in events:
        orch.event_log.append(event)
    state = TickServiceState(last_supervisor_inspection_at=100)
    with patch('zf.runtime.tick_services._run_run_manager',
               return_value=MagicMock(changed=True, repairs_dispatched=0)) as manager:
        assert not run_standard_tick_services(orch, state=state, now=105).run_manager
        manager.assert_not_called()
        cfg.runtime.host_verification.max_executions_per_run = 120
        assert run_standard_tick_services(orch, state=state, now=110).run_manager
        manager.assert_called_once()
    types = [e.type for e in orch.event_log.read_all()]
    assert 'run.goal.quiescent.entered' in types and 'run.goal.quiescent.exited' in types
    assert types.count('human.escalate') == 1
    assert not any(t in types for t in ('test.passed', 'task.done', 'run.goal.completed'))


@pytest.mark.parametrize('invalid', [
    'disabled', 'exhausted', 'untrusted', 'wrong_actor', 'missing_run',
    'different_reason', 'other_hold', 'unresolved_effect',
])
def test_budget_wake_does_not_bypass_other_holds(invalid):
    cfg, events = checkpoint()
    cfg.runtime.host_verification.max_executions_per_run = 120
    alert = events[-1]
    if invalid == 'disabled': cfg.runtime.host_verification.enabled = False
    if invalid == 'exhausted': cfg.runtime.host_verification.max_executions_per_run = 1
    if invalid == 'untrusted': alert.origin = 'worker'
    if invalid == 'wrong_actor': alert.actor = 'worker'
    if invalid == 'missing_run': alert.correlation_id = None
    if invalid == 'different_reason': alert.payload['reason'] = 'unknown outcome'
    if invalid == 'other_hold':
        events.insert(0, ZfEvent(type='human.escalate', origin='kernel', payload={
            'reason': 'protected source changed', 'host_manifest_digest': 'a' * 64}))
    if invalid == 'unresolved_effect':
        events[0].payload['manifest_digest'] = 'a' * 64
    assert quiescent_now(events, config=cfg, now_epoch=datetime.now(timezone.utc).timestamp()).quiescent


def test_budget_wake_counts_the_same_run_and_approval_and_stops_at_the_new_cap():
    cfg, events = checkpoint()
    cfg.runtime.host_verification.max_executions_per_run = 2
    for run, approval in [('other-run', 'owner:test'), ('run-1', 'other-owner')]:
        events.append(ZfEvent(type='runtime.action.attempt.started', origin='kernel', payload={
            'workflow_run_id': run, 'approval_ref': approval, 'manifest_digest': 'unrelated'}))
    assert not quiescent_now(events, config=cfg, now_epoch=datetime.now(timezone.utc).timestamp()).quiescent
    events.append(ZfEvent(type='runtime.action.attempt.started', origin='kernel', payload={
        'workflow_run_id': 'run-1', 'approval_ref': 'owner:test', 'manifest_digest': 'new-probe'}))
    assert quiescent_now(events, config=cfg, now_epoch=datetime.now(timezone.utc).timestamp()).quiescent


def test_budget_hold_allows_only_same_run_handoff_without_new_execution(tmp_path, monkeypatch):
    cfg, events = checkpoint()
    orch = _FakeOrchestrator(_state(tmp_path), cfg)
    for event in events:
        orch.event_log.append(event)
    calls = []
    def reconcile(service, **kwargs):
        calls.append(kwargs)
        return 0
    monkeypatch.setattr('zf.runtime.host_verification.reconcile_host_verification', reconcile)
    for _ in range(2):
        result = run_standard_tick_services(orch, state=TickServiceState(), now=105)
        assert not result.run_manager
    assert calls == [{'allow_execution': False, 'workflow_run_id': 'run-1'}] * 2
    assert sum(e.type == 'runtime.action.attempt.started' for e in orch.event_log.read_all()) == 1


def test_unhandled_candidate_failure_wakes_diagnosis_at_exhausted_budget(tmp_path):
    from zf.runtime.candidate_infrastructure_recovery import pending_candidate_infrastructure_diagnoses
    cfg, events = checkpoint()
    failure = ZfEvent(type='test.failed', origin='kernel', task_id='ANCHOR',
        correlation_id='run-1', payload={'workflow_run_id': 'run-1', 'failure_scope': 'candidate',
        'fanout_id': 'candidate-fanout', 'failure_kind': 'infra'})
    events.append(failure)
    status = quiescent_now(events, config=cfg, now_epoch=datetime.now(timezone.utc).timestamp())
    assert not status.quiescent and status.reason == 'candidate_infrastructure_diagnosis'
    orch = _FakeOrchestrator(_state(tmp_path), cfg)
    for event in events: orch.event_log.append(event)
    with patch('zf.runtime.tick_services._run_run_manager',
               return_value=MagicMock(changed=True, repairs_dispatched=0)) as manager:
        assert run_standard_tick_services(orch, state=TickServiceState(), now=105).run_manager
        manager.assert_called_once()
    action = pending_candidate_infrastructure_diagnoses(events)[0]
    events.append(ZfEvent(type='run.manager.resident.prompted', origin='kernel', payload={
        'source_action_checkpoint_id': action['checkpoint_id'], 'request_id': 'diagnosis-1'}))
    assert not pending_candidate_infrastructure_diagnoses(events)
    assert quiescent_now(events, config=cfg, now_epoch=datetime.now(timezone.utc).timestamp()).quiescent


def test_budget_handoff_preserves_other_unresolved_owner_gate(tmp_path, monkeypatch):
    from zf.runtime.quiescent_reader_recovery import reconcile_quiescent_reader_handoff
    cfg, events = checkpoint()
    events.insert(0, ZfEvent(type='human.escalate', origin='kernel',
        payload={'reason': 'owner decision', 'blocking_scope': 'run'}))
    orch = _FakeOrchestrator(_state(tmp_path), cfg)
    for event in events: orch.event_log.append(event)
    effect = MagicMock()
    monkeypatch.setattr('zf.runtime.host_verification.reconcile_host_verification', effect)
    assert not reconcile_quiescent_reader_handoff(state_dir=orch.state_dir, config=cfg,
        writer=orch.event_writer, project_root=tmp_path, events=events, escalation_id=events[-1].id)
    effect.assert_not_called()


def test_bound_diagnosis_response_wakes_once_for_consumption():
    from zf.runtime.candidate_infrastructure_recovery import pending_candidate_infrastructure_diagnoses
    cfg, events = checkpoint()
    events.append(ZfEvent(type='test.failed', origin='kernel', correlation_id='run-1', payload={
        'workflow_run_id': 'run-1', 'failure_scope': 'candidate', 'failure_kind': 'infra', 'fanout_id': 'f1'}))
    action = pending_candidate_infrastructure_diagnoses(events)[0]
    events.append(ZfEvent(type='run.manager.action.planned', origin='kernel', payload=action))
    events.append(ZfEvent(type='run.manager.resident.prompted', origin='kernel', payload={
        'source_action_checkpoint_id': action['checkpoint_id'], 'request_id': 'diag-1'}))
    now = datetime.now(timezone.utc).timestamp()
    assert quiescent_now(events, config=cfg, now_epoch=now).quiescent
    events.append(ZfEvent(type='run.manager.agent.recommendation', actor='run-manager',
        payload={'request_id': 'unrelated'}))
    assert quiescent_now(events, config=cfg, now_epoch=now).quiescent
    reply = ZfEvent(type='run.manager.agent.recommendation', actor='run-manager', payload={'request_id': 'diag-1'})
    events.append(reply)
    assert not quiescent_now(events, config=cfg, now_epoch=now).quiescent
    events.append(ZfEvent(type='run.manager.agent.recommendation.consumed', origin='kernel',
        payload={'source_event_id': reply.id}))
    assert quiescent_now(events, config=cfg, now_epoch=now).quiescent
