import argparse
import pytest

from tests.test_task_pipeline_operator_retry import case
from tests.test_task_pipeline_reconciler import _policy
from zf.cli.recover import register
from zf.core.task.store import TaskStore
from zf.core.events.model import ZfEvent
from zf.runtime.task_pipeline_contexts import task_pipeline_generation_contexts
from zf.runtime.task_pipeline_reconciler import TaskPipelineReconciler, _derive_stage
from zf.runtime.task_pipeline_rework import derive_impl_rework_requests
from zf.runtime.workflow_operation import reduce_workflow_operations


def test_exhausted_retry_is_explicit_and_only_grants_one_generation(case, monkeypatch):
    state, log, _, _, _ = case
    monkeypatch.setattr('zf.runtime.task_pipeline_reconciler.task_pipeline_policy_for_contexts',
                        lambda *a, **k: {**_policy(), 'max_rework_attempts': 0})
    def invoke(*extra):
        parser = argparse.ArgumentParser()
        register(parser.add_subparsers())
        args = parser.parse_args(['recover', 'task-impl', '--state-dir', str(state),
            '--operation', 'op-1', '--reason', 'runtime fault repaired', *extra])
        return args.func(args)
    before = log.path.read_bytes()
    assert invoke() == 1
    assert invoke('--authorize-extra-attempt') == 1
    assert log.path.read_bytes() == before
    assert invoke('--authorize-extra-attempt', '--approval-ref', 'operator:reviewed') == 0
    assert log.path.read_bytes() == before
    assert invoke('--authorize-extra-attempt', '--approval-ref', 'operator:reviewed', '--apply') == 0
    assert invoke('--authorize-extra-attempt', '--approval-ref', 'operator:reviewed', '--apply') == 0
    events = log.read_all()
    assert len([e for e in events if e.type == 'task.rework.requested']) == 1
    ops = list(reduce_workflow_operations(events).values())
    requests = derive_impl_rework_requests(events=events,
        generation_contexts=task_pipeline_generation_contexts(events), operation_rows=ops)
    plan = TaskPipelineReconciler().reconcile(policy={**_policy(), 'max_rework_attempts': 0},
        tasks=TaskStore(state / 'kanban.json').list_all(), operations=ops, attempts=[],
        impl_rework_requests=requests)
    assert plan['dispatchable']['impl'][0]['operation_generation'] == '2'
    for generation in (2, 3):
        stage, _, _ = _derive_stage({'impl': {'status': 'settled', 'semantic_verdict': 'failed',
            'operation_generation': generation}}, task={}, policy={'max_rework_attempts': 0},
            impl_rework_request=requests['T1'])
        assert stage == 'blocked'


@pytest.mark.parametrize('change', ['origin', 'generation', 'approval'])
def test_unbound_extra_generation_does_not_open_budget(case, change):
    state, log, writer, identity, admitted = case
    payload = {**identity, 'source': 'task_pipeline_operator_retry',
               'source_event_id': admitted.payload['control_result_ref']['source_event_id'],
               'authorized_next_operation_generation': 2, 'approval_ref': 'operator:reviewed'}
    origin = 'kernel'
    if change == 'origin': origin = 'worker'
    if change == 'generation': payload['authorized_next_operation_generation'] = 3
    if change == 'approval': payload['approval_ref'] = ''
    writer.append(ZfEvent(type='task.rework.requested', task_id='T1', origin=origin, payload=payload))
    events = log.read_all(); ops = list(reduce_workflow_operations(events).values())
    requests = derive_impl_rework_requests(events=events,
        generation_contexts=task_pipeline_generation_contexts(events), operation_rows=ops)
    plan = TaskPipelineReconciler().reconcile(policy={**_policy(), 'max_rework_attempts': 0},
        tasks=TaskStore(state / 'kanban.json').list_all(), operations=ops, attempts=[],
        impl_rework_requests=requests)
    assert plan['dispatchable']['impl'] == []
