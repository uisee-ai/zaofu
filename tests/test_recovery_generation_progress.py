"""A successful owner recovery retires only its old no-progress window."""
from copy import deepcopy

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.run_manager_router import build_no_progress_projection
from tests.test_reader_repair_target import setup_reader


def recovered_events(tmp_path):
    service, _, payload, apply = setup_reader(tmp_path)
    payload.pop('repair_target')
    escalation = service.writer.event_log.read_all()[-1]
    for _ in range(3):
        escalation = service.writer.append(ZfEvent(type=escalation.type, correlation_id='RUN',
            payload={**escalation.payload, 'reason': 'stage replan cap exhausted'}))
    payload['escalation_event_id'] = escalation.id
    before = service.writer.event_log.read_all()
    assert build_no_progress_projection(before)['status'] == 'tripped'
    assert apply()['ok']
    return service, escalation, apply, before


def test_actual_controlled_recovery_retires_old_no_progress_window(tmp_path):
    service, _, _, before = recovered_events(tmp_path)
    projection = build_no_progress_projection(service.writer.event_log.read_all())
    assert projection['status'] == 'clear'
    assert build_no_progress_projection(before)['status'] == 'tripped'


@pytest.mark.parametrize('defect', ['request_only', 'failed', 'trigger_missing', 'wrong_run', 'wrong_source'])
def test_unproven_generation_does_not_retire_failures(tmp_path, defect):
    service, _, _, _ = recovered_events(tmp_path)
    events = deepcopy(service.writer.event_log.read_all())
    completed = next(e for e in events if e.type == 'runtime.action.completed')
    if defect == 'request_only':
        events.remove(completed)
    elif defect == 'failed':
        completed.payload['status'] = 'failed'
    elif defect == 'trigger_missing':
        completed.payload['stage_trigger_event_id'] = 'missing'
    elif defect == 'wrong_run':
        completed.payload['run_id'] = 'OTHER'
    else:
        completed.payload['escalation_event_id'] = 'other-escalation'
    assert build_no_progress_projection(events)['status'] == 'tripped'


def test_new_failures_and_other_run_remain_counted_and_replay_does_not_reset(tmp_path):
    service, escalation, apply, _ = recovered_events(tmp_path)
    for _ in range(3):
        service.writer.append(ZfEvent(type=escalation.type, correlation_id='RUN',
            payload=deepcopy(escalation.payload)))
    assert apply()['replayed']
    projection = build_no_progress_projection(service.writer.event_log.read_all())
    assert projection['status'] == 'tripped'
    assert projection['items'][0]['count'] == 3


def test_recovery_does_not_clear_other_run_same_failure(tmp_path):
    service, escalation, _, before = recovered_events(tmp_path)
    other = [ZfEvent(type=escalation.type, correlation_id='OTHER',
        payload={**escalation.payload, 'workflow_run_id': 'OTHER'}) for _ in range(3)]
    events = [*before, *other, *service.writer.event_log.read_all()[len(before):]]
    projection = build_no_progress_projection(events)
    assert projection['status'] == 'tripped'
    assert len(projection['items']) == 1
    assert projection['items'][0]['count'] == 3


def test_run_manager_executes_new_plan_repair_after_owner_generation(tmp_path):
    from zf.runtime.run_manager import (
        build_run_manager_projection, run_manager_tick,
    )
    from tests.test_run_manager import _config

    service, _, _, _ = recovered_events(tmp_path)
    log = service.writer.event_log
    for event in log.read_all():
        if event.type == 'human.escalate':
            service.writer.append(ZfEvent(type='run.manager.human_decision.applied',
                correlation_id='RUN',
                payload={'escalation_event_id': event.id,
                    'decision_token': event.causation_id or event.id,
                    'decision': 'dismissed', 'status': 'dismissed'}))
    service.writer.append(ZfEvent(type='task_map.ready', correlation_id='RUN', payload={
        'pdd_id': 'T1', 'workflow_run_id': 'RUN', 'trace_id': 'RUN',
        'task_map_ref': 'artifacts/map.json', 'task_map_generation': 'GEN',
    }))
    rejection = service.writer.append(ZfEvent(type='plan.rejected', correlation_id='RUN', payload={
        'pdd_id': 'T1', 'workflow_run_id': 'RUN', 'trace_id': 'RUN',
        'plan_id': 'current-plan', 'reason': 'Complete task input body is missing.',
    }))
    config = _config()
    projection = build_run_manager_projection(service.state_dir, events=log.read_all(),
        config=config, project_root=tmp_path)
    assert projection['no_progress']['status'] == 'clear'
    assert projection['continuation']['next_operation'] is not None
    assert projection['continuation']['next_operation']['action'] == 'candidate-rework-apply'
    result = run_manager_tick(state_dir=service.state_dir, writer=service.writer,
        event_log=log, config=config, project_root=tmp_path, spawn_repairs=False)
    assert result.actions_applied == 1
    assert any(e.type == 'orchestrator.replan_requested' and
        e.payload.get('rework_of') == rejection.id for e in log.read_all())
