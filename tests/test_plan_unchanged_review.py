"""Prior evidence must bind unchanged inputs and the actual dispatched review."""
from copy import deepcopy

import pytest

from tests.test_plan_local_review import _policy_fixture
from zf.runtime.artifact_read_ledger import canonical_required_reads
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.plan_unchanged_review import (
    EVIDENCE_ARTIFACT, EVIDENCE_ID, EVIDENCE_SCHEMA, build_evidence,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def fixture(state, scenario=""):
    manifest, _, events = _policy_fixture(state, scenario)
    next(event for event in events if event.id == 'trigger').payload['target_ref'] = 'a' * 40
    manifest['sources'] = [s for s in manifest['sources'] if s.get('artifact_id') != 'scoped_plan_review.json']
    old_contract = next(s for s in manifest['sources'] if s['source_id'] == 'plan-synth-contract')
    contract = hydrate_sidecar_ref(state, old_contract).payload
    contract['target_ref'] = 'a' * 40
    evidence = build_evidence(state, events=events, sources=manifest['sources'], run='run-1',
                              fanout='new-fanout', stage='plan', trigger_id='trigger', critic_role='plan-critic')
    assert evidence is not None
    ref = write_immutable_json_sidecar(state, evidence, root='fixtures/evidence', kind='unchanged_plan_review',
                                       schema_version=EVIDENCE_SCHEMA, created_by='test')
    source = {**ref, 'source_id': EVIDENCE_ID, 'artifact_id': EVIDENCE_ARTIFACT, 'allowed_paths': ['$']}
    manifest['sources'].append(source)
    contract['source_bindings'] = [{k: s[k] for k in ('source_id', 'artifact_id', 'ref', 'sha256')}
                                   for s in manifest['sources'] if s['source_id'] != 'plan-synth-contract']
    contract_ref = write_immutable_json_sidecar(state, contract, root='fixtures/new-contract',
                                               kind='plan_synth_contract', schema_version=contract['schema_version'],
                                               created_by='test')
    old_contract.update(contract_ref)
    return manifest, canonical_required_reads(manifest, output_profile_id='plan-synth'), events, evidence


def test_advisory_delta_never_replaces_required_originals(tmp_path):
    manifest, required, events, evidence = fixture(tmp_path, 'large_changed_port')
    from zf.runtime.plan_structural_delta import replay_delta
    import json

    changed = evidence['changed_ports']
    assert [item['logical_name'] for item in changed] == ['execution_spec']
    old = json.loads((tmp_path / changed[0]['prior_ref']).read_text())
    new = json.loads((tmp_path / changed[0]['ref']).read_text())
    assert replay_delta(old, changed[0]['delta']) == new
    selected = {(row['source_id'], row['json_path']) for row in required}
    assert ('child-artifact-planner-2', '$') in selected
    assert ('child-artifact-planner-3', '$') in selected
    assert ('child-artifact-planner-1', '$') in selected
    assert ('requirement', '$') in selected
    assert (EVIDENCE_ID, '$') not in selected


def test_untrusted_or_missing_prior_evidence_never_creates_advisory(tmp_path):
    manifest, required, events, _ = fixture(tmp_path)
    events = [event for event in events if event.type != 'workflow.call.result.admitted']
    assert build_evidence(tmp_path, events=events, sources=manifest['sources'], run='run-1',
                          fanout='new-fanout', stage='plan', trigger_id='trigger',
                          critic_role='plan-critic') is None
    assert ('child-artifact-planner-2', '$') in {(r['source_id'], r['json_path']) for r in required}


def test_controlled_replan_requires_exact_applied_action_chain(tmp_path):
    from zf.core.events.model import ZfEvent

    manifest, _, events, _ = fixture(tmp_path)
    trigger = next(e for e in events if e.id == 'trigger')
    trigger.origin = ''
    trigger.actor = 'zf-cli'
    trigger.causation_id = 'evt-123456789abc'
    requested = ZfEvent(id=trigger.causation_id, type='control.action.requested', actor='zf-cli',
        correlation_id='run-1', payload={'action': 'stage-replan-new-generation'})
    completed = ZfEvent(type='runtime.action.attempt.completed', actor='zf-cli', correlation_id='run-1', payload={
        'action': 'stage-replan-new-generation', 'status': 'applied',
        'attempt_id': 'act-evt-12345678', 'result_event_id': trigger.id})
    events.extend([requested, completed])
    args = dict(sources=manifest['sources'], run='run-1', fanout='new-fanout', stage='plan',
                trigger_id='trigger', critic_role='plan-critic')
    assert build_evidence(tmp_path, events=events, **args) is not None
    completed.payload['result_event_id'] = 'unrelated'
    assert build_evidence(tmp_path, events=events, **args) is None


def test_actual_dispatch_and_result_bind_unchanged_policy_to_candidate(tmp_path, monkeypatch):
    from tests.test_call_result_runtime import _runtime
    from zf.runtime.artifact_read_ledger import read_attempt_artifact, seal_read_ledger, render_attempt_source_briefing
    from zf.runtime.call_result_runtime import admit_runtime_call_result, mark_call_operation_started, prepare_call_operation
    from zf.core.events.model import ZfEvent
    from zf.runtime import plan_artifact_package_runtime as package_runtime
    from zf.runtime.plan_local_review_output import validate_local_review_output

    runtime = _runtime(tmp_path)
    manifest, _, events, _ = fixture(runtime.state_dir)
    for event in events:
        runtime.event_writer.append(event)
    manifest['sources'] = [s for s in manifest['sources'] if s['source_id'] != EVIDENCE_ID]
    contract = next(s for s in manifest['sources'] if s['source_id'] == 'plan-synth-contract')
    old_contract = hydrate_sidecar_ref(runtime.state_dir, contract).payload
    old_contract['source_bindings'] = [s for s in old_contract['source_bindings'] if s['source_id'] != EVIDENCE_ID]
    from zf.runtime.call_result_envelope import canonical_json_sha256

    old_contract['plan_revision'] = 'plan-r' + canonical_json_sha256({
        k: v for k, v in old_contract.items() if k != 'plan_revision'})[:12]
    ref = write_immutable_json_sidecar(runtime.state_dir, old_contract, root='fixtures/original-contract',
                                      kind='plan_synth_contract', schema_version=old_contract['schema_version'], created_by='test')
    contract.update(ref)
    payload = {'workflow_run_id': 'run-1', 'fanout_id': 'new-fanout', 'stage_id': 'plan',
               'role_instance': 'plan-critic', 'output_profile_id': 'plan-synth', 'input_refs': manifest['sources'],
               'canonical_success_event': 'fanout.synth.completed', 'canonical_failure_event': 'fanout.synth.completed',
               'plan_revision': old_contract['plan_revision'], 'plan_synth_contract_ref': contract['ref'],
               'plan_synth_contract_digest': contract['sha256'], 'reviewed_plan_candidate_digest': contract['sha256']}
    original_payload = deepcopy(payload)
    prepared = prepare_call_operation(runtime, payload=payload, operation_type='fanout_synth', operation_key='critic',
                                      stage_id='plan', task_id='', dispatch_id='new-attempt', causation_id='trigger')
    assert prepared.should_dispatch
    assert payload['plan_synth_contract_digest'] != contract['sha256']
    pinned_contract = next(s for s in payload['input_refs'] if s['source_id'] == 'plan-synth-contract')
    contract_body = hydrate_sidecar_ref(runtime.state_dir, pinned_contract).payload
    assert any(b['source_id'] == EVIDENCE_ID for b in contract_body['source_bindings'])
    selected = {(r['source_id'], r['json_path']) for r in payload['required_reads']}
    assert ('child-artifact-planner-3', '$') in selected
    assert (EVIDENCE_ID, '$') not in selected
    assert 'optional navigation aid' in render_attempt_source_briefing(payload)
    assert payload['plan_review_optimization']['mode'] == 'full'
    assert payload['plan_review_optimization']['reason'] == 'impact_scope_unavailable'
    replay = prepare_call_operation(runtime, payload=deepcopy(payload), operation_type='fanout_synth',
                                    operation_key='critic', stage_id='plan', task_id='',
                                    dispatch_id='new-attempt', causation_id='trigger')
    assert replay.request_hash == prepared.request_hash
    rebuilt = prepare_call_operation(runtime, payload=original_payload, operation_type='fanout_synth',
                                     operation_key='critic', stage_id='plan', task_id='',
                                     dispatch_id='new-attempt', causation_id='trigger')
    assert rebuilt.request_hash == prepared.request_hash
    pinned = hydrate_sidecar_ref(runtime.state_dir, payload['attempt_source_manifest']).payload
    mark_call_operation_started(runtime, prepared, task_id='', dispatch_id='new-attempt')
    result = ZfEvent(type='fanout.synth.completed', actor='plan-critic', correlation_id='run-1', payload={
        **payload, 'status': 'completed', 'recommendation': 'approve', 'summary': 'reviewed',
        'report': {'status': 'passed', 'recommendation': 'approve', 'summary': 'reviewed'}})
    assert admit_runtime_call_result(runtime, result, mode='blocking', dispatch_correction=False).repair_requested
    for row in payload['required_reads']:
        read_attempt_artifact(runtime.state_dir, manifest=pinned, source_id=row['source_id'],
                              artifact_id=row['artifact_id'], json_path=row['json_path'])
    seal_read_ledger(runtime.state_dir, 'new-attempt')
    assert admit_runtime_call_result(runtime, result, mode='blocking', dispatch_correction=False).admitted
    report = hydrate_sidecar_ref(runtime.state_dir, next(s for s in pinned['sources']
                                if s['source_id'] == 'child-result-planner')).payload
    output = {'workflow_run_id': 'run-1', 'flow_kind': 'prd', 'task_map_ref': report['task_map_ref'],
              'plan_ports': report['plan_ports']}
    calls = []
    monkeypatch.setattr(package_runtime, '_admit', lambda *_args, **_kwargs: calls.append(True) or {})
    monkeypatch.setattr(package_runtime, 'flow_metadata_for', lambda *_args, **_kwargs: {})
    runtime._contract_failure_payload = lambda body, reason: {**body, 'reason': reason}
    status, _, failure = package_runtime.admit_synthesized_plan_package(runtime, event=result,
        manifest={'workflow_run_id': 'run-1'}, stage_id='plan', trace_id='run-1', success_event='task_map.ready',
        final_status='completed', recommendation='approve', artifact_payload=output)
    assert status == 'completed' and calls == [True], failure.get('reason')



def test_existing_full_operation_never_receives_new_delta_contract(tmp_path, monkeypatch):
    from tests.test_call_result_runtime import _runtime
    from zf.runtime.call_result_runtime import prepare_call_operation
    from zf.runtime import plan_unchanged_review

    runtime = _runtime(tmp_path)
    manifest, _, events, _ = fixture(runtime.state_dir)
    for event in events:
        runtime.event_writer.append(event)
    manifest['sources'] = [source for source in manifest['sources'] if source['source_id'] != EVIDENCE_ID]
    contract = next(source for source in manifest['sources'] if source['source_id'] == 'plan-synth-contract')
    payload = {'workflow_run_id': 'run-1', 'fanout_id': 'new-fanout', 'stage_id': 'plan',
        'role_instance': 'plan-critic', 'output_profile_id': 'plan-synth', 'input_refs': manifest['sources'],
        'plan_revision': 'new-rev', 'plan_synth_contract_ref': contract['ref'],
        'plan_synth_contract_digest': contract['sha256'], 'reviewed_plan_candidate_digest': contract['sha256']}
    fresh = deepcopy(payload)
    with monkeypatch.context() as patch:
        patch.setattr(plan_unchanged_review, 'build_evidence', lambda *args, **kwargs: None)
        initial = prepare_call_operation(runtime, payload=payload, operation_type='fanout_synth',
            operation_key='critic', stage_id='plan', task_id='', dispatch_id='new-attempt', causation_id='trigger')
    assert initial.should_dispatch
    replay = prepare_call_operation(runtime, payload=fresh, operation_type='fanout_synth',
        operation_key='critic', stage_id='plan', task_id='', dispatch_id='new-attempt', causation_id='trigger')
    assert replay.request_hash == initial.request_hash
    assert not any(source.get('source_id') == EVIDENCE_ID for source in fresh['input_refs'])
