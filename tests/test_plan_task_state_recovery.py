"""Task-state admission conflicts reach RM without starting another Planner."""
import json
from types import SimpleNamespace

import pytest

from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime.plan_admission import emit_plan_admission_cancel
from zf.runtime.supervisor_attention import build_attention_items
from zf.runtime.run_manager import _pending_attention_diagnostic_actions
from tests.test_plan_admission_replan_lineage import _config


def test_terminal_admission_has_one_diagnosis_owner_and_no_schema_failure(tmp_path):
    log = EventLog(tmp_path / 'events.jsonl')
    writer = EventWriter(log)
    runtime = SimpleNamespace(event_log=log, event_writer=writer, config=_config())
    trigger = writer.append(ZfEvent(type='task_map.ready', origin='kernel', correlation_id='run',
        payload={'workflow_run_id': 'run', 'task_map_ref': 'map.json', 'rework_categories': ['schema_gap']}))
    args = dict(trigger_event=trigger, stage_id='impl', trace_id='run', pdd_id='P1',
                feature_id='P1', task_map_ref='map.json', reason='terminal_tasks', task_ids=['T1'])
    emit_plan_admission_cancel(runtime, **args)
    emit_plan_admission_cancel(runtime, **args)
    events = log.read_all()
    assert not any(e.type in {'prd.plan.failed', 'human.escalate'} for e in events)
    rejected = [e for e in events if e.type == 'task.fanout.rejected']
    assert len(rejected) == 1
    assert 'rework_categories' not in rejected[0].payload
    attention = build_attention_items(events=events, automation={}, failure_signals=[], plan_integrity={})
    path = tmp_path / 'projections/supervisor/snapshot.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'attention_items': attention}))
    actions = _pending_attention_diagnostic_actions(tmp_path, events)
    assert len(actions) == 1
    assert actions[0]['action'] == 'diagnose-attention'
    cancelled = next(e for e in events if e.type == 'fanout.cancelled')
    assert actions[0]['source_event_ids'] == [cancelled.id]
    assert actions[0]['workflow_run_id'] == 'run'


def test_replay_repairs_diagnosis_append_after_cancellation_crash(tmp_path, monkeypatch):
    log = EventLog(tmp_path / 'events.jsonl')
    writer = EventWriter(log)
    runtime = SimpleNamespace(event_log=log, event_writer=writer, config=_config())
    trigger = writer.append(ZfEvent(type='task_map.ready', origin='kernel',
                                   payload={'workflow_run_id': 'run'}))
    args = dict(trigger_event=trigger, stage_id='impl', trace_id='run', pdd_id='P1',
                feature_id='P1', task_map_ref='map.json', reason='terminal_tasks', task_ids=['T1'])
    append = writer.append
    def crash(event):
        if event.type == 'task.fanout.rejected':
            raise RuntimeError('simulated crash before diagnosis')
        return append(event)
    monkeypatch.setattr(writer, 'append', crash)
    with pytest.raises(RuntimeError, match='simulated crash'):
        emit_plan_admission_cancel(runtime, **args)
    # The first durable event already owns diagnosis, even if no trigger is
    # replayed after restart. The second append is an audit, not a wake dependency.
    attention = build_attention_items(events=log.read_all(), automation={}, failure_signals=[], plan_integrity={})
    path = tmp_path / 'projections/supervisor/snapshot.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'attention_items': attention}))
    actions = _pending_attention_diagnostic_actions(tmp_path, log.read_all())
    assert len(actions) == 1
    assert actions[0]['action'] == 'diagnose-attention'
    assert actions[0]['workflow_run_id'] == 'run'
    monkeypatch.setattr(writer, 'append', append)
    emit_plan_admission_cancel(runtime, **args)
    emit_plan_admission_cancel(runtime, **args)
    events = log.read_all()
    cancelled = [e for e in events if e.type == 'fanout.cancelled']
    rejected = [e for e in events if e.type == 'task.fanout.rejected']
    assert len(cancelled) == len(rejected) == 1
    assert rejected[0].causation_id == cancelled[0].id
    rebuilt = build_attention_items(events=[rejected[0]], history_events=events,
                                   automation={}, failure_signals=[], plan_integrity={})
    assert rebuilt == attention
    assert build_attention_items(events=[rejected[0]], automation={},
                                 failure_signals=[], plan_integrity={}) == []
    cancelled[0].origin = 'worker'
    assert build_attention_items(events=[cancelled[0]], automation={},
                                 failure_signals=[], plan_integrity={}) == []
