from zf.core.events.model import ZfEvent
from zf.runtime import run_admission
from zf.runtime.task_pipeline_contexts import task_pipeline_generation_contexts
from zf.runtime.task_pipeline_operator_retry import SOURCE
from zf.runtime.task_pipeline_rework import derive_impl_rework_requests
from zf.runtime.workflow_operation import reduce_workflow_operations


def retry_events():
    events = []
    for i in range(4):
        identity = dict(
            task_id=f'T{i}', operation_id=f'op-{i}', workflow_run_id='run-1',
            task_map_generation='map-1', operation_generation=1,
            task_pipeline_stage='impl',
        )
        events.append(ZfEvent(type='task.pipeline.generation.admitted', payload={
            **identity, 'schema_version': 'task-pipeline-generation.v1',
            'task_ids': [identity['task_id']],
        }))
        events.append(ZfEvent(type='workflow.operation.requested', payload=identity))
        source = ZfEvent(type='dev.blocked', task_id=identity['task_id'],
                         origin='kernel', payload=identity)
        events.append(source)
        events.append(ZfEvent(type='workflow.call.result.admitted', payload={
            **identity, 'semantic_verdict': 'failed',
            'control_result_ref': {'source_event_id': source.id},
        }))
        events.append(ZfEvent(type='workflow.operation.settled', payload=identity))
        events.append(ZfEvent(type='task.rework.requested', origin='kernel',
                             task_id=identity['task_id'], payload={
                                 **identity, 'source': SOURCE,
                                 'source_event_id': source.id, 'reason': 'repair checked',
                             }))
    return events


def project(events):
    return derive_impl_rework_requests(
        events=events, generation_contexts=task_pipeline_generation_contexts(events),
        operation_rows=reduce_workflow_operations(events).values(),
    )


def test_retry_projection_reuses_only_its_current_terminal_snapshot(monkeypatch):
    events = retry_events()
    original = run_admission.fold_terminal_run_scope
    calls = []
    def fold(rows):
        calls.append(len(rows))
        return original(rows)
    monkeypatch.setattr(run_admission, 'fold_terminal_run_scope', fold)
    first = project(events)
    assert set(first) == {'T0', 'T1', 'T2', 'T3'}
    assert len(calls) == 1
    for event_type, status, expected in [
        ('run.goal.blocked', '', {}),
        ('run.goal.updated', 'active', first),
        ('run.completed', '', {}),
        ('run.goal.updated', 'active', {}),
    ]:
        events.append(ZfEvent(type=event_type, payload={
            'workflow_run_id': 'run-1', 'status': status,
        }))
        calls.clear()
        assert project(events) == expected
        assert calls == [len(events)]


def test_unrelated_run_and_ineligible_requests_do_not_reuse_old_scope(monkeypatch):
    events = retry_events()
    before = project(events)
    events.append(ZfEvent(type='run.completed', payload={'workflow_run_id': 'other-run'}))
    assert project(events) == before
    for request in events:
        if request.type == 'task.rework.requested':
            request.payload = {**request.payload, 'operation_id': 'absent'}
    def unexpected(_):
        raise AssertionError('no eligible retry should fold the ledger')
    monkeypatch.setattr(run_admission, 'fold_terminal_run_scope', unexpected)
    assert project(events) == {}
