"""Preauthorized publication is a bounded Git effect, never an Impl verdict."""
from dataclasses import asdict

import pytest

from tests.test_task_host_actions import case
from zf.core.config.task_publication import TaskPublicationConfig, parse_task_publication
from zf.core.config.schema import ZfConfig
from zf.core.events import ZfEvent
from zf.runtime.sidecar_refs import write_sidecar_json
from zf.runtime.task_host_manifest import git
from zf.runtime.task_publication import reconcile_task_publication


def prepare(case):
    service, root, identity = case
    service.config = ZfConfig()
    service.config.runtime.task_publication = TaskPublicationConfig(
        enabled=True, approval_ref='owner:publication', paths=['probe.py'], max_publications_per_run=2)
    body = {**identity, 'changed_files': ['probe.py'], 'verdict': 'blocked',
            'target_commit': git(root, 'rev-parse', 'HEAD')}
    ref = write_sidecar_json(service.state_dir, 'artifacts/blocked.json', body,
        kind='call_control_result', schema_version='implementation-result.v1', created_by='test',
        source_event_id='blocked')
    service.writer.append(ZfEvent(id='blocked', type='dev.blocked', task_id='T1',
        payload={**identity, 'control_result_ref': ref}))
    service.writer.append(ZfEvent(type='workflow.call.result.admitted', payload={**identity,
        'semantic_verdict': 'blocked', 'control_result_ref': ref}))
    service.writer.append(ZfEvent(type='workflow.operation.settled', payload=identity))
    return service, root


def test_publication_commits_once_then_requests_existing_retry(case):
    service, root = prepare(case)
    before = git(root, 'rev-parse', 'HEAD')
    assert reconcile_task_publication(service) == 1
    assert git(root, 'rev-parse', 'HEAD') != before
    assert not git(root, 'status', '--porcelain')
    assert reconcile_task_publication(service) == 1
    assert reconcile_task_publication(service) == 0
    events = service.writer.event_log.read_all()
    retries = [e for e in events if e.type == 'task.rework.requested']
    assert len(retries) == 1 and retries[0].payload['operation_id'] == 'op-1'
    assert 'source_commit' in retries[0].payload['reason']
    assert sum(e.type == 'runtime.action.attempt.started' and e.payload.get('kind') == 'commit' for e in events) == 1
    assert not any(e.type in {'dev.build.done', 'task.done', 'run.goal.completed'} for e in events)


@pytest.mark.parametrize('change', ['disabled', 'outside', 'paused', 'active', 'unknown_effect', 'budget'])
def test_publication_denies_unavailable_authority(case, monkeypatch, change):
    service, root = prepare(case)
    before = git(root, 'rev-parse', 'HEAD')
    p = service.config.runtime.task_publication
    if change == 'disabled': p.enabled = False
    if change == 'outside': p.paths = ['other.py']
    if change == 'paused': service.writer.emit('run.paused', payload={'workflow_run_id': 'run-1'})
    if change == 'active': monkeypatch.setattr('zf.runtime.task_publication.active_codex_turn_from_events', lambda *a: True)
    if change == 'unknown_effect': service.writer.emit('runtime.action.attempt.started', payload={
        'action': 'task-host-assist', 'kind': 'commit', 'operation_id': 'op-1', 'manifest_digest': 'unknown'})
    if change == 'budget':
        p.max_publications_per_run = 1
        service.writer.emit('runtime.action.attempt.started', payload={
            'action': 'task-host-assist', 'kind': 'commit', 'workflow_run_id': 'run-1'})
    reconcile_task_publication(service)
    reconcile_task_publication(service)
    assert git(root, 'rev-parse', 'HEAD') == before
    escalations = [e for e in service.writer.event_log.read_all() if e.type == 'human.escalate']
    assert len(escalations) <= 1


def test_config_requires_specific_pre_authorization():
    base = asdict(TaskPublicationConfig(enabled=True, approval_ref='owner:test',
        paths=['app/src/a.ts'], max_publications_per_run=2))
    assert parse_task_publication(base).paths == base['paths']
    for mutation in ({'approval_ref': ''}, {'paths': []}, {'paths': ['../x']},
                     {'paths': ['*']}, {'max_publications_per_run': 0}, {'enabled': 'yes'}, {'unknown': 1}):
        with pytest.raises(ValueError): parse_task_publication({**base, **mutation})


def test_loader_wires_publication_policy():
    from zf.core.config.loader import _build_runtime
    raw = asdict(TaskPublicationConfig(enabled=True, approval_ref='owner:test',
        paths=['probe.py'], max_publications_per_run=2))
    assert asdict(_build_runtime({'task_publication': raw}).task_publication) == raw


def test_run_manager_calls_publication_only_with_auto_execute(case, monkeypatch):
    from zf.runtime.run_manager import run_manager_tick
    from tests.test_run_manager import _config
    service, _ = prepare(case)
    config = _config()
    config.runtime.task_publication = service.config.runtime.task_publication
    calls = []
    monkeypatch.setattr('zf.runtime.task_publication.reconcile_task_publication', lambda svc: calls.append(svc) or 0)
    run_manager_tick(state_dir=service.state_dir, writer=service.writer, config=config,
        project_root=service.project_root, auto_execute=False)
    assert not calls
    run_manager_tick(state_dir=service.state_dir, writer=service.writer, config=config,
        project_root=service.project_root, auto_execute=True)
    assert len(calls) == 1 and calls[0].actor == 'run-manager'


def test_changed_published_candidate_does_not_retry(case):
    service, root = prepare(case)
    assert reconcile_task_publication(service) == 1
    (root / 'probe.py').write_text('changed after publication')
    assert reconcile_task_publication(service) == 0
    assert not any(e.type == 'task.rework.requested' for e in service.writer.event_log.read_all())


def test_binding_rejection_is_observable_without_effect(case, monkeypatch, caplog):
    service, root = prepare(case)
    before = git(root, 'rev-parse', 'HEAD')
    def unavailable(*args):
        raise ValueError('task generation is no longer current')
    monkeypatch.setattr('zf.runtime.task_publication.binding', unavailable)
    assert reconcile_task_publication(service) == 0
    assert git(root, 'rev-parse', 'HEAD') == before
    assert 'op-1' in caplog.text
    assert 'task generation is no longer current' in caplog.text
    assert not any(e.type == 'human.escalate' for e in service.writer.event_log.read_all())


@pytest.mark.parametrize('exclusive', [False, True])
def test_publication_respects_environment_readers_and_mutators(case, exclusive):
    import fcntl
    from zf.runtime.environment_exec import resource_locks
    from zf.runtime.environment_resources import environment_lock_plan
    service, root = prepare(case)
    before = git(root, 'rev-parse', 'HEAD')
    with resource_locks(environment_lock_plan(root), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH):
        assert reconcile_task_publication(service) == (0 if exclusive else 1)
    assert (git(root, 'rev-parse', 'HEAD') == before) == exclusive
    if exclusive:
        assert not any(e.type == 'runtime.action.attempt.started' for e in service.writer.event_log.read_all())
        assert reconcile_task_publication(service) == 1
