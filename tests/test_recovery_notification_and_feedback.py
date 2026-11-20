import pytest

from tests.test_control_actions_recovery import _req, _service
from tests.test_run_manager import _config, _state
from zf.core.events.model import ZfEvent


def test_web_dismiss_is_consumed_without_running_a_recovery(tmp_path, monkeypatch):
    from zf.web.server import _web_action
    from zf.runtime.run_manager import run_manager_tick
    state,log,writer=_state(tmp_path);config=_config()
    escalation=writer.append(ZfEvent(type='human.escalate',actor='run-manager',origin='kernel',
        payload={'schema_version':'human-escalation-package.v1','decision_token':'hdec-test',
        'task_id':'T1','failure_class':'resident_agent_recommendation','owner_route':'run_manager',
        'blocking_scope':'task','mutating_resume_supported':False,'action':'agent-recommendation'}))
    before=(state/'kanban.json').read_bytes()
    monkeypatch.setenv('ZF_WEB_ACTION_TOKEN','test-token')
    result=_web_action(state,'human-decision-dismiss',payload={'decision_token':'hdec-test',
        'escalation_event_id':escalation.id,'created_event_id':escalation.id,
        'approval_ref':'owner:test','reason':'host queue already owns recovery'},
        authorization=None,x_zf_web_token='test-token',x_idempotency_key='dismiss-once',
        config=config,project_root=tmp_path)
    assert result['ok']
    monkeypatch.setattr('zf.runtime.run_manager._execute_controlled_run_action',
        lambda **kw:pytest.fail('dismiss must not execute recovery'))
    first=run_manager_tick(state_dir=state,writer=writer,config=config,project_root=tmp_path)
    assert first.human_decisions_applied==1
    assert first.human_decisions_rejected==0
    run_manager_tick(state_dir=state,writer=writer,config=config,project_root=tmp_path)
    rows=[e for e in log.read_all() if e.type=='run.manager.human_decision.applied']
    assert len(rows)==1
    assert rows[0].payload['source_event_id']==result['event_id']
    assert rows[0].payload['reason']=='host queue already owns recovery'
    assert rows[0].payload['next_route']=='none'
    assert (state/'kanban.json').read_bytes()==before
    assert not any(e.type in {'workflow.resume.applied','task.done','test.passed'} for e in log.read_all())


def test_unknown_dismiss_is_rejected(tmp_path):
    from zf.runtime.run_manager import run_manager_tick

    state, log, writer = _state(tmp_path)
    writer.append(ZfEvent(type='human.escalation.acknowledged', actor='zf-web', origin='kernel',
        payload={'decision_token': 'hdec-unknown', 'decision': 'dismissed'}))
    result = run_manager_tick(state_dir=state, writer=writer, config=_config(), project_root=tmp_path)
    assert result.human_decisions_applied == 0
    assert result.human_decisions_rejected == 1
    assert not any(e.type == 'workflow.reconcile.requested' for e in log.read_all())


@pytest.mark.parametrize('feedback',[['Inspect retained receipts against original scope'],[{}]])
def test_retrigger_feedback_reaches_frozen_recovery_input(tmp_path,feedback):
    from zf.runtime.reader_replan_feedback import repair_input_refs
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref
    service=_service(tmp_path)
    source=service.writer.append(ZfEvent(type='candidate.ready',actor='zf-cli',origin='kernel',
        payload={'workflow_run_id':'R1','task_ids':['T1','T2'],'commit':'a'*40,
            'candidate_ref':'candidate/P1','artifact_refs':['artifact.json']}))
    request=_req({});result=service.execute(action='stage-retrigger',requested_action='stage-retrigger',
        requested=request,payload={'source_event_id':source.id,'operator_feedback':feedback})
    emitted=[e for e in service.writer.event_log.read_all() if e.payload.get('redrive_of')==source.id]
    if not isinstance(feedback[0],str):
        assert not result['ok'];assert not emitted;return
    assert result['ok'];trigger=emitted[0]
    assert trigger.payload['task_ids']==source.payload['task_ids']
    assert trigger.payload['commit']==source.payload['commit']
    assert trigger.payload['artifact_refs']==source.payload['artifact_refs']
    refs=repair_input_refs(service.state_dir,trigger.payload,workflow_run_id='R1',task_id='T1',source_event_id=trigger.id)
    body=hydrate_sidecar_ref(service.state_dir,refs[0]).payload
    assert body['operator_feedback']==feedback
    assert body['rework_feedback'][-1]['source_event_id']==request.id
