"""An intentional Run pause is never an orphan or an exhausted idle scan."""
from datetime import datetime, timezone

import pytest

from tests.test_orphan_timeout import state_dir, config, transport, _seed_busy_task
from tests.test_goal_idle_driver import _cfg, _state, _Writer, _goal_started, _tick_until_fire
from zf.core.events import ZfEvent
from zf.runtime.goal_idle_driver import maybe_emit_goal_idle_rescan
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.pause_lifecycle import active_request_age_seconds


def _event(kind, stamp, run='r-1', task_id=None):
    return ZfEvent(type=kind, origin='kernel', task_id=task_id,
        ts=datetime.fromtimestamp(stamp, timezone.utc).isoformat(),
        payload={'workflow_run_id': run})


def test_paused_run_never_spends_goal_rescans_then_resumes():
    events = [_goal_started(), _event('run.paused', 10)]
    writer, state = _Writer(), _state()
    assert _tick_until_fire(events, _cfg(), state, writer, ticks=12) == ''
    assert not writer.appended
    events.append(_event('run.resumed', 4000))
    assert _tick_until_fire(events, _cfg(), state, writer) == 'rescan'
    assert writer.appended[0].payload['rescan_ordinal'] == 1


def test_other_run_pause_does_not_freeze_goal():
    events = [_goal_started(), _event('run.paused', 10, run='other')]
    writer = _Writer()
    assert _tick_until_fire(events, _cfg(), _state(), writer) == 'rescan'


def test_orphan_sweep_preserves_paused_task_and_excludes_pause_after_resume(state_dir, config, transport):
    orch = Orchestrator(state_dir, config, transport)
    for task, run in [('T1', 'r-1'), ('T2', 'other')]:
        _seed_busy_task(state_dir, task, dispatch_ts=0)
        orch.event_writer.append(_event('run.admission.admitted', 0, run=run, task_id=task))
        orch._dispatch_epoch[task] = 0
    orch.event_writer.append(_event('run.paused', 100))
    orch._now = lambda: 4000
    orch._check_orphaned_tasks()
    assert orch.task_store.get('T1').status == 'in_progress'
    assert orch.task_store.get('T2').status == 'backlog'
    orch.event_writer.append(_event('run.resumed', 4000))
    orch._now = lambda: 4001
    orch._check_orphaned_tasks()
    assert orch.task_store.get('T1').status == 'in_progress'
    assert not any(e.type in {'task.orphan_warning', 'task.orphaned'} and e.task_id == 'T1'
                   for e in orch.event_log.read_all())
    orch._now = lambda: 6000
    orch._check_orphaned_tasks()
    assert orch.task_store.get('T1').status == 'backlog'


def test_overlapping_project_and_run_pauses_exclude_union_once():
    events = [_event('run.paused', 10), _event('dispatch.paused', 20),
              _event('run.resumed', 30), _event('dispatch.resumed', 40)]
    assert active_request_age_seconds(events, created_at=datetime.fromtimestamp(0, timezone.utc),
        now=datetime.fromtimestamp(50, timezone.utc), run_id='r-1') == 20
