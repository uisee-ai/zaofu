"""Controlled repair preserves historical evidence and retires only stopped misroutes."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.test_reader_stage_recovery import recovery, execute
from zf.runtime.sidecar_refs import write_sidecar_json
from zf.runtime.workflow_operation import reduce_workflow_operations


def typed_failure(service, original, verdict='abstained', schema='verification-result.v1', failure_class=''):
    writer = service.writer
    identity = {'workflow_run_id': 'run-1', 'operation_id': 'verify-1', 'request_hash': 'hash'}
    result = write_sidecar_json(service.state_dir, 'artifacts/verify.json', {
        **identity, 'schema_version': schema, 'verdict': verdict, 'failure_class': failure_class},
        kind='call_control_result', schema_version=schema, created_by='test')
    writer.emit('workflow.operation.requested', payload=identity)
    writer.emit('workflow.call.result.admitted', payload={**identity, 'control_result_ref': result})
    writer.emit('workflow.operation.settled', payload=identity)
    writer.emit('fanout.child.failed', payload={**identity, 'fanout_id': 'f1', 'child_id': 'verify',
        'control_result_ref': result, 'semantic_verdict': verdict, 'control_result_schema': schema})
    failure = writer.emit(original.type, correlation_id='run-1', payload={
        k:v for k,v in original.payload.items() if k != 'failure_kind'})
    return failure, result


@pytest.mark.parametrize('schema,failure_class', [
    ('verification-result.v1', ''), ('workflow-read-result.v1', 'reader_execution_failure'),
])
def test_historical_missing_aggregate_kind_recovered_without_mutating_events(recovery, schema, failure_class):
    service, failure, trigger = recovery
    failure, _ = typed_failure(service, failure, schema=schema, failure_class=failure_class)
    before = service.writer.event_log.path.read_bytes()
    assert execute(service, failure.id)['ok']
    assert service.writer.event_log.path.read_bytes().startswith(before)
    assert 'failure_kind' not in failure.payload


@pytest.mark.parametrize('fault', ['rejected', 'missing', 'tampered', 'wrong_run'])
def test_repair_cannot_invent_infrastructure_from_unbound_child(recovery, fault):
    service, failure, trigger = recovery
    failure, ref = typed_failure(service, failure, 'rejected' if fault == 'rejected' else 'abstained')
    if fault == 'missing': (service.state_dir / ref['ref']).unlink()
    if fault == 'tampered': (service.state_dir / ref['ref']).write_text('{}')
    if fault == 'wrong_run':
        failure = service.writer.emit(failure.type, correlation_id='other', payload={**failure.payload, 'workflow_run_id': 'other'})
    assert not execute(service, failure.id)['ok']


def misroute(service, failure, monkeypatch):
    from zf.core.config.schema import RoleConfig
    from zf.core.safety import write_workdir_owner_marker
    writer = service.writer
    service.config.roles = [RoleConfig(name='planner', instance_id='planner', role_kind='reader')]
    root = service.state_dir / 'workdirs/planner'
    (root / 'project').mkdir(parents=True)
    write_workdir_owner_marker(root, project_name='test', instance_id='planner',
        project_root=service.project_root, created_by='test')
    monkeypatch.setattr('zf.runtime.workdirs.WorkdirManager.plan', lambda *_: SimpleNamespace(
        enabled=True, role_kind='reader', workdir=root, project_path=root / 'project'))
    trigger = writer.emit('scan.completed', correlation_id='run-1', payload={'rework_of': failure.id})
    writer.emit('fanout.started', correlation_id='run-1', payload={
        'fanout_id': 'bad-plan', 'stage_id': 'plan', 'trigger_event_id': trigger.id})
    identity = {'workflow_run_id': 'run-1', 'operation_id': 'bad-planner', 'request_hash': 'hash',
                'role_instance': 'planner', 'attempt_domain': 'plan'}
    writer.emit('workflow.operation.requested', payload=identity)
    writer.emit('fanout.child.dispatched', correlation_id='run-1', payload={
        **identity, 'fanout_id': 'bad-plan', 'stage_id': 'plan', 'child_id': 'planner'})
    writer.emit('workflow.operation.interrupted', payload={**identity, 'reason': 'graceful_stop'})
    return identity


def test_controlled_repair_retires_only_explicit_stopped_misroute(recovery, monkeypatch):
    service, failure, original = recovery
    misroute(service, failure, monkeypatch)
    assert execute(service, failure.id, misrouted_operation_ids=['bad-planner'])['ok']
    events = service.writer.event_log.read_all()
    assert reduce_workflow_operations(events)['bad-planner']['status'] == 'cancelled'
    assert len([e for e in events if e.type == 'fanout.cancelled' and e.payload['fanout_id'] == 'bad-plan']) == 1
    assert len([e for e in events if e.type == original.type]) == 2


@pytest.mark.parametrize('fault', ['running', 'unrelated', 'admitted', 'holders', 'published'])
def test_misroute_retirement_fail_closed_without_partial_effects(recovery, monkeypatch, fault):
    service, failure, original = recovery
    identity = misroute(service, failure, monkeypatch)
    if fault == 'running': service.writer.emit('workflow.operation.started', payload=identity)
    if fault == 'admitted': service.writer.emit('workflow.call.result.admitted', payload=identity)
    if fault == 'published': service.writer.emit('task_map.ready', correlation_id='run-1', payload={'fanout_id': 'bad-plan'})
    if fault == 'holders':
        from zf.runtime.environment_usage import EnvironmentInUse
        def occupied(*_): raise EnvironmentInUse('worker remains')
        monkeypatch.setattr('zf.runtime.environment_usage.assert_workspace_idle', occupied)
    result = execute(service, failure.id, misrouted_operation_ids=['other' if fault == 'unrelated' else 'bad-planner'])
    assert not result['ok']
    events = service.writer.event_log.read_all()
    assert not [e for e in events if e.type in {'workflow.operation.cancelled','fanout.cancelled'}]
    assert len([e for e in events if e.type == original.type]) == 1
