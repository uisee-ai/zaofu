"""New Plan authority ends old Candidate probes before another freeze exists."""
import subprocess
import pytest

from tests.test_candidate_host_binding import candidate
from tests.test_task_host_actions import case, approve
from tests.test_host_verification import policy
from zf.core.events import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.candidate_plan_recovery import candidate_recovery_superseded
from zf.runtime.run_manager import run_manager_tick
from zf.runtime.sidecar_refs import write_sidecar_json
from zf.runtime.task_contract_snapshot import build_task_contract_snapshot, write_task_contract_snapshot
from zf.runtime.task_host_manifest import preview, git


def supersede(service, kind='task_map.ready', *, run='run-1', origin='kernel'):
    return service.writer.append(ZfEvent(type=kind, origin=origin,
        correlation_id=run, payload={'workflow_run_id': run, 'task_map_generation': 'map-2'}))


@pytest.mark.parametrize('kind', ['task_map.ready', 'plan.artifact_package.admitted'])
def test_new_plan_rejects_preview_and_previously_reviewed_apply(candidate, kind):
    service, _, _, _ = candidate
    manifest = preview(service, operation_id='candidate-1', kind='verify', command_id='contract-verification')
    supersede(service, kind)
    before = service.writer.event_log.read_all()
    with pytest.raises(ValueError, match='superseded'):
        preview(service, operation_id='candidate-1', kind='verify', command_id='contract-verification')
    with pytest.raises(ValueError, match='superseded'):
        approve(service, manifest)
    assert service.writer.event_log.read_all() == before


@pytest.mark.parametrize('run,origin', [('other-run', 'kernel'), ('run-1', 'worker')])
def test_unrelated_or_untrusted_plan_does_not_revoke_candidate(candidate, run, origin):
    service, _, _, _ = candidate
    supersede(service, run=run, origin=origin)
    manifest = preview(service, operation_id='candidate-1', kind='verify', command_id='contract-verification')
    assert approve(service, manifest)['result']['exit_code'] == 0


def test_supersession_preserves_existing_receipt(candidate):
    service, _, _, _ = candidate
    manifest = preview(service, operation_id='candidate-1', kind='verify', command_id='contract-verification')
    receipt = approve(service, manifest)
    before = {p: p.read_bytes() for p in (service.state_dir / 'artifacts/task-host-actions').rglob('*') if p.is_file()}
    supersede(service)
    # Re-reading the exact recorded effect is idempotent; it never executes it again.
    assert approve(service, manifest)['status'] == 'already_recorded'
    assert all(p.read_bytes() == body for p, body in before.items())
    assert receipt['result']['product_verdict'] == 'not_evaluated'


def current_task_probe(service):
    root = service.state_dir / 'workdirs/current/project'
    subprocess.run(['git', '-C', str(service.project_root), 'worktree', 'add', '-b',
                    'current-task', str(root), 'worker-test'], check=True, capture_output=True)
    task = Task(id='T2', status='in_progress', contract=TaskContract(
        acceptance='current probe passes', verification='python3 probe.py',
        evidence_contract={'task_map_generation': 'map-2'}))
    TaskStore(service.state_dir / 'kanban.json').add(task)
    descriptor = write_task_contract_snapshot(service.state_dir, build_task_contract_snapshot(
        task, workflow_run_id='run-1', task_map_generation_id='map-2',
        base_commit=git(root, 'rev-parse', 'HEAD'), task_ref='task/T2'))
    identity = {'operation_id': 'current-task', 'task_id': 'T2', 'workflow_run_id': 'run-1',
        'task_map_generation': 'map-2', 'task_pipeline_stage': 'impl', 'operation_generation': 1,
        'active_attempt_id': 'current-attempt', 'lease_id': 'current-lease', 'dispatch_id': 'current-dispatch'}
    ref = write_sidecar_json(service.state_dir, 'artifacts/current-request.json', {'request': {
        'result_identity': {'source_branch': 'current-task', 'contract_snapshot_ref': descriptor['ref'],
                            'contract_snapshot_digest': descriptor['sha256']},
        'workdir_write_scopes': ['probe.py']}}, kind='test', schema_version='test.v1', created_by='test')
    for kind, extra in [('task.pipeline.generation.admitted', {'schema_version': 'task-pipeline-generation.v1', 'task_ids': ['T2']}),
                       ('workflow.operation.requested', {'request_ref': ref}),
                       ('task.pipeline.stage.dispatched', {'workdir': str(root), 'source_branch': 'current-task'}),
                       ('workflow.call.result.admitted', {'semantic_verdict': 'blocked', 'control_result_ref': {'source_event_id': 'blocked'}}),
                       ('workflow.operation.settled', {})]:
        service.writer.append(ZfEvent(type=kind, origin='kernel', payload={**identity, **extra}))


def test_run_manager_keeps_task_probes_but_drops_superseded_candidate(candidate, monkeypatch):
    service, _, _, _ = candidate
    service.config.runtime.host_verification = policy()
    service.writer.append(ZfEvent(type='test.failed', correlation_id='run-1', payload={
        'workflow_run_id': 'run-1', 'fanout_id': 'fanout', 'failure_kind': 'mixed'}))
    supersede(service)
    current_task_probe(service)
    preview(service, operation_id='current-task', kind='verify', command_id='contract-verification')
    executed = []
    def probe(_service, _root, manifest, _policy):
        executed.append(manifest['operation_id'])
        return {'status': 'completed', 'source_stable': True, 'exit_code': 0,
                'execution_origin': 'preauthorized_container'}, 'pass'
    monkeypatch.setattr('zf.runtime.host_verification.verify_in_container', probe)
    for _ in range(2):
        run_manager_tick(state_dir=service.state_dir, writer=service.writer, config=service.config,
            project_root=service.project_root, auto_execute=True, spawn_repairs=False)
    assert executed == ['current-task']
    events = service.writer.event_log.read_all()
    assert not any(e.type == 'runtime.action.attempt.started'
        and e.payload.get('operation_id') == 'candidate-1' for e in events)
    assert not any(e.type in {'task.done', 'test.passed', 'run.goal.completed'} for e in events)


def test_plan_before_candidate_does_not_revoke_its_recovery():
    plan = ZfEvent(type='task_map.ready', correlation_id='run-1', origin='kernel')
    frozen = ZfEvent(type='candidate.ready', correlation_id='run-1')
    assert not candidate_recovery_superseded([plan, frozen], frozen, 'run-1')
    assert candidate_recovery_superseded([frozen, plan], frozen, 'run-1')
