"""An aborted admission must not remain approvable or lend approval authority."""
from pathlib import Path

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.plan_approval_replay import approved_equivalent_plan_id, pending_plan_requests
from zf.runtime.plan_approval_state import decide_pending_plan


def events():
    request = ZfEvent(type='plan.approval.requested', origin='kernel', payload={
        'plan_id':'ready-1','workflow_run_id':'run-1','stage_id':'impl','pdd_id':'P1',
        'plan_fingerprint':'plan-approval.v2:'+'a'*64})
    cancel = ZfEvent(type='fanout.cancelled', origin='kernel', causation_id='ready-1',
        payload={'failure_scope':'plan_admission','trigger_event_id':'ready-1'})
    return request, cancel


@pytest.mark.parametrize('late_request', [False, True])
def test_cancelled_admission_is_no_longer_pending(late_request):
    request, cancel = events()
    assert pending_plan_requests([cancel, request] if late_request else [request, cancel]) == []


@pytest.mark.parametrize('event_type', ['plan.approved', 'plan.rejected'])
def test_cancelled_admission_cannot_be_decided(tmp_path: Path, event_type: str):
    writer=EventWriter(EventLog(tmp_path/'events.jsonl'))
    for event in events():writer.append(event)
    before=writer.event_log.read_all()
    result, reason=decide_pending_plan(writer,state_dir=tmp_path,plan_id='ready-1',event_type=event_type,payload={})
    assert result is None and 'not awaiting review' in reason
    assert writer.event_log.read_all()==before


def test_old_approval_cannot_authorize_replacement_after_cancel():
    request,cancel=events()
    approved=ZfEvent(type='plan.approved',actor='operator',payload={'plan_id':'ready-1'})
    assert not approved_equivalent_plan_id([request,approved,cancel],{**request.payload,'plan_id':'ready-2'})


@pytest.mark.parametrize('fault', ['origin','cause','scope','trigger'])
def test_unrelated_cancel_does_not_retire(fault):
    request,cancel=events()
    if fault=='origin':cancel.origin='worker'
    elif fault=='cause':cancel.causation_id='other'
    elif fault=='scope':cancel.payload['failure_scope']='task'
    else:cancel.payload['trigger_event_id']='other'
    assert pending_plan_requests([request,cancel])==[request]


def test_new_trigger_requires_fresh_review():
    request,cancel=events()
    new=ZfEvent(type=request.type,origin='kernel',payload={**request.payload,'plan_id':'ready-2'})
    assert pending_plan_requests([request,cancel,new])==[new]


def test_terminal_task_conflict_does_not_revoke_plan_review():
    request, cancel = events()
    cancel.payload['reason'] = 'terminal_tasks'
    approved = ZfEvent(type='plan.approved', actor='operator', payload={'plan_id': 'ready-1'})
    assert approved_equivalent_plan_id([request, approved, cancel],
                                      {**request.payload, 'plan_id': 'ready-2'}) == 'ready-1'
