"""Workflow-submitted authority must reach the independent Plan critic."""
import json

import pytest

from zf.runtime.plan_synth_handoff import build_plan_synth_call_payload
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


@pytest.mark.parametrize('in_trigger', [False, True])
def test_source_refs_authority_is_pinned_beside_requirement_snapshot(tmp_path, in_trigger):
    state = tmp_path / '.state'
    state.mkdir()
    authority = tmp_path / 'original.md'
    authority.write_text('Original complete requirement, including a negative case.\n')
    requirement = state / 'requirement.json'
    requirement.write_text(json.dumps({'objective': 'Short objective', 'source_ref': str(authority)}))
    origin = {'source_refs': {'source_ref': str(authority)}, 'requirement_spec_ref': str(requirement)}
    manifest = {'fanout_id': 'f-plan', 'trace_id': 'run-1', 'stage_id': 'prd-plan',
                'trigger_event_id': 'event-plan', 'target_ref': 'HEAD',
                **({'trigger_payload': origin} if in_trigger else origin)}
    payload = build_plan_synth_call_payload(state_dir=state, project_root=tmp_path,
        manifest=manifest, reports=[{'child_id': 'planner', 'report': {'status': 'passed'}}],
        run_id='run-synth', role_instance='plan-critic')
    refs = [r for r in payload['input_refs'] if r['source_id'] == 'requirement']
    bodies = [hydrate_sidecar_ref(state, ref).payload for ref in refs]
    assert 'Original complete requirement, including a negative case.\n' in bodies
    assert any(isinstance(body, dict) and body.get('objective') == 'Short objective' for body in bodies)
    authority.write_text('Changed later')
    assert 'Original complete requirement, including a negative case.\n' in [
        hydrate_sidecar_ref(state, ref).payload for ref in refs]
