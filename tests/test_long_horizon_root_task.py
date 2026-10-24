"""Run-owned roots survive provider shutdown and boot reconciliation."""
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from zf.cli.start import _requeue_inflight_for_start
from zf.core.config.schema import ZfConfig
from zf.core.events.log import EventLog
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.shutdown import GracefulShutdown
from zf.runtime.workflow_anchor import mark_workflow_fanout_anchor, mark_workflow_managed_task


@pytest.mark.parametrize('caller', ['start', 'stop'])
@pytest.mark.parametrize('anchor', [False, True])
def test_run_owned_root_survives_worker_reconciliation(
    tmp_path: Path, caller: str, anchor: bool,
) -> None:
    state = tmp_path / 'state'
    state.mkdir()
    store = TaskStore(state / 'kanban.json')
    root = Task(id='ROOT', title='long horizon root', status='in_progress')
    store.add(mark_workflow_fanout_anchor(root) if anchor else mark_workflow_managed_task(root))
    store.add(Task(id='WORKER', title='interrupted worker', status='in_progress', assigned_to='dev'))
    if caller == 'start':
        assert _requeue_inflight_for_start(
            state_dir=state, config=ZfConfig(), control_plane_only=False,
        )
    else:
        assert GracefulShutdown(state, MagicMock())._emit_stale_inflight_cleanup()
    assert store.get('ROOT').status == 'in_progress'
    assert store.get('WORKER').status == 'backlog'
    assert not any(
        e.type == 'task.requeued' and e.task_id == 'ROOT'
        for e in EventLog(state / 'events.jsonl').read_all()
    )
