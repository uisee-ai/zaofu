"""Unrelated gap amendments must not erase a retained task's replacement lineage."""
from copy import deepcopy

import pytest

from zf.runtime.artifact_read_ledger import ArtifactReadError
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.plan_artifact_package import hydrate_plan_artifact_package, write_plan_artifact_package
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from tests.test_replacement_input_lineage import _next
from tests.test_replacement_plan_inputs import _replacement
from tests.test_task_plan_consumption import case  # noqa: F401


def _retain(state, identity, fault=''):
    prior = {'ref': identity['plan_artifact_package_ref'], 'sha256': identity['plan_artifact_package_digest']}
    package = deepcopy(hydrate_plan_artifact_package(state, prior))
    port = next(p for p in package['produced'] if p['logical_name'] == 'task_map')
    task_map = deepcopy(hydrate_sidecar_ref(state, port).payload)
    if fault == 'semantic_drift':
        task_map['tasks'][0]['description'] = 'unreviewed new behavior'
    if fault == 'duplicate':
        task_map['tasks'].append(deepcopy(task_map['tasks'][0]))
    if fault == 'missing':
        task_map['tasks'] = []
    task_map['tasks'].append({'task_id': 'OTHER', 'payload': {'supersedes_task_ids': ['OTHER-OLD']},
                              'evidence_contract': {'supersedes_task_ids': ['OTHER-OLD']}})
    task_map['amend'] = {'kind': 'goal_gap', 'gap_task_ids': ['OTHER'], 'superseded_task_ids': ['OTHER-OLD']}
    ref = write_immutable_json_sidecar(state, task_map, root='fixtures', kind='task_map',
                                      schema_version='v1', created_by='test')
    port.update(ref=ref['ref'], sha256=ref['sha256'])
    for p in package['inherited']:
        p.update(source_package_ref=prior['ref'], source_package_digest=prior['sha256'])
    package.update(supersedes_package_ref=prior['ref'], supersedes_package_digest=prior['sha256'])
    ref = write_plan_artifact_package(state, package)
    return {**identity, 'plan_artifact_package_ref': ref['ref'], 'plan_artifact_package_digest': ref['sha256']}


@pytest.mark.parametrize('profile', ['implementation', 'task-verify'])
def test_retained_predecessor_finds_original_admitted_replacement_edge(case, profile):
    state, resolver, identity = _replacement(case)
    identity = _retain(state, identity)
    identity = _retain(state, identity)
    identity = _next(state, identity, 'NEW', 'NEW-2')
    before = (state / identity['plan_artifact_package_ref']).read_bytes()
    sources = resolver._current_plan_port_sources(workflow_run_id='run', task_id='NEW-2',
                                                 currentness=identity, profile=profile)
    inputs = [p for p in sources if p['artifact_id'] in {'task_execution_input', 'execution_spec'}]
    assert len(inputs) == 2
    assert all(p['source_id'].endswith('-predecessor-T1') for p in inputs)
    assert all(p['allowed_paths'] == ['$.shared', '$.tasks[0]'] for p in inputs)
    assert (state / identity['plan_artifact_package_ref']).read_bytes() == before


@pytest.mark.parametrize('fault', ['semantic_drift', 'duplicate', 'missing'])
def test_retained_predecessor_cannot_hide_changed_or_missing_intermediate_task(case, fault):
    state, resolver, identity = _replacement(case)
    identity = _retain(state, identity, fault)
    identity = _retain(state, identity)
    identity = _next(state, identity, 'NEW', 'NEW-2')
    with pytest.raises(ArtifactReadError):
        resolver._current_plan_port_sources(workflow_run_id='run', task_id='NEW-2',
                                           currentness=identity, profile='implementation')
