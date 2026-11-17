"""Candidate readers retain their immutable authority after child Tasks finish."""
import pytest

from tests.test_artifact_input_lifecycle import admission
from tests.test_plan_artifact_package import _package
from zf.runtime.plan_artifact_package import write_plan_artifact_package
from tests.test_candidate_verification_authority import _runtime, _seed_frozen_candidate
from zf.core.events import ZfEvent
from zf.runtime.artifact_input_lifecycle import input_lifecycle
from zf.runtime.call_result_runtime import prepare_call_operation, mark_call_operation_started
from zf.runtime.sidecar_refs import hydrate_sidecar_ref, write_sidecar_json


def setup(tmp_path, profile="candidate-verify"):
    runtime = _runtime(tmp_path)
    seeded = _seed_frozen_candidate(runtime)
    body = _package(runtime.state_dir, generation=seeded['generation'])
    body['workflow_run_id'] = seeded['run_id']
    descriptor = write_plan_artifact_package(runtime.state_dir, body)
    package = admission(workflow_run_id=seeded['run_id'], task_map_generation=seeded['generation'],
        package_ref=descriptor['ref'], package_digest=descriptor['sha256'])
    runtime.event_log.append(package)
    freeze = next(e for e in runtime.event_log.read_all() if e.id == 'evt-candidate-ready')
    runtime.event_log.append(ZfEvent(id='candidate-with-package', type='candidate.ready', payload={
        **freeze.payload, 'plan_artifact_package_ref': package.payload['package_ref'],
        'plan_artifact_package_digest': package.payload['package_digest']}))
    payload = {'workflow_run_id': seeded['run_id'], 'task_id': 'FLOW-ANCHOR',
        'fanout_id': 'candidate-readers', 'child_id': 'verify-lane-0', 'role_instance': 'verify-lane-0',
        'stage_id': 'issue-lanes-verify', 'output_profile_id': profile,
        'artifact_package_mode': 'shadow', 'target_commit': seeded['candidate_head'],
        'plan_artifact_package_ref': package.payload['package_ref'],
        'plan_artifact_package_digest': package.payload['package_digest'],
        'task_map_generation': seeded['generation']}
    if profile == 'thin-judge-goal-closure':
        identity = {'workflow_run_id': seeded['run_id'], 'goal_id': 'goal-1',
            'task_map_generation': seeded['generation'],
            'plan_artifact_package_ref': package.payload['package_ref'],
            'plan_artifact_package_digest': package.payload['package_digest']}
        target = {**identity, 'target_commit': seeded['candidate_head'], 'closure_identity': 'closure-1'}
        for name, body in [('contract', identity), ('target', target)]:
            schema = f'goal-closure-{name}-snapshot.v1'
            ref = write_sidecar_json(runtime.state_dir, f'artifacts/goal-{name}.json',
                {**body, 'schema_version': schema}, kind='goal_closure_snapshot', schema_version=schema, created_by='test')
            payload[f'{name}_snapshot_ref'] = ref['ref']
            payload[f'{name}_snapshot_digest'] = ref['sha256']
        payload.update(target)
        runtime.event_writer.emit('flow.goal.closed', payload=target)
    prepared = prepare_call_operation(runtime, payload=payload, operation_type='fanout_reader_child',
        operation_key='verify-lane-0', stage_id='issue-lanes-verify', task_id='FLOW-ANCHOR',
        dispatch_id='candidate-dispatch', causation_id='candidate-with-package')
    mark_call_operation_started(runtime, prepared, task_id='FLOW-ANCHOR',
        dispatch_id='candidate-dispatch', causation_id='candidate-with-package')
    manifest = hydrate_sidecar_ref(runtime.state_dir, payload['attempt_source_manifest']).payload
    runtime.task_store.update('FLOW-ANCHOR', status='done')
    return runtime, manifest, prepared, payload


def test_candidate_reader_does_not_require_active_anchor_task(tmp_path):
    runtime, manifest, _, _ = setup(tmp_path)
    result = input_lifecycle(manifest, runtime.event_log.read_all(), state_dir=runtime.state_dir)
    assert result['requires_rebind'] is False
    assert result['status'] == 'current'
    assert result['authorizes_rebind'] is False


@pytest.mark.parametrize('fault', ['manifest', 'cancelled', 'new_attempt', 'fanout_cancelled',
    'new_candidate', 'request_bytes', 'target_bytes', 'contract_bytes', 'freeze_bytes'])
def test_candidate_reader_rejects_stale_or_tampered_authority(tmp_path, fault):
    runtime, manifest, prepared, payload = setup(tmp_path)
    if fault == 'manifest': manifest['target_commit'] = 'f' * 40
    elif fault == 'cancelled': runtime.event_writer.emit('workflow.operation.cancelled', payload={'operation_id': prepared.operation_id})
    elif fault == 'new_attempt': runtime.event_writer.emit('workflow.operation.started', payload={
        'operation_id': prepared.operation_id, 'active_attempt_id': 'new-attempt'})
    elif fault == 'fanout_cancelled': runtime.event_writer.emit('fanout.cancelled', payload={
        'workflow_run_id': manifest['workflow_run_id'], 'fanout_id': 'candidate-readers'})
    elif fault == 'new_candidate':
        freeze = next(e for e in runtime.event_log.read_all() if e.id == 'candidate-with-package')
        runtime.event_writer.emit('candidate.ready', payload={**freeze.payload, 'candidate_head_commit': 'f' * 40})
    else:
        if fault == 'request_bytes':
            request = next(e for e in runtime.event_log.read_all() if e.type == 'workflow.operation.requested')
            ref = request.payload['request_ref']['ref']
        elif fault == 'freeze_bytes':
            freeze = next(e for e in runtime.event_log.read_all() if e.id == 'candidate-with-package')
            ref = freeze.payload['freeze_receipt_ref']['ref']
        else: ref = payload['target_snapshot_ref' if fault == 'target_bytes' else 'contract_snapshot_ref']
        (runtime.state_dir / ref).write_text('{}')
    assert input_lifecycle(manifest, runtime.event_log.read_all(), state_dir=runtime.state_dir)['requires_rebind']


@pytest.mark.parametrize('profile', ['workflow-read', 'thin-judge-goal-closure'])
def test_frozen_workflow_readers_do_not_inherit_anchor_dispatch(tmp_path, profile):
    runtime, manifest, _, _ = setup(tmp_path, profile)
    assert not manifest.get('contract_revision')
    lifecycle = input_lifecycle(manifest, runtime.event_log.read_all(), state_dir=runtime.state_dir)
    assert lifecycle['requires_rebind'] is False
    assert lifecycle['status'] == 'current'


@pytest.mark.parametrize('fault', ['target', 'generation', 'cancelled', 'new_attempt', 'manifest', 'contract', 'profile'])
def test_frozen_workflow_reader_rejects_invalid_binding(tmp_path, fault):
    runtime, manifest, prepared, _ = setup(tmp_path, 'workflow-read')
    if fault in {'target', 'generation'}:
        freeze = next(e for e in runtime.event_log.read_all() if e.id == 'candidate-with-package')
        field = 'candidate_head_commit' if fault == 'target' else 'task_map_generation'
        runtime.event_writer.emit('candidate.ready', payload={**freeze.payload, field: 'different'})
    elif fault == 'cancelled': runtime.event_writer.emit('fanout.cancelled', payload={
        'workflow_run_id': manifest['workflow_run_id'], 'fanout_id': 'candidate-readers'})
    elif fault == 'new_attempt': runtime.event_writer.emit('workflow.operation.started', payload={
        'operation_id': prepared.operation_id, 'active_attempt_id': 'new-attempt'})
    elif fault == 'manifest': manifest['read_purpose'] = 'tampered-purpose'
    elif fault == 'contract': manifest['contract_revision'] = 'task-contract-r1'
    elif fault == 'profile': manifest['handoff_authority_contract']['output_profile_id'] = 'implementation'
    assert input_lifecycle(manifest, runtime.event_log.read_all(), state_dir=runtime.state_dir)['requires_rebind']


@pytest.mark.parametrize('fault', ['', 'missing_redrive', 'wrong_request', 'wrong_run', 'changed_lease', 'wrong_cause'])
def test_authorized_transport_redrive_preserves_immutable_input_attempt(tmp_path, fault):
    from zf.runtime.workflow_operation import reduce_workflow_operations
    runtime, manifest, prepared, _ = setup(tmp_path, 'workflow-read')
    row = reduce_workflow_operations(runtime.event_log.read_all())[prepared.operation_id]
    identity = {k: row[k] for k in ('operation_id', 'workflow_run_id', 'task_id', 'request_hash')}
    if fault != 'missing_redrive':
        redrive = {**identity, 'source_attempt_id': row['active_attempt_id'],
                   'recovery_decision_event_id': 'recovery-proof', 'recovery_decision_owner': 'kernel_replay'}
        if fault == 'wrong_request': redrive['request_hash'] = 'wrong'
        if fault == 'wrong_run': redrive['workflow_run_id'] = 'wrong'
        runtime.event_writer.emit('workflow.operation.redrive_admitted', payload=redrive)
    runtime.event_writer.emit('workflow.operation.started', causation_id='wrong' if fault == 'wrong_cause' else 'recovery-proof', payload={
        **identity, 'dispatch_id': 'transport-retry', 'active_attempt_id': row['active_attempt_id'],
        'role_instance': row['role_instance'], 'lease_id': 'changed' if fault == 'changed_lease' else row['lease_id']})
    lifecycle = input_lifecycle(manifest, runtime.event_log.read_all(), state_dir=runtime.state_dir)
    assert lifecycle['requires_rebind'] is bool(fault)


@pytest.mark.parametrize('fault', ['contract_bytes', 'target_bytes', 'closure_changed'])
def test_thin_judge_input_requires_current_typed_closure(tmp_path, fault):
    runtime, manifest, _, payload = setup(tmp_path, 'thin-judge-goal-closure')
    if fault == 'closure_changed':
        runtime.event_writer.emit('flow.goal.closed', payload={
            'workflow_run_id': manifest['workflow_run_id'], 'goal_id': 'goal-1', 'closure_identity': 'new'})
    else:
        ref = payload['contract_snapshot_ref' if fault == 'contract_bytes' else 'target_snapshot_ref']
        (runtime.state_dir / ref).write_text('{}')
    assert input_lifecycle(manifest, runtime.event_log.read_all(), state_dir=runtime.state_dir)['requires_rebind']
