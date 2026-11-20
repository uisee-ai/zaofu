"""Known common Provider failure must not disable mechanical supervision."""
from tests.test_run_manager_diagnosis_consumer import _setup, _tick
from zf.core.events import ZfEvent
from zf.runtime.provider_health import project_provider_health
from zf.runtime.recovery_case import build_recovery_case_projection
from zf.runtime.run_manager import build_run_manager_projection


def test_common_quota_wait_keeps_tick_alive_without_semantic_retry_storm(tmp_path, monkeypatch):
    state, log, writer, config, calls = _setup(tmp_path, monkeypatch)
    writer.default_origin = 'kernel'
    for instance in ['dev', 'orchestrator', 'run-manager', 'autoresearch']:
        writer.emit('provider.health.changed', payload={
            'instance_id': instance, 'backend': config.runtime.run_manager.backend,
            'reason': 'quota_exhausted', 'status': 'exhausted',
        })
    for _ in range(3):
        _tick(state, log, writer, config, tmp_path)
    events = log.read_all()
    assert len([e for e in events if e.type == 'run.manager.tick.completed']) == 3
    assert len(project_provider_health(state, events=events)['providers']) == 4
    assert not any(c[:2] == ['tmux', 'send-keys'] for c in calls)
    assert not any(e.type == 'run.manager.autoresearch.requested' for e in events)
    projection = build_run_manager_projection(state, events=events, config=config, project_root=tmp_path)
    action = next(a for a in projection['pending_actions'] if a['action'] == 'diagnose-attention')
    assert action['external_wait']['reason'] == 'quota_exhausted'
    assert action['recovery_admission']['status'] == 'blocked'
    # Untrusted recovery assertions do not release a kernel-observed wait.
    writer.append(ZfEvent(type='provider.health.changed', origin='worker', payload={
        'instance_id': 'run-manager', 'backend': config.runtime.run_manager.backend, 'status': 'healthy'}))
    _tick(state, log, writer, config, tmp_path)
    assert not any(c[:2] == ['tmux', 'send-keys'] for c in calls)
    writer.emit('dispatch.paused')
    writer.emit('provider.health.changed', payload={
        'instance_id': 'run-manager', 'backend': config.runtime.run_manager.backend, 'status': 'healthy'})
    _tick(state, log, writer, config, tmp_path)
    assert not any(c[:2] == ['tmux', 'send-keys'] for c in calls)
    writer.emit('dispatch.resumed')
    _tick(state, log, writer, config, tmp_path)
    _tick(state, log, writer, config, tmp_path)
    prompts = [e for e in log.read_all() if e.type == 'run.manager.resident.prompted' and e.payload.get('reprompt')]
    assert len(prompts) == 1
    assert all(c['status'] != 'resolved' for c in build_recovery_case_projection(log.read_all())['cases'])
