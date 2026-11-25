"""Timeouts retain admitted findings without inventing a missing Reader verdict."""
from dataclasses import replace
import time

import pytest

from tests.test_candidate_plan_recovery import _incident
from tests.test_candidate_semantic_handoff import _peer_failure
from tests.test_fanout_hardening import _state, _start
from tests.test_orchestrator_agent_semantic_failure import _submit_rework
from zf.core.events import ZfEvent
from zf.runtime.orchestrator_agent_decision_apply import apply_orchestrator_agent_decision
from zf.runtime.orchestrator_agent_semantic_failure import (
    SemanticFailureCheckpointError, request_semantic_failure_checkpoint,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import WorkflowOperationService


def _timeout_incident(tmp_path, fault=''):
    roster = [{'child_id': role, 'role_instance': role} for role in (
        'verify-0', 'verify-peer', 'verify-timeout')]
    if fault in {'missing_member', 'pending_member', 'completed_member'}:
        roster.append({'child_id': 'verify-extra', 'role_instance': 'verify-extra'})
    if fault == 'extra_member': roster.pop()
    runtime, request, candidate, child = _incident(tmp_path,
        expected_children=None if fault == 'missing_roster' else roster)
    request, _ = _peer_failure(runtime, request, child)
    original = hydrate_sidecar_ref(runtime.state_dir, child['control_result_ref']).payload
    run = 'run-semantic-1'
    def emit(kind, payload, cause=None):
        return runtime.event_writer.append(ZfEvent(type=kind, actor='zf-cli', origin='kernel',
            task_id='TASK-1', correlation_id=run, causation_id=cause, payload=payload))
    identity = {**original, 'role_instance': 'verify-timeout', 'child_id': 'verify-timeout'}
    service = WorkflowOperationService(state_dir=runtime.state_dir,
        event_log=runtime.event_log, event_writer=runtime.event_writer)
    operation = service.ensure_operation(workflow_run_id=run, operation_id='op-timeout',
        operation_type='fanout_reader_child', role_instance='verify-timeout', task_id='TASK-1',
        parent_stage_id='candidate-verify', active_attempt_id='timeout-call', request={
            'fanout_id': 'verify-fanout', 'child_id': 'verify-timeout',
            'role_instance': 'verify-timeout', 'stage_id': 'candidate-verify', 'result_identity': identity})
    service.mark_started(operation_id=operation.operation_id, request_hash=operation.request_hash,
        workflow_run_id=run, role_instance='verify-timeout', active_attempt_id='timeout-call')
    call = {'operation_id': operation.operation_id, 'request_hash': operation.request_hash,
            'attempt_id': 'timeout-call', 'workflow_run_id': run}
    dispatch = {'fanout_id': 'verify-fanout', 'child_id': 'verify-timeout',
        'role_instance': 'verify-timeout', 'task_id': 'TASK-1', 'workflow_run_id': run,
        'stage_id': 'candidate-verify', 'run_id': 'retry-transport',
        'operation_id': operation.operation_id, 'attempt_id': 'ta-transport', 'payload': call}
    if fault == 'dispatch_role': dispatch['role_instance'] = 'foreign'
    if fault == 'call_attempt': call['attempt_id'] = 'foreign'
    if fault == 'call_hash': call['request_hash'] = 'f' * 64
    if fault == 'stage_mismatch': dispatch['stage_id'] = 'foreign'
    if fault != 'missing_dispatch': emit('fanout.child.dispatched', dispatch)
    if fault == 'ambiguous_dispatch': emit('fanout.child.dispatched', dispatch)
    if fault in {'pending_member', 'completed_member'}:
        emit('fanout.child.dispatched' if fault == 'pending_member' else 'fanout.child.completed', {
            'fanout_id': 'verify-fanout', 'child_id': 'verify-extra', 'role_instance': 'verify-extra',
            'stage_id': 'candidate-verify', 'workflow_run_id': run, 'run_id': 'extra-carrier'})
    timeout_child = emit('fanout.child.failed', {k: dispatch[k] for k in (
        'fanout_id', 'child_id', 'role_instance', 'task_id', 'stage_id', 'run_id')} | {
        'trace_id': run, 'role_instance': 'verify-timeout', 'reason': 'idle',
        'timeout_seconds': 0, 'failure_kind': 'infra'})
    timeout = emit('fanout.timed_out', {'fanout_id': 'verify-fanout', 'trace_id': run,
        'pending_children': ['verify-timeout'], 'timeout_seconds': 0}, timeout_child.id)
    service.fail(operation_id=operation.operation_id, request_hash=operation.request_hash,
        workflow_run_id=run, task_id='TASK-1', reason='fanout_timeout',
        causation_id=timeout.id if fault != 'failure_cause' else 'foreign')
    if fault == 'superseded': emit('workflow.operation.superseded', call)
    if fault == 'admitted_result':
        emit('workflow.call.result.admitted', {**call, 'semantic_verdict': 'passed',
            'control_result_ref': child['control_result_ref']})
    if fault == 'request_digest':
        from zf.runtime.workflow_operation import reduce_workflow_operations
        op = reduce_workflow_operations(runtime.event_log.read_all())[operation.operation_id]
        (runtime.state_dir / op['request_ref']['ref']).write_text('{}')
    failed = ['verify-timeout'] if fault != 'unknown_member' else ['missing']
    aggregate = emit('fanout.aggregate.completed', {'fanout_id': 'verify-fanout',
        'workflow_run_id': run, 'status': 'failed', 'reason': 'timeout',
        'failed_children': failed, 'pending_children': ['verify-timeout'],
        'timeout_seconds': 0, 'failure_kind': 'infra'}, timeout.id)
    failure = emit('test.failed', {'workflow_run_id': run, 'pdd_id': 'TASK-1',
        'fanout_id': 'verify-fanout', 'task_map_generation': 'g1',
        'reason': 'timeout', 'failure_kind': 'infra'}, aggregate.id)
    request.payload.update(recovery_scope='candidate', failure_event_ids=[failure.id])
    request.payload.pop('target_operation_id', None)
    return runtime, request, operation


def test_legacy_timeout_handoff_keeps_negative_results_and_unknown_then_applies(tmp_path):
    runtime, request, _ = _timeout_incident(tmp_path)
    prepared = request_semantic_failure_checkpoint(runtime, request)
    sources = prepared.context.input_body['sources']
    source = next(s for s in sources if s['source_id'] == 'semantic-failure-input')
    candidate = hydrate_sidecar_ref(runtime.state_dir, source).payload['candidate_verify']
    assert len(candidate['members']) == 2
    assert {m['verdict'] for m in candidate['members']} == {'rejected', 'abstained'}
    unknown, = candidate['unresolved_members']
    assert unknown['operation_id'] == 'op-timeout'
    assert unknown['attempt_id'] == 'timeout-call'
    assert unknown['transport_run_id'] == 'retry-transport'
    assert unknown['outcome'] == 'unknown'
    assert not unknown.get('control_result_ref') and not unknown.get('verdict')
    assert not any(s['source_id'] == 'candidate-verification-verify-timeout' for s in sources)
    from tests.test_candidate_mixed_repair import partition

    decision = _submit_rework(runtime, prepared, action='return_to_plan',
                              directive_fields={'finding_dispositions': partition(runtime, request)})
    assert apply_orchestrator_agent_decision(runtime, decision)['applied']
    replan = [e for e in runtime.event_log.read_all() if e.type == 'orchestrator.replan_requested']
    assert len(replan) == 1 and replan[0].payload['failed_task_ids'] == ['TASK-2']
    assert not any(e.type in {'test.passed', 'task.done'} for e in runtime.event_log.read_all())


@pytest.mark.parametrize('fault', ['missing_dispatch', 'ambiguous_dispatch', 'dispatch_role',
    'call_attempt', 'call_hash', 'failure_cause', 'superseded', 'unknown_member',
    'request_digest', 'admitted_result', 'missing_roster', 'missing_member',
    'pending_member', 'extra_member', 'stage_mismatch'])
def test_timeout_unknown_member_requires_exact_lineage(tmp_path, fault):
    runtime, request, _ = _timeout_incident(tmp_path, fault)
    with pytest.raises(SemanticFailureCheckpointError):
        request_semantic_failure_checkpoint(runtime, request)


def test_timeout_apply_rechecks_unresolved_operation(tmp_path):
    runtime, request, _ = _timeout_incident(tmp_path)
    prepared = request_semantic_failure_checkpoint(runtime, request)
    decision = _submit_rework(runtime, prepared, action='return_to_plan')
    runtime.event_writer.append(ZfEvent(type='workflow.operation.superseded', origin='kernel',
        payload={'operation_id': 'op-timeout', 'workflow_run_id': 'run-semantic-1'}))
    assert not apply_orchestrator_agent_decision(runtime, decision)['applied']


@pytest.mark.parametrize('change', ['fanout.child.dispatched', 'fanout.aggregate.started'])
def test_timeout_apply_rejects_new_carrier_without_mutating_old_operation(tmp_path, change):
    runtime, request, _ = _timeout_incident(tmp_path)
    prepared = request_semantic_failure_checkpoint(runtime, request)
    decision = _submit_rework(runtime, prepared, action='return_to_plan')
    runtime.event_writer.append(ZfEvent(type=change, actor='zf-cli', origin='kernel', payload={
        'fanout_id': 'verify-fanout', 'child_id': 'verify-timeout', 'role_instance': 'verify-timeout',
        'workflow_run_id': 'run-semantic-1', 'operation_id': 'new-operation', 'run_id': 'new-carrier'}))
    assert not apply_orchestrator_agent_decision(runtime, decision)['applied']
    assert not any(e.type in {'orchestrator.replan_requested', 'workflow.operation.cancelled'}
                   for e in runtime.event_log.read_all())


def test_timeout_does_not_reclassify_completed_sibling(tmp_path):
    runtime, request, _ = _timeout_incident(tmp_path, 'completed_member')
    prepared = request_semantic_failure_checkpoint(runtime, request)
    source = next(s for s in prepared.context.input_body['sources'] if s['source_id'] == 'semantic-failure-input')
    body = hydrate_sidecar_ref(runtime.state_dir, source).payload['candidate_verify']
    assert len(body['members']) == 2 and len(body['unresolved_members']) == 1
    assert body['completed_members'][0]['child_id'] == 'verify-extra'
    assert not body['completed_members'][0].get('verdict')


@pytest.mark.parametrize('fault,expected', [('', 'mixed'), ('request_digest', 'unknown')])
def test_old_timeout_classification_preserves_semantic_owner(tmp_path, fault, expected):
    from zf.runtime.reader_failure_repair import repaired_failure_kind
    runtime, request, _ = _timeout_incident(tmp_path, fault)
    events = runtime.event_log.read_all()
    failure = next(e for e in events if e.id == request.payload['failure_event_ids'][0])
    before = runtime.event_log.path.read_bytes()
    assert repaired_failure_kind(runtime.state_dir, events, failure) == expected
    assert failure.payload['failure_kind'] == 'infra'
    assert runtime.event_log.path.read_bytes() == before


@pytest.mark.parametrize('prior_recovered', [False, True])
def test_runtime_timeout_keeps_prior_semantic_failure(tmp_path, prior_recovered):
    _, log, _, runtime = _state(tmp_path, idle_threshold=10)
    runtime.config.roles.append(replace(runtime.config.roles[0], name='review-b', instance_id='review-b'))
    runtime.config.workflow.stages[0].roles.append('review-b')
    _start(runtime)
    dispatch = next(e for e in log.read_all() if e.type == 'fanout.child.dispatched'
                    and e.payload['child_id'] == 'review-b')
    runtime.event_writer.append(ZfEvent(type='fanout.child.failed', actor='zf-cli', origin='kernel',
        payload={**dispatch.payload, 'failure_kind': 'quality',
            'findings': [{'message': 'retained semantic counterexample', 'child_id': 'review-b'}]}))
    if prior_recovered:
        runtime.event_writer.append(ZfEvent(type='fanout.child.completed', actor='zf-cli', origin='kernel',
            payload={**dispatch.payload, 'status': 'completed'}))
    runtime._now = lambda: time.time() + 100
    runtime._check_fanout_timeouts()
    aggregate = next(e for e in log.read_all() if e.type == 'fanout.aggregate.completed')
    assert set(aggregate.payload['failed_children']) == ({'review-a'} if prior_recovered else {'review-a', 'review-b'})
    assert aggregate.payload['pending_children'] == ['review-a']
    assert aggregate.payload['failure_kind'] == ('infra' if prior_recovered else 'mixed')
    assert any(f['message'] == 'retained semantic counterexample' for f in aggregate.payload['findings']) is not prior_recovered
    failure = next(e for e in log.read_all() if e.type == 'review.rejected')
    assert failure.payload['findings'] == aggregate.payload['findings']
