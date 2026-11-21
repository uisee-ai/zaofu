"""A Critic locator is not a file path; recover only its exact reviewed author."""
import hashlib
import json
from dataclasses import asdict

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.operator_plan_preview import build_plan_preview
from zf.runtime.plan_output_lineage import reviewed_plan_generation
from zf.runtime.plan_preview_artifacts import bind_plan_approval_preview
from tests.test_operator_plan_preview import _artifacts


@pytest.mark.parametrize('fault', [
    'none', 'serialized', 'locator_only', 'review_digest', 'run', 'generation', 'causation',
    'author_bytes', 'plan_bytes', 'map_bytes', 'other_map', 'ambiguous_author', 'partial_plan',
])
def test_preview_recovers_only_current_reviewed_author_plan(tmp_path, fault):
    state, full, refs = _artifacts(tmp_path)
    original = state / refs['plan_artifact_ref']
    refs['plan_artifact_ref'] = 'artifacts/fanouts/fanout-1/planner/docs/plans/plan.md'
    destination = state / refs['plan_artifact_ref']
    destination.parent.mkdir(parents=True)
    original.rename(destination)

    def put(body):
        return write_immutable_json_sidecar(state, body, root='fixtures/review', kind='fixture',
            schema_version='fixture.v1', created_by='test')

    author = put({'child_id':'planner', 'plan_artifact_ref':refs['plan_artifact_ref'],
                  'task_map_ref':'artifacts/other.json' if fault == 'other_map' else refs['task_map_ref'],
                  'plan_content_profile':'derived_plan_full'})
    author.update(source_id='child-result-planner', artifact_id='planner.json')
    identity = dict(workflow_run_id='run-1', fanout_id='fanout-1', stage_id='prd-plan', plan_revision='r1')
    child = {'child_id':'planner', **{k:author[k] for k in ('source_id','artifact_id','sha256')}}
    contract = put({'schema_version':'plan-synth-contract.v1', **identity,
        'child_bindings':[child, child] if fault == 'ambiguous_author' else [child],
        'source_bindings':[author,
            {'source_id':'child-artifact-planner-1', 'artifact_id':'plan.md',
             'ref':refs['plan_artifact_ref'], 'sha256':refs['plan_artifact_digest'],
             **({'allowed_paths':['$.summary']} if fault == 'partial_plan' else {})},
            {'source_id':'child-artifact-planner-2', 'artifact_id':'tasks.json',
             'ref':refs['task_map_ref'], 'sha256':hashlib.sha256((state/refs['task_map_ref']).read_bytes()).hexdigest()}]})
    review = ZfEvent(type='fanout.synth.completed', actor='plan-critic', payload={**identity,
        'plan_synth_contract_ref':contract['ref'], 'plan_synth_contract_digest':contract['sha256'],
        'reviewed_plan_candidate_digest':contract['sha256'],
        'evidence_refs':[f"child-artifact-planner-1:{refs['plan_artifact_digest']}",
                         *([] if fault == 'locator_only' else [refs['plan_artifact_ref']])]})
    ready = ZfEvent(id='evt-plan', type='task_map.ready', actor='kernel', causation_id=review.id,
        payload={**refs, **identity, 'plan_revision':reviewed_plan_generation(review),
                 'plan_artifact_ref':'docs/plans/missing-critic-plan.md', 'plan_content_profile':'derived_plan_full'})
    ready.payload.pop('plan_artifact_digest')
    payload = bind_plan_approval_preview(state, ready.payload, tmp_path, {**ready.payload, 'trace_id':'run-1'})
    if fault == 'review_digest':
        review.payload['reviewed_plan_candidate_digest'] = '0'*64
    elif fault == 'run':
        review.payload['workflow_run_id'] = 'other'
    elif fault == 'generation':
        ready.payload['plan_revision'] = payload['plan_revision'] = 'other'
    elif fault == 'causation':
        ready.causation_id = 'unrelated'
    elif fault == 'author_bytes':
        (state/author['ref']).write_text('{}')
    elif fault == 'plan_bytes':
        (state/refs['plan_artifact_ref']).write_text('# Changed Plan')
    elif fault == 'map_bytes':
        (state/refs['task_map_ref']).write_text('{"tasks":[{"id":"another"}]}')
    request = ZfEvent(type='plan.approval.requested', actor='kernel', payload=payload)
    events = [review, ready, request]
    if fault == 'serialized':
        events = [asdict(e) for e in events]
    before = {p:p.read_bytes() for p in state.rglob('*') if p.is_file()}
    result = build_plan_preview(state, events, plan_id='evt-plan', project_root=tmp_path)
    assert {p:p.read_bytes() for p in state.rglob('*') if p.is_file()} == before
    if fault in {'none','serialized','locator_only'}:
        assert result['markdown'] == full
        assert result['refs']['markdown_ref'] == refs['plan_artifact_ref']
        assert result['content_error'] == ''
        assert any(p['name']=='accepted_plan' and p['sha256']==refs['plan_artifact_digest'] for p in result['artifacts'])
    else:
        assert result['content_kind'] == 'unavailable' and result['content_error']
