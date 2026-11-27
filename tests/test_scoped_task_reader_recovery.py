"""A partitioned reader verifies its task against the integrated candidate."""
import json

import pytest

from tests.test_artifact_input_lifecycle import admission, binding
from tests.test_artifact_read_capability import _grant
from zf.cli.main import main
from zf.core.events import EventLog, EventWriter
from zf.core.task.schema import Task, TaskContract, TaskExecutionBinding
from zf.core.task.store import TaskStore
from zf.runtime.artifact_input_lifecycle import input_lifecycle
from zf.runtime.artifact_query.handoff import CanonicalHandoffResolver, build_handoff_authority_contract
from zf.runtime.artifact_read_ledger import (
    build_input_consumption_policy, source_manifest_from_payload, write_input_consumption_policy,
)
from zf.runtime.call_result_envelope import canonical_json_sha256
from zf.runtime.task_contract_snapshot import (
    build_target_snapshot, build_task_contract_snapshot, current_task_contract_identity,
    write_target_snapshot, write_task_contract_snapshot,
)
from zf.runtime.workflow_operation import WorkflowOperationService


def reader_attempt(state, *, recovered_dispatch=False):
    package = binding()
    task = Task(id='T7', title='integrated task', status='in_progress', assigned_to='writer',
        execution_binding=TaskExecutionBinding(workflow_run_id='r'), contract=TaskContract(
            behavior='show timeline', evidence_contract={'source_refs': {**package, 'task_map_generation': '1'}}))
    TaskStore(state / 'kanban.json').add(task)
    contract = build_task_contract_snapshot(task, workflow_run_id='r', task_map_generation_id='1',
                                           base_commit='a' * 40, task_ref='task/T7')
    ref = write_task_contract_snapshot(state, contract)
    head = 'c' * 40
    target = write_target_snapshot(state, build_target_snapshot(ref, target_commit=head, contract_snapshot=contract))
    identity = {**current_task_contract_identity(task), **package, 'base_commit': 'a' * 40, 'task_ref': 'task/T7',
                'contract_snapshot_ref': ref['ref'], 'contract_snapshot_digest': ref['sha256'],
                'target_snapshot_ref': target['ref'], 'target_snapshot_digest': target['sha256'], 'target_commit': head}
    writer = EventWriter(EventLog(state / 'events.jsonl'), default_origin='kernel')
    writer.append(admission())
    writer.emit('task.ref.updated', task_id='T7', payload={'workflow_run_id': 'r', 'source_commit': 'b' * 40})
    producer = writer.emit('candidate.ready', payload={'workflow_run_id': 'r', 'completed_task_ids': ['T7'],
        'candidate_base_commit': 'a' * 40, 'candidate_head_commit': head, 'task_map_generation': '1',
        'fanout_id': 'writer-fanout', 'candidate_ref': 'candidate/r', 'integration_attempt_id': 'integration', **package})
    if recovered_dispatch:
        _reemit_candidate(writer, producer)
    authority = build_handoff_authority_contract({**identity, 'attempt_domain': 'task'}, output_profile_id='task-verify', stage_id='issue-lanes-verify',
                                                operation_type='fanout_reader_child')
    authority['requires_candidate_lineage'] = True
    request = {'output_profile_id': 'task-verify', 'handoff_authority_contract': authority, 'attempt_domain': 'task',
               'task_map_generation': '1', 'fanout_id': 'f', 'result_identity': identity}
    resolver = CanonicalHandoffResolver(state_dir=state, project_root=state.parent, config=None)
    candidate = resolver._validate_candidate_authority(payload={**request, **identity}, workflow_run_id='r',
                                                        currentness=dict(identity))
    manifest, _ = source_manifest_from_payload(state_dir=state, project_root=state.parent,
        payload={'input_refs': [{'source_id': 'contract', 'artifact_id': 'contract', 'ref': ref['ref']}]},
        workflow_run_id='r', task_id='T7', attempt_id='reader', dispatch_id='reader', manifest_metadata={
            **identity, 'read_purpose': 'task-verify', 'handoff_authority_contract': authority,
            'candidate_event_id': candidate['candidate_event_id'], 'candidate_snapshot_digest': candidate['identity_digest'],
            'candidate_head_commit': head})
    reads = [{'source_id': 'contract', 'artifact_id': 'contract', 'artifact_sha256': ref['sha256'], 'json_path': '$'}]
    policy = write_input_consumption_policy(state, build_input_consumption_policy(
        workflow_run_id='r', attempt_id='reader', required_reads=reads))
    service = WorkflowOperationService(state_dir=state, event_log=writer.event_log, event_writer=writer)
    op = service.ensure_operation(workflow_run_id='r', operation_id='op', operation_type='fanout_reader_child',
        task_id='T7', role_instance='dev-1', active_attempt_id='reader', lease_id='reader', request={
            **request, 'required_reads': reads, 'input_consumption_policy_ref': policy,
            'source_manifest_digest': canonical_json_sha256(manifest)})
    service.mark_started(operation_id='op', request_hash=op.request_hash, workflow_run_id='r', task_id='T7',
                         dispatch_id='reader', active_attempt_id='reader', role_instance='dev-1', lease_id='reader')
    return manifest, identity, writer


@pytest.mark.parametrize('recovered_dispatch', [False, True])
def test_partitioned_reader_recovery_uses_live_operation_and_integrated_head(tmp_path, monkeypatch, capsys, recovered_dispatch):
    manifest, _, _ = reader_attempt(tmp_path, recovered_dispatch=recovered_dispatch)
    _grant(tmp_path, monkeypatch, manifest=manifest, operation_id='op')
    monkeypatch.setenv('ZF_ARTIFACT_PURPOSE', 'task-verify')
    assert main(['artifact', 'recover', '--attempt', 'reader', '--state-dir', str(tmp_path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'bound'
    assert result['authorizes_execution'] is False
    assert result['inputs']['lifecycle']['status'] == 'current'


@pytest.mark.parametrize('defect', ['contract', 'retired_task', 'target_bytes', 'candidate', 'cancelled', 'package', 'manifest', 'writer_dispatch'])
def test_partitioned_reader_refuses_changed_bindings(tmp_path, defect):
    manifest, identity, writer = reader_attempt(tmp_path)
    store = TaskStore(tmp_path / 'kanban.json')
    if defect == 'contract':
        task = store.get('T7')
        task.contract.behavior = 'changed'
        store.update(task.id, contract=task.contract)
    elif defect == 'retired_task': store.update('T7', status='superseded')
    elif defect == 'target_bytes': (tmp_path / identity['target_snapshot_ref']).write_text('{}')
    elif defect == 'candidate':
        producer = writer.emit('candidate.ready', payload={'workflow_run_id': 'r', 'completed_task_ids': ['T7'],
            'candidate_head_commit': 'd' * 40, 'task_map_generation': '1'})
    elif defect == 'cancelled': writer.emit('fanout.cancelled', payload={'workflow_run_id': 'r', 'fanout_id': 'f'})
    elif defect == 'package': writer.append(admission('2'))
    elif defect == 'manifest': manifest['candidate_snapshot_digest'] = '1' * 64
    elif defect == 'writer_dispatch':
        store.update('T7', assigned_to='dev-1', active_dispatch_id='reader')
        (tmp_path / identity['target_snapshot_ref']).write_text('{}')
    assert input_lifecycle(manifest, writer.event_log.read_all(), state_dir=tmp_path)['requires_rebind']


@pytest.mark.parametrize('defect', ['', 'missing_receipt', 'different_head', 'different_integration', 'new_native', 'receipt_source'])
def test_only_audited_redispatch_retains_the_original_task_reader_source(tmp_path, defect):
    manifest, _, writer = reader_attempt(tmp_path)
    source = next(e for e in writer.event_log.read_all() if e.type == 'candidate.ready')
    _reemit_candidate(writer, source, defect)
    result = input_lifecycle(manifest, writer.event_log.read_all(), state_dir=tmp_path)
    assert result['requires_rebind'] is bool(defect)


def _reemit_candidate(writer, source, defect=''):
    if defect == 'new_native':
        writer.emit('candidate.ready', payload={**source.payload, 'candidate_head_commit': 'd' * 40})
    copy = {**source.payload, 'candidate_event_id': source.id, 'resume_checkpoint_ref': 'checkpoint',
            'source': 'workflow_resume_batch'}
    if defect == 'different_head': copy['candidate_head_commit'] = 'd' * 40
    if defect == 'different_integration': copy['integration_attempt_id'] = 'different-integration'
    if defect != 'missing_receipt':
        writer.emit('workflow.resume.checkpoint', payload={'workflow_run_id': 'r', 'safe_resume_action': 'reemit_candidate_ready',
            'source_event_id': 'failure', 'checkpoint_id': 'checkpoint',
            'candidate_snapshot_event_id': 'other' if defect == 'receipt_source' else source.id})
    writer.emit('candidate.ready', causation_id='failure', payload=copy)
