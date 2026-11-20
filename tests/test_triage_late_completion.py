"""Late admitted task completion reconciles an expired triage effect, once."""
from dataclasses import replace

import pytest

from tests.test_run_manager import _config, _state
from zf.core.events import ZfEvent
from zf.runtime.run_manager import (
    _settle_pending_action_effects, _unresolved_action_effects, run_manager_tick,
)


def history():
    body = {
        'action': 'orchestrator-rework-triage', 'request_id': 'triage-1',
        'workflow_run_id': 'run-1', 'task_id': 'T1', 'task_map_generation': 'g1',
        'effect_id': 'effect-1', 'expected_event_types': ['task.done', 'task.done.accepted'],
        'effect_timeout_seconds': 1800,
    }
    pending = ZfEvent(type='run.manager.action.effect.pending', origin='kernel', payload=body)
    failed = ZfEvent(type='run.manager.action.effect.failed', origin='kernel', payload={
        **body, 'effect_pending_event_id': pending.id,
        'reason': 'recovery effect timed out', 'observed_event_id': '', 'status': 'failed',
    })
    done = ZfEvent(type='task.done', origin='kernel', actor='zf-cli',
        task_id='T1', correlation_id='run-1', payload={
            'workflow_run_id': 'run-1', 'task_map_generation': 'g1',
        })
    return pending, failed, done


def test_tick_reconciles_late_task_completion_without_erasing_timeout(tmp_path):
    state, log, writer = _state(tmp_path)
    writer.default_origin = 'kernel'
    pending, failed, done = history()
    for event in (pending, failed, done):
        writer.append(event)
    run_manager_tick(state_dir=state, writer=writer, config=_config(),
                     project_root=tmp_path, auto_execute=False, spawn_repairs=False)
    passed = [e for e in log.read_all() if e.type == 'run.manager.action.effect.passed']
    assert len(passed) == 1
    assert passed[0].payload['observed_event_id'] == done.id
    assert passed[0].payload['effect_pending_event_id'] == pending.id
    assert any(e.id == failed.id for e in log.read_all())
    assert _unresolved_action_effects(log.read_all()) == ([], [])
    assert _settle_pending_action_effects(log.read_all(), writer) == 0


@pytest.mark.parametrize('invalid', [
    'no_completion', 'worker_completion', 'wrong_task', 'wrong_run',
    'wrong_generation', 'missing_generation', 'missing_run', 'conflicting_correlation',
    'missing_task', 'real_failure', 'wrong_pending', 'worker_timeout', 'other_action',
])
def test_late_completion_cannot_launder_unbound_or_real_failures(tmp_path, invalid):
    _, log, writer = _state(tmp_path)
    pending, failed, done = history()
    if invalid == 'worker_completion':
        done = replace(done, origin='worker')
    elif invalid == 'missing_task':
        done = replace(done, task_id=None)
    elif invalid in {'wrong_task', 'wrong_run', 'wrong_generation', 'missing_generation', 'missing_run'}:
        field = {'wrong_task': 'task_id', 'wrong_run': 'workflow_run_id',
                 'wrong_generation': 'task_map_generation', 'missing_generation': 'task_map_generation',
                 'missing_run': 'workflow_run_id'}[invalid]
        done = replace(done, payload={**done.payload, field: '' if invalid.startswith('missing') else 'other'})
    elif invalid == 'conflicting_correlation':
        done = replace(done, correlation_id='other')
    elif invalid == 'real_failure':
        failed = replace(failed, payload={**failed.payload, 'reason': 'recovery failed to make progress'})
    elif invalid == 'wrong_pending':
        failed = replace(failed, payload={**failed.payload, 'effect_pending_event_id': 'other'})
    elif invalid == 'worker_timeout':
        failed = replace(failed, origin='worker')
    elif invalid == 'other_action':
        pending = replace(pending, payload={**pending.payload, 'action': 'other'})
    events = [pending, failed] + ([] if invalid == 'no_completion' else [done])
    assert _settle_pending_action_effects(events, writer) == 0
    assert not log.read_all()
