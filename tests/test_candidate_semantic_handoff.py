"""Real RM handoff and aggregate Candidate authority, without a fake TaskAttempt."""
from dataclasses import asdict

import pytest

from tests.test_candidate_plan_recovery import _incident
from tests.test_host_verification import policy
from tests.test_reader_stage_recovery import recovery
from zf.core.config.schema import WorkflowOrchestrationConfig
from zf.runtime.host_verification_handoff import resume_reader_after_probes
from zf.runtime.orchestrator_agent_semantic_failure import request_semantic_failure_checkpoint
from zf.runtime.run_manager import _execute_orchestrator_triage_request
from zf.runtime.run_manager_rework_triage import pending_rework_triage_actions
from zf.runtime.sidecar_refs import write_sidecar_json
from zf.runtime.task_host_manifest import digest


def _cap_action(runtime, request):
    runtime.event_writer.default_origin = 'kernel'
    runtime.event_writer.emit('candidate.rework.capped', task_id='TASK-1', payload={
        'workflow_run_id': 'run-semantic-1', 'flow_kind': 'prd', 'failure_scope': 'candidate',
        'pdd_id': 'TASK-1', 'trace_id': 'run-semantic-1', 'semantic_triage_required': True,
        'failure_count': 1, 'failure_event_ids': request.payload['failure_event_ids'],
        'trigger_event_type': 'test.failed', 'failure_fingerprint': 'candidate-failure',
    })
    return pending_rework_triage_actions(runtime.event_log.read_all(), threshold=1, stale_seconds=300)[0]


def test_candidate_cap_enters_semantic_checkpoint_without_anchor_attempt(tmp_path):
    runtime, original, _, _ = _incident(tmp_path)
    action = _cap_action(runtime, original)
    assert _execute_orchestrator_triage_request(writer=runtime.event_writer, action=action,
        causation_id='tick', state_dir=runtime.state_dir, config=runtime.config) == 'applied'
    request = next(e for e in reversed(runtime.event_log.read_all())
                   if e.type == 'orchestrator.semantic.failure.requested')
    assert not runtime.task_store.get('TASK-1').active_dispatch_id
    prepared = request_semantic_failure_checkpoint(runtime, request)
    context = prepared.context.input_body['checkpoint_context']
    assert context['target_attempt_id'] == 'reader-attempt'
    assert context['target_role_instance'] == 'verify-0'
    assert context['candidate_failure_scope'] == 'aggregate'


@pytest.mark.parametrize('semantic', [True, False])
def test_rm_post_verify_follows_selected_semantic_route(tmp_path, semantic):
    runtime, original, _, _ = _incident(tmp_path)
    if not semantic:
        runtime.config.workflow.orchestration = WorkflowOrchestrationConfig()
    action = _cap_action(runtime, original)
    _execute_orchestrator_triage_request(writer=runtime.event_writer, action=action,
        causation_id='tick', state_dir=runtime.state_dir, config=runtime.config)
    events = runtime.event_log.read_all()
    assert not any(e.type == 'run.manager.action.verify.failed' for e in events)
    checked = [e for e in events if e.type == 'run.manager.action.verify.passed']
    assert len(checked) == 1
    expected = 'orchestrator.semantic.failure.requested' if semantic else 'orchestrator.rework.triage.requested'
    assert expected in checked[0].payload['observed_event_types']


@pytest.mark.parametrize('failure_kind', ['mixed', 'quality', 'unknown'])
def test_complete_host_evidence_does_not_request_infra_retry_for_other_failures(recovery, failure_kind):
    service, failure, _ = recovery
    p = policy()
    operation = {'operation_id': 'op-reader', 'workflow_run_id': 'run-1'}
    service.writer.emit('fanout.child.dispatched', payload={**operation, 'fanout_id': 'f1'})
    service.writer.emit(failure.type, correlation_id='run-1',
                        payload={**failure.payload, 'failure_kind': failure_kind})
    ref = write_sidecar_json(service.state_dir, 'artifacts/host-complete.json', {
        'command_id': 'probe', 'status': 'completed', 'source_stable': True, 'exit_code': 0,
        'execution_origin': 'preauthorized_container', 'policy_digest': digest(asdict(p)),
    }, kind='test', schema_version='test.v1', created_by='test')
    service.writer.emit('runtime.action.attempt.completed', payload={**operation,
        'approval_ref': p.approval_ref, 'kind': 'verify', 'receipt_ref': ref})
    contract = {'verification_commands': [{'command_id': 'probe', 'command': 'python3 probe.py'}]}
    before = len(service.writer.event_log.read_all())
    for _ in range(2):
        assert not resume_reader_after_probes(service, operation, contract, p, service.writer.event_log.read_all())
    assert len(service.writer.event_log.read_all()) == before


def _peer_failure(runtime, original, child, *, verdict='abstained'):
    from copy import deepcopy
    from zf.core.events import ZfEvent
    from zf.runtime.call_result_envelope import write_immutable_json_sidecar
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref
    base = hydrate_sidecar_ref(runtime.state_dir, child['control_result_ref']).payload
    result = deepcopy(base)
    result.update(operation_id='peer-operation', role_instance='verify-peer', attempt_id='peer-attempt',
                  active_attempt_id='peer-attempt', verdict=verdict,
                  execution_status='failed' if verdict == 'abstained' else 'completed')
    ref = write_immutable_json_sidecar(runtime.state_dir, result, root='fixtures/peer',
        kind='call_control_result', schema_version='verification-result.v1', created_by='test')
    op = {**child, 'operation_id': 'peer-operation', 'role_instance': 'verify-peer',
          'active_attempt_id': 'peer-attempt', 'child_id': 'verify-peer',
          'control_result_ref': ref, 'verification_result': result}
    def emit(kind, payload, cause=None):
        return runtime.event_writer.append(ZfEvent(type=kind, origin='kernel', task_id='TASK-1',
            correlation_id='run-semantic-1', causation_id=cause, payload=payload))
    emit('workflow.operation.requested', op)
    emit('workflow.call.result.admitted', {**op, 'semantic_verdict': verdict})
    emit('workflow.operation.settled', op)
    emit('fanout.child.failed', op)
    aggregate = emit('fanout.aggregate.completed', {**op, 'status': 'failed',
        'failed_children': ['verify-0', 'verify-peer']})
    failure = emit('test.failed', {'workflow_run_id': 'run-semantic-1', 'pdd_id': 'TASK-1',
        'fanout_id': 'verify-fanout', 'task_map_generation': 'g1'}, aggregate.id)
    original.payload.update(recovery_scope='candidate', failure_event_ids=[failure.id])
    original.payload.pop('target_operation_id')
    return original, ref


def test_aggregate_preserves_abstention_and_rejection_then_applies_bound_plan_request(tmp_path):
    from tests.test_orchestrator_agent_semantic_failure import _submit_rework
    from zf.runtime.orchestrator_agent_decision_apply import apply_orchestrator_agent_decision
    runtime, request, _, child = _incident(tmp_path)
    request, ref = _peer_failure(runtime, request, child)
    prepared = request_semantic_failure_checkpoint(runtime, request)
    sources = prepared.context.input_body['sources']
    assert {s['source_id'] for s in sources} >= {
        'candidate-verification-verify-0', 'candidate-verification-verify-peer'}
    assert any(s.get('ref') == ref['ref'] for s in sources)
    from tests.test_candidate_mixed_repair import partition

    dispositions = partition(runtime, request)
    decision = _submit_rework(runtime, prepared, action='return_to_plan',
                              directive_fields={'finding_dispositions': dispositions})
    outcome = apply_orchestrator_agent_decision(runtime, decision)
    assert outcome['applied']
    requests = [e for e in runtime.event_log.read_all() if e.type == 'orchestrator.replan_requested']
    assert len(requests) == 1
    assert requests[0].payload['failed_task_ids'] == ['TASK-2']
    assert not any(e.type in {'test.passed', 'task.done'} for e in runtime.event_log.read_all())


@pytest.mark.parametrize('corruption', ['member_digest', 'member_operation', 'superseded_candidate', 'unknown_member'])
def test_aggregate_does_not_ignore_invalid_peer(tmp_path, corruption):
    from zf.core.events import ZfEvent
    from zf.runtime.orchestrator_agent_semantic_failure import SemanticFailureCheckpointError
    runtime, request, candidate, child = _incident(tmp_path)
    request, ref = _peer_failure(runtime, request, child)
    if corruption == 'member_digest':
        (runtime.state_dir / ref['ref']).write_text('{}')
    elif corruption == 'member_operation':
        runtime.event_writer.append(ZfEvent(type='workflow.operation.superseded', origin='kernel',
            payload={'operation_id': 'peer-operation', 'workflow_run_id': 'run-semantic-1'}))
    elif corruption == 'superseded_candidate':
        runtime.event_writer.append(ZfEvent(type=candidate.type, origin='kernel', payload=candidate.payload))
    else:
        failure = runtime.event_log.read_all()[-1]
        aggregate = runtime.event_writer.append(ZfEvent(type='fanout.aggregate.completed', origin='kernel',
            payload={'fanout_id': 'verify-fanout', 'status': 'failed',
                     'failed_children': ['verify-0', 'verify-peer', 'missing']}))
        failure = runtime.event_writer.append(ZfEvent(type=failure.type, origin='kernel',
            payload=failure.payload, causation_id=aggregate.id))
        request.payload['failure_event_ids'] = [failure.id]
    with pytest.raises(SemanticFailureCheckpointError):
        request_semantic_failure_checkpoint(runtime, request)


@pytest.mark.parametrize('fault', ['', 'unadmitted', 'untrusted', 'foreign_run', 'changed_target'])
def test_candidate_retrigger_binding_requires_exact_kernel_admission(tmp_path, fault):
    from copy import deepcopy
    from zf.core.events import ZfEvent
    from zf.runtime.candidate_failure_checkpoint import aggregate_candidate_binding
    runtime, request, candidate, child = _incident(tmp_path)
    events = runtime.event_log.read_all()
    successor = next(e for e in events if e.id == candidate.id)
    parent = ZfEvent(type=candidate.type, origin='kernel', task_id=candidate.task_id,
                    correlation_id=candidate.correlation_id, payload=deepcopy(candidate.payload))
    events.insert(events.index(successor), parent)
    successor.origin = ''
    successor.payload.update(redrive_of=parent.id, rework_of=parent.id)
    start = next(e for e in events if e.type == 'fanout.started')
    if fault == 'unadmitted': events.remove(start)
    elif fault == 'untrusted': start.origin = 'external'
    elif fault == 'foreign_run': start.payload['workflow_run_id'] = 'foreign'
    elif fault == 'changed_target': parent.payload['candidate_head_commit'] = 'd' * 40
    def resolve():
        return aggregate_candidate_binding(runtime.state_dir, events,
            failure_id=request.payload['failure_event_ids'][-1], run_id='run-semantic-1', task_id='TASK-1')
    if fault:
        with pytest.raises(ValueError): resolve()
    else:
        result = resolve()
        assert result['candidate_event_id'] == candidate.id
        assert result['trigger_event_id'] == candidate.id


def test_aggregate_apply_revalidates_all_member_refs(tmp_path):
    from tests.test_orchestrator_agent_semantic_failure import _submit_rework
    from zf.runtime.orchestrator_agent_decision_apply import apply_orchestrator_agent_decision
    runtime, request, _, child = _incident(tmp_path)
    request, ref = _peer_failure(runtime, request, child)
    prepared = request_semantic_failure_checkpoint(runtime, request)
    decision = _submit_rework(runtime, prepared, action='return_to_plan')
    (runtime.state_dir / ref['ref']).write_text('{}')
    assert not apply_orchestrator_agent_decision(runtime, decision)['applied']
    assert not any(e.type == 'prd.plan.requested' for e in runtime.event_log.read_all())


@pytest.mark.parametrize('size_allowed', [False, True])
def test_candidate_checkpoint_includes_scoped_new_host_facts(tmp_path, size_allowed):
    import base64
    import hashlib
    from tests.test_host_evidence_media import PNG
    from zf.core.events import ZfEvent
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref
    runtime, request, _, child = _incident(tmp_path)
    request, _ = _peer_failure(runtime, request, child)
    runtime.config.runtime.host_verification.image = 'bound-image'
    if not size_allowed:
        runtime.config.runtime.host_verification.max_artifact_bytes = 1
    evidence = write_sidecar_json(runtime.state_dir, 'artifacts/fixtures/media.json', {
        'image': 'bound-image', 'target_commit': 'c' * 40, 'files': [{
            'path': 'desktop.png', 'sha256': hashlib.sha256(PNG).hexdigest(),
            'base64': base64.b64encode(PNG).decode(),
        }]}, kind='container_evidence', schema_version='container-evidence.v1', created_by='test')
    for op, commit in [('peer-operation', 'c' * 40), ('foreign-operation', 'd' * 40)]:
        ref = write_sidecar_json(runtime.state_dir, f'artifacts/fixtures/{op}.json', {
            'schema_version': 'task-host-receipt.v1', 'workflow_run_id': 'run-semantic-1',
            'operation_id': op, 'kind': 'verify', 'target_commit': commit,
            'command_id': 'browser', 'command': 'node tests/browser.cjs',
            'status': 'completed', 'exit_code': 0, 'source_stable': True,
            'image': 'bound-image', 'evidence_ref': evidence,
            'execution_origin': 'preauthorized_container'}, kind='task_host_receipt',
            schema_version='task-host-receipt.v1', created_by='run-manager')
        runtime.event_writer.append(ZfEvent(type='runtime.action.attempt.completed', origin='kernel',
            payload={'action': 'task-host-assist', 'operation_id': op, 'workflow_run_id': 'run-semantic-1',
                     'kind': 'verify', 'receipt_ref': ref}))
    prepared = request_semantic_failure_checkpoint(runtime, request)
    source = next(s for s in prepared.context.input_body['sources'] if s['source_id'] == 'semantic-failure-input')
    body = hydrate_sidecar_ref(runtime.state_dir, source).payload
    effects = body['preauthorized_host_recovery']['effects']
    assert len(effects) == 1
    assert effects[0]['operation_id'] == 'peer-operation'
    assert effects[0]['command_id'] == 'browser'
    assert effects[0]['target_commit'] == 'c' * 40
    assert effects[0]['receipt_status'] == 'observed'
    assert body['candidate_verify']['permitted_recovery_actions'] == ['return_to_plan', 'rework']
    assert 'candidate_repair' in body['candidate_verify']['rework_contract']
    media = [s for s in prepared.context.input_body['sources']
             if s['source_id'].startswith('candidate-host-media-')]
    if size_allowed:
        assert len(media) == 1
        assert 'host_media_unavailable' not in body
    else:
        assert not media
        assert 'limit' in body['host_media_unavailable']
