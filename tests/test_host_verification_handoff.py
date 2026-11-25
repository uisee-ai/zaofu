from dataclasses import asdict
import pytest
from tests.test_reader_stage_recovery import recovery
from tests.test_host_verification import policy
from zf.runtime.sidecar_refs import write_sidecar_json
from zf.runtime.task_host_manifest import digest
from zf.runtime.host_verification_handoff import resume_reader_after_probes


@pytest.mark.parametrize('increase_budget', [False, True])
@pytest.mark.parametrize(('status', 'exit_code', 'stable', 'expected'), [
    ('completed', 0, True, True), ('failed', 1, True, True), ('failed', 125, True, True),
    ('failed', 1, False, False), ('started', None, True, False), ('failed', None, True, False),
])
def test_completed_probe_routes_through_controlled_retry_once(recovery, status, exit_code, stable, expected, increase_budget):
    service, failure, original = recovery
    p = policy()
    operation={'operation_id':'op-reader','workflow_run_id':'run-1'}
    service.writer.emit('fanout.child.dispatched', payload={**operation,'fanout_id':'f1'})
    # A newer failure after the last dispatch owns the retry.
    failure=service.writer.emit(failure.type, correlation_id='run-1', payload=failure.payload)
    ref=write_sidecar_json(service.state_dir,'artifacts/probe.json',{
        'command_id':'probe','status':status,'source_stable':stable,'exit_code':exit_code,
        'execution_origin':'preauthorized_container','policy_digest':digest(asdict(p))},
        kind='test',schema_version='test.v1',created_by='test')
    service.writer.emit('runtime.action.attempt.completed',payload={**operation,'approval_ref':p.approval_ref,
        'kind':'verify','receipt_ref':ref})
    contract={'verification_commands':[{'command_id':'probe','command':'python3 probe.py'}]}
    if increase_budget:
        p.max_executions_per_run = 120
    assert resume_reader_after_probes(service,operation,contract,p,service.writer.event_log.read_all()) is expected
    if not expected:
        assert not any(e.payload.get('repaired_failure_event_id') == failure.id for e in service.writer.event_log.read_all())
        return
    assert not resume_reader_after_probes(service,operation,contract,p,service.writer.event_log.read_all())
    trigger=[e for e in service.writer.event_log.read_all() if e.type==original.type][-1]
    assert trigger.payload['repaired_failure_event_id']==failure.id
    assert 'probe.json' in trigger.payload['operator_feedback'][0]
    assert not any(e.type=='test.passed' for e in service.writer.event_log.read_all())


@pytest.mark.parametrize(('status','code','stable','command_id','expected'), [
    ('failed',125,True,'first',True), ('failed',1,True,'first',True), ('completed',0,True,'first',False),
    ('failed',125,False,'first',False), ('failed',None,True,'first',False), ('failed',125,True,'outside',False),
])
def test_failed_probe_can_return_to_reader_without_running_remaining_commands(recovery,status,code,stable,command_id,expected):
    service,failure,_=recovery
    p=policy();p.commands.append('python3 later.py')
    operation={'operation_id':'op-reader','workflow_run_id':'run-1'}
    service.writer.emit('fanout.child.dispatched',payload={**operation,'fanout_id':'f1'})
    service.writer.emit(failure.type,correlation_id='run-1',payload=failure.payload)
    ref=write_sidecar_json(service.state_dir,'artifacts/first-probe.json',{
        'command_id':command_id,'status':status,'source_stable':stable,'exit_code':code,
        'execution_origin':'preauthorized_container','policy_digest':digest(asdict(p))},
        kind='test',schema_version='test.v1',created_by='test')
    service.writer.emit('runtime.action.attempt.completed',payload={**operation,'approval_ref':p.approval_ref,
        'kind':'verify','receipt_ref':ref})
    contract={'verification_commands':[{'command_id':'first','command':'python3 probe.py'},
                                       {'command_id':'later','command':'python3 later.py'}]}
    assert resume_reader_after_probes(service,operation,contract,p,service.writer.event_log.read_all()) is expected
    assert not any(e.type in {'test.passed','task.done'} for e in service.writer.event_log.read_all())


@pytest.mark.parametrize("interruption", ["supersession", "unknown_denial", "request_only"])
def test_host_handoff_recovers_one_exact_request_after_its_gate_clears(recovery, monkeypatch, interruption):
    from zf.runtime import reader_stage_recovery

    service, failure, trigger = recovery
    service.writer.default_origin = "kernel"
    p = policy()
    operation = {"workflow_run_id": "run-1", "operation_id": "op-reader"}
    service.writer.emit("fanout.child.dispatched", payload={**operation, "fanout_id": "f1"})
    failure = service.writer.emit(failure.type, correlation_id="run-1", payload=failure.payload)
    ref = write_sidecar_json(service.state_dir, "artifacts/probe.json", {
        "command_id": "probe", "status": "completed", "source_stable": True,
        "exit_code": 0, "execution_origin": "preauthorized_container", "policy_digest": digest(asdict(p)),
    }, kind="test", schema_version="test.v1", created_by="test")
    service.writer.emit("runtime.action.attempt.completed", payload={
        **operation, "approval_ref": p.approval_ref, "kind": "verify", "receipt_ref": ref,
    })
    contract = {"verification_commands": [{"command_id": "probe", "command": "python3 probe.py"}]}

    def reconcile():
        return resume_reader_after_probes(service, operation, contract, p, service.writer.event_log.read_all())

    def denial(*args):
        reason = reader_stage_recovery.SUPERSEDED_FANOUT_REASON if interruption == "supersession" else "unclassified"
        raise ValueError(reason)

    with monkeypatch.context() as scoped:
        if interruption == "request_only":
            scoped.setattr(service, "execute", lambda **kwargs: {"ok": False})
        else:
            scoped.setattr(reader_stage_recovery, "repaired_reader_stage_trigger", denial)
        assert not reconcile()
        assert not reconcile()

    should_resume = interruption != "unknown_denial"
    assert reconcile() is should_resume
    assert not reconcile()
    events = service.writer.event_log.read_all()
    requests = [e for e in events if e.type == "control.action.requested"]
    assert len(requests) == (2 if interruption == "supersession" else 1)
    if len(requests) == 2:
        assert requests[1].causation_id == requests[0].id and requests[1].payload == requests[0].payload
    effects = [e for e in events if e.type == trigger.type and e.payload.get("repaired_failure_event_id") == failure.id]
    assert len(effects) == int(should_resume)
    if effects:
        assert effects[0].causation_id == requests[-1].id
    assert not any(e.payload.get("action") == "task-host-assist" for e in events)


@pytest.mark.parametrize('invalid', ['', 'request_only', 'worker_ready', 'other_run', 'other_failure'])
def test_reconciler_skips_old_reader_only_after_kernel_successor(recovery, monkeypatch, invalid):
    from unittest.mock import Mock
    from zf.runtime import host_verification as module
    service, _, _ = recovery
    service.writer.default_origin = 'kernel'
    service.config.runtime.host_verification = policy()
    old = {'operation_id': 'old', 'workflow_run_id': 'run-1', 'status': 'settled', 'semantic_verdict': 'abstained'}
    successor = {**old, 'operation_id': 'new'}
    service.writer.emit('fanout.child.dispatched', payload={**old, 'fanout_id': 'failed-fanout'})
    failed = service.writer.emit('test.failed', payload={'fanout_id': 'failed-fanout', 'workflow_run_id': 'run-1'})
    service.writer.default_origin = 'worker' if invalid == 'worker_ready' else 'kernel'
    service.writer.emit('control.action.requested' if invalid == 'request_only' else 'candidate.ready',
        payload={
            'workflow_run_id': 'other' if invalid == 'other_run' else 'run-1',
            'repaired_failure_event_id': 'unrelated' if invalid == 'other_failure' else failed.id})
    monkeypatch.setattr(module, 'reduce_workflow_operations', lambda events: {'old': old, 'new': successor})
    binding = Mock(side_effect=ValueError('binding unavailable in this consumer test'))
    monkeypatch.setattr(module, 'binding', binding)
    assert module.reconcile_host_verification(service) == 0
    assert [call.args[1] for call in binding.call_args_list] == (['new'] if not invalid else ['old', 'new'])


def test_partial_allowlist_cannot_reopen_reader_as_if_all_commands_were_probed(recovery):
    service, failure, _ = recovery
    p = policy()
    operation = {'operation_id': 'op-reader', 'workflow_run_id': 'run-1'}
    service.writer.emit('fanout.child.dispatched', payload={**operation, 'fanout_id': 'f1'})
    failure = service.writer.emit(failure.type, correlation_id='run-1', payload=failure.payload)
    ref = write_sidecar_json(service.state_dir, 'artifacts/partial.json', {
        'command_id': 'probe', 'status': 'completed', 'source_stable': True, 'exit_code': 0,
        'execution_origin': 'preauthorized_container', 'policy_digest': digest(asdict(p))},
        kind='test', schema_version='test.v1', created_by='test')
    service.writer.emit('runtime.action.attempt.completed', payload={**operation,
        'approval_ref': p.approval_ref, 'kind': 'verify', 'receipt_ref': ref})
    contract = {'verification_commands': [
        {'command_id': 'probe', 'command': 'python3 probe.py'},
        {'command_id': 'not-authorized', 'command': 'python3 another.py'}]}
    for _ in range(2):
        assert not resume_reader_after_probes(service, operation, contract, p, service.writer.event_log.read_all())
    events = service.writer.event_log.read_all()
    assert not any(e.payload.get('repaired_failure_event_id') == failure.id for e in events)
    waits = [e for e in events if e.type == 'human.escalate']
    assert len(waits) == 1
    assert waits[0].payload['unverified_command_ids'] == ['not-authorized']
    assert p.commands == ['python3 probe.py']


@pytest.mark.parametrize('different', ['', 'fanout', 'target', 'contract', 'run'])
@pytest.mark.parametrize('late_failure', [False, True])
@pytest.mark.parametrize('ownership', ['receipt', 'unresolved'])
def test_completed_candidate_probe_owner_does_not_duplicate_sibling_effects(recovery, monkeypatch, different, late_failure, ownership):
    from zf.runtime import host_verification as module
    from zf.runtime import host_verification_handoff as handoff
    service, _, _ = recovery
    service.config.runtime.host_verification = policy()
    result = write_sidecar_json(service.state_dir, 'artifacts/environment.json',
        {'failure_class': 'environment'}, kind='test', schema_version='test.v1', created_by='test')
    first = {'operation_id': 'owner', 'workflow_run_id': 'run-1', 'status': 'settled',
             'semantic_verdict': 'abstained', 'admitted_control_result_ref': result}
    second = {**first, 'operation_id': 'sibling'}
    if different == 'run': second['workflow_run_id'] = 'run-2'
    contract = {'authority_scope': 'candidate', 'verification_commands': [
        {'command_id': 'probe', 'command': 'python3 probe.py'}]}
    requests = {op['operation_id']: {'fanout_id': 'fanout-1', 'target_commit': 'target-1',
        'result_identity': {'contract_snapshot_digest': 'contract-1'}} for op in (first, second)}
    if different in {'fanout', 'target'}:
        requests['sibling']['fanout_id' if different == 'fanout' else 'target_commit'] = 'changed'
    if different == 'contract': requests['sibling']['result_identity']['contract_snapshot_digest'] = 'changed'
    operations = {'owner': first, 'sibling': second}
    if late_failure:
        operations = {'sibling': second, 'owner': first}
    monkeypatch.setattr(module, 'reduce_workflow_operations', lambda events: operations)
    monkeypatch.setattr(module, 'binding', lambda service, oid: (operations[oid], requests[oid], contract, service.project_root))
    monkeypatch.setattr(handoff, 'completed_reader_probes', lambda service, op, *args:
        ({'probe'}, {'probe': result} if ownership == 'receipt' and op['operation_id'] == 'owner' else {}, False))
    service.writer.default_origin = 'kernel'
    service.writer.emit('runtime.action.attempt.completed' if ownership == 'receipt' else 'runtime.action.attempt.started',
        payload={'manifest_digest': 'owner', 'action':'task-host-assist', 'kind':'verify',
            'operation_id':'owner', 'workflow_run_id':'run-1', 'approval_ref':'owner:test'})
    monkeypatch.setattr(module, 'preview', lambda service, **kw:
        {'manifest_digest': kw['operation_id'], 'identity': {'workflow_run_id': operations[kw['operation_id']]['workflow_run_id']}})
    effects = []
    monkeypatch.setattr(module, 'apply', lambda service, manifest, **kw: effects.append(manifest['manifest_digest']))
    assert module.reconcile_host_verification(service) == bool(different)
    assert effects == (['sibling'] if different else [])
