from dataclasses import asdict
from tests.test_reader_stage_recovery import recovery
from tests.test_host_verification import policy
from zf.runtime.sidecar_refs import write_sidecar_json
from zf.runtime.task_host_manifest import digest
from zf.runtime.host_verification_handoff import resume_reader_after_probes


def test_completed_probe_routes_through_controlled_retry_once(recovery):
    service, failure, original = recovery
    p = policy()
    operation={'operation_id':'op-reader','workflow_run_id':'run-1'}
    service.writer.emit('fanout.child.dispatched', payload={**operation,'fanout_id':'f1'})
    # A newer failure after the last dispatch owns the retry.
    failure=service.writer.emit(failure.type, correlation_id='run-1', payload=failure.payload)
    ref=write_sidecar_json(service.state_dir,'artifacts/probe.json',{
        'command_id':'probe','status':'completed','source_stable':True,'exit_code':0,
        'execution_origin':'preauthorized_container','policy_digest':digest(asdict(p))},
        kind='test',schema_version='test.v1',created_by='test')
    service.writer.emit('runtime.action.attempt.completed',payload={**operation,'approval_ref':p.approval_ref,
        'kind':'verify','receipt_ref':ref})
    contract={'verification_commands':[{'command_id':'probe','command':'python3 probe.py'}]}
    assert resume_reader_after_probes(service,operation,contract,p,service.writer.event_log.read_all())
    assert not resume_reader_after_probes(service,operation,contract,p,service.writer.event_log.read_all())
    trigger=[e for e in service.writer.event_log.read_all() if e.type==original.type][-1]
    assert trigger.payload['repaired_failure_event_id']==failure.id
    assert 'probe.json' in trigger.payload['operator_feedback'][0]
    assert not any(e.type=='test.passed' for e in service.writer.event_log.read_all())
