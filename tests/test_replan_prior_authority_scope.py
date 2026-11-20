from types import SimpleNamespace

import pytest

from zf.core.events import ZfEvent
from zf.runtime.replan_resynth import build_replan_resynth_event
from zf.runtime.plan_synth_handoff import build_plan_handoff_input_refs
from zf.runtime.stage_failure_replan import plan_reader_stage_replan
from tests.test_replan_authority_inputs import _return_to_plan
from tests.test_stage_failure_replan import _config


def test_new_candidate_replan_keeps_previous_oa_as_evidence_only(tmp_path):
    runtime, marker, old = _return_to_plan(tmp_path)
    before = dict(old.payload)
    config = SimpleNamespace(workflow=SimpleNamespace(
        admission_replan=SimpleNamespace(enabled=True, resynth_trigger=old.type), stages=[]))
    plan = SimpleNamespace(trace_id=old.payload['trace_id'], pdd_id=old.payload['pdd_id'],
        source_event_id='new-candidate-failure', source_event_type='test.failed',
        attempt=1, classification='yaml_routing')
    result = build_replan_resynth_event(plan=plan, events=runtime.event_log.read_all(),
        config=config, state_dir=runtime.state_dir)
    assert result is not None
    assert 'orchestrator_decision_ref' not in result.payload
    assert result.payload['historical_orchestration_context']['orchestrator_decision_ref'] == marker.payload['orchestrator_decision_ref']
    refs = build_plan_handoff_input_refs(state_dir=runtime.state_dir,
        project_root=runtime.project_root, payload=result.payload)
    assert any(r['source_id'] == 'plan-rework-observation' for r in refs)
    assert not any(r['source_id'] == 'plan-rework-decision' for r in refs)
    assert old.payload == before


@pytest.mark.parametrize('authorized,classification', [
    (True, 'yaml_routing'), (False, 'yaml_routing'),
    (True, 'oa_semantic_return_to_plan'), (True, ''),
])
def test_only_explicit_new_generation_retires_prior_non_oa_authority(authorized, classification):
    ref = {'ref': 'prior-decision.json', 'sha256': 'a' * 64}
    origin = ZfEvent(id='origin', type='issue.requested', payload={
        'workflow_run_id': 'run', 'replan_classification': classification,
        'orchestrator_decision_ref': ref, 'orchestration_delta_ref': 'old-delta.json',
    })
    failure = ZfEvent(type='issue.triage.failed', payload={
        'workflow_run_id': 'run', 'trigger_event_id': origin.id})
    result, _ = plan_reader_stage_replan(_config(), [origin, failure], failure,
        authorized_generation='owner-repair' if authorized else '',
        authorization_event_id='owner-request' if authorized else '',
        authorization_ref='owner:repair' if authorized else '')
    assert result is not None
    if authorized and classification == 'yaml_routing':
        assert 'orchestrator_decision_ref' not in result.payload
        context = result.payload['historical_orchestration_context']
        assert context['orchestrator_decision_ref'] == ref
        assert context['authority'] == 'observation_only'
        assert result.payload['operator_authorization_ref'] == 'owner:repair'
    else:
        assert result.payload['orchestrator_decision_ref'] == ref
    assert origin.payload['orchestrator_decision_ref'] == ref
