from types import SimpleNamespace

import pytest

from zf.core.events.model import ZfEvent
from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.runtime.escalation_terminal import converge_unrecoverable_escalations
from zf.runtime.stage_failure_replan import plan_reader_stage_replan


def scenario():
    def event(kind, id, **payload):
        return ZfEvent(type=kind, id=id, origin='kernel', correlation_id='run',
                       payload={'workflow_run_id': 'run', **payload})
    source = event('dev.blocked', 'source', task_id='old')
    trigger = event('flow.discovery.trigger', 'trigger', rework_of=source.id,
                    task_id='old', affected_task_ids=['old'])
    started = event('fanout.started', 'fanout', trigger_event_id=trigger.id,
                    fanout_id='fanout', stage_id='prd-discovery')
    ready = event('task_map.ready', 'ready', rework_of=source.id,
                  rework_summary={'gap_tasks': [
                      {'task_id': 'new', 'supersedes_task_ids': ['old']}]})
    admitted = event('task.pipeline.generation.admitted', 'admitted',
                     schema_version='task-pipeline-generation.v1',
                     trigger_event_id=ready.id, task_ids=['new'])
    failure = event('flow.discovery.failed', 'failed', fanout_id='fanout',
                    stage_id='prd-discovery', affected_task_ids=['old'])
    config = SimpleNamespace(workflow=SimpleNamespace(stages=[SimpleNamespace(
        id='prd-discovery', topology='fanout_reader', trigger='flow.discovery.trigger',
        failure_event='', aggregate=SimpleNamespace(failure_event=failure.type),
    )]))
    return config, [source, trigger, started, ready, admitted, failure], failure


def test_admitted_replacement_stops_old_reader_replan():
    config, events, failure = scenario()
    result, reason = plan_reader_stage_replan(config, events, failure)
    assert result is None
    assert reason == 'superseded_by_admitted_task_replacement'


@pytest.mark.parametrize('change', ['unadmitted', 'other_run', 'other_cause', 'other_task',
                                     'partial', 'not_kernel', 'wrong_trigger', 'partial_split', 'retained_old',
                                     'missing_source', 'failure_other_run'])
def test_unproven_replacement_keeps_existing_recovery(change):
    config, events, failure = scenario()
    ready, admitted = events[3:5]
    if change == 'unadmitted':
        events.remove(admitted)
    elif change == 'other_run':
        admitted.payload['workflow_run_id'] = 'another-run'
    elif change == 'other_cause':
        ready.payload['rework_of'] = 'another-failure'
    elif change == 'other_task':
        ready.payload['rework_summary']['gap_tasks'][0]['supersedes_task_ids'] = ['another-task']
    elif change == 'partial':
        failure.payload['affected_task_ids'].append('unresolved')
    elif change == 'not_kernel':
        admitted.origin = 'external'
    elif change == 'partial_split':
        ready.payload['rework_summary']['gap_tasks'].append(
            {'task_id': 'new-second', 'supersedes_task_ids': ['old']})
    elif change == 'retained_old':
        admitted.payload['task_ids'].append('old')
    elif change == 'missing_source':
        events.pop(0)
    elif change == 'failure_other_run':
        failure.payload['workflow_run_id'] = 'another-run'
    else:
        admitted.payload['trigger_event_id'] = 'another-ready'
    result, _ = plan_reader_stage_replan(config, events, failure)
    assert result is not None


def test_run_manager_does_not_terminalize_late_obsolete_escalation(tmp_path):
    _, events, failure = scenario()
    escalation = ZfEvent(type='human.escalate', correlation_id='run', payload={
        'workflow_run_id': 'run', 'failure_class': 'stage_replan_cap_exhausted',
        'source_event_id': failure.id, 'recoverable': False,
    })
    log = EventLog(tmp_path/'events.jsonl')
    for event in [*events, escalation]:
        log.append(event)
    assert converge_unrecoverable_escalations(log.read_all(), writer=EventWriter(log)) == 0
    assert not any(e.type == 'run.goal.blocked' for e in log.read_all())


def test_reactor_uses_supersession_before_escalation(tmp_path):
    from zf.runtime.orchestrator_reactor import EventReactorMixin
    config, events, failure = scenario()
    log = EventLog(tmp_path/'events.jsonl')
    for event in events:
        log.append(event)
    runtime = SimpleNamespace(config=config, state_dir=tmp_path, event_log=log,
                              event_writer=EventWriter(log))
    assert EventReactorMixin._on_reader_stage_failure_replan(runtime, failure) is None
    assert len(log.read_all()) == len(events)
