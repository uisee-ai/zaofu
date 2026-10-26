from pathlib import Path

import pytest

from zf.core.events.model import ZfEvent
from zf.core.state.task_attempts import TaskAttemptStore
from zf.runtime.task_attempt_runtime import (
    dispatch_attempt_payload, reconcile_task_attempts, settle_task_attempt_result,
)
from zf.runtime.workflow_operation import reduce_workflow_operations
from zf.runtime.sdlc_graph import build_sdlc_graph
from tests.test_scheduler_task_attempt import _runtime, _send
from tests.test_sdlc_graph import sources


@pytest.mark.parametrize('recover', [False, True])
@pytest.mark.parametrize('result_type', ['dev.blocked', 'dev.failed'])
def test_semantic_failure_closes_active_operation_and_graph(tmp_path: Path, recover, result_type):
    runtime, _, state = _runtime(tmp_path)
    context = _send(runtime, state, dispatch_id='disp-1')
    assert context and context.operation_id
    payload = {'workflow_run_id':'RUN-1', 'operation_id':context.operation_id,
        'request_hash':'hash', 'task_id':'TASK-1', 'dispatch_id':context.dispatch_id,
        'active_attempt_id':context.dispatch_id, 'role_instance':'dev'}
    for kind in ['requested', 'started']:
        runtime.event_writer.append(ZfEvent(type='workflow.operation.'+kind,
            origin='kernel', task_id='TASK-1', payload=payload))
    result = runtime.event_writer.append(ZfEvent(type=result_type, actor='dev', origin='worker', task_id='TASK-1',
        payload={**dispatch_attempt_payload(context), 'reason':'browser setup failed'}))
    if recover:
        TaskAttemptStore(state/'task_attempts.json').update(context.attempt_id,
            status='failed', updated_at=result.ts, terminal_event_id=result.id,
            failure_class='semantic_result_failed', failure_reason='browser setup failed',
            retryable=False, recovery_owner='workflow')
    else:
        settle_task_attempt_result(runtime, result)
    reconcile_task_attempts(runtime)
    reconcile_task_attempts(runtime)
    events = runtime.event_log.read_all()
    terminal = [e for e in events if e.type == 'workflow.operation.failed']
    assert len(terminal) == 1
    assert terminal[0].causation_id == result.id
    assert terminal[0].origin == 'kernel'
    assert reduce_workflow_operations(events)[context.operation_id]['status'] == 'failed'
    source = sources(events=list(enumerate(events, 1)))
    source['scope']['run_id'] = 'RUN-1'
    operation = next(n for n in build_sdlc_graph(source)['nodes']
                     if n['id'] == 'operation:'+context.operation_id)
    assert operation['status'] == 'failed'
    assert operation['data']['finished_at']


def test_old_failed_attempt_does_not_close_new_dispatch(tmp_path: Path):
    runtime, _, state = _runtime(tmp_path)
    context = _send(runtime, state, dispatch_id='disp-1')
    payload = {'workflow_run_id':'RUN-1', 'operation_id':context.operation_id,
        'request_hash':'hash', 'task_id':'TASK-1', 'dispatch_id':'new-dispatch',
        'active_attempt_id':'new-dispatch'}
    runtime.event_writer.append(ZfEvent(type='workflow.operation.started', origin='kernel',
                                      task_id='TASK-1', payload=payload))
    result = runtime.event_writer.append(ZfEvent(type='dev.failed', actor='dev', origin='worker', task_id='TASK-1',
                                               payload=dispatch_attempt_payload(context)))
    settle_task_attempt_result(runtime, result)
    reconcile_task_attempts(runtime)
    assert reduce_workflow_operations(runtime.event_log.read_all())[context.operation_id]['status'] == 'running'
