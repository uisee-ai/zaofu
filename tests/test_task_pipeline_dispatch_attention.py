"""Repeated pre-dispatch failures need diagnosis without reviving stale work."""
from copy import deepcopy
import json

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.supervisor_attention import build_attention_items, apply_attention_lifecycle
from zf.runtime.run_manager import _pending_attention_diagnostic_actions


def failure_events():
    generation = ZfEvent(type='task.pipeline.generation.admitted', origin='kernel', payload={
        'schema_version':'task-pipeline-generation.v1', 'workflow_run_id':'run',
        'task_map_generation':'map', 'generation_id':'gen', 'task_ids':['TASK']})
    failed = ZfEvent(type='task.pipeline.stage.dispatch_deferred', origin='kernel', actor='orchestrator',
        task_id='TASK', causation_id=generation.id, correlation_id='run', payload={
            'schema_version':'task-pipeline-stage-dispatch-deferred.v1', 'workflow_run_id':'run',
            'task_id':'TASK', 'task_map_generation':'map', 'task_pipeline_stage':'impl',
            'operation_generation':1, 'reason':'TaskPipelineRuntimeError: invalid partial lineage'})
    cooldown = ZfEvent(type='orchestrator.dispatch_cooldown', origin='kernel', actor='zf-cli',
        task_id='TASK', causation_id=failed.id, correlation_id='run',
        payload={'failures_in_window':3, 'window_seconds':60.0, 'cooldown_seconds':120.0})
    return [generation, failed, cooldown]


def items(events):
    return build_attention_items(events=events, automation={}, failure_signals=[], plan_integrity={})


def pipeline_items(events):
    return [r for r in items(events) if r.get('failure_class') == 'task_pipeline_dispatch_deferred']


def dispatch(failed, **changes):
    payload = dict(failed.payload, operation_id='impl-op', dispatch_id='dispatch', attempt_id='attempt')
    payload.update(changes)
    return ZfEvent(type='task.pipeline.stage.dispatched', origin='kernel',
                   task_id=payload['task_id'], correlation_id=payload['workflow_run_id'], payload=payload)


def test_supervisor_routes_repeated_bound_failure_to_run_manager(tmp_path):
    events = failure_events()
    rows = pipeline_items(events)
    assert len(rows) == 1
    row = rows[0]
    assert row['owner_route'] == 'run_manager'
    assert row['action_policy'] == 'needs_diagnosis'
    assert row['source_event_ids'] == [events[1].id, events[2].id]
    assert row['suggested_action']['workflow_run_id'] == 'run'
    assert row['suggested_action']['task_map_generation'] == 'map'
    directory = tmp_path / 'projections/supervisor'
    directory.mkdir(parents=True)
    (directory / 'snapshot.json').write_text(json.dumps({'attention_items':rows}))
    actions = _pending_attention_diagnostic_actions(tmp_path, events)
    assert len(actions) == 1
    assert actions[0]['action'] == 'diagnose-attention'
    assert actions[0]['workflow_run_id'] == 'run'
    assert actions[0]['task_id'] == 'TASK'
    assert actions[0]['source_event_ids'] == row['source_event_ids']
    assert pipeline_items(events + [deepcopy(events[-1])]) == rows


@pytest.mark.parametrize('fault', ['single','ordinary_wait','foreign_run','foreign_task',
    'unbound_cooldown','worker_failure','worker_cooldown','unadmitted','old_generation','worker_generation'])
def test_dispatch_attention_requires_current_kernel_repetition(fault):
    events = failure_events()
    if fault == 'single': events.pop()
    elif fault == 'ordinary_wait': events[1].type = 'task.pipeline.stage.waiting'
    elif fault == 'foreign_run': events[-1].correlation_id = 'other'
    elif fault == 'foreign_task': events[-1].task_id = 'OTHER'
    elif fault == 'unbound_cooldown': events[-1].causation_id = None
    elif fault == 'worker_failure': events[1].origin = 'worker'
    elif fault == 'worker_cooldown': events[-1].origin = 'worker'
    elif fault == 'unadmitted': events.pop(0)
    elif fault == 'worker_generation': events[0].origin = 'worker'
    elif fault == 'old_generation':
        later = deepcopy(events[0]); later.id = 'new-gen'; later.payload['task_map_generation'] = 'new-map'
        events.append(later)
    assert not pipeline_items(events)


@pytest.mark.parametrize('progress', ['dispatch','new_generation'])
def test_success_retires_attention_and_stale_rm_snapshot(tmp_path, progress):
    events = failure_events(); rows = pipeline_items(events)
    assert rows
    directory = tmp_path / 'projections/supervisor'; directory.mkdir(parents=True)
    (directory / 'snapshot.json').write_text(json.dumps({'attention_items':rows}))
    if progress == 'dispatch': events.append(dispatch(events[1]))
    else:
        later = deepcopy(events[0]); later.id = 'new-gen'; later.payload['task_map_generation'] = 'new-map'
        events.append(later)
    assert not pipeline_items(events)
    assert apply_attention_lifecycle(rows, events)[0]['status'] == 'resolved'
    assert not _pending_attention_diagnostic_actions(tmp_path, events)


@pytest.mark.parametrize('progress', ['heartbeat','other_task','other_run','other_stage','other_generation','worker_dispatch'])
def test_unrelated_activity_cannot_hide_dispatch_failure(progress):
    events = failure_events(); rows = pipeline_items(events)
    assert rows
    if progress == 'heartbeat':
        later = ZfEvent(type='worker.heartbeat', origin='kernel', task_id='TASK', correlation_id='run', payload={'task_id':'TASK'})
    elif progress == 'other_task': later = dispatch(events[1], task_id='OTHER')
    elif progress == 'other_run': later = dispatch(events[1], workflow_run_id='other')
    elif progress == 'other_stage': later = dispatch(events[1], task_pipeline_stage='verify')
    elif progress == 'other_generation': later = dispatch(events[1], task_map_generation='other-map')
    else:
        later = dispatch(events[1]); later.origin = 'worker'
    events.append(later)
    assert pipeline_items(events)
    assert apply_attention_lifecycle(rows, events)[0]['status'] == 'open'


def test_success_before_late_cooldown_does_not_reopen_diagnosis():
    generation, failed, cooldown = failure_events()
    assert not pipeline_items([generation, failed, dispatch(failed), cooldown])


def test_supervisor_entry_keeps_old_unresolved_dispatch_in_full_history(tmp_path):
    from zf.runtime.supervisor_inspection import build_supervisor_snapshot

    events = failure_events() + [ZfEvent(type='worker.heartbeat', origin='kernel',
        task_id='OTHER', payload={'task_id':'OTHER'}) for _ in range(501)]
    state = tmp_path / 'state'; state.mkdir()
    snapshot = build_supervisor_snapshot(state, project_root=tmp_path, project_id='dispatch-test', events=events)
    rows = [r for r in snapshot['attention_items'] if r.get('failure_class') == 'task_pipeline_dispatch_deferred']
    assert len(rows) == 1 and rows[0]['status'] == 'open'
    events.append(dispatch(events[1]))
    snapshot = build_supervisor_snapshot(state, project_root=tmp_path, project_id='dispatch-test', events=events)
    assert not [r for r in snapshot['attention_items'] if r.get('failure_class') == 'task_pipeline_dispatch_deferred']


def test_multiple_obsolete_failures_do_not_bypass_causal_repetition_guard():
    events = failure_events()
    old = deepcopy(events[1]); old.id = 'old-failure'; old.payload['task_map_generation'] = 'old-map'
    assert not pipeline_items([events[0], old, events[1]])
    assert len(pipeline_items([events[0], old, events[1], events[2]])) == 1


def test_task_completion_before_late_cooldown_stays_retired():
    generation, failed, cooldown = failure_events()
    done = ZfEvent(type='task.done', origin='kernel', task_id='TASK', correlation_id='run',
                  payload={'workflow_run_id':'run'})
    assert not pipeline_items([generation, failed, done, cooldown])
