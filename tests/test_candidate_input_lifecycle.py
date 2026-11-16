"""Candidate readers retain their immutable authority after child Tasks finish."""
import pytest

from tests.test_artifact_input_lifecycle import admission
from tests.test_plan_artifact_package import _package
from zf.runtime.plan_artifact_package import write_plan_artifact_package
from tests.test_candidate_verification_authority import _runtime, _seed_frozen_candidate
from zf.core.events import ZfEvent
from zf.runtime.artifact_input_lifecycle import input_lifecycle
from zf.runtime.call_result_runtime import prepare_call_operation, mark_call_operation_started
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def setup(tmp_path):
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
        'stage_id': 'issue-lanes-verify', 'output_profile_id': 'candidate-verify',
        'artifact_package_mode': 'shadow', 'target_commit': seeded['candidate_head']}
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
