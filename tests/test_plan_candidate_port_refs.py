"""Reject package-incompatible explicit ports before spending a Critic turn."""
import hashlib
import json

import pytest

from zf.runtime.plan_candidate_ports import plan_port_bodies
from zf.runtime.plan_candidate_preflight import evaluate_plan_candidate_preflight


@pytest.mark.parametrize('fault', ['absolute', 'traversal', 'missing-digest', 'missing-ref'])
def test_explicit_port_obeys_package_ref_contract(tmp_path, fault):
    state = tmp_path / 'state'
    path = state / 'artifacts/adapter.json'
    path.parent.mkdir(parents=True)
    raw = b'{"schema_version":"skill-adapter-plan.v1"}'
    path.write_bytes(raw)
    ref = 'artifacts/adapter.json'
    if fault == 'absolute':
        ref = str(path)
    elif fault == 'traversal':
        ref = 'artifacts/../artifacts/adapter.json'
    elif fault == 'missing-ref':
        ref = ''
    port = {'logical_name': 'skill_adapter_plan', 'ref': ref}
    if fault != 'missing-digest':
        port['sha256'] = hashlib.sha256(raw).hexdigest()
    result = evaluate_plan_candidate_preflight(
        state_dir=state, project_root=tmp_path,
        reports=[{'report': {'plan_ports': [port]}}], manifest={}, metadata={},
    )
    errors = [e for e in result['errors'] if e['field'] == 'report[0].plan_ports[0]']
    assert errors and errors[0]['code'] == 'plan_port_unreadable'


def test_explicit_sidecar_and_materializable_direct_ref_keep_their_distinct_contracts(tmp_path):
    state = tmp_path / 'state'
    path = state / 'artifacts/adapter.json'
    path.parent.mkdir(parents=True)
    raw = b'{"schema_version":"skill-adapter-plan.v1"}'
    path.write_bytes(raw)
    errors = []
    bodies = plan_port_bodies([{'plan_ports': [{
        'logical_name': 'skill_adapter_plan', 'ref': 'artifacts/adapter.json',
        'sha256': hashlib.sha256(raw).hexdigest(),
    }], 'source_index_ref': str(path)}], trigger={}, state_dir=state,
        project_root=tmp_path, errors=errors)
    assert not errors
    assert bodies['skill_adapter_plan'] == bodies['source_index'] == json.loads(raw)


def test_invalid_explicit_ref_stops_actual_synth_dispatch(tmp_path):
    from tests.test_plan_candidate_preflight import _PlanSynthHarness, _source_index, _task_map

    path = tmp_path / 'adapter.json'
    path.write_text('{}')
    harness = _PlanSynthHarness(tmp_path, reports=[{'child_id': 'planner', 'report': {
        'task_map': _task_map(), 'source_index': _source_index(), 'plan_md': '# Complete plan',
        'plan_ports': [{'logical_name': 'skill_adapter_plan', 'ref': str(path)}],
    }}])
    harness._dispatch_fanout_synth('fanout-plan', {
        'fanout_id': 'fanout-plan', 'trace_id': 'run-plan', 'stage_id': 'issue-triage',
        'trigger_event_id': 'evt-input', 'trigger_payload': {'flow_kind': 'issue', 'issue_ref': 'docs/issue.md'},
        'aggregate_config': {'success_event': 'task_map.ready', 'failure_event': 'issue.plan.failed',
                             'synth_role': 'plan-critic'}, 'children': [],
    }, 'wait_for_all', 'plan-critic')
    assert not harness.critic_dispatch_attempted
    assert harness.finalized.payload['failure_class'] == 'plan_candidate_preflight'
    assert 'requires ref/sha256' in json.dumps(harness.finalized.payload)


@pytest.mark.parametrize('changed', [False, True])
def test_final_aggregate_preserves_prepared_project_port_binding(tmp_path, changed):
    from types import SimpleNamespace
    from zf.core.config.schema import ZfConfig
    from zf.runtime.fanout_artifact_refs import prepare_fanout_synth_reports
    from zf.runtime.plan_artifact_package_ports import materialize_explicit_plan_ports
    from zf.runtime.sidecar_refs import SidecarRefError
    from zf.runtime.writer_fanout_data import WriterFanoutDataMixin

    state = tmp_path / 'state'
    path = tmp_path / 'artifacts/workflow/skill-adapter-plan.json'
    path.parent.mkdir(parents=True)
    path.write_text('{"skills":[]}')
    original = {'plan_ports': [{'logical_name': 'skill_adapter_plan', 'ref': str(path),
                               'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}]}
    runtime = SimpleNamespace(state_dir=state, project_root=tmp_path, config=ZfConfig())
    manifest = {'fanout_id': 'fanout-plan'}
    prepared = prepare_fanout_synth_reports(reports=[{'child_id': 'planner', 'report': original}],
        manifest=manifest, state_dir=state, project_root=tmp_path, config=runtime.config, roles=[])
    reviewed = prepared[0]['report']['plan_ports'][0]
    if changed:
        path.write_text('{"skills":["changed after review"]}')
    final = WriterFanoutDataMixin._relocate_reader_fanout_artifact_refs(runtime,
        payload=original, payload_sources=[original], manifest=manifest)
    if changed:
        with pytest.raises((SidecarRefError, ValueError)):
            materialize_explicit_plan_ports(state_dir=state, project_root=tmp_path,
                                           payload=final, flow_kind='prd')
    else:
        assert final['plan_ports'][0] == reviewed
        ports = materialize_explicit_plan_ports(state_dir=state, project_root=tmp_path,
                                               payload=final, flow_kind='prd')
        assert ports['skill_adapter_plan']['sha256'] == reviewed['sha256']
    assert original['plan_ports'][0]['ref'] == str(path)
