"""Previous Plan diagnostics retain semantic bodies and exact replay inputs."""
from copy import deepcopy

import pytest

from tests.test_call_result_runtime import _runtime
from zf.runtime.call_result_runtime import prepare_call_operation
from zf.runtime.plan_synth_handoff import build_plan_handoff_input_refs
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import WorkflowOperationError


def preflight():
    return {'schema_version': 'plan-candidate-preflight.v1', 'status': 'failed',
        'errors': [{'code': 'retain-this-gap'}], 'future_semantic_field': {'acceptance': 'keep'},
        'execution_review': {'schema_version': 'plan-execution-review.v1',
            'input_binding': {'task_map_digest': 'a' * 64},
            'verification': {'task_requirements': [{'task_id': 'T', 'acceptance_criteria': ['AC']}]},
            'usage_observations': {'schema_version': 'execution-usage-facts.v1',
                'available': True, 'groups': [{'operation_id': 'old-' * 10000, 'total_tokens': 123}]}}}


def payload(runtime, body, *, allowed_paths=None, profile='plan-synth', access_scope=None):
    source = write_immutable_json_sidecar(runtime.state_dir, body, root='fixtures/preflight',
        kind='plan_candidate_artifact', schema_version=body['schema_version'], created_by='test')
    if allowed_paths is not None:
        source['allowed_paths'] = allowed_paths
    if access_scope is not None:
        source['access_scope'] = access_scope
    refs = build_plan_handoff_input_refs(state_dir=runtime.state_dir, project_root=runtime.project_root,
        payload={'previous_plan_candidate_refs': [source]}, source_event_id='rework')
    return {'workflow_run_id': 'run', 'fanout_id': 'fanout', 'role_instance': 'critic',
        'output_profile_id': profile, 'canonical_success_event': 'fanout.synth.completed',
        'canonical_failure_event': 'fanout.synth.completed', 'input_refs': refs}


def prepare(runtime, body):
    operation_type = 'fanout_reader_child' if body['output_profile_id'] == 'workflow-read' else 'fanout_synth'
    return prepare_call_operation(runtime, payload=body, operation_type=operation_type,
        operation_key='critic', stage_id='plan', task_id='', dispatch_id='attempt')


def manifest(runtime, body):
    return hydrate_sidecar_ref(runtime.state_dir, body['attempt_source_manifest']).payload


@pytest.mark.parametrize('profile', ['workflow-read', 'plan-synth'])
def test_real_call_projects_only_usage_and_keeps_digest_bound_detail(tmp_path, profile):
    runtime = _runtime(tmp_path)
    original = preflight()
    body = payload(runtime, original, profile=profile)
    source = next(s for s in body['input_refs'] if s['source_id'].startswith('previous-plan-candidate'))
    raw = (runtime.state_dir / source['ref']).read_bytes()
    first = prepare(runtime, body)
    sources = manifest(runtime, body)['sources']
    projected = next(s for s in sources if s['source_id'] == source['source_id'])
    delivered = hydrate_sidecar_ref(runtime.state_dir, projected).payload
    assert delivered['schema_version'] == 'plan-preflight-delivery.v1'
    value = delivered['preflight']
    expected = deepcopy(original)
    summary = value['execution_review']['usage_observations']
    expected['execution_review']['usage_observations'] = summary
    assert value == expected
    assert summary['group_count'] == 1 and summary['total_tokens'] == 123
    detail_source = next(s for s in sources if s['source_id'] == summary['detail_source_id'])
    assert summary['detail_ref'] == detail_source['ref']
    assert summary['detail_sha256'] == detail_source['sha256']
    assert hydrate_sidecar_ref(runtime.state_dir, detail_source).payload == original['execution_review']['usage_observations']
    original_source = next(s for s in sources if s['source_id'] == delivered['original_source']['source_id'])
    assert hydrate_sidecar_ref(runtime.state_dir, original_source).payload == original
    assert (runtime.state_dir / source['ref']).read_bytes() == raw
    required = body['required_reads']
    ids = {r['source_id'] for r in required}
    assert projected['source_id'] in ids
    assert detail_source['source_id'] not in ids and original_source['source_id'] not in ids
    assert len((runtime.state_dir / projected['ref']).read_bytes()) < len(raw) / 4
    second_payload = payload(runtime, original, profile=profile)
    second = prepare(runtime, second_payload)
    assert second.replay_hit and second.request_hash == first.request_hash
    assert manifest(runtime, second_payload) == manifest(runtime, body)
    changed = deepcopy(original)
    changed['errors'][0]['code'] = 'changed-semantic-gap'
    with pytest.raises(WorkflowOperationError, match='diverged'):
        prepare(runtime, payload(runtime, changed, profile=profile))


def test_replay_rejects_tampered_usage_detail(tmp_path):
    runtime = _runtime(tmp_path)
    original = preflight()
    body = payload(runtime, original)
    prepare(runtime, body)
    detail = next(s for s in manifest(runtime, body)['sources']
                  if s['source_id'].startswith('preflight-usage-'))
    (runtime.state_dir / detail['ref']).write_text('{}')
    with pytest.raises(ValueError, match='mismatch'):
        prepare(runtime, payload(runtime, original))


def test_derived_view_and_details_preserve_source_read_restrictions(tmp_path):
    from zf.runtime.artifact_access import artifact_access_allowed
    runtime = _runtime(tmp_path)
    scope = {'visibility': 'project', 'actors': ['critic'], 'purposes': ['workflow-read']}
    body = payload(runtime, preflight(), access_scope=scope)
    prepare(runtime, body)
    derived = [s for s in manifest(runtime, body)['sources']
               if s['source_id'].startswith(('previous-plan-candidate-', 'preflight-'))]
    assert len(derived) == 3
    for source in derived:
        assert not artifact_access_allowed(source['access_scope'], actor='other', purpose='workflow-read')
        assert not artifact_access_allowed(source['access_scope'], actor='critic', purpose='different')
        assert artifact_access_allowed(source['access_scope'], actor='critic', purpose='workflow-read')
    fresh = payload(runtime, preflight(), access_scope=scope)
    assert prepare(runtime, fresh).replay_hit
    with pytest.raises(WorkflowOperationError, match='diverged'):
        prepare(runtime, payload(runtime, preflight(), access_scope={}))


def test_producer_cannot_claim_a_kernel_delivery_view(tmp_path):
    with pytest.raises(ValueError, match='cannot declare a runtime delivery projection'):
        build_plan_handoff_input_refs(state_dir=tmp_path, project_root=tmp_path,
            payload={'previous_plan_candidate_refs': [{'kind': 'plan_preflight_delivery',
                'ref': 'artifacts/forged.json', 'sha256': 'a' * 64}]}, source_event_id='rework')


def test_multiple_previous_usage_sources_do_not_collide_with_current_review(tmp_path):
    from zf.runtime.plan_execution_review import attach_execution_review
    runtime = _runtime(tmp_path)
    first, second = preflight(), preflight()
    second['execution_review']['usage_observations']['groups'][0]['total_tokens'] = 456
    body = payload(runtime, first)
    peer = next(s for s in payload(runtime, second)['input_refs']
                if s['source_id'].startswith('previous-plan-candidate-'))
    body['input_refs'].append({**peer, 'source_id': 'previous-plan-candidate-2'})
    attach_execution_review(state_dir=runtime.state_dir, payload=body,
        review=first['execution_review'], source_event_id='current')
    prepare(runtime, body)
    sources = manifest(runtime, body)['sources']
    assert len({s['source_id'] for s in sources}) == len(sources)
    totals = []
    for source in sources:
        if source.get('kind') != 'plan_preflight_delivery': continue
        view = hydrate_sidecar_ref(runtime.state_dir, source).payload
        summary = view['preflight']['execution_review']['usage_observations']
        detail = next(s for s in sources if s['source_id'] == summary['detail_source_id'])
        assert detail['sha256'] == summary['detail_sha256']
        totals.append(hydrate_sidecar_ref(runtime.state_dir, detail).payload['groups'][0]['total_tokens'])
    assert sorted(totals) == [123, 456]
    assert any(s['source_id'] == 'plan-execution-usage' for s in sources)


@pytest.mark.parametrize('case', ['partial', 'unknown_schema', 'unknown_usage'])
def test_unrecognized_or_partial_source_keeps_original_required_body(tmp_path, case):
    runtime = _runtime(tmp_path)
    original = preflight()
    if case == 'unknown_schema': original['schema_version'] = 'other.v1'
    if case == 'unknown_usage': original['execution_review']['usage_observations']['schema_version'] = 'other.v1'
    body = payload(runtime, original, allowed_paths=['$.errors'] if case == 'partial' else None)
    source = next(s for s in body['input_refs'] if s['source_id'].startswith('previous-plan-candidate'))
    if case == 'partial':
        with pytest.raises(ValueError, match='outside the published source scope'):
            prepare(runtime, body)
        actual = next(s for s in body['input_refs'] if s['source_id'] == source['source_id'])
        assert actual == source
        return
    prepare(runtime, body)
    actual = next(s for s in manifest(runtime, body)['sources'] if s['source_id'] == source['source_id'])
    assert actual['sha256'] == source['sha256']
    assert actual['allowed_paths'] == source['allowed_paths']


def test_legacy_existing_operation_is_not_reprojected(tmp_path, monkeypatch):
    import zf.runtime.previous_preflight_delivery as module
    runtime = _runtime(tmp_path)
    original = preflight()
    with monkeypatch.context() as patch:
        patch.setattr(module, 'prepare_previous_preflight_delivery', lambda *a, **kw: None)
        first_payload = payload(runtime, original)
        first = prepare(runtime, first_payload)
    second_payload = payload(runtime, original)
    second = prepare(runtime, second_payload)
    assert second.replay_hit and second.request_hash == first.request_hash
    assert manifest(runtime, second_payload) == manifest(runtime, first_payload)


def test_legacy_partial_policy_only_replays_on_the_original_attempt(tmp_path, monkeypatch):
    from zf.runtime import plan_diagnosis_read_scope

    runtime = _runtime(tmp_path)
    original = preflight()
    def partial():
        return payload(runtime, original, allowed_paths=["$.errors"])
    with monkeypatch.context() as legacy:
        # Reproduce the released policy that accepted partial preflight on its source ID alone.
        legacy.setattr(plan_diagnosis_read_scope, "previous_candidate_slices",
                       lambda *_: {"previous-plan-candidate-1"})
        first = prepare(runtime, partial())
    replay = prepare(runtime, partial())
    assert replay.replay_hit and replay.request_hash == first.request_hash
    different = payload(runtime, original, allowed_paths=["$.status"])
    with pytest.raises(ValueError, match="outside the published source scope"):
        prepare(runtime, different)
    with pytest.raises(ValueError, match="outside the published source scope"):
        prepare_call_operation(runtime, payload=partial(), operation_type="fanout_synth",
            operation_key="new-critic", stage_id="plan", task_id="", dispatch_id="new-attempt")


def test_producer_cannot_relabel_a_partial_previous_preflight_as_a_declared_input(tmp_path):
    runtime = _runtime(tmp_path)
    source = write_immutable_json_sidecar(runtime.state_dir, preflight(), root="fixtures/preflight",
        kind="plan_declared_input", schema_version="plan-candidate-preflight.v1", created_by="test")
    body = payload(runtime, preflight())
    body["input_refs"] = build_plan_handoff_input_refs(state_dir=runtime.state_dir, project_root=runtime.project_root,
        payload={"previous_plan_candidate_refs": [{**source, "allowed_paths": ["$.errors"]}]}, source_event_id="rework")
    with pytest.raises(ValueError, match="outside the published source scope"):
        prepare(runtime, body)


def test_previous_candidate_grant_cannot_alias_another_artifact(tmp_path):
    runtime = _runtime(tmp_path)
    body = payload(runtime, preflight(), allowed_paths=["$.errors"])
    source = next(s for s in body["input_refs"] if s["source_id"].startswith("previous-plan-candidate-"))
    body["input_refs"].append({**source, "artifact_id": "another-artifact.json"})
    with pytest.raises(ValueError, match="ambiguous previous Plan citation"):
        prepare(runtime, body)
