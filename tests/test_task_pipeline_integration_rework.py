import argparse

import pytest

from tests.test_task_pipeline_integration_retry import case
from tests.test_task_pipeline_reconciler import _policy
from zf.cli.recover import register
from zf.core.events.model import ZfEvent
from zf.core.task.store import TaskStore
from zf.runtime.task_pipeline_contexts import task_pipeline_generation_contexts
from zf.runtime.task_pipeline_reconciler import TaskPipelineReconciler
from zf.runtime.task_pipeline_rework import derive_impl_rework_requests
from zf.runtime.workflow_operation import reduce_workflow_operations


def bind_results(writer, identity, target='same-head'):
    for stage in ('impl', 'verify'):
        p = {**identity, 'operation_id': f'op-{stage}', 'task_pipeline_stage': stage}
        source = writer.append(ZfEvent(
            type='dev.build.done' if stage == 'impl' else 'task.pipeline.verify.completed',
            task_id='T1', origin='kernel', payload={**p,
                ('source_commit' if stage == 'impl' else 'target_commit'): target}))
        writer.append(ZfEvent(type='workflow.call.result.admitted', task_id='T1', payload={
            **p, 'semantic_verdict': 'passed', 'control_result_ref': {'source_event_id': source.id}}))
        writer.append(ZfEvent(type='workflow.operation.settled', task_id='T1', payload=p))


def invoke(state, *, apply=False, approval='operator-reviewed-probe', rework=True):
    parser = argparse.ArgumentParser()
    register(parser.add_subparsers())
    args = parser.parse_args(['recover', 'task-integration', '--state-dir', str(state),
                             '--operation', 'op-integration', '--reason', 'bounded fixture repair',
                             *(['--rework-impl', '--approval-ref', approval] if rework else []),
                             *(['--apply'] if apply else [])])
    return args.func(args)


def plan(state, log):
    events = log.read_all()
    ops = list(reduce_workflow_operations(events).values())
    rework = derive_impl_rework_requests(events=events,
        generation_contexts=task_pipeline_generation_contexts(events), operation_rows=ops)
    return TaskPipelineReconciler().reconcile(policy={**_policy(), 'max_rework_attempts': 0},
        tasks=TaskStore(state / 'kanban.json').list_all(), operations=ops, attempts=[],
        impl_rework_requests=rework)


@pytest.mark.mock_e2e
def test_cli_rework_preserves_verdicts_and_dispatches_only_next_impl(case):
    state, log, writer, identity, source = case
    bind_results(writer, identity)
    before = log.path.read_bytes()
    assert invoke(state) == 0
    assert log.path.read_bytes() == before
    assert invoke(state, apply=True) == 0
    first = log.path.read_bytes()
    assert first.startswith(before)
    assert invoke(state, apply=True) == 0
    assert log.path.read_bytes() == first
    requests = [e for e in log.read_all() if e.type == 'task.rework.requested']
    assert len(requests) == 1 and requests[0].causation_id == source.id
    projected = plan(state, log)
    assert projected['tasks'][0]['stage'] == 'impl_rework_ready'
    assert projected['dispatchable']['impl'][0]['operation_generation'] == '2'
    assert projected['dispatchable']['verify'] == []
    assert projected['queues']['integration_ready'] == []
    rows = reduce_workflow_operations(log.read_all())
    assert rows['op-verify']['semantic_verdict'] == 'passed'
    assert rows['op-integration']['status'] == 'blocked'
    p = {**identity, 'operation_id': 'impl-2', 'task_pipeline_stage': 'impl', 'operation_generation': 2}
    writer.append(ZfEvent(type='workflow.operation.requested', task_id='T1', payload=p))
    writer.append(ZfEvent(type='workflow.operation.started', task_id='T1', payload=p))
    assert plan(state, log)['tasks'][0]['stage'] == 'impl_active'
    assert plan(state, log)['dispatchable']['impl'] == []
    writer.append(ZfEvent(type='workflow.call.result.admitted', task_id='T1',
                         payload={**p, 'semantic_verdict': 'passed'}))
    writer.append(ZfEvent(type='workflow.operation.settled', task_id='T1', payload=p))
    projected = plan(state, log)
    assert projected['tasks'][0]['stage'] == 'verify_ready'
    assert projected['dispatchable']['verify'][0]['operation_generation'] == '2'
    assert projected['queues']['integration_ready'] == []
    assert invoke(state, apply=True) == 1


@pytest.mark.parametrize('fault', ['missing_results', 'no_approval', 'wrong_target',
    'active_successor', 'new_impl', 'old_map', 'task_done', 'run_done', 'verify_failed'])
def test_cli_rejects_unsafe_rework_without_writing(case, fault):
    state, log, writer, identity, _ = case
    if fault != 'missing_results':
        bind_results(writer, identity)
    if fault == 'wrong_target':
        source = writer.append(ZfEvent(type='task.pipeline.verify.completed', task_id='T1',
            origin='kernel', payload={**identity, 'operation_id': 'op-verify', 'target_commit': 'other'}))
        writer.append(ZfEvent(type='workflow.call.result.admitted', task_id='T1', payload={
            **identity, 'operation_id': 'op-verify', 'semantic_verdict': 'passed',
            'control_result_ref': {'source_event_id': source.id}}))
    elif fault in {'active_successor', 'new_impl'}:
        p = {**identity, 'operation_id': 'impl-new', 'task_pipeline_stage': 'impl', 'operation_generation': 2}
        writer.append(ZfEvent(type='workflow.operation.requested', task_id='T1', payload=p))
        if fault == 'new_impl':
            writer.append(ZfEvent(type='workflow.operation.settled', task_id='T1', payload=p))
    elif fault == 'old_map':
        writer.append(ZfEvent(type='task.pipeline.generation.admitted', payload={
            **identity, 'schema_version': 'task-pipeline-generation.v1', 'task_ids': ['T1'],
            'task_map_generation': 'new-map'}))
    elif fault == 'task_done':
        TaskStore(state / 'kanban.json').update('T1', status='done')
    elif fault == 'run_done':
        writer.append(ZfEvent(type='run.completed', payload={'workflow_run_id': 'run-1'}))
    elif fault == 'verify_failed':
        writer.append(ZfEvent(type='workflow.operation.failed', task_id='T1', payload={'operation_id': 'op-verify'}))
    before = log.path.read_bytes()
    assert invoke(state, apply=True, approval='' if fault == 'no_approval' else 'approval') == 1
    assert log.path.read_bytes() == before


def test_new_mode_keeps_run_contract_gate(case, monkeypatch):
    state, log, writer, identity, _ = case
    bind_results(writer, identity)
    monkeypatch.setattr('zf.cli.recover._run_contract_recovery_allowed', lambda *args: False)
    before = log.path.read_bytes()
    assert invoke(state, apply=True) == 1
    assert log.path.read_bytes() == before


def test_new_mode_requires_settled_attempt(case):
    from zf.core.state.task_attempts import TaskAttemptStore
    state, log, writer, identity, _ = case
    bind_results(writer, identity)
    TaskAttemptStore(state / 'task_attempts.json').ensure_for_dispatch(
        run_id='run-1', task_id='T1', dispatch_id='verify-delivery', role='verify',
        instance_id='verify-1', operation_id='op-verify', briefing_ref='briefing.md',
        created_at='2026-11-07T12:00:00+00:00', lease_expires_at='2026-11-07T13:00:00+00:00',
        max_attempts=3)
    before = log.path.read_bytes()
    assert invoke(state, apply=True) == 1
    assert log.path.read_bytes() == before
