"""The first Reader recovery must validate retained media before redispatch."""
import pytest

from tests.test_host_evidence_media import media_receipt
from tests.test_reader_stage_recovery import recovery
from zf.runtime.host_verification_handoff import resume_reader_after_probes
from zf.runtime.sidecar_refs import write_sidecar_json


@pytest.mark.parametrize('request_bound', [False, True])
@pytest.mark.parametrize('producer_paths, expected', [
    (['ZF_EVIDENCE_DIR/desktop.png'], True),
    (['artifacts/evidence/browser/'], False),
    (['missing.png'], False),
])
def test_first_handoff_requires_matching_media_before_controlled_retry(
    recovery, request_bound, producer_paths, expected,
):
    service, failure, original = recovery
    policy, receipt = media_receipt(service.state_dir)
    operation = {'operation_id': 'op-reader', 'workflow_run_id': 'run-1'}
    if request_bound:
        operation['request_ref'] = write_sidecar_json(
            service.state_dir, 'artifacts/request.json', {'request': {}},
            kind='call_request', schema_version='call-request.v1', created_by='test',
        )
    writer = service.writer
    writer.emit('fanout.child.dispatched', payload={**operation, 'fanout_id': 'f1'})
    writer.emit(failure.type, correlation_id='run-1', payload=failure.payload)
    writer.emit('runtime.action.attempt.completed', payload={**operation,
        'approval_ref': policy.approval_ref, 'kind': 'verify', 'receipt_ref': receipt})
    contract = {'verification_commands': [{'command_id': 'probe',
        'command': 'python3 probe.py', 'producer_paths': producer_paths}]}
    original_receipt = (service.state_dir / receipt['ref']).read_bytes()

    assert resume_reader_after_probes(
        service, operation, contract, policy, writer.event_log.read_all(),
    ) is expected
    # Unchanged evidence cannot spin another Reader or another host command.
    assert not resume_reader_after_probes(
        service, operation, contract, policy, writer.event_log.read_all(),
    )
    events = writer.event_log.read_all()
    assert len([event for event in events if event.type == original.type]) == 1 + int(expected)
    assert not any(event.type in {'test.passed', 'task.done'} or (
        event.type == 'runtime.action.attempt.started' and event.payload.get('kind') == 'verify'
    ) for event in events)
    assert (service.state_dir / receipt['ref']).read_bytes() == original_receipt
