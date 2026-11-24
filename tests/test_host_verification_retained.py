"""A failed successor does not authorize repeating already retained effects."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.test_candidate_host_consumption import evidence, prepare, consumer_result
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import reduce_workflow_operations
from zf.runtime import host_verification_retained as retained


@pytest.mark.parametrize('evidence', ['output-root'], indirect=True)
def test_container_output_root_reaches_real_consumer_admission(evidence):
    runtime, _, _, _, _, _ = evidence
    _, payload, service, kwargs = consumer_result(evidence)
    body = hydrate_sidecar_ref(runtime.state_dir, payload['candidate_host_evidence_ref']).payload
    assert body['receipts'][0]['status'] == 'available'
    assert service.validate_plan_candidate(**kwargs)['status'] == 'passed'
    assert service.submit(**kwargs).admitted_event_id


@pytest.mark.parametrize('path', ['ZF_EVIDENCE_DIR/../x', 'ZF_EVIDENCE_DIR//x',
                                  '/evidence/x', '$ZF_EVIDENCE_DIR/x', 'ZF_EVIDENCE_DIR/./x'])
def test_output_mapping_rejects_unsafe_or_undeclared_expansion(path):
    from zf.runtime.host_evidence_media import required_media_paths
    with pytest.raises(ValueError):
        required_media_paths({'producer_paths': [path]})


def setup_consumer(evidence, monkeypatch):
    runtime, seeded, _, _, receipt, _ = evidence
    prepared, payload = prepare(runtime, seeded, 'successor-reader')
    events = runtime.event_log.read_all()
    operation = reduce_workflow_operations(events)[prepared.operation_id]
    contract = hydrate_sidecar_ref(runtime.state_dir, {
        'ref': payload['contract_snapshot_ref'], 'sha256': payload['contract_snapshot_digest']}).payload
    service = SimpleNamespace(state_dir=runtime.state_dir, config=runtime.config,
                              writer=runtime.event_writer, project_root=runtime.state_dir)
    monkeypatch.setattr(retained, 'binding', lambda *a: (operation, payload, contract, runtime.state_dir))
    monkeypatch.setattr(retained, 'git', lambda *a: seeded['candidate_head'])
    monkeypatch.setattr(retained, 'source_files', lambda *a: [])
    declared_dependencies = hydrate_sidecar_ref(runtime.state_dir, receipt).payload['manifest']['dependency_digest']
    monkeypatch.setattr(retained, 'dependency_digest', lambda *a: declared_dependencies)
    return service, operation, contract, runtime.config.runtime.host_verification, events, receipt


@pytest.mark.parametrize('fault', ['', 'policy', 'dependency', 'files', 'head', 'receipt', 'acl'])
def test_retained_effects_require_bound_current_inputs_and_policy(evidence, monkeypatch, fault):
    service, operation, contract, policy, events, receipt = setup_consumer(evidence, monkeypatch)
    if fault == 'policy': policy.network = 'different'
    if fault == 'dependency': monkeypatch.setattr(retained, 'dependency_digest', lambda *a: 'changed')
    if fault == 'files': monkeypatch.setattr(retained, 'source_files', lambda *a: [{'path': 'changed'}])
    if fault == 'head': monkeypatch.setattr(retained, 'git', lambda *a: 'changed')
    if fault == 'receipt': (service.state_dir / receipt['ref']).write_text('{}')
    if fault == 'acl':
        monkeypatch.setattr(retained, 'require_artifact_access', Mock(side_effect=ValueError('denied')))
    result = retained.retained_reader_probes(service, operation, contract, policy, events)
    assert bool(result) is (not fault)
    if not fault:
        assert list(result.values()) == [receipt]


def test_real_reconciler_never_spends_again_for_inherited_effect(evidence, monkeypatch):
    from zf.runtime import host_verification as module
    from zf.runtime.sidecar_refs import write_sidecar_json
    service, operation, contract, policy, events, receipt = setup_consumer(evidence, monkeypatch)
    result = write_sidecar_json(service.state_dir, 'artifacts/blocked.json',
        {'failure_class': 'dependency_blocked'}, kind='test', schema_version='test.v1', created_by='test')
    operation.update(status='settled', semantic_verdict='blocked', admitted_control_result_ref=result)
    payload = retained._request(service.state_dir, operation)
    monkeypatch.setattr(module, 'reduce_workflow_operations', lambda events: {operation['operation_id']: operation})
    monkeypatch.setattr(module, 'binding', lambda *a: (operation, payload, contract, service.state_dir))
    execute = Mock(side_effect=AssertionError('retained effect must not execute again'))
    monkeypatch.setattr(module, 'apply', execute)
    for _ in range(3):
        assert module.reconcile_host_verification(service) == 0
    execute.assert_not_called()
    assert sum(e.type == 'runtime.action.attempt.started' for e in service.writer.event_log.read_all()) == 1


def test_same_available_input_cannot_drive_another_reader_generation(evidence, monkeypatch):
    service, operation, contract, policy, events, _ = setup_consumer(evidence, monkeypatch)
    receipts = retained.retained_reader_probes(service, operation, contract, policy, events)
    assert receipts
    assert not retained.reader_probe_inputs_improved(service, operation, contract, policy, receipts)


@pytest.mark.parametrize('evidence', ['output-root'], indirect=True)
def test_repaired_projection_improves_input_without_mutating_history(evidence, monkeypatch):
    # Freeze the real legacy namespace error, then restore the corrected code.
    from zf.runtime import candidate_host_consumption as consumption
    correct = consumption.required_media_paths
    with monkeypatch.context() as legacy:
        legacy.setattr(consumption, 'required_media_paths', lambda cmd: set(cmd['producer_paths']))
        service, operation, contract, policy, events, receipt = setup_consumer(evidence, monkeypatch)
    request_bytes = (service.state_dir / operation['request_ref']['ref']).read_bytes()
    receipts = retained.retained_reader_probes(service, operation, contract, policy, events)
    assert list(receipts.values()) == [receipt]
    assert consumption.required_media_paths is correct
    assert retained.reader_probe_inputs_improved(service, operation, contract, policy, receipts)
    assert (service.state_dir / operation['request_ref']['ref']).read_bytes() == request_bytes


@pytest.mark.parametrize('evidence', ['output-root'], indirect=True)
def test_repaired_input_hands_off_at_exhausted_budget_without_new_effect(evidence, monkeypatch):
    from zf.runtime import host_verification as module
    from zf.runtime import candidate_host_consumption as consumption
    from zf.runtime import host_verification_handoff as handoff
    from zf.runtime.sidecar_refs import write_sidecar_json
    with monkeypatch.context() as legacy:
        legacy.setattr(consumption, 'required_media_paths', lambda cmd: set(cmd['producer_paths']))
        service, operation, contract, policy, events, receipt = setup_consumer(evidence, monkeypatch)
    operation.update(status='settled', semantic_verdict='blocked', admitted_control_result_ref=
        write_sidecar_json(service.state_dir, 'artifacts/blocked.json', {'failure_class': 'dependency_blocked'},
            kind='test', schema_version='test.v1', created_by='test'))
    request = retained._request(service.state_dir, operation)
    service.writer.emit('fanout.child.dispatched', payload={
        'operation_id': operation['operation_id'], 'fanout_id': 'candidate-fanout'})
    failure = service.writer.emit('test.failed', payload={'fanout_id': 'candidate-fanout',
        'workflow_run_id': operation['workflow_run_id'], 'failure_kind': 'infra'})
    monkeypatch.setattr(handoff, 'reader_stage_for_failure', lambda *a: (SimpleNamespace(trigger='candidate.ready'), None))
    monkeypatch.setattr(module, 'reduce_workflow_operations', lambda events: {operation['operation_id']: operation})
    monkeypatch.setattr(module, 'binding', lambda *a: (operation, request, contract, service.state_dir))
    calls = []
    def controlled(**kwargs):
        calls.append(kwargs)
        service.writer.default_origin = 'kernel'
        service.writer.emit('candidate.ready', payload={
            'workflow_run_id': operation['workflow_run_id'], 'repaired_failure_event_id': failure.id})
        return {'ok': True}
    service.execute = controlled
    # The existing receipt policy remains valid; there is no remaining allowance.
    assert sum(e.type == 'runtime.action.attempt.started' for e in events) == 1
    # Simulate consumed attempts from other commands in the same Run.
    for i in range(policy.max_executions_per_run - 1):
        service.writer.emit('runtime.action.attempt.started', payload={
            'workflow_run_id': operation['workflow_run_id'], 'approval_ref': policy.approval_ref,
            'manifest_digest': f'unrelated-{i}'})
    execute = Mock(side_effect=AssertionError('handoff cannot execute containers'))
    monkeypatch.setattr(module, 'apply', execute)
    assert module.reconcile_host_verification(service, allow_execution=False,
        workflow_run_id=operation['workflow_run_id']) == 1
    assert module.reconcile_host_verification(service, allow_execution=False,
        workflow_run_id=operation['workflow_run_id']) == 0
    execute.assert_not_called()
    assert len(calls) == 1 and calls[0]['payload']['repaired_failure_event_id'] == failure.id
