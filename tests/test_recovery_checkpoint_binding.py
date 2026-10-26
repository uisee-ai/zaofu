"""A checkpoint is useful only for the current canonical Run and Attempt."""
from pathlib import Path

import pytest

from zf.core.events import EventLog
from zf.core.state.task_attempts import TaskAttemptStore
from zf.core.task.schema import Task, TaskExecutionBinding
from zf.core.task.store import TaskStore
from zf.runtime.long_horizon import TaskEventIndex, build_resume_packet
from zf.runtime.maintenance import create_checkpoint
from zf.runtime.recovery import build_recovery_briefing
from zf.runtime.recovery_context import build_task_recovery_context
from tests.test_maintenance_worktree_checkpoint import _project


@pytest.mark.parametrize('case', ['current', 'old_run', 'old_dispatch', 'tampered', 'unavailable'])
def test_recovery_consumers_revalidate_checkpoint_identity_and_digest(tmp_path: Path, case: str):
    root = tmp_path / 'project'
    _project(root)
    state = tmp_path / 'state'
    store = TaskStore(state / 'kanban.json')
    task = Task(id='T', title='source task', status='in_progress', assigned_to='dev-0',
                active_dispatch_id='dispatch-1', execution_binding=TaskExecutionBinding(workflow_run_id='run-1'))
    store.add(task)
    (state / 'feature_list.json').write_text('[]')
    TaskAttemptStore(state / 'task_attempts.json').ensure_for_dispatch(
        run_id='run-1', task_id='T', dispatch_id='dispatch-1', role='dev', instance_id='dev-0',
        operation_id='op-1', briefing_ref='', created_at='2026-10-24T00:00:00+00:00',
        lease_expires_at='', max_attempts=3,
    )
    checkpoint = create_checkpoint(state, project_root=root if case != 'unavailable' else tmp_path,
                                   task_id='T', assigned_worker='dev-0')
    if case == 'old_run':
        task.execution_binding.workflow_run_id = 'run-2'
        store.update('T', execution_binding=task.execution_binding)
    elif case == 'old_dispatch':
        task.active_dispatch_id = 'dispatch-2'
        store.update('T', active_dispatch_id='dispatch-2')
    elif case == 'tampered':
        (state / checkpoint.workspace_snapshot_ref['ref']).write_text('{}')
    events = EventLog(state / 'events.jsonl').read_all()
    context = build_task_recovery_context(state, events, task_id='T')
    packet = build_resume_packet(state, 'T', project_root=root, index=TaskEventIndex(events))
    for view in (context, packet):
        facts = view['maintenance_checkpoint']
        assert facts['authorizes_continuation'] is False
        if case == 'current':
            assert facts['status'] == 'bound'
            assert facts['checkpoint_id'] == checkpoint.checkpoint_id
            assert facts['workspace_snapshot_ref'] == checkpoint.workspace_snapshot_ref
            assert facts['provider_quiescence'] == 'unverified'
        else:
            assert facts['status'] == 'unavailable'
            assert 'workspace_snapshot_ref' not in facts
    briefing = build_recovery_briefing(state, 'dev', store.get('T'), project_root=root)
    if case == 'current':
        assert checkpoint.checkpoint_id in briefing
    else:
        assert checkpoint.checkpoint_id not in briefing
