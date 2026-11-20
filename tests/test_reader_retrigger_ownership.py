from types import SimpleNamespace

import pytest

from tests.test_control_actions_recovery import _req, _service
from zf.core.events import ZfEvent
from zf.runtime.stage_failure_replan import plan_reader_stage_replan
from zf.runtime.workflow_resume import _reader_stage_replan_checkpoints


def setup_retrigger(tmp_path):
    service = _service(tmp_path)
    stage = SimpleNamespace(id='candidate-review', topology='fanout_reader',
        trigger='candidate.ready', attempt_domain='verify', failure_event='',
        aggregate=SimpleNamespace(failure_event='test.failed', success_event='test.passed'))
    service.config = SimpleNamespace(workflow=SimpleNamespace(stages=[stage]))
    original = service.writer.append(ZfEvent(type='candidate.ready', origin='kernel',
        correlation_id='R1', payload={'workflow_run_id':'R1', 'pdd_id':'T1',
            'task_map_generation':'g1', 'target_ref':'candidate/T1'}))
    service.writer.append(ZfEvent(type='fanout.started', origin='kernel', correlation_id='R1',
        payload={'fanout_id':'old', 'stage_id':stage.id, 'trigger_event_id':original.id}))
    failure = service.writer.append(ZfEvent(type='test.failed', origin='kernel', correlation_id='R1',
        payload={'workflow_run_id':'R1', 'stage_id':stage.id, 'fanout_id':'old',
            'pdd_id':'T1', 'task_map_generation':'g1', 'failure_kind':'mixed'}))
    assert plan_reader_stage_replan(service.config, service.writer.event_log.read_all(), failure)[0]
    result = service.execute(action='stage-retrigger', requested_action='stage-retrigger',
        requested=_req({}), payload={'source_event_id':original.id,
            'operator_feedback':['Preserve the prior findings and receipts']})
    assert result['ok']
    trigger = next(e for e in service.writer.event_log.read_all() if e.id==result['event_id'])
    return service, stage, failure, trigger


def test_admitted_retrigger_retires_old_checkpoint_but_not_its_own_failure(tmp_path):
    service, stage, failure, trigger = setup_retrigger(tmp_path)
    service.writer.append(ZfEvent(type='fanout.started', origin='kernel', correlation_id='R1',
        payload={'fanout_id':'new', 'stage_id':stage.id, 'trigger_event_id':trigger.id}))
    events = service.writer.event_log.read_all()
    assert not _reader_stage_replan_checkpoints(events, [], service.config, state_dir=service.state_dir)
    assert plan_reader_stage_replan(service.config, events, failure)[1]=='owned_by_admitted_reader_retrigger'
    newer = service.writer.append(ZfEvent(type='test.failed', origin='kernel', correlation_id='R1',
        payload={**failure.payload, 'fanout_id':'new'}))
    events = service.writer.event_log.read_all()
    checkpoints = _reader_stage_replan_checkpoints(events, [], service.config, state_dir=service.state_dir)
    assert [c.blocking_event_id for c in checkpoints]==[newer.id]
    assert trigger.payload['operator_feedback']==['Preserve the prior findings and receipts']
    assert not any(e.type=='test.passed' for e in events)


@pytest.mark.parametrize('fault', ['unadmitted', 'foreign_stage', 'foreign_run', 'foreign_source',
    'untrusted', 'cycle', 'old_admission'])
def test_unproven_retrigger_does_not_hide_failure(tmp_path, fault):
    service, stage, failure, trigger = setup_retrigger(tmp_path)
    events = service.writer.event_log.read_all()
    successor = next(e for e in events if e.id==trigger.id)
    admission = ZfEvent(type='fanout.started', origin='kernel', correlation_id='R1',
        payload={'fanout_id':'new','stage_id':stage.id,'trigger_event_id':trigger.id})
    if fault=='foreign_stage': admission.payload['stage_id']='other'
    elif fault=='foreign_run': successor.payload['workflow_run_id']='R2'
    elif fault=='foreign_source': successor.payload['redrive_of']='unrelated'
    elif fault=='untrusted': admission.origin='external'
    elif fault=='cycle': successor.payload['redrive_of']=successor.id
    if fault=='old_admission': events.insert(events.index(failure), admission)
    elif fault!='unadmitted': events.append(admission)
    assert plan_reader_stage_replan(service.config, events, failure)[0] is not None
