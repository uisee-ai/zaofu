"""The real admitted gap-map shape retires its source failure upstream."""

import pytest

from tests.test_reader_failure_supersession import scenario
from tests.test_run_manager_rework_triage import _config, _state
from zf.core.events.model import ZfEvent
from zf.runtime.run_manager import build_run_manager_projection, run_manager_tick
from zf.runtime.run_manager_rework_triage import (
    active_immediate_replan_task_ids, pending_immediate_replan_actions,
)


def admitted_events():
    _, rows, _ = scenario()
    source, _, _, ready, admitted, _ = rows
    source.task_id = 'old'  # Canonical Impl failure identity, as emitted by admission.
    triage = ZfEvent(type='task.rework.triage.completed', task_id='old', id='triage',
                    origin='kernel', correlation_id='run', payload={
                        'failed_event_id': source.id, 'recommended_action': 'request_replan',
                        'retryable': False,
                    })
    # Production task_map.ready carries supersedes inside rework_summary, not
    # a top-level supersedes_task_ids field. No discovery needs to exist yet.
    return [source, triage, ready, admitted]


def test_admitted_successor_retires_immediate_recovery():
    events = admitted_events()
    assert pending_immediate_replan_actions(events) == []
    assert active_immediate_replan_task_ids(events) == set()


@pytest.mark.parametrize('change', ['unadmitted', 'other_run', 'other_source', 'other_task',
                                   'partial_split', 'retained_old', 'external', 'late_triage'])
def test_only_causal_complete_kernel_admission_retires_recovery(change):
    events = admitted_events()
    source, triage, ready, admitted = events
    if change == 'unadmitted':
        events.remove(admitted)
    elif change == 'other_run':
        admitted.payload['workflow_run_id'] = 'other'
    elif change == 'other_source':
        ready.payload['rework_of'] = 'another-failure'
    elif change == 'other_task':
        ready.payload['rework_summary']['gap_tasks'][0]['supersedes_task_ids'] = ['other']
    elif change == 'partial_split':
        ready.payload['rework_summary']['gap_tasks'].append(
            {'task_id': 'second', 'supersedes_task_ids': ['old']})
    elif change == 'retained_old':
        admitted.payload['task_ids'].append('old')
    elif change == 'external':
        admitted.origin = 'external'
    else:
        # Classification can arrive after recovery, but must not reopen its
        # already replaced source failure.
        events = [source, ready, admitted, triage]
        assert pending_immediate_replan_actions(events) == []
        assert active_immediate_replan_task_ids(events) == set()
        return
    assert len(pending_immediate_replan_actions(events)) == 1
    assert active_immediate_replan_task_ids(events) == {'old'}


def test_run_manager_entry_does_not_plan_obsolete_recovery(tmp_path):
    state, log, writer = _state(tmp_path)
    for event in admitted_events():
        writer.append(event)
    config = _config()
    projection = build_run_manager_projection(state, config=config)
    assert not any(a.get('task_id') == 'old' for a in projection['pending_actions'])
    run_manager_tick(state_dir=state, writer=writer, event_log=log, config=config,
                     spawn_repairs=False)
    assert not any(e.type in {'flow.discovery.requested', 'run.manager.action.planned'}
                   and (e.task_id == 'old' or e.payload.get('task_id') == 'old')
                   for e in log.read_all())
