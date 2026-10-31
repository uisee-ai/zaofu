"""Producer and Critic preflight must respect the Package hydration boundary."""
import hashlib

import pytest

from zf.runtime.call_result_envelope import canonical_json_bytes
from zf.runtime.plan_artifact_package_ports import materialize_explicit_plan_ports
from zf.runtime.plan_candidate_preflight import evaluate_plan_candidate_preflight
from zf.runtime.plan_candidate_submit_validation import validate_plan_candidate_semantic
from zf.runtime.sidecar_refs import DEFAULT_HYDRATE_LIMIT_BYTES, hydrate_sidecar_ref
from tests.test_plan_candidate_preflight import _PlanSynthHarness, _source_index, _task_map


def _body(size):
    remaining = size - len(canonical_json_bytes({'note': ''}))
    return {'note': '雪' * (remaining // 3) + 'x' * (remaining % 3)}


def _semantic(port):
    return {'task_map': _task_map(), 'source_index': _source_index(),
            'source_index_ref': 'inline:source-index', 'plan_md': '# Plan',
            'plan_ports': [port]}


@pytest.mark.parametrize('inline', [False, True])
@pytest.mark.parametrize('delta', [-1, 0, 1])
def test_port_size_matches_package_hydration_before_submit(tmp_path, inline, delta):
    state = tmp_path / 'state'
    path = state / 'artifacts/plan/execution.json'
    path.parent.mkdir(parents=True)
    body = _body(DEFAULT_HYDRATE_LIMIT_BYTES + delta)
    raw = canonical_json_bytes(body)
    path.write_bytes(raw)
    port = {'logical_name': 'execution_spec'}
    port.update({'body': body} if inline else {
        'ref': 'artifacts/plan/execution.json', 'sha256': hashlib.sha256(raw).hexdigest(),
    })
    semantic = _semantic(port)
    validation = {'manifest': {'trigger_payload': {'flow_kind': 'prd', 'prd_ref': 'prd.md'}},
                  'metadata': {'flow_kind': 'prd'}}
    before = sorted(p.relative_to(state) for p in state.rglob('*') if p.is_file())
    results = [evaluate_plan_candidate_preflight(
        state_dir=state, project_root=tmp_path, reports=[{'report': semantic}], **validation,
    ), validate_plan_candidate_semantic(
        state_dir=state, project_root=tmp_path, request={'plan_candidate_validation': validation},
        semantic=semantic, profile_id='workflow-read',
    )]
    assert before == sorted(p.relative_to(state) for p in state.rglob('*') if p.is_file())
    for result in results:
        assert result['status'] == ('failed' if delta > 0 else 'passed'), result['errors']
        if delta > 0:
            error = next(e for e in result['errors'] if e['code'] == 'plan_port_too_large')
            assert 'execution_spec' in error['message']
            assert str(DEFAULT_HYDRATE_LIMIT_BYTES) in error['message']
    if delta <= 0:
        ports = materialize_explicit_plan_ports(state_dir=state, project_root=tmp_path,
            payload={'plan_ports': [port], 'stage_id': 'prd-plan'}, flow_kind='prd')
        assert hydrate_sidecar_ref(state, ports['execution_spec']).payload == body


def test_oversized_port_prevents_actual_critic_dispatch(tmp_path):
    path = tmp_path / 'oversized.json'
    raw = canonical_json_bytes(_body(DEFAULT_HYDRATE_LIMIT_BYTES + 1))
    path.write_bytes(raw)
    harness = _PlanSynthHarness(tmp_path, [{'child_id': 'planner', 'report': _semantic({
        'logical_name': 'execution_spec', 'ref': str(path),
        'sha256': hashlib.sha256(raw).hexdigest(),
    })}])
    harness._dispatch_fanout_synth('fanout-size', {
        'fanout_id': 'fanout-size', 'trace_id': 'run-size', 'stage_id': 'issue-triage',
        'trigger_event_id': 'evt-input',
        'trigger_payload': {'flow_kind': 'issue', 'issue_ref': 'issue.md'},
        'aggregate_config': {'success_event': 'task_map.ready',
                            'failure_event': 'issue.plan.failed', 'synth_role': 'plan-critic'},
        'children': [],
    }, 'wait_for_all', 'plan-critic')
    assert not harness.critic_dispatch_attempted
    assert harness.finalized.payload['failure_class'] == 'plan_candidate_preflight'
    findings = harness.finalized.payload['report']['findings']
    assert any(f.get('code') == 'plan_port_too_large' for f in findings), findings


@pytest.mark.parametrize('inline', [True, False])
def test_direct_task_map_cannot_bypass_port_size_limit(tmp_path, inline):
    semantic = _semantic({'logical_name': 'execution_spec', 'body': {'note': 'small'}})
    task_map = semantic.pop('task_map')
    task_map['notes'] = 'x' * DEFAULT_HYDRATE_LIMIT_BYTES
    if inline:
        semantic['task_map'] = task_map
    else:
        path = tmp_path / 'task_map.json'
        path.write_bytes(canonical_json_bytes(task_map))
        semantic['task_map_ref'] = str(path)
    result = evaluate_plan_candidate_preflight(state_dir=tmp_path / 'state', project_root=tmp_path,
        reports=[{'report': semantic}], manifest={'trigger_payload': {'flow_kind': 'prd', 'prd_ref': 'prd.md'}},
        metadata={'flow_kind': 'prd'})
    assert result['status'] == 'failed'
    assert any(e['code'] == 'plan_port_too_large' and 'task_map' in e['message'] for e in result['errors'])
