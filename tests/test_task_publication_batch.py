"""Publication capacity must drain independent candidates without weakening gates."""
from dataclasses import asdict

import pytest

from tests.test_task_host_actions import case
from tests.test_task_publication import prepare
from zf.core.config.task_publication import parse_task_publication
from zf.core.events import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.sidecar_refs import write_sidecar_json
from zf.runtime.task_contract_snapshot import build_task_contract_snapshot, write_task_contract_snapshot
from zf.runtime.task_host_manifest import digest, git
from zf.runtime.task_publication import reconcile_task_publication


@pytest.fixture
def batch(case):
    service, first = prepare(case)
    policy = service.config.runtime.task_publication
    policy.max_publications_per_run = 8
    # Setting the requested capacity also reproduces the old code ignoring it.
    policy.max_actions_per_tick = 8
    roots = [first]
    for number in range(2, 9):
        task_id, operation_id = f'T{number}', f'op-{number}'
        root = service.state_dir / f'workdirs/task{number}/project'
        branch = f'worker-{number}'
        git(service.project_root, 'worktree', 'add', '-b', branch, str(root), 'main')
        task = Task(id=task_id, status='in_progress', contract=TaskContract(
            acceptance='probe returns success', verification='python3 probe.py',
            evidence_contract={'task_map_generation': 'map-1'}))
        TaskStore(service.state_dir / 'kanban.json').add(task)
        snapshot = build_task_contract_snapshot(task, workflow_run_id='run-1',
            task_map_generation_id='map-1', base_commit=git(root, 'rev-parse', 'HEAD'),
            task_ref=f'task/{task_id}')
        descriptor = write_task_contract_snapshot(service.state_dir, snapshot)
        identity = {'operation_id': operation_id, 'task_id': task_id,
            'workflow_run_id': 'run-1', 'task_map_generation': 'map-1',
            'task_pipeline_stage': 'impl', 'operation_generation': 1,
            'active_attempt_id': f'attempt-{number}', 'lease_id': f'lease-{number}',
            'dispatch_id': f'dispatch-{number}', 'role_instance': f'dev-{number}'}
        request = {'result_identity': {'source_branch': branch,
            'contract_snapshot_ref': descriptor['ref'],
            'contract_snapshot_digest': descriptor['sha256']}, 'workdir_write_scopes': ['probe.py']}
        ref = write_sidecar_json(service.state_dir, f'artifacts/request-{number}.json',
            {'request': request}, kind='test', schema_version='test.v1', created_by='test')
        service.writer.append(ZfEvent(type='workflow.operation.requested',
            payload={**identity, 'request_ref': ref}))
        service.writer.append(ZfEvent(type='task.pipeline.stage.dispatched', origin='kernel',
            payload={**identity, 'workdir': str(root), 'source_branch': branch}))
        result = {**identity, 'changed_files': ['probe.py'], 'verdict': 'blocked',
            'failure_class': 'host_publication_required', 'target_commit': git(root, 'rev-parse', 'HEAD')}
        ref = write_sidecar_json(service.state_dir, f'artifacts/blocked-{number}.json', result,
            kind='call_control_result', schema_version='implementation-result.v1',
            created_by='test', source_event_id=f'blocked-{number}')
        service.writer.append(ZfEvent(id=f'blocked-{number}', type='dev.blocked', task_id=task_id,
            payload={**identity, 'control_result_ref': ref}))
        service.writer.append(ZfEvent(type='workflow.call.result.admitted',
            payload={**identity, 'semantic_verdict': 'blocked', 'control_result_ref': ref}))
        service.writer.append(ZfEvent(type='workflow.operation.settled', payload=identity))
        (root / 'probe.py').write_text(f"print('task {number}')\n")
        roots.append(root)
    service.writer.append(ZfEvent(type='task.pipeline.generation.admitted', payload={
        'schema_version': 'task-pipeline-generation.v1', 'workflow_run_id': 'run-1',
        'task_map_generation': 'map-1', 'task_ids': [f'T{i}' for i in range(1, 9)]}))
    return service, roots


def commits(service):
    return [e for e in service.writer.event_log.read_all()
        if e.type == 'runtime.action.attempt.completed' and e.payload.get('kind') == 'commit']


def test_eight_candidates_publish_then_retry_once_each(batch):
    service, roots = batch
    assert reconcile_task_publication(service) == 8
    assert len(commits(service)) == 8
    assert all(not git(root, 'status', '--porcelain') for root in roots)
    assert reconcile_task_publication(service) == 8
    assert reconcile_task_publication(service) == 0
    events = service.writer.event_log.read_all()
    retries = [e for e in events if e.type == 'task.rework.requested']
    assert {e.payload['operation_id'] for e in retries} == {f'op-{i}' for i in range(1, 9)}
    assert len(retries) == 8 and len(commits(service)) == 8
    assert not any(e.type in {'task.done', 'dev.build.done', 'run.goal.completed'} for e in events)


def test_batch_rechecks_run_budget_after_each_effect(batch):
    service, roots = batch
    service.config.runtime.task_publication.max_publications_per_run = 3
    assert reconcile_task_publication(service) == 3
    assert len(commits(service)) == 3
    assert all(git(root, 'status', '--porcelain') for root in roots[3:])


def test_pause_during_batch_stops_later_effects(batch, monkeypatch):
    import zf.runtime.task_publication as module
    service, roots = batch
    original = module.apply
    def pause_after_commit(*args, **kwargs):
        result = original(*args, **kwargs)
        service.writer.emit('run.paused', payload={'workflow_run_id': 'run-1'})
        return result
    monkeypatch.setattr(module, 'apply', pause_after_commit)
    assert reconcile_task_publication(service) == 1
    assert len(commits(service)) == 1
    assert all(git(root, 'status', '--porcelain') for root in roots[1:])


def test_waiting_or_ambiguous_candidate_does_not_block_independent_batch(batch, monkeypatch):
    service, roots = batch
    monkeypatch.setattr('zf.runtime.task_publication.active_codex_turn_from_events',
        lambda events, role: {'turn_id': 'live'} if role == 'dev-2' else None)
    service.writer.emit('runtime.action.attempt.started', payload={
        'action': 'task-host-assist', 'kind': 'commit', 'operation_id': 'op-1',
        'manifest_digest': 'unknown-effect'})
    assert reconcile_task_publication(service) == 6
    assert {e.task_id for e in commits(service)} == {f'T{i}' for i in range(3, 9)}
    assert all(git(root, 'status', '--porcelain') for root in roots[:2])
    assert len([e for e in service.writer.event_log.read_all()
        if e.type == 'human.escalate' and e.payload.get('task_publish_operation') == 'op-1']) == 1


def test_default_batch_limit_and_legacy_receipt_work_after_upgrade(batch):
    service, roots = batch
    policy = service.config.runtime.task_publication
    policy.max_actions_per_tick = 1
    legacy_payload = asdict(policy)
    legacy_payload.pop('max_actions_per_tick')
    assert reconcile_task_publication(service) == 1
    assert commits(service)[0].payload['approval_ref'] == policy.approval_ref + '#' + digest(legacy_payload)
    assert all(git(root, 'status', '--porcelain') for root in roots[1:])
    assert reconcile_task_publication(service) == 1
    assert len(commits(service)) == 1
    retries = [e for e in service.writer.event_log.read_all() if e.type == 'task.rework.requested']
    assert len(retries) == 1 and retries[0].payload['operation_id'] == 'op-1'


def test_changing_batch_authorization_cannot_reuse_an_old_receipt(case):
    service, _ = prepare(case)
    assert reconcile_task_publication(service) == 1
    service.config.runtime.task_publication.max_actions_per_tick = 8
    assert reconcile_task_publication(service) == 0
    events = service.writer.event_log.read_all()
    assert any(e.type == 'human.escalate' for e in events)
    assert not any(e.type == 'task.rework.requested' for e in events)


@pytest.mark.parametrize('value', [0, -1, 101, True, '8'])
def test_batch_capacity_must_be_finite_integer(value):
    with pytest.raises(ValueError):
        parse_task_publication({'max_actions_per_tick': value})


def test_batch_config_is_opt_in_and_legacy_authority_stays_identical():
    default = parse_task_publication(None)
    assert default.max_actions_per_tick == 1
    legacy = asdict(default)
    legacy.pop('max_actions_per_tick')
    assert default.authorization_payload() == legacy
    policy = parse_task_publication({'enabled': True, 'approval_ref': 'owner:batch',
        'paths': ['probe.py'], 'max_publications_per_run': 8, 'max_actions_per_tick': 8})
    assert policy.authorization_payload() == asdict(policy)
    assert digest(policy.authorization_payload()) != digest({**asdict(policy), 'max_actions_per_tick': 1})


def test_run_manager_drains_authorized_batch_without_business_verdict(batch):
    from tests.test_run_manager import _config
    from zf.runtime.run_manager import run_manager_tick
    service, _ = batch
    config = _config()
    config.runtime.task_publication = service.config.runtime.task_publication
    run_manager_tick(state_dir=service.state_dir, writer=service.writer, config=config,
        project_root=service.project_root, auto_execute=False)
    assert not commits(service)
    run_manager_tick(state_dir=service.state_dir, writer=service.writer, config=config,
        project_root=service.project_root, auto_execute=True)
    assert len(commits(service)) == 8
