"""An availability observation can release only the Provider scope it names."""
from dataclasses import replace
import pytest
from zf.core.events import ZfEvent
from zf.runtime.provider_wait import provider_external_wait


def fault():
    return ZfEvent(type='provider.health.changed', origin='kernel', payload={
        'instance_id': 'run-manager', 'backend': 'mock', 'account_alias': 'scope-a',
        'model': 'test', 'reason': 'quota_exhausted', 'status': 'exhausted'})


@pytest.mark.parametrize('changed', [
    {'instance_id': 'other'}, {'backend': 'foreign'}, {'account_alias': 'scope-b'},
    {'model': 'other'}, {'model': ''},
])
def test_availability_of_other_scope_does_not_clear_wait(changed):
    unavailable = fault()
    healthy = replace(unavailable, id='healthy', payload={
        **unavailable.payload, 'reason': 'recovered', 'status': 'healthy', **changed})
    assert provider_external_wait([unavailable, healthy], backend='mock', instance_id='run-manager')


def test_cooldown_and_untrusted_health_cannot_clear_quota_but_exact_kernel_recovery_can():
    unavailable = fault()
    healthy = replace(unavailable, id='healthy', payload={**unavailable.payload,
        'reason': 'recovered', 'status': 'healthy'})
    events = [unavailable, replace(healthy, origin='external'),
              replace(healthy, type='provider.cooldown.started')]
    assert provider_external_wait(events, backend='mock', instance_id='run-manager')
    assert not provider_external_wait(events + [healthy], backend='mock', instance_id='run-manager')


def test_send_boundary_rechecks_provider_state_without_consuming_obligation(tmp_path, monkeypatch):
    from tests.test_run_manager_diagnosis_consumer import _setup
    from zf.runtime.run_manager import _execute_resident_agent_reprompt
    state, log, writer, config, calls = _setup(tmp_path, monkeypatch)
    unavailable = fault()
    writer.append(replace(unavailable, payload={**unavailable.payload,
        'backend': config.runtime.run_manager.backend}))
    before = len(log.read_all())
    result = _execute_resident_agent_reprompt(state_dir=state, writer=writer,
        config=config, project_root=tmp_path, action={'instance_id': 'run-manager'}, causation_id='tick')
    assert result == 'waiting_external'
    assert len(log.read_all()) == before
    assert not calls


@pytest.mark.parametrize('consumer', ['recommendation', 'approval'])
def test_external_wait_retains_original_recommendation_or_approval(tmp_path, monkeypatch, consumer):
    import zf.runtime.run_manager as rm
    from tests.test_run_manager_diagnosis_consumer import _setup
    state, log, writer, config, _ = _setup(tmp_path, monkeypatch)
    action = {'action': 'resident-agent-reprompt', 'safe_resume_action': 'resident_agent_reprompt',
        'checkpoint_id': 'reprompt-1', 'instance_id': 'run-manager', 'tmux_session': 'zf-rm',
        'briefing_path': str(state / 'briefings/resident.md')}
    if consumer == 'recommendation':
        writer.emit('run.manager.agent.recommendation', actor='run-manager', payload={
            **action, 'recommended_route': 'controlled_action'})
        consume = rm._consume_agent_recommendations
        extra = {'reflect_fn': None}
        terminal = 'run.manager.agent.recommendation.consumed'
    else:
        writer.emit('human.escalate', payload={**action, 'decision_token': 'approved-1'})
        writer.emit('human.escalation.acknowledged', payload={
            'decision_token': 'approved-1', 'decision': 'approve_controlled_action'})
        consume = rm._consume_human_decisions
        extra = {}
        terminal = rm.RUN_MANAGER_HUMAN_DECISION_APPLIED
    status = 'waiting_external'
    monkeypatch.setattr(rm, '_execute_controlled_run_action', lambda **kw: status)
    def tick():
        return consume(state_dir=state, writer=writer, config=config, project_root=tmp_path,
                       events=log.read_all(), causation_id='mock-tick', **extra)
    assert not any(tick())
    assert not any(e.type == terminal for e in log.read_all())
    status = 'applied'
    tick()
    tick()
    assert len([e for e in log.read_all() if e.type == terminal]) == 1
