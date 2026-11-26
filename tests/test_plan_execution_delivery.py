"""Required mechanical summaries retain digest-bound, readable detail evidence."""
from copy import deepcopy
import json

import pytest

from zf.runtime.plan_execution_review import attach_execution_review, review_execution
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def _review():
    return review_execution({'tasks': [{'task_id': 'T', 'validation': {'commands': [
        {'id': 'runtime', 'owner': 'task_verify', 'producer_task_id': 'T'}]},
        'acceptance_criteria': [{'id': 'AC', 'verification_command_ids': ['runtime']}]}]},
        ports={}, policy={}, metadata={}, identity={'task_map_generation': 'generation'})


def test_cli_summary_distinguishes_empty_initial_acceptance_from_an_unresolved_contract(tmp_path):
    from zf.cli.result import _validation_summary
    from zf.runtime.plan_review_claims import POLICY, review_claim_briefing

    tasks = []
    for task_id, command_owner in [('catalog', 'task_verify'), ('loans', 'candidate_verify')]:
        tasks.append({
            'task_id': task_id, 'execution_mode': 'runtime_only', 'runtime_only': True,
            'write_free': True, 'allowed_paths': [], 'verification_read_paths': ['catalog.json'],
            'acceptance_criteria': [{'id': 'inspect-' + task_id, 'statement': 'Published records agree',
                                     'verification_owner': 'candidate_verify', 'verification_tier': 'runtime',
                                     'verification_command_ids': ['check-' + task_id]}],
            'validation': {'commands': [{'id': 'check-' + task_id, 'command': 'inspect ' + task_id,
                                         'owner': command_owner, 'tier': 'runtime',
                                         'acceptance_ids': ['inspect-' + task_id]}]},
        })
    tasks.append({**tasks[0], 'task_id': 'unresolved', 'acceptance_criteria': [], 'validation': {}})
    review = review_execution({'tasks': tasks}, ports={}, policy={}, metadata={'task_pipeline': {'mode': 'blocking'}})
    summary = _validation_summary({'status': 'passed', 'errors': [], 'execution_review': review})
    scope = summary['execution_comparisons']['readonly_command_scope']
    assert scope['task_count'] == 3
    assert scope['resolved_acceptance_task_count'] == 2
    assert scope['without_mandatory_acceptance_count'] == 1
    assert scope['sample_without_mandatory_acceptance'] == ['catalog']
    assert summary['status'] == 'passed' and not summary['errors']
    payload = {}
    attach_execution_review(state_dir=tmp_path, payload=payload, review=review, source_event_id='review')
    detail = next(r for r in payload['input_refs'] if r['source_id'] == 'plan-execution-verification')
    bound = hydrate_sidecar_ref(tmp_path, detail).payload
    rows = {row['task_id']: row['initial_readonly_verify'] for row in bound['task_requirements']}
    assert rows['catalog']['mandatory_acceptance_ids'] == [] and rows['catalog']['command_ids'] == ['check-catalog']
    assert rows['loans']['mandatory_acceptance_ids'] == ['inspect-loans']
    assert 'mandatory_acceptance_ids' not in rows['unresolved']
    hint = ' '.join(review_claim_briefing(POLICY))
    assert detail['source_id'] in hint and 'initial_readonly_verify.mandatory_acceptance_ids' in hint


def test_delivery_keeps_verification_details_bound_and_on_demand(tmp_path):
    from zf.runtime.artifact_read_ledger import source_manifest_from_payload
    from zf.runtime.artifact_read_requirements import build_canonical_required_reads

    review = _review()
    original = deepcopy(review)
    payload = {}
    attach_execution_review(state_dir=tmp_path, payload=payload, review=review, source_event_id='evt')
    sources = {s['source_id']: s for s in payload['input_refs']}
    body = hydrate_sidecar_ref(tmp_path, sources['plan-execution-review']).payload
    verification = body['verification']
    assert 'task_requirements' not in verification
    detail = hydrate_sidecar_ref(tmp_path, sources['plan-execution-verification']).payload
    assert detail['task_requirements'] == original['verification']['task_requirements']
    assert detail['input_binding'] == original['input_binding']
    assert verification['task_requirement_count'] == 1
    assert verification['command_count'] == 1
    assert verification['acceptance_count'] == 1
    assert verification['detail_sha256'] == sources['plan-execution-verification']['sha256']
    assert review == original
    manifest, _ = source_manifest_from_payload(state_dir=tmp_path, project_root=tmp_path,
        payload=payload, workflow_run_id='run', task_id='T', attempt_id='attempt', dispatch_id='dispatch')
    required = build_canonical_required_reads(manifest, output_profile_id='plan-synth')
    assert 'plan-execution-review' in {r['source_id'] for r in required}
    assert 'plan-execution-verification' not in {r['source_id'] for r in required}
    assert any(s['source_id'] == 'plan-execution-verification' for s in manifest['sources'])


def test_delivery_estimate_uses_same_summary_without_writing(tmp_path):
    from zf.runtime.plan_candidate_delivery_cost import candidate_delivery_cost
    from zf.runtime.artifact_text_codec import encode_selected

    review = _review()
    review['usage_observations'] = {'schema_version': 'execution-usage-facts.v1',
        'groups': [{'operation_id': 'history' * 10000, 'total_tokens': 5}]}
    estimate = candidate_delivery_cost(state_dir=tmp_path, project_root=tmp_path,
        request={}, semantic={}, execution_review=review, limits={})
    assert list(tmp_path.iterdir()) == []
    payload = {}
    attach_execution_review(state_dir=tmp_path, payload=payload, review=review, source_event_id='evt')
    descriptor = next(s for s in payload['input_refs'] if s['source_id'] == 'plan-execution-review')
    delivered = hydrate_sidecar_ref(tmp_path, descriptor).payload
    cost = next(r for r in estimate['components'] if r['kind'] == 'execution_review')
    assert cost['rendered_bytes'] == len(encode_selected(delivered).encode())
    assert cost['rendered_bytes'] < 10000


@pytest.mark.parametrize('indexed_legacy', [False, True])
def test_legacy_review_replay_keeps_original_required_body(tmp_path, indexed_legacy):
    from tests.test_call_result_runtime import _runtime
    from zf.runtime.call_result_envelope import write_immutable_json_sidecar
    from zf.runtime.call_result_runtime import prepare_call_operation
    from zf.runtime.workflow_operation import WorkflowOperationError
    import pytest

    runtime = _runtime(tmp_path)
    review = _review()
    review['input_binding']['effective_policy_digest'] = 'sha256:' + 'a' * 64
    key = 'standard_with_empty_write_scope'
    if indexed_legacy:
        review['execution_comparisons'] = {key: {'task_ids': ['T']}}
    indexed = {**review, 'execution_comparisons': {key: {
        'task_ids': ['T'], 'before_rework': True,
        'facts': {'entries': [{'task_id': 'T', 'entry_mode': 'standard'}]},
    }}}
    source = write_immutable_json_sidecar(runtime.state_dir, review, root='plan-execution-reviews',
        kind='plan_execution_review', schema_version='plan-execution-review.v1', created_by='plan-preflight', source_event_id='evt')
    legacy = {'workflow_run_id': 'run', 'fanout_id': 'fanout', 'role_instance': 'critic',
        'output_profile_id': 'plan-synth', 'canonical_success_event': 'fanout.synth.completed',
        'canonical_failure_event': 'fanout.synth.completed',
        'input_refs': [{**source, 'source_id': 'plan-execution-review',
                        'artifact_id': 'plan-execution-review.json', 'allowed_paths': ['$']}]}
    kwargs = dict(operation_type='fanout_synth', operation_key='critic', stage_id='plan',
                  task_id='', dispatch_id='attempt')
    base = {k: v for k, v in legacy.items() if k != 'input_refs'}
    first = prepare_call_operation(runtime, payload=legacy, **kwargs)
    fresh = deepcopy(base)
    attach_execution_review(state_dir=runtime.state_dir, payload=fresh, review=indexed, source_event_id='evt')
    second = prepare_call_operation(runtime, payload=fresh, **kwargs)
    assert second.replay_hit and first.request_hash == second.request_hash
    assert not any(s['source_id'] == 'plan-execution-verification' for s in fresh['input_refs'])
    old = next(s for s in fresh['input_refs'] if s['source_id'] == 'plan-execution-review')
    assert hydrate_sidecar_ref(runtime.state_dir, old).payload == json.loads(json.dumps(review))
    changed = deepcopy(indexed)
    changed['verification']['task_requirements'][0]['commands'][0]['owner'] = 'human'
    fresh = deepcopy(base)
    attach_execution_review(state_dir=runtime.state_dir, payload=fresh, review=changed, source_event_id='evt')
    with pytest.raises(WorkflowOperationError, match='diverged'):
        prepare_call_operation(runtime, payload=fresh, **kwargs)


def test_verification_detail_tampering_fails_closed(tmp_path):
    import pytest
    from zf.runtime.plan_execution_delivery import expanded_verification

    review = _review()
    payload = {}
    attach_execution_review(state_dir=tmp_path, payload=payload, review=review, source_event_id='evt')
    sources = {s['source_id']: s for s in payload['input_refs']}
    body = hydrate_sidecar_ref(tmp_path, sources['plan-execution-review']).payload
    detail = sources['plan-execution-verification']
    (tmp_path / detail['ref']).write_text('{}')
    with pytest.raises(ValueError, match='mismatch'):
        expanded_verification(tmp_path, body, payload['input_refs'])


def test_critic_summary_identifies_missing_audit_entry_and_unbound_candidate_probes(tmp_path):
    from zf.runtime.plan_execution_delivery import execution_review_delivery

    audit = {'task_id': 'AUDIT', 'execution_mode': 'requested_review', 'allowed_paths': []}
    commands = [
        {'id': key, 'owner': 'candidate_verify', 'command': f'run {key}', 'acceptance_ids': []}
        for key in ('build', 'all-tests', 'browser')
    ]
    impl = {'task_id': 'IMPL', 'allowed_paths': ['src/tool'], 'validation': {'commands': commands}}
    consumer = {'task_id': 'OTHER', 'allowed_paths': ['src/other'], 'acceptance_criteria': [
        {'id': 'journey', 'statement': 'Real browser', 'verification_owner': 'candidate_verify',
         'verification_command_ids': ['browser']}]}
    def check():
        return review_execution({'tasks': [audit, impl, consumer]}, ports={}, policy={},
                                metadata={'task_pipeline': {'mode': 'blocking'}})

    first = check()
    payload = {}
    attach_execution_review(state_dir=tmp_path, payload=payload, review=first, source_event_id='evt')
    sources = {s['source_id']: s for s in payload['input_refs']}
    summary = hydrate_sidecar_ref(tmp_path, sources['plan-execution-review']).payload
    assert {'standard_with_empty_write_scope', 'candidate_commands_without_mandatory_binding'} <= set(summary['review_items'])
    detail = hydrate_sidecar_ref(tmp_path, sources['plan-execution-verification']).payload
    rows = {row['task_id']: row for row in detail['task_requirements']}
    actual = summary['execution_comparisons']['standard_with_empty_write_scope']
    assert actual['before_rework'] is True
    assert actual['facts']['entries'] == [{
        'task_id': 'AUDIT', 'declared_execution_mode': 'requested_review',
        'entry_mode': 'standard', 'first_stage_on_admission': 'impl_ready',
        'allowed_paths': [], 'declared_command_ids': [],
    }]
    assert rows['AUDIT']['standard_with_empty_write_scope'] is True
    assert 'initial_readonly_verify' not in rows['AUDIT']
    assert rows['IMPL']['unbound_candidate_command_ids'] == ['build', 'all-tests']
    assert 'unbound_candidate_command_ids' not in rows['OTHER']
    audit.update(execution_mode='runtime_only', runtime_only=True, write_free=True)
    consumer['acceptance_criteria'][0]['verification_command_ids'].extend(['build', 'all-tests'])
    for command in commands:
        command['acceptance_ids'] = ['journey']
    fixed, _ = execution_review_delivery(check())
    assert not {'standard_with_empty_write_scope', 'candidate_commands_without_mandatory_binding'} & set(fixed['review_items'])
