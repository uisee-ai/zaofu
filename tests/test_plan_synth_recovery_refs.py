"""Failed Critic recovery must bind the current, not an older, candidate."""

from copy import deepcopy

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.plan_synth_recovery_refs import failed_critic_candidate_refs, resume_failed_critic_candidate


def _case(state):
    candidate = {'source_id': 'child-result-planner', 'artifact_id': 'current.json',
                 'ref': 'artifacts/fanouts/current.json', 'sha256': 'a' * 64,
                 'allowed_paths': ['$.approved']}
    old = {'source_id': 'previous-plan-candidate-1', 'artifact_id': 'old.json',
           'ref': 'artifacts/fanouts/old.json', 'sha256': 'b' * 64, 'allowed_paths': ['$']}
    manifest = write_immutable_json_sidecar(state, {
        'schema_version': 'attempt-source-manifest.v1', 'attempt_id': 'attempt',
        'dispatch_id': 'attempt', 'workflow_run_id': 'run', 'sources': [candidate, old],
    }, root='attempts/source-manifests', kind='attempt_source_manifest',
       schema_version='attempt-source-manifest.v1', created_by='test')
    binding = {key: candidate[key] for key in ('source_id', 'artifact_id', 'ref', 'sha256')}
    contract = write_immutable_json_sidecar(state, {
        'schema_version': 'plan-synth-contract.v1', 'workflow_run_id': 'run',
        'fanout_id': 'fanout', 'stage_id': 'prd-plan', 'plan_revision': 'plan-r1',
        'child_bindings': [{'child_id': 'planner', **binding}], 'source_bindings': [binding],
    }, root='plan-synth/contracts', kind='plan_synth_contract',
       schema_version='plan-synth-contract.v1', created_by='test')
    payload = {'failure_class': 'reader_execution_failure', 'workflow_run_id': 'run',
        'fanout_id': 'fanout', 'stage_id': 'prd-plan', 'plan_revision': 'plan-r1',
        'attempt_id': 'attempt', 'operation_id': 'op', 'plan_synth_contract_ref': contract['ref'],
        'plan_synth_contract_digest': contract['sha256'], 'reviewed_plan_candidate_digest': contract['sha256']}
    dispatch = ZfEvent(type='fanout.synth.dispatched', payload={'operation_id': 'op',
        'attempt_id': 'attempt', 'run_id': 'attempt', 'fanout_id': 'fanout',
        'attempt_source_manifest': manifest})
    return payload, dispatch, candidate


def test_failed_critic_recovers_only_bound_current_candidate_and_scope(tmp_path):
    payload, dispatch, candidate = _case(tmp_path)
    assert failed_critic_candidate_refs(tmp_path, [dispatch], payload) == [candidate]


@pytest.mark.parametrize('change', [
    {'plan_revision': 'old'}, {'reviewed_plan_candidate_digest': 'f' * 64},
    {'failure_class': 'product_rejection'}, {'attempt_id': 'other'},
])
def test_unmatched_critic_identity_never_selects_candidate(tmp_path, change):
    payload, dispatch, _ = _case(tmp_path)
    assert failed_critic_candidate_refs(tmp_path, [dispatch], payload | change) == []


def test_duplicate_dispatch_or_tampered_manifest_never_selects_candidate(tmp_path):
    payload, dispatch, _ = _case(tmp_path)
    assert failed_critic_candidate_refs(tmp_path, [dispatch, deepcopy(dispatch)], payload) == []
    tampered = deepcopy(dispatch)
    tampered.payload['attempt_source_manifest']['sha256'] = 'f' * 64
    assert failed_critic_candidate_refs(tmp_path, [tampered], payload) == []


def test_tampered_contract_never_selects_candidate(tmp_path):
    payload, dispatch, _ = _case(tmp_path)
    path = tmp_path / payload['plan_synth_contract_ref']
    path.write_text(path.read_text() + ' ')
    assert failed_critic_candidate_refs(tmp_path, [dispatch], payload) == []


def _resume_case(tmp_path, monkeypatch):
    payload, dispatch, candidate = _case(tmp_path)
    control = {'execution_status': 'failed', 'failure_class': 'reader_execution_failure'}
    monkeypatch.setattr('zf.runtime.call_result_runtime.hydrate_admitted_control_result',
                        lambda *_: control)
    descriptor = {'ref': 'control', 'sha256': 'a' * 64, 'schema_version': 'plan-synthesis-result.v1'}
    monkeypatch.setattr('zf.runtime.call_result_envelope.hydrate_call_result_envelope',
                        lambda *_: {'control_result': descriptor})
    synth = ZfEvent(id='synth', type='fanout.synth.completed', payload={
        **payload, 'control_result_ref': descriptor})
    admission = ZfEvent(id='admission', type='workflow.call.result.admitted', payload={
        'source_event_id': synth.id, 'workflow_run_id': 'run', 'operation_id': 'op',
        'control_result_ref': descriptor, 'envelope_ref': {'ref': 'envelope'}})
    previous = ZfEvent(id='previous', type='prd.plan.failed', causation_id=synth.id,
                       payload={'workflow_run_id': 'run', 'stage_id': 'prd-plan'})
    failure = ZfEvent(id='failure', type='prd.plan.failed', payload={
        'workflow_run_id': 'run', 'stage_id': 'prd-plan', 'rework_of': previous.id})
    return [dispatch, admission, synth, previous, failure], failure, candidate, control


def test_explicit_resume_recovers_preceding_admitted_critic(tmp_path, monkeypatch):
    events, failure, candidate, _ = _resume_case(tmp_path, monkeypatch)
    assert resume_failed_critic_candidate(tmp_path, events, failure, 'synth') == [candidate]


def test_explicit_resume_rejects_envelope_with_different_control(tmp_path, monkeypatch):
    events, failure, _, _ = _resume_case(tmp_path, monkeypatch)
    monkeypatch.setattr('zf.runtime.call_result_envelope.hydrate_call_result_envelope',
                        lambda *_: {'control_result': {'ref': 'different', 'sha256': 'a' * 64,
                                                     'schema_version': 'plan-synthesis-result.v1'}})
    with pytest.raises(ValueError):
        resume_failed_critic_candidate(tmp_path, events, failure, 'synth')


@pytest.mark.parametrize('mutation', ['wrong_run', 'wrong_stage', 'wrong_ancestor',
                                      'different_control', 'semantic_reject', 'new_candidate'])
def test_explicit_resume_rejects_unrelated_or_superseded_checkpoint(tmp_path, monkeypatch, mutation):
    events, failure, _, control = _resume_case(tmp_path, monkeypatch)
    if mutation == 'wrong_run': failure.payload['workflow_run_id'] = 'other'
    if mutation == 'wrong_stage': failure.payload['stage_id'] = 'other'
    if mutation == 'wrong_ancestor': failure.payload['rework_of'] = 'other'
    if mutation == 'different_control': events[1].payload['control_result_ref'] = {}
    if mutation == 'semantic_reject': control['failure_class'] = 'product_rejection'
    if mutation == 'new_candidate':
        events.insert(-1, ZfEvent(type='prd.plan.child.completed', payload={'workflow_run_id': 'run'}))
    with pytest.raises(ValueError):
        resume_failed_critic_candidate(tmp_path, events, failure, 'synth')
