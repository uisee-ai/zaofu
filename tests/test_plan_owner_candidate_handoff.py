"""Admitted semantic rejection retains the exact reviewed Planner candidate."""
import hashlib
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime.call_result_adapters import ControlResultAdapterRegistry
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.plan_synth_runtime import PlanSynthRuntimeMixin
from zf.runtime.operator_plan_preview import build_plan_preview
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


@pytest.mark.parametrize('fault', ['', 'tampered_plan', 'wrong_revision', 'wrong_digest'])
def test_owner_checkpoint_reads_same_candidate_without_promoting_verdict(tmp_path, monkeypatch, fault):
    state = tmp_path / '.state'
    writer = EventWriter(EventLog(state / 'events.jsonl'))
    def sidecar(body, schema):
        return write_immutable_json_sidecar(state, body, root='artifacts/test', kind='test',
                                           schema_version=schema, created_by='test')
    report = sidecar({'status': 'passed', 'summary': 'Candidate prepared'}, 'child-report.v1')
    report = {**report, 'source_id': 'child-result-planner', 'artifact_id': 'candidate.json', 'allowed_paths': ['$']}
    plan = state / 'artifacts/planner/plan.md'
    plan.parent.mkdir(parents=True)
    body = '# PRD\nOriginal requirement\n# SDD\nDesign\n# TDD\nNOT_RUN\n'
    plan.write_text(body)
    plan_ref = {'ref': str(plan.relative_to(state)), 'sha256': hashlib.sha256(plan.read_bytes()).hexdigest(),
                'source_id': 'child-artifact-planner-1', 'artifact_id': 'plan.md', 'allowed_paths': ['$']}
    bindings = [{k: r[k] for k in ('source_id', 'artifact_id', 'ref', 'sha256')} for r in [report, plan_ref]]
    identity = {'workflow_run_id': 'run-one', 'fanout_id': 'fanout-one', 'stage_id': 'prd-plan', 'plan_revision': 'r1'}
    contract = sidecar({'schema_version': 'plan-synth-contract.v1', **identity,
        'child_bindings': [{'child_id': 'planner', **bindings[0]}], 'source_bindings': bindings}, 'plan-synth-contract.v1')
    sources = sidecar({'schema_version': 'attempt-source-manifest.v1', 'attempt_id': 'attempt-one',
        'dispatch_id': 'attempt-one', 'workflow_run_id': 'run-one', 'sources': [report, plan_ref]}, 'attempt-source-manifest.v1')
    writer.append(ZfEvent(type='fanout.synth.dispatched', payload={**identity, 'operation_id': 'op-one',
        'attempt_id': 'attempt-one', 'run_id': 'attempt-one', 'attempt_source_manifest': sources}))
    payload = {**identity, 'operation_id': 'op-one', 'attempt_id': 'attempt-one', 'output_profile_id': 'plan-synth',
        'plan_synth_contract_ref': contract['ref'], 'plan_synth_contract_digest': contract['sha256'],
        'reviewed_plan_candidate_digest': contract['sha256'], 'status': 'completed', 'recommendation': 'needs_rework',
        'owner_decision_items': [{'decision_id': 'GIT-01', 'question': 'Does no Git apply only to this preview?', 'blocking': True}]}
    if fault == 'wrong_revision': payload['plan_revision'] = 'other'
    if fault == 'wrong_digest': payload['reviewed_plan_candidate_digest'] = 'f' * 64
    if fault == 'tampered_plan': plan.write_text('wrong body')
    event = writer.append(ZfEvent(type='fanout.synth.completed', correlation_id='run-one', payload=payload))
    adapted = ControlResultAdapterRegistry().adapt_for_profile(state, event, profile_id='plan-synth', revision='1')
    assert adapted.payload['verdict'] == 'rejected'
    # Result admission itself has separate contract tests; this seam supplies its
    # admitted descriptor. The adapter, source proof, checkpoint and preview are real.
    monkeypatch.setattr('zf.runtime.call_result_runtime.admit_runtime_call_result', lambda *a, **kw:
        SimpleNamespace(repair_requested=False, status='admitted', admitted=True,
                        envelope_ref=adapted.descriptor, control_result_ref=adapted.descriptor))
    monkeypatch.setattr('zf.runtime.call_result_runtime.hydrate_admitted_control_result',
        lambda s, ref: hydrate_sidecar_ref(s, ref).payload)
    runtime = SimpleNamespace(state_dir=state, project_root=tmp_path, event_log=writer.event_log, event_writer=writer,
        _fanout_identity_stale_reason=lambda _: ('', ''), _fanout_manifest=lambda _: identity,
        _finalize_fanout_synth=Mock(side_effect=AssertionError('Owner question must hold the Run')))
    PlanSynthRuntimeMixin._handle_fanout_synth_completed(runtime, event)
    events = writer.event_log.read_all()
    approval = next(e for e in events if e.type == 'approval.requested')
    preview = build_plan_preview(state, events, plan_id=approval.payload['plan_id'], project_root=tmp_path)
    if fault:
        assert preview['content_kind'] == 'unavailable'
    else:
        assert approval.payload['plan_artifact_ref'] == plan_ref['ref']
        assert approval.payload['plan_artifact_digest'] == plan_ref['sha256']
        assert preview['markdown'] == body
        assert preview['content_profile'] == 'derived_plan'
    assert not any(e.type in {'task_map.ready', 'plan.approved', 'fanout.aggregate.completed'} for e in events)
    PlanSynthRuntimeMixin._handle_fanout_synth_completed(runtime, event)
    assert sum(e.type == 'approval.requested' for e in writer.event_log.read_all()) == 1
