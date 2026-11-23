"""Sibling progress cannot discard a still-current Reader diagnosis."""
import pytest

from tests.test_run_manager import _config, _state
from zf.runtime.run_continuation import build_run_continuation_projection
from zf.runtime.run_manager import run_manager_tick


@pytest.mark.parametrize('format', ['operation', 'role-v1'])
@pytest.mark.parametrize('change', ['sibling', 'successor', 'terminal', 'foreign_target', 'generation', 'goal', 'result', 'wrong_dispatch', 'wrong_admission'])
def test_actual_recommendation_consumption_uses_failed_reader_scope(tmp_path, change, format):
    state, log, writer = _state(tmp_path)
    writer.default_origin = 'kernel'
    writer.emit('run.started', payload={'workflow_run_id': 'r'})
    writer.emit('task_map.ready', payload={'workflow_run_id': 'r', 'task_map_generation': 'g'})
    identity = {'workflow_run_id': 'r', 'task_id': 'anchor', 'role_instance': 'reader-1',
                'operation_type': 'fanout_reader_child', 'parent_stage_id': 'verify',
                'operation_id': 'op-failed', 'request_hash': 'a' * 64,
                'active_attempt_id': 'attempt-1', 'dispatch_id': 'dispatch-1', 'task_map_generation': 'g'}
    writer.emit('workflow.operation.requested', payload=identity)
    writer.emit('workflow.operation.failed', payload=identity)
    failed = dict(identity)
    if format == 'role-v1':
        ref = {'ref': 'admitted.json', 'sha256': 'b' * 64, 'source_event_id': 'failed-result'}
        writer.emit('workflow.call.result.admitted', payload={**identity,
            'semantic_verdict': 'abstained', 'control_result_ref': ref})
        admitted = writer.emit('workflow.operation.settled', payload={**identity,
            'semantic_verdict': 'abstained', 'admitted_call_result_ref': ref})
        failed = {k: v for k, v in identity.items() if k not in {'request_hash', 'active_attempt_id'}}
        failed.update(identity_version='role-v1', attempt_id='task-attempt-1',
                      admission_event_id=admitted.id, source_event_id='failed-result')
    if change == 'wrong_dispatch':
        failed['dispatch_id'] = 'other'
        failed['request_hash'] = 'wrong' if format == 'operation' else ''
    if change == 'wrong_admission':
        failed['admission_event_id'] = 'missing'
        failed['request_hash'] = 'wrong' if format == 'operation' else ''
    failure = writer.emit('task.attempt.failed', task_id='anchor', payload=failed)
    current = build_run_continuation_projection(log.read_all(), goal={'run_id': 'r'},
                                               pending_actions=[], completion_profile={})
    writer.emit('run.manager.action.effect.pending', payload={
        'request_id': 'diagnose', 'task_id': 'anchor', 'workflow_run_id': 'r',
        'source_event_ids': [failure.id], 'action': 'resident-agent-reprompt',
        'request_kind': 'recovery_diagnosis',
        'continuation_expected_events': ['run.manager.agent.recommendation'],
        'operation_precondition': {k: current[k] for k in ('run_id', 'generation', 'progress_digest')}})
    writer.emit('run.manager.resident.prompted', payload={
        'request_id': 'diagnose', 'instance_id': 'run-manager'})
    writer.emit('fanout.child.completed', payload={'workflow_run_id': 'r', 'child_id': 'sibling'})
    if change == 'successor':
        writer.emit('workflow.operation.requested', payload={**identity,
            'operation_id': 'op-new', 'active_attempt_id': 'attempt-2'})
    if change == 'generation':
        writer.emit('task_map.amended', payload={'workflow_run_id': 'r', 'task_map_generation': 'new'})
    if change == 'goal':
        writer.emit('run.goal.updated', payload={'workflow_run_id': 'r', 'objective': 'new goal'})
    if change == 'result':
        writer.emit('workflow.call.result.admitted', payload={**identity, 'semantic_verdict': 'passed',
            'control_result_ref': {'ref': 'passed.json', 'sha256': 'c' * 64}})
        writer.emit('workflow.operation.settled', payload={**identity, 'semantic_verdict': 'passed'})
    if change == 'terminal':
        writer.emit('run.goal.completed', payload={'workflow_run_id': 'r'})
    recommendation = writer.emit('run.manager.agent.recommendation', actor='run-manager', payload={
        'request_id': 'diagnose', 'task_id': 'anchor', 'workflow_run_id': 'r',
        'target_operation_id': 'foreign' if change == 'foreign_target' else 'op-failed',
        'active_attempt_id': 'attempt-1', 'recommended_route': 'human',
        'summary': 'Exact Reader execution failure requires bounded repair authority.'})
    run_manager_tick(state_dir=state, writer=writer, config=_config(), spawn_repairs=False)
    consumed = [e for e in log.read_all() if e.type == 'run.manager.agent.recommendation.consumed'
                and e.payload.get('source_event_id') == recommendation.id]
    assert len(consumed) == 1
    assert (consumed[0].payload['status'] == 'consumed') == (change == 'sibling')
    assert any(e.type == 'human.escalate' for e in log.read_all()) == (change == 'sibling')
