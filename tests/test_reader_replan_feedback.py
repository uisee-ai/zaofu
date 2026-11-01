"""Authorized recovery retains new operator feedback without rewriting history."""
import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.plan_synth_handoff import build_plan_handoff_input_refs
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from tests.test_control_actions_recovery import _req
from tests.test_reader_repair_target import setup_reader


def _apply(service, payload):
    return service.execute(action='stage-replan-new-generation',
        requested_action='stage-replan-new-generation', payload=payload, requested=_req(payload))


def test_operator_feedback_is_bound_to_new_generation_and_replay(tmp_path):
    svc, origin, payload, _ = setup_reader(tmp_path)
    payload.pop('repair_target')
    payload['operator_feedback'] = ['Wire the cold setup command.', 'Bind task-local input ports.']
    result = _apply(svc, payload)
    assert result['ok'], result
    trigger = next(e for e in svc.writer.event_log.read_all() if e.id == result['stage_trigger_event_id'])
    assert trigger.payload['operator_feedback'] == payload['operator_feedback']
    feedback = trigger.payload['rework_feedback']
    assert feedback[0]['message'] == 'flow.discovery.failed'
    assert [f['message'] for f in feedback[1:]] == payload['operator_feedback']
    assert trigger.payload['contract_snapshot_ref'] == origin.payload['contract_snapshot_ref']
    sources = build_plan_handoff_input_refs(state_dir=svc.state_dir, project_root=tmp_path,
        payload={'trigger_payload': trigger.payload}, source_event_id=trigger.id)
    context = next(s for s in sources if s['source_id'] == 'plan-rework-context')
    body = hydrate_sidecar_ref(svc.state_dir, context).payload
    assert body['rework_feedback'] == feedback
    assert body['operator_feedback'] == payload['operator_feedback']
    assert _apply(svc, payload)['replayed']
    changed = _apply(svc, {**payload, 'operator_feedback': ['Different instructions']})
    assert not changed['ok']
    assert 'different operator feedback' in changed['reason']
    assert len([e for e in svc.writer.event_log.read_all() if e.type == 'run.goal.updated']) == 1


@pytest.mark.parametrize('bad', ['text', None, [1], ['  '], [{'message': 'text'}]])
def test_invalid_operator_feedback_cannot_reopen_run(tmp_path, bad):
    svc, _, payload, _ = setup_reader(tmp_path)
    payload.pop('repair_target')
    result = _apply(svc, {**payload, 'operator_feedback': bad})
    assert not result['ok']
    assert not any(e.type == 'run.goal.updated' for e in svc.writer.event_log.read_all())


def test_later_generation_without_feedback_does_not_reuse_old_input(tmp_path):
    svc, _, payload, _ = setup_reader(tmp_path)
    payload.pop('repair_target')
    first = _apply(svc, {**payload, 'operator_feedback': ['Previous repair direction.']})
    assert first['ok']
    svc.writer.append(ZfEvent(type='fanout.started', correlation_id='RUN', payload={
        'fanout_id': 'second-reader', 'stage_id': 'prd-post-verify-discovery',
        'trigger_event_id': first['stage_trigger_event_id'],
    }))
    failure = svc.writer.append(ZfEvent(type='flow.discovery.failed', task_id='T1',
        correlation_id='RUN', payload={'workflow_run_id': 'RUN', 'fanout_id': 'second-reader',
        'reason': 'New failure', 'stage_replan_generation': first['stage_replan_generation']}))
    escalation = svc.writer.append(ZfEvent(type='human.escalate', task_id='T1',
        correlation_id='RUN', causation_id=failure.id, payload={
            'failure_class': 'stage_replan_cap_exhausted', 'source_event_id': failure.id,
            'allowed_actions': ['operator_review', 'start_new_generation'],
        }))
    second_payload = {**payload, 'escalation_event_id': escalation.id}
    second = _apply(svc, second_payload)
    assert second['ok'], second
    trigger = next(e for e in svc.writer.event_log.read_all() if e.id == second['stage_trigger_event_id'])
    assert 'operator_feedback' not in trigger.payload
    assert _apply(svc, second_payload)['replayed']
