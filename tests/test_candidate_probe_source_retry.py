"""New candidate evidence must wake its failed producer while sibling Readers work."""
from dataclasses import asdict

import pytest

from tests.test_host_verification import policy
from tests.test_reader_stage_recovery import recovery
from tests.test_reader_settled_failure_rebuild import failed_reader
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.host_verification_handoff import resume_reader_after_probes
from zf.runtime.sidecar_refs import hydrate_sidecar_ref, write_sidecar_json
from zf.runtime.task_host_manifest import digest


@pytest.mark.parametrize('case', ['success', 'crash_after_intent', 'partial', 'wrong_target',
                                  'unavailable_input', 'paused', 'missing_media', 'prior_effect'])
def test_only_one_bound_producer_retries_before_the_candidate_barrier(tmp_path, case, monkeypatch):
    cls = 'reader_execution_failure' if case == 'unavailable_input' else 'verifier_execution_failure'
    state, log, transport, runtime, exact, failed, op = failed_reader(
        tmp_path, pending_sibling=True, failure_class=cls)
    service = ControlledActionService(state, runtime.event_writer, config=runtime.config,
                                      project_root=runtime.project_root, actor='run-manager')
    req = hydrate_sidecar_ref(state, op['request_ref']).payload['request']
    policy_config = policy()
    contract = {'authority_scope': 'candidate', 'verification_commands': [{
        'command_id': 'probe', 'command': 'python3 probe.py',
        'producer_paths': ['missing.png'] if case == 'missing_media' else []}]}
    receipt = write_sidecar_json(state, 'artifacts/producer.json', {
        'operation_id': op['operation_id'], 'workflow_run_id': op['workflow_run_id'],
        'active_attempt_id': op['active_attempt_id'], 'request_hash': op['request_hash'],
        'target_commit': 'x' if case == 'wrong_target' else req['target_commit'],
        'manifest': {'contract_digest': req['contract_snapshot_digest']},
        'command_id': 'probe', 'status': 'completed', 'source_stable': True, 'exit_code': 0,
        'execution_origin': 'preauthorized_container', 'policy_digest': digest(asdict(policy_config))
    }, kind='task_host_receipt', schema_version='task-host-receipt.v1', created_by='run-manager')
    if case != 'partial':
        runtime.event_writer.emit('runtime.action.attempt.completed', payload={
            'action': 'task-host-assist', 'kind': 'verify', 'operation_id': op['operation_id'],
            'approval_ref': policy_config.approval_ref, 'receipt_ref': receipt})
    if case == 'paused':
        runtime.event_writer.emit('run.paused', payload={'workflow_run_id': op['workflow_run_id']})
    if case == 'prior_effect':
        from zf.runtime.reader_rebuild_admission import reader_rebuild_key
        runtime.event_writer.emit('repair.action.requested', payload={
            'idempotency_key': reader_rebuild_key(log.read_all(), exact), 'kind': 'rerun_fanout_child'})
    def recover():
        return resume_reader_after_probes(service, op, contract, policy_config, log.read_all())

    if case == 'crash_after_intent':
        with monkeypatch.context() as scoped:
            def crash(**kwargs):
                raise RuntimeError('interrupted before applying the intent')
            scoped.setattr(service, 'execute', crash)
            with pytest.raises(RuntimeError, match='interrupted'):
                recover()
    fresh = case in {'success', 'crash_after_intent'}
    assert recover() is fresh
    assert not recover()
    requests = [e for e in log.read_all() if e.type == 'control.action.requested'
                and e.payload.get('action') == 'child-rebuild']
    assert len(requests) == int(fresh)
    if fresh:
        assert requests[0].causation_id == failed.id
        repairs = [e for e in log.read_all() if e.type == 'repair.action.requested']
        assert len(repairs) == 1 and all(repairs[0].payload[k] == v for k, v in exact.items())
        before = len(transport.sent)
        runtime.run_once(events=repairs)
        assert len(transport.sent) == before + 1
        assert not recover()
        dispatch = [e.payload for e in log.read_all() if e.type == 'fanout.child.dispatched']
        assert [d['child_id'] for d in dispatch] == ['verify-1', 'verify-2', 'verify-1']
        assert dispatch[-1]['payload']['retry_of_operation_id'] == op['operation_id']
    assert not any(e.type in {'test.failed', 'test.passed', 'task.done'} for e in log.read_all())


@pytest.mark.parametrize('difference', ['', 'run', 'fanout', 'target', 'contract', 'policy', 'origin'])
def test_sibling_cannot_repeat_previously_transferred_container_effects(recovery, monkeypatch, difference):
    from zf.core.events import ZfEvent
    from zf.runtime import host_verification as host
    service, _, _ = recovery
    p = policy()
    service.config.runtime.host_verification = p
    service.writer.default_origin = 'kernel'
    receipt = write_sidecar_json(service.state_dir, 'artifacts/unavailable.json',
        {'failure_class': 'verifier_execution_failure'}, kind='test', schema_version='test.v1', created_by='test')
    original = {'workflow_run_id': 'run-1', 'operation_id': 'op-other', 'status': 'settled',
                'semantic_verdict': 'abstained', 'admitted_control_result_ref': receipt}
    request = {'fanout_id': 'f1', 'target_commit': 'head',
               'result_identity': {'contract_snapshot_digest': 'digest'}}
    handoff = {'target_commit': 'head', 'contract_snapshot_digest': 'digest',
               'policy_digest': digest(asdict(p)), 'command_receipts': {'probe': receipt['sha256']}}
    source = {'action': 'child-rebuild', 'workflow_run_id': 'run-1', 'fanout_id': 'f1',
              'source_event_id': 'failed', 'checkpoint_id': 'failed', 'host_verification': handoff}
    if difference in {'run', 'fanout'}:
        source['workflow_run_id' if difference == 'run' else 'fanout_id'] = 'changed'
    if difference in {'target', 'contract', 'policy'}:
        handoff[{'target':'target_commit', 'contract':'contract_snapshot_digest', 'policy':'policy_digest'}[difference]] = 'changed'
    service.writer.append(ZfEvent(type='control.action.requested', actor='run-manager',
        origin='worker' if difference == 'origin' else 'kernel', causation_id='failed', payload=source))
    contract = {'authority_scope': 'candidate',
                'verification_commands': [{'command_id':'probe', 'command':'python3 probe.py'}]}
    monkeypatch.setattr(host, 'reduce_workflow_operations', lambda events: {'op-other': original})
    monkeypatch.setattr(host, 'binding', lambda *args: (original, request, contract, service.project_root))
    monkeypatch.setattr(host, 'preview', lambda *args, **kw: {'manifest_digest':'sibling-manifest'})
    effects = []
    monkeypatch.setattr(host, 'apply', lambda *args, **kw: effects.append('executed'))
    assert bool(host.reconcile_host_verification(service)) is bool(difference)
    assert len(effects) == int(bool(difference))
