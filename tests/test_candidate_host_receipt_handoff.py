"""Approved supplementary execution receipts survive candidate reader recovery."""
from copy import deepcopy
from dataclasses import asdict

import pytest

from tests.test_reader_stage_recovery import recovery
from tests.test_host_verification import policy
from zf.core.events import ZfEvent
from zf.runtime.host_verification_handoff import resume_reader_after_probes
from zf.runtime.sidecar_refs import write_sidecar_json
from zf.runtime.task_host_manifest import digest


@pytest.mark.parametrize('invalid', ['', 'target', 'contract', 'command', 'generation',
    'run', 'approval', 'unstable', 'untrusted', 'manifest_digest', 'request', 'exit_code',
    'web', 'failed_probe', 'worker_actor'])
def test_controlled_recovery_carries_only_bound_supplemental_receipt(recovery, invalid):
    service, failure, original = recovery
    service.writer.default_origin = 'kernel'
    state = service.state_dir
    p = policy()
    identity = {'workflow_run_id': 'run-1', 'task_id': 'T', 'task_map_generation': 'G'}
    operation = {**identity, 'operation_id': 'current-reader'}
    target = 'a' * 40
    supplemental = {'command_id': 'supplement', 'command': 'python3 supplement.py',
                    'command_digest': 'b' * 64}
    contract = {'authority_scope': 'candidate', 'verification_commands': [
        {'command_id': 'probe', 'command': 'python3 probe.py'}, supplemental]}
    operation['request_ref'] = write_sidecar_json(state, 'artifacts/request.json', {
        **operation, 'request': {'target_commit': target,
            'result_identity': {'contract_snapshot_digest': digest(contract)}}},
        kind='request', schema_version='request.v1', created_by='test')
    service.writer.emit('fanout.child.dispatched', payload={**operation, 'fanout_id': 'f1'})
    service.writer.emit(failure.type, correlation_id='run-1', payload=failure.payload)
    base = write_sidecar_json(state, 'artifacts/probe.json', {
        'command_id': 'probe', 'status': 'completed', 'source_stable': True, 'exit_code': 0,
        'execution_origin': 'preauthorized_container', 'policy_digest': digest(asdict(p))},
        kind='test', schema_version='test.v1', created_by='test')
    service.writer.emit('runtime.action.attempt.completed', payload={**operation,
        'kind': 'verify', 'approval_ref': p.approval_ref, 'receipt_ref': base})
    manifest = {'schema_version': 'task-host-manifest.v1', 'kind': 'verify',
        'operation_id': 'earlier-reader', 'identity': deepcopy(identity), 'head': target,
        'contract_digest': digest(contract), 'command': deepcopy(supplemental)}
    if invalid == 'target': manifest['head'] = 'c' * 40
    if invalid == 'contract': manifest['contract_digest'] = 'c' * 64
    if invalid == 'command': manifest['command']['command'] = 'unapproved command'
    if invalid == 'generation': manifest['identity']['task_map_generation'] = 'other'
    if invalid == 'run': manifest['identity']['workflow_run_id'] = 'other'
    manifest['manifest_digest'] = digest(manifest)
    receipt = {**identity, 'schema_version': 'task-host-receipt.v1', 'kind': 'verify',
        'operation_id': 'earlier-reader', 'execution_origin': 'operator_authorized_host',
        'approval_ref': 'owner-reviewed', 'status': 'completed', 'exit_code': 0,
        'source_stable': True, 'target_commit': target, **supplemental,
        'manifest': manifest, 'manifest_digest': manifest['manifest_digest']}
    if invalid == 'approval': receipt['approval_ref'] = ''
    if invalid == 'unstable': receipt['source_stable'] = False
    if invalid == 'exit_code': receipt['exit_code'] = None
    if invalid == 'failed_probe': receipt.update(status='failed', exit_code=1)
    if invalid == 'manifest_digest': receipt['manifest_digest'] = 'c' * 64
    extra = write_sidecar_json(state, 'artifacts/operator-supplement.json', receipt,
        kind='task_host_receipt', schema_version='task-host-receipt.v1', created_by='operator')
    service.writer.default_origin = ''
    service.writer.append(ZfEvent(type='runtime.action.attempt.completed',
        actor='web' if invalid == 'web' else 'worker' if invalid == 'worker_actor' else 'run-manager',
        origin='' if invalid in {'web', 'worker_actor'} else 'worker' if invalid == 'untrusted' else 'kernel',
        payload={**identity, 'operation_id': 'earlier-reader', 'action': 'task-host-assist',
                 'kind': 'verify', 'approval_ref': receipt['approval_ref'],
                 'manifest_digest': manifest['manifest_digest'], 'receipt_ref': extra}))
    service.writer.default_origin = 'kernel'
    if invalid == 'request':
        (state / operation['request_ref']['ref']).write_text('{}')
    assert resume_reader_after_probes(service, operation, contract, p, service.writer.event_log.read_all())
    trigger = [e for e in service.writer.event_log.read_all() if e.type == original.type][-1]
    feedback = trigger.payload['operator_feedback'][0]
    valid = invalid in {'', 'web', 'failed_probe'}
    assert ('operator-supplement.json' in feedback) is valid
    if not valid:
        assert 'Commands without receipts remain unverified: supplement' in feedback
    assert not any(e.type == 'test.passed' for e in service.writer.event_log.read_all())
