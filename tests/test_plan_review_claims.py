"""A correctly identified candidate can still receive factually wrong criticism."""
import pytest

from tests.test_result_submit import _running_plan_synth_operation
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.plan_review_claims import POLICY
from zf.runtime.result_submit import ResultSubmitError


def _case(tmp_path, *, policy=POLICY):
    state = tmp_path / 'project/.zf'
    def source(name, value):
        ref = write_immutable_json_sidecar(state, value, root='claim-inputs', kind='test',
                                         schema_version='test.v1', created_by='test')
        return {**ref, 'source_id': name, 'artifact_id': name + '.json', 'allowed_paths': ['$']}
    current = source('child-artifact-planner-map', {'decision': {'status': 'confirmed'}})
    old = source('previous-plan-candidate-1', {'decision': {'status': 'pending'}})
    requirement = source('requirement', {'rule': 'Apply the confirmed owner decision'})
    contract = source('plan-synth-contract', {
        'child_bindings': [{'child_id': 'planner'}],
        'source_bindings': [current, old, requirement],
    })
    runtime, prepared, service, token = _running_plan_synth_operation(tmp_path, verdict_only=True,
        extra_payload={'plan_review_evidence_policy': policy,
            'plan_synth_contract_ref': contract['ref'], 'plan_synth_contract_digest': contract['sha256'],
            'reviewed_plan_candidate_digest': contract['sha256'],
            'input_refs': [current, old, requirement, contract]})
    cite = {k: current[k] for k in ('source_id', 'artifact_id', 'sha256')}
    cite.update(json_path='$.decision.status', observed_value='pending')
    req = {k: requirement[k] for k in ('source_id', 'artifact_id', 'sha256')}
    req.update(json_path='$.rule', quote='Apply the confirmed owner decision')
    semantic = {'status': 'completed', 'recommendation': 'reject', 'summary': 'Decision remains pending',
        'report': {'status': 'failed', 'recommendation': 'reject', 'summary': 'Decision remains pending', 'fix_items': [{
            'observed_gap': 'Decision remains pending', 'required_change': 'Apply decision',
            'done_when': 'Decision confirmed', 'basis': {'obligation': 'Apply owner decision',
                'requirement_evidence': [req], 'current_evidence': [cite]}}]}}
    return runtime, prepared, service, token, semantic, old


def test_preflight_and_submit_reject_false_current_value(tmp_path):
    runtime, prepared, service, token, result, _ = _case(tmp_path)
    before = [e.id for e in runtime.event_log.read_all()]
    check = service.validate_plan_candidate(operation_id=prepared.operation_id, semantic_result=result,
        role_instance='plan-critic', credential=token)
    assert any(e['code'] == 'plan_review_evidence_invalid' for e in check['errors']), check
    with pytest.raises(ResultSubmitError, match='output binding failed'):
        service.submit(operation_id=prepared.operation_id, semantic_result=result,
                       role_instance='plan-critic', credential=token)
    assert [e.id for e in runtime.event_log.read_all()] == before


def test_legacy_frozen_request_preserves_old_contract_and_exposes_original_gap(tmp_path):
    _, prepared, service, token, result, _ = _case(tmp_path, policy='')
    check = service.validate_plan_candidate(operation_id=prepared.operation_id, semantic_result=result,
        role_instance='plan-critic', credential=token)
    assert not check['errors'], check  # Old admission bound identities, but not the claimed current value.


@pytest.mark.parametrize('fault', ['historical', 'digest', 'missing_basis', 'quote', 'selector'])
def test_current_basis_is_bound_and_not_just_an_existing_path(tmp_path, fault):
    _runtime, prepared, service, token, result, old = _case(tmp_path)
    item = result['report']['fix_items'][0]
    citation = item['basis']['current_evidence'][0]
    citation['observed_value'] = 'confirmed'
    if fault == 'historical':
        citation.update({k: old[k] for k in ('source_id', 'artifact_id', 'sha256')}, observed_value='pending')
    elif fault == 'digest': citation['sha256'] = 'f' * 64
    elif fault == 'missing_basis': item.pop('basis')
    elif fault == 'quote':
        citation.pop('observed_value'); citation['quote'] = 'pending'
    else: citation['json_path'] = '$.absent'
    check = service.validate_plan_candidate(operation_id=prepared.operation_id, semantic_result=result,
        role_instance='plan-critic', credential=token)
    assert any(e['code'] == 'plan_review_evidence_invalid' for e in check['errors']), check


def test_valid_factual_basis_does_not_force_approval(tmp_path):
    runtime, prepared, service, token, result, _ = _case(tmp_path)
    item = result['report']['fix_items'][0]
    item['basis']['current_evidence'][0]['observed_value'] = 'confirmed'
    item['observed_gap'] = 'Decision is confirmed but its semantics still conflict with the requirement'
    from zf.runtime.artifact_read_ledger import read_attempt_artifact
    import json
    manifest_path = next((runtime.state_dir / 'artifacts/attempts' / prepared.attempt_id / 'source-manifests').glob('*.json'))
    manifest = json.loads(manifest_path.read_text())
    for source in manifest['sources']:
        read_attempt_artifact(runtime.state_dir, manifest=manifest,
            source_id=source['source_id'], artifact_id=source['artifact_id'], json_path='$')
    submitted = service.submit(operation_id=prepared.operation_id, semantic_result=result,
        role_instance='plan-critic', credential=token)
    assert submitted.canonical_event_type == 'fanout.synth.completed'


@pytest.mark.parametrize('shape', ['no_outer_status', 'stale_outer_approval', 'malformed_array'])
def test_nested_semantic_rejection_cannot_bypass_claim_validation(tmp_path, shape):
    _, prepared, service, token, result, _ = _case(tmp_path)
    if shape == 'no_outer_status': result.pop('status')
    if shape == 'stale_outer_approval':
        result['recommendation'] = 'approve'
        result['report']['fix_items'] = []
    if shape == 'malformed_array': result['report']['fix_items'] = 3
    check = service.validate_plan_candidate(operation_id=prepared.operation_id, semantic_result=result,
        role_instance='plan-critic', credential=token)
    assert any(e['code'] == 'plan_review_evidence_invalid' for e in check['errors']), check
