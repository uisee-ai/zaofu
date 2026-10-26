"""A terminal reader carrier cannot retain ownership across recovery."""
from pathlib import Path

import pytest

from tests.test_workflow_operation_resume import _config, _seed_live_retry
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.workflow_operation import WorkflowOperationService, reduce_workflow_operations
from zf.runtime.workflow_resume import build_workflow_resume_projection
from zf.runtime.workflow_resume_apply import apply_workflow_resume


@pytest.mark.parametrize('case', ['terminal', 'other_dispatch', 'other_run', 'new_dispatch', 'live_retry'])
def test_terminal_reader_recovery_keeps_current_parent_and_live_attempt(tmp_path: Path, case: str):
    state = tmp_path / 'state'
    state.mkdir()
    TaskStore(state / 'kanban.json').add(Task(id='FLOW-1', status='in_progress'))
    log = EventLog(state / 'events.jsonl')
    writer = EventWriter(log)
    service = WorkflowOperationService(state_dir=state, event_log=log, event_writer=writer)
    op = service.ensure_operation(workflow_run_id='run-1', operation_id='reader-old',
        operation_type='fanout_reader_child', task_id='FLOW-1', parent_task_id='FLOW-1',
        request={'candidate_head': 'a' * 40})
    identity = {'workflow_run_id': 'run-1', 'operation_id': op.operation_id,
                'fanout_id': 'fanout-old', 'child_id': 'verify-1', 'run_id': 'dispatch-1'}
    writer.append(ZfEvent(type='fanout.child.dispatched', task_id='FLOW-1', payload=identity))
    writer.append(ZfEvent(type='fanout.child.failed', task_id='FLOW-1', payload={
        'fanout_id': 'fanout-old', 'child_id': 'verify-1',
        'trace_id': 'run-2' if case == 'other_run' else 'run-1',
        'run_id': 'dispatch-2' if case == 'other_dispatch' else 'dispatch-1',
        'failure_class': 'worker_noop_or_terminal_missing',
    }))
    if case == 'new_dispatch':
        writer.append(ZfEvent(type='fanout.child.dispatched', task_id='FLOW-1',
                              payload={**identity, 'run_id': 'dispatch-2'}))
    service.interrupt(operation_id=op.operation_id, request_hash=op.request_hash,
        workflow_run_id='run-1', task_id='FLOW-1', reason='fast_stop')
    if case == 'live_retry':
        _seed_live_retry(state, operation_id=op.operation_id, task_id='FLOW-1')
    checkpoint = build_workflow_resume_projection(state, _config())['operation_checkpoints'][0]
    if case != 'terminal':
        assert checkpoint['safe_resume_action'] == (
            'no_action' if case == 'live_retry' else 'redrive_interrupted_reader_operation')
        return
    assert checkpoint['safe_resume_action'] == 'cancel_terminal_reader_operation'
    result = apply_workflow_resume(state, _config(), checkpoint_id=checkpoint['checkpoint_id'])
    assert result['applied'] == 1
    assert reduce_workflow_operations(log.read_all())[op.operation_id]['status'] == 'cancelled'
    assert TaskStore(state / 'kanban.json').get('FLOW-1').status == 'in_progress'
    assert not any(e.type == 'workflow.operation.redrive_admitted' for e in log.read_all())
    assert apply_workflow_resume(state, _config(), checkpoint_id=checkpoint['checkpoint_id'])['applied'] == 0
