"""Isolated reproduction of K5 input-failure -> unrelated host-probe routing."""
import pytest
from tests.test_candidate_host_binding import candidate, case
from tests.test_host_verification import policy
from zf.core.events import ZfEvent
from zf.runtime.sidecar_refs import hydrate_sidecar_ref, write_sidecar_json
from zf.runtime.workflow_operation import reduce_workflow_operations
from zf.runtime.host_verification import reconcile_host_verification


@pytest.mark.parametrize('failure_class,expected_calls', [
    ('reader_execution_failure', 0),
    ('verifier_execution_failure', 1),
])
def test_host_probe_matches_execution_failure_not_input_delivery(candidate, monkeypatch, failure_class, expected_calls):
    service, root, identity, frozen = candidate
    service.config.runtime.host_verification = policy()
    op = reduce_workflow_operations(service.writer.event_log.read_all())['candidate-1']
    body = dict(hydrate_sidecar_ref(service.state_dir, op['admitted_control_result_ref']).payload)
    body.update(execution_status='failed', verdict='abstained', failure_class=failure_class,
                summary='Required artifact output truncated' if expected_calls == 0 else 'Docker socket denied')
    ref = write_sidecar_json(service.state_dir, 'artifacts/typed-candidate-failure.json', body,
        kind='test', schema_version='verification-result.v1', created_by='test')
    service.writer.append(ZfEvent(type='workflow.call.result.admitted', payload={
        **identity, 'semantic_verdict': 'abstained', 'control_result_ref': ref}))
    service.writer.append(ZfEvent(type='workflow.operation.settled', payload=identity))
    calls = []
    def verify(*args):
        calls.append(args)
        return {'status': 'completed', 'source_stable': True, 'exit_code': 0,
                'execution_origin': 'preauthorized_container'}, 'mock probe output'
    monkeypatch.setattr('zf.runtime.host_verification.verify_in_container', verify)
    reconcile_host_verification(service)
    assert len(calls) == expected_calls
