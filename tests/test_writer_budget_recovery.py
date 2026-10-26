"""A resumed Run must finish its admitted writer aggregate after budget stop."""
from pathlib import Path
from contextlib import nullcontext
from unittest.mock import patch

import pytest

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from tests.test_writer_fanout_runtime import (
    _child, _commit, _fanout_id, _manifest, _seed_tasks, _start, _state,
)


@pytest.mark.parametrize('idle_recovery', [False, True])
@pytest.mark.parametrize('resume,cancel_reason,ready', [
    (True, 'workflow_run_terminal:run.goal.blocked', True),
    (False, 'workflow_run_terminal:run.goal.blocked', False),
    (True, 'operator_cancelled', False),
])
def test_writer_budget_recovery_finishes_only_reactivated_run(
    tmp_path: Path, resume: bool, cancel_reason: str, ready: bool, idle_recovery: bool,
) -> None:
    state, log, _transport, orch = _state(tmp_path)
    _seed_tasks(state)
    _start(orch)
    fanout_id = _fanout_id(log)
    manifest = _manifest(state, fanout_id)
    children = [_child(manifest, task) for task in ('TASK-1', 'TASK-2')]
    commits = {
        child['task_id']: _commit(
            Path(child['workdir']), name, name + '\n', child['task_id'],
        )
        for child, name in zip(children, ('a.txt', 'b.txt'), strict=True)
    }

    def finish(child):
        orch.run_once(events=[ZfEvent(
            type='dev.build.done', actor=child['role_instance'],
            task_id=child['task_id'], correlation_id='trace-1',
            payload={
                'fanout_id': fanout_id, 'child_id': child['child_id'],
                'run_id': child['run_id'], 'pdd_id': 'F-11111111',
                'source_commit': commits[child['task_id']],
                'source_branch': child['source_branch'], 'workdir': child['workdir'],
            },
        )])

    finish(children[0])
    writer = EventWriter(log)
    writer.append(ZfEvent(
        type='run.goal.blocked', actor='zf-cli', correlation_id='trace-1',
        payload={'workflow_run_id': 'trace-1', 'reason': 'workflow_budget_exceeded:tokens'},
    ))
    writer.append(ZfEvent(
        type='fanout.cancelled', actor='zf-cli', correlation_id='trace-1',
        payload={'fanout_id': fanout_id, 'trace_id': 'trace-1', 'reason': cancel_reason},
    ))
    if resume:
        writer.append(ZfEvent(
            type='run.goal.updated', actor='operator', correlation_id='trace-1',
            payload={'workflow_run_id': 'trace-1', 'status': 'active'},
        ))
    # Simulate shutdown before aggregate processing; idle recovery must finish
    # from admitted children without redispatching the implementation.
    with (patch.object(orch, '_writer_fanout_aggregate_recoverable', return_value=False)
          if idle_recovery else nullcontext()):
        finish(children[1])
    # Restart/idle recovery must also be idempotent.
    orch.run_once(events=[])
    candidates = [e for e in log.read_all() if e.type == 'candidate.ready']
    assert len(candidates) == int(ready)
    if ready:
        result = _manifest(state, fanout_id)
        assert result['aggregate']['recovered_from_aggregate_status'] == 'cancelled'
        assert all(c['status'] == 'completed' for c in result['children'])
        assert sum(e.type == 'fanout.child.dispatched' for e in log.read_all()) == 2
