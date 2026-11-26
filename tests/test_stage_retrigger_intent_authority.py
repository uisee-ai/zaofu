"""A Web intent proposes a recovery. The Owner decision authorizes it."""
import pytest

from zf.core.config.schema import ProjectConfig, WorkflowStageConfig, ZfConfig
from zf.core.events import EventLog, EventWriter
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.run_manager import _consume_human_decisions
from zf.web.server import _web_action


@pytest.fixture
def recovery(tmp_path, monkeypatch):
    monkeypatch.setenv('ZF_WEB_ACTION_TOKEN', 'local-test-token')
    state = tmp_path / '.state'
    state.mkdir()
    config = ZfConfig(project=ProjectConfig(name='test', state_dir='.state'))
    config.workflow.stages = [WorkflowStageConfig(
        id='plan', trigger='prd.scan.completed', topology='fanout_reader',
    )]
    writer = EventWriter(EventLog(state / 'events.jsonl'))
    source = writer.emit('prd.scan.completed', actor='orchestrator', payload={'status': 'completed'})
    service = ControlledActionService(state, writer, config=config, project_root=tmp_path, actor='operator')

    def web(action, body):
        return _web_action(state, action, payload=body, authorization=None, x_zf_web_token='local-test-token',
                           config=config, project_root=tmp_path)

    return service, source, web


def test_web_proposed_intent_cannot_authorize_stage_retrigger(recovery):
    service, source, web = recovery
    request = {'source_event_id': source.id}
    created = web('operator-intent-create', {'message': 'Review recovery of the Plan stage',
                  'action': 'stage-retrigger', 'action_request': request})
    assert created['status'] == 'proposed'
    log = service.writer.event_log
    intent = next(event for event in log.read_all() if event.id == created['event_id'])
    assert intent.payload['mutates_truth_directly'] is False
    result = service.execute(action='stage-retrigger', requested_action='stage-retrigger',
                             requested=intent, payload=request)
    assert not result['ok'], result
    assert result['_status_code'] == 409
    events = log.read_all()
    assert any(e.type == 'runtime.action.failed' and e.causation_id == intent.id for e in events)
    assert not any(e.payload.get('redrive_of') == source.id for e in events)


def test_owner_approved_run_manager_stage_retrigger_keeps_its_source(recovery):
    service, source, web = recovery
    gate = service.writer.emit('human.escalate', actor='run-manager', payload={
        'decision_token': 'review-plan-stage', 'action': 'stage-retrigger',
        'checkpoint_id': 'plan-stage', 'fingerprint': 'failed-plan-stage',
        'safe_resume_action': 'stage_retrigger', 'source_event_id': source.id,
        'reason': 'Review recovery of the Plan stage',
    })
    approval = web('human-decision-approve-controlled-action', {
        'decision_token': gate.payload['decision_token'], 'checkpoint_id': 'plan-stage',
        'created_event_id': gate.id, 'reason': 'Owner reviewed this Plan checkpoint',
    })
    assert approval['ok'], approval
    log = service.writer.event_log
    assert _consume_human_decisions(state_dir=service.state_dir, writer=service.writer,
                                    config=service.config, project_root=service.project_root,
                                    events=log.read_all(), causation_id=approval['event_id']) == (1, 0)
    events = log.read_all()
    redrives = [e for e in events if e.type == source.type and e.payload.get('redrive_of') == source.id]
    assert len(redrives) == 1
    planned = next(e for e in events if e.id == redrives[0].causation_id)
    assert planned.type == 'run.manager.action.planned' and planned.causation_id == approval['event_id']
