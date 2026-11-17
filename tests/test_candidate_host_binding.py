"""Frozen candidate host probes retain reader identity and reject stale authority."""
import subprocess

import pytest

from tests.test_task_host_actions import case, approve
from zf.core.config.schema import ZfConfig, RoleConfig
from zf.core.events import ZfEvent
from zf.core.safety import write_workdir_owner_marker
from zf.runtime.sidecar_refs import write_sidecar_json
from zf.runtime.task_contract_snapshot import write_task_contract_snapshot, build_target_snapshot, write_target_snapshot
from zf.runtime.task_host_manifest import binding, preview, git


@pytest.fixture
def candidate(case):
    service, root, previous = case
    approve(service, preview(service, operation_id='op-1', kind='commit', paths=['probe.py']))
    _, _, contract, _ = binding(service, 'op-1')
    head = git(root, 'rev-parse', 'HEAD')
    subprocess.run(['git', '-C', str(root), 'checkout', '--detach'], check=True, capture_output=True)
    config = ZfConfig(roles=[RoleConfig(name='verify', instance_id='task', role_kind='reader')])
    config.runtime.workdirs.enabled = True
    config.runtime.workdirs.mode = 'worktree'
    config.runtime.workdirs.root = str(service.state_dir / 'workdirs')
    config.runtime.workdirs.required_paths = []
    config.runtime.workdirs.provision_paths = []
    service.config = config
    write_workdir_owner_marker(root.parent, project_name='test', instance_id='task',
        project_root=service.project_root, created_by='test')
    frozen = {'schema_version': 'candidate-freeze-receipt.v1', 'workflow_run_id': 'run-1',
              'task_map_generation': 'map-1', 'candidate_head_commit': head, 'candidate_ref': 'candidate/test'}
    frozen_ref = write_sidecar_json(service.state_dir, 'artifacts/freeze.json', frozen,
        kind='candidate_freeze_receipt', schema_version='candidate-freeze-receipt.v1', created_by='test')
    event = service.writer.append(ZfEvent(type='candidate.ready', payload={**frozen, 'freeze_receipt_ref': frozen_ref}))
    contract = {**contract, 'authority_scope': 'candidate', 'contract_revision': 'candidate-r1',
                'verification_owner': 'candidate_verify', 'candidate_event_id': event.id}
    contract['verification_commands'] = [{**row, 'owner': 'candidate_verify', 'reusable': False}
                                         for row in contract['verification_commands']]
    descriptor = write_task_contract_snapshot(service.state_dir, contract)
    target = build_target_snapshot(descriptor, target_commit=head, contract_snapshot=contract)
    target.update(authority_scope='candidate', candidate_ref='candidate/test',
                  freeze_receipt_ref=frozen_ref['ref'], freeze_receipt_digest=frozen_ref['sha256'])
    target_ref = write_target_snapshot(service.state_dir, target)
    identity = {**previous, 'operation_id': 'candidate-1', 'task_pipeline_stage': '',
                'operation_generation': 0, 'role_instance': 'task', 'request_hash': 'candidate-hash'}
    request = {'output_profile_id': 'candidate-verify', 'target_commit': head, 'result_identity': {
        'contract_snapshot_ref': descriptor['ref'], 'contract_snapshot_digest': descriptor['sha256'],
        'target_snapshot_ref': target_ref['ref'], 'target_snapshot_digest': target_ref['sha256']}}
    req = write_sidecar_json(service.state_dir, 'artifacts/candidate-request.json', {'request': request},
        kind='test', schema_version='test.v1', created_by='test')
    result = write_sidecar_json(service.state_dir, 'artifacts/candidate-result.json',
        {**identity, 'schema_version': 'verification-result.v1', 'verdict': 'blocked', 'target_commit': head},
        kind='test', schema_version='verification-result.v1', created_by='test')
    for kind, extra in [('workflow.operation.requested', {'request_ref': req}),
                        ('fanout.child.dispatched', {}),
                        ('workflow.call.result.admitted', {'semantic_verdict': 'blocked', 'control_result_ref': result}),
                        ('workflow.operation.settled', {})]:
        service.writer.append(ZfEvent(type=kind, payload={**identity, **extra}))
    return service, root, identity, frozen


def test_candidate_probe_executes_on_exact_detached_target_without_product_verdict(candidate):
    service, root, _, _ = candidate
    manifest = preview(service, operation_id='candidate-1', kind='verify', command_id='contract-verification')
    assert manifest['branch'] == 'HEAD'
    result = approve(service, manifest)['result']
    assert result['exit_code'] == 0 and result['source_stable']
    assert result['target_commit'] == git(root, 'rev-parse', 'HEAD')
    assert result['operation_id'] == 'candidate-1'
    assert result['product_verdict'] == 'not_evaluated'


@pytest.mark.parametrize('change', ['source', 'branch', 'target', 'generation', 'receipt', 'new_operation', 'paused', 'commit'])
def test_candidate_probe_rejects_changed_authority_or_source_write(candidate, change):
    service, root, identity, frozen = candidate
    if change == 'source': (root / 'probe.py').write_text("print('changed')\n")
    if change == 'branch': subprocess.run(['git', '-C', str(root), 'checkout', 'worker-test'], check=True, capture_output=True)
    if change in {'target', 'generation', 'receipt'}:
        newer = {**frozen, 'candidate_head_commit': 'f' * 40} if change == 'target' else dict(frozen)
        if change == 'generation': newer['task_map_generation'] = 'next-map'
        service.writer.append(ZfEvent(type='candidate.ready', payload=newer))
    if change == 'new_operation':
        service.writer.append(ZfEvent(type='workflow.operation.requested', payload={**identity, 'operation_id': 'next-reader'}))
    if change == 'paused': service.writer.emit('run.paused', payload={'workflow_run_id': 'run-1'})
    with pytest.raises(ValueError):
        preview(service, operation_id='candidate-1', kind='commit' if change == 'commit' else 'verify',
                paths=['probe.py'] if change == 'commit' else [], command_id='contract-verification')
