"""A live recovery reader may inspect its failed Task's unchanged input generation."""
import json

import pytest

from tests.test_artifact_input_lifecycle import admission, binding
from tests.test_artifact_read_capability import _grant
from zf.cli.main import main
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.core.task.schema import Task, TaskContract, TaskExecutionBinding
from zf.core.task.store import TaskStore
from zf.runtime.artifact_input_lifecycle import input_lifecycle
from zf.runtime.artifact_read_ledger import (
    source_manifest_from_payload, build_input_consumption_policy, write_input_consumption_policy,
)
from zf.runtime.call_result_envelope import canonical_json_sha256
from zf.runtime.task_contract_snapshot import current_task_contract_identity
from zf.runtime.workflow_operation import WorkflowOperationService, reduce_workflow_operations


def setup(state, *, request_fault='', control_source=False):
    task = Task(id='T7', title='failed task', status='in_progress', assigned_to='old-dev',
        active_dispatch_id='old-impl', execution_binding=TaskExecutionBinding(workflow_run_id='r'),
        contract=TaskContract(behavior='original behavior', evidence_contract={'task_map_generation': '1'}))
    TaskStore(state / 'kanban.json').add(task)
    identity = current_task_contract_identity(task)
    authority = {'attempt_domain': 'task', 'operation_type': 'fanout_reader_child',
                 'continuation_key': 'failure'}
    source = state / 'artifacts/input.json'
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text('{"body":"original pinned requirements"}')
    manifest, _ = source_manifest_from_payload(state_dir=state, project_root=state.parent,
        payload={'input_refs': [{'source_id': 'plan', 'artifact_id': 'input', 'ref': 'artifacts/input.json'}]},
        workflow_run_id='r', task_id='T7', attempt_id='reader-a', dispatch_id='reader-a',
        manifest_metadata={**binding(), **identity, 'read_purpose': 'implementation',
            'target_commit': 'a' * 40, 'handoff_authority_contract': authority})
    writer = EventWriter(EventLog(state / 'events.jsonl'), default_origin='kernel')
    writer.append(admission())
    writer.append(ZfEvent(id='failure', type='flow.discovery.requested' if control_source else 'dev.blocked',
        task_id=None if control_source else task.id, payload=identity))
    writer.append(admission('2'))
    # Cancelling an older scan must not revoke this reader's unchanged task package.
    writer.emit('fanout.cancelled', payload={'workflow_run_id': 'r', 'fanout_id': 'old-scan'})
    result_identity = {**identity, **binding(), 'target_commit': 'a' * 40}
    if request_fault:
        result_identity[request_fault] = 'wrong'
    reads = [{'source_id': 'plan', 'artifact_id': 'input',
              'artifact_sha256': manifest['sources'][0]['sha256'], 'json_path': '$'}]
    policy = write_input_consumption_policy(state, build_input_consumption_policy(
        workflow_run_id='r', attempt_id='reader-a', required_reads=reads))
    service = WorkflowOperationService(state_dir=state, event_log=writer.event_log, event_writer=writer)
    operation = service.ensure_operation(workflow_run_id='r', operation_id='op',
        operation_type='fanout_reader_child', task_id='T7', role_instance='dev-1',
        active_attempt_id='reader-a', lease_id='reader-a', request={
            'attempt_domain': 'task', 'fanout_id': 'new-scan',
            'handoff_authority_contract': authority, 'result_identity': result_identity,
            'task_map_generation': '1', 'source_manifest_digest': canonical_json_sha256(manifest),
            'input_consumption_policy_ref': policy, 'required_reads': reads})
    service.mark_started(operation_id='op', request_hash=operation.request_hash, workflow_run_id='r',
        task_id='T7', dispatch_id='reader-a', active_attempt_id='reader-a', role_instance='dev-1', lease_id='reader-a')
    return task, manifest, writer


@pytest.mark.parametrize('control_source', [False, True])
def test_public_index_and_recover_keep_failed_task_reader_inputs(tmp_path, monkeypatch, capsys, control_source):
    task, manifest, writer = setup(tmp_path, control_source=control_source)
    _grant(tmp_path, monkeypatch, manifest=manifest, operation_id='op')
    for command in ['index', 'recover']:
        assert main(['artifact', command, '--attempt', 'reader-a', '--state-dir', str(tmp_path)]) == 0
        result = json.loads(capsys.readouterr().out)
        lifecycle = result['inputs']['lifecycle'] if command == 'recover' else result['lifecycle']
        assert lifecycle['requires_rebind'] is False
        assert lifecycle['reason'] == 'current_recovery_reader_binding'
        assert lifecycle['authorizes_rebind'] is False
        if command == 'recover':
            assert result['status'] == 'bound' and result['authorizes_execution'] is False
    assert TaskStore(tmp_path / 'kanban.json').get(task.id).status == 'in_progress'
    assert reduce_workflow_operations(writer.event_log.read_all())['op']['status'] == 'running'


@pytest.mark.parametrize('fault', [
    'contract', 'generation', 'task_done', 'task_backlog', 'active_impl', 'active_verify', 'wrong_run', 'manifest',
    'request_bytes', 'operation_cancelled', 'fanout_cancelled', 'new_attempt', 'reader_domain',
    'missing_failure', 'wrong_failure', 'rejected_package',
])
def test_reader_exception_rejects_unproven_or_retired_bindings(tmp_path, fault):
    task, manifest, writer = setup(tmp_path)
    store = TaskStore(tmp_path / 'kanban.json')
    if fault == 'contract':
        task.contract.behavior = 'changed'; store.update(task.id, contract=task.contract)
    elif fault == 'generation':
        task.contract.evidence_contract['task_map_generation'] = '2'; store.update(task.id, contract=task.contract)
    elif fault in {'task_done', 'task_backlog'}:
        store.update(task.id, status='done' if fault == 'task_done' else 'backlog')
    elif fault in {'active_impl', 'active_verify'}:
        writer.emit('workflow.operation.requested', task_id=task.id, payload={
            'operation_id': 'new-pipeline', 'workflow_run_id': 'r',
            'task_pipeline_stage': fault.removeprefix('active_')})
    elif fault == 'wrong_run': store.update(task.id, execution_binding=TaskExecutionBinding(workflow_run_id='other'))
    elif fault == 'manifest': manifest['target_commit'] = 'b' * 40
    elif fault == 'request_bytes':
        row = reduce_workflow_operations(writer.event_log.read_all())['op']
        (tmp_path / row['request_ref']['ref']).write_text('{}')
    elif fault == 'operation_cancelled': writer.emit('workflow.operation.cancelled', payload={'operation_id': 'op'})
    elif fault == 'fanout_cancelled': writer.emit('fanout.cancelled', payload={'fanout_id': 'new-scan', 'workflow_run_id': 'r'})
    elif fault == 'new_attempt': writer.emit('workflow.operation.started', payload={'operation_id': 'op', 'active_attempt_id': 'new'})
    elif fault == 'reader_domain': manifest['handoff_authority_contract']['attempt_domain'] = 'plan'
    elif fault == 'rejected_package': writer.emit('fanout.cancelled', payload={
        'workflow_run_id': 'r', 'failure_scope': 'plan_admission', 'trigger_event_id': 'critic-1'})
    events = writer.event_log.read_all()
    if fault == 'missing_failure': events = [e for e in events if e.id != 'failure']
    elif fault == 'wrong_failure': next(e for e in events if e.id == 'failure').task_id = 'other'
    assert input_lifecycle(manifest, events, state_dir=tmp_path)['requires_rebind']


@pytest.mark.parametrize('field', ['task_id', 'workflow_run_id', 'contract_revision', 'task_map_generation',
    'target_commit', 'plan_artifact_package_ref', 'plan_artifact_package_digest'])
def test_reader_request_must_bind_exact_manifest_identity(tmp_path, field):
    _, manifest, writer = setup(tmp_path, request_fault=field)
    assert input_lifecycle(manifest, writer.event_log.read_all(), state_dir=tmp_path)['requires_rebind']
