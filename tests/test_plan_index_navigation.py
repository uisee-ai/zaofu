"""Targeted index navigation preserves authoritative and explicit reads."""
import hashlib
import json

import pytest

from tests.test_plan_input_read_selection import fixture
from zf.runtime.artifact_read_requirements import build_canonical_required_reads
from zf.runtime.plan_input_read_selection import select_plan_input_reads


def index_fixture(tmp_path, fault):
    manifest, _, report, _ = fixture(tmp_path)
    path = tmp_path / 'artifacts/source-index.json'
    body = {'schema_version': 'source-index.v1', 'traceability': [{'source_id': 'S1', 'statement': '原始要求' * 100}]}
    if fault == 'unknown': body['schema_version'] = 'future-index.v9'
    path.write_text(json.dumps(body))
    index = {'source_id': 'child-artifact-planner-index', 'artifact_id': 'index',
             'ref': 'artifacts/source-index.json', 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'allowed_paths': ['$']}
    manifest['sources'].append(index)
    producer = json.loads((tmp_path / report['ref']).read_text())
    producer.update(source_index_ref=index['ref'], source_index=body)
    if fault in {'index_port', 'wrong_index_port', 'conflicting_port'}:
        producer.setdefault('plan_ports', []).append({'logical_name': 'source_index' if fault == 'index_port' else 'task_map',
                                                     'ref': index['ref'], 'sha256': index['sha256']})
        if fault == 'wrong_index_port':
            producer['plan_ports'][-1].update(logical_name='source_index', sha256='0' * 64)
    if fault == 'core': producer['task_map_ref'] = index['ref']
    (tmp_path / report['ref']).write_text(json.dumps(producer))
    report['sha256'] = hashlib.sha256((tmp_path / report['ref']).read_bytes()).hexdigest()
    if fault == 'ambiguous':
        other = tmp_path / 'artifacts/other.json';other.write_text(json.dumps(producer))
        manifest['sources'].append({**report, 'source_id': 'child-result-other', 'artifact_id': 'other',
                                    'ref': 'artifacts/other.json'})
    if fault == 'digest': path.write_text('{}')
    return manifest, index, report


@pytest.mark.parametrize('fault', ['none', 'index_port', 'wrong_index_port', 'conflicting_port', 'full', 'explicit', 'unknown', 'digest', 'core', 'ambiguous'])
def test_index_navigation_is_bound_and_conservative(tmp_path, fault):
    manifest, index, report = index_fixture(tmp_path, fault)
    required = build_canonical_required_reads(manifest, output_profile_id='plan-synth')
    selected, _ = select_plan_input_reads(tmp_path, manifest, required,
        policy={'mode': 'full' if fault == 'full' else 'targeted', 'focus': ['source mapping']}, explicit=fault == 'explicit')
    assert any(row['source_id'] == index['source_id'] for row in selected) == (fault not in {'none', 'index_port'})
    if fault in {'none', 'index_port'}:
        assert not any(row['source_id'] == report['source_id'] and row['json_path'] == '$.source_index' for row in selected)
        assert index in manifest['sources']  # Navigation never revokes access or asserts it was read.


def test_actual_dispatch_keeps_index_access_without_requiring_its_body(tmp_path):
    from tests.test_call_result_runtime import _runtime
    from zf.runtime.call_result_runtime import prepare_call_operation

    runtime = _runtime(tmp_path)
    manifest, index, _ = index_fixture(runtime.state_dir, 'index_port')
    runtime.config.workflow.flow_metadata['plan_review'] = {'mode': 'targeted', 'focus': ['failure'], 'reason': 'Owner'}
    payload = {'workflow_run_id': 'run', 'role_instance': 'critic', 'output_profile_id': 'plan-synth',
               'input_refs': manifest['sources'], 'canonical_success_event': 'fanout.synth.completed',
               'canonical_failure_event': 'fanout.synth.completed'}
    prepared = prepare_call_operation(runtime, payload=payload, operation_type='fanout_synth',
        operation_key='critic', stage_id='plan', task_id='', dispatch_id='attempt')
    assert prepared.should_dispatch
    assert payload['plan_review_optimization']['mode'] == 'targeted'
    assert not any(row['source_id'] == index['source_id'] for row in payload['required_reads'])
    pinned = json.loads((runtime.state_dir / payload['attempt_source_manifest_ref']).read_text())
    assert any(row['source_id'] == index['source_id'] and row['sha256'] == index['sha256'] for row in pinned['sources'])
