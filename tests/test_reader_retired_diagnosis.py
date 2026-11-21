"""A retired diagnosis must not occupy the next failure's recovery slot."""
import pytest

from zf.core.config.schema import ZfConfig
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime.run_manager import build_run_manager_projection


@pytest.mark.parametrize('change', ['same_fanout', 'new_fanout', 'pending', 'untrusted', 'wrong_reply'])
def test_new_failure_after_retired_diagnosis_is_dispatchable(tmp_path, change):
    writer = EventWriter(EventLog(tmp_path / 'events.jsonl'), default_origin='kernel')
    cfg = ZfConfig()
    cfg.runtime.run_manager.resident_agent.enabled = True
    writer.emit('run.started', payload={'workflow_run_id': 'r'})
    body = {'workflow_run_id': 'r', 'pdd_id': 'anchor', 'fanout_id': 'scan-1',
            'stage_id': 'prd-scan', 'failure_kind': 'infra'}
    first = writer.emit('prd.scan.failed', payload=body)
    initial = build_run_manager_projection(tmp_path, events=writer.event_log.read_all(), config=cfg)['pending_actions'][0]
    pending = {**initial, 'action': 'resident-agent-reprompt', 'request_kind': 'recovery_diagnosis',
               'request_id': 'diagnose', 'continuation_expected_events': ['run.manager.agent.recommendation']}
    writer.emit('run.manager.action.effect.pending', payload=pending)
    writer.emit('run.manager.resident.prompted', payload={'request_id': 'diagnose', 'instance_id': 'run-manager'})
    reply = writer.emit('run.manager.agent.recommendation', actor='run-manager', payload={
        'request_id': 'diagnose', 'recommended_route': 'human'})
    writer.emit('run.manager.action.effect.passed', payload=pending)
    if change != 'pending':
        writer.append(ZfEvent(type='run.manager.agent.recommendation.consumed', origin='worker' if change == 'untrusted' else 'kernel',
            payload={'source_event_id': 'wrong' if change == 'wrong_reply' else reply.id,
                     'status': 'superseded', 'reason': 'operation_precondition_changed'}))
    second = writer.emit('prd.scan.failed', payload={**body,
        'fanout_id': 'scan-2' if change == 'new_fanout' else 'scan-1'})
    p = build_run_manager_projection(tmp_path, events=writer.event_log.read_all(), config=cfg)
    actions = [a for a in p['pending_actions'] if a.get('failure_class') == 'prd_scan_failed']
    assert len(actions) == int(change in {'same_fanout', 'new_fanout'})
    if actions:
        action = actions[0]
        assert action['source_event_ids'] == [second.id]
        assert action['recovery_case_id'] == initial['recovery_case_id']
        from zf.runtime.run_manager import _resident_diagnosis_reprompt_action
        from zf.runtime.recovery_case import recovery_action_admission

        dispatch = _resident_diagnosis_reprompt_action(
            {**action, 'suggested_route': 'run_manager_resident_agent'},
            {'status': 'observing', 'tmux_session': 'resident', 'briefing_path': '/tmp/brief',
             'instance_id': 'run-manager'})
        assert dispatch is not None
        assert recovery_action_admission(writer.event_log.read_all(), dispatch)['status'] == 'admitted'
        writer.emit('run.manager.action.effect.pending', payload={**action, 'action': 'resident-agent-reprompt',
            'request_kind': 'recovery_diagnosis', 'request_id': 'new-diagnosis'})
        p = build_run_manager_projection(tmp_path, events=writer.event_log.read_all(), config=cfg)
        assert not [a for a in p['pending_actions'] if a.get('failure_class') == 'prd_scan_failed']
    assert sum(e.id == first.id for e in writer.event_log.read_all()) == 1
