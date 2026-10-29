"""Task-only inputs remain pinned without becoming global verification reads."""
from copy import deepcopy
import json

import pytest

from zf.core.task.store import TaskStore
from zf.runtime.artifact_read_ledger import ArtifactReadError
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.plan_artifact_package import (
    PlanArtifactPackageError, hydrate_plan_artifact_package,
    prepare_plan_artifact_package, write_plan_artifact_package,
)
from zf.runtime.run_contract import stable_json_sha256, write_run_contract
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_map_required_ports import plan_input_declaration_errors
from zf.runtime.task_scoped_plan_inputs import current_plan_port_sources
from tests.test_task_plan_consumption import case  # noqa: F401


def _add_task_local_port(case):
    state, resolver, identity, policy = case
    package = hydrate_plan_artifact_package(state, {
        'ref': identity['plan_artifact_package_ref'],
        'sha256': identity['plan_artifact_package_digest'],
    })
    policy = deepcopy(policy)
    for role in ('implementation', 'task-verify'):
        policy[role].append('task_detail_t1')
    evidence = {'plan_input_selection': policy, 'required_plan_ports': ['task_detail_t1']}
    task_map_port = next(p for p in package['produced'] if p['logical_name'] == 'task_map')
    task_map = hydrate_sidecar_ref(state, task_map_port).payload
    task_map['required_plan_ports'] = package['required_ports']
    task_map['tasks'][0]['evidence_contract'] = evidence
    ref = write_immutable_json_sidecar(state, task_map, root='fixtures', kind='task_map',
                                     schema_version='task-map.v1', created_by='test')
    task_map_port.update(ref=ref['ref'], sha256=ref['sha256'])
    detail = write_immutable_json_sidecar(state, {'task_id': 'T1', 'rule': 'retain owner constraints'},
        root='fixtures', kind='task_detail', schema_version='v1', created_by='test')
    package['produced'].append({'logical_name': 'task_detail_t1', 'artifact_kind': 'task_detail',
        'schema_version': 'v1', 'producer_stage_id': 'plan', 'ref': detail['ref'], 'sha256': detail['sha256']})
    descriptor = write_plan_artifact_package(state, package)
    identity = {**identity, 'plan_artifact_package_ref': descriptor['ref'],
                'plan_artifact_package_digest': descriptor['sha256']}
    store = TaskStore(state / 'kanban.json')
    task = store.get('T1')
    task.contract.evidence_contract = evidence
    store.update('T1', contract=task.contract)
    return state, resolver, identity, task_map, detail


def test_local_port_reaches_impl_and_verify_without_global_read_amplification(case):
    state, resolver, identity, task_map, _ = _add_task_local_port(case)
    assert plan_input_declaration_errors(task_map) == []
    for profile in ('implementation', 'task-verify'):
        sources = resolver._current_plan_port_sources(workflow_run_id='run', task_id='T1',
            currentness=identity, profile=profile)
        assert [s['artifact_id'] for s in sources] == ['task_execution_input', 'task_detail_t1']
    candidate = current_plan_port_sources(state_dir=state, workflow_run_id='run', task_id='',
        currentness=identity, allow_without_task=True, candidate=True, profile='candidate-verify')
    assert {s['artifact_id'] for s in candidate} == set(task_map['required_plan_ports'])
    assert all(s['allowed_paths'] == ['$'] for s in candidate)


@pytest.mark.parametrize('fault', ['missing', 'changed'])
def test_local_port_remains_hash_bound(case, fault):
    state, resolver, identity, _, detail = _add_task_local_port(case)
    path = state / detail['ref']
    if fault == 'missing':
        path.unlink()
    else:
        path.write_text('{}')
    with pytest.raises(ArtifactReadError, match='cannot be hydrated'):
        resolver._current_plan_port_sources(workflow_run_id='run', task_id='T1',
            currentness=identity, profile='implementation')


def test_undeclared_local_selection_is_rejected(case):
    _, _, _, task_map, _ = _add_task_local_port(case)
    task_map['tasks'][0]['evidence_contract'].pop('required_plan_ports')
    assert any('unbound' in error for error in plan_input_declaration_errors(task_map))


@pytest.mark.parametrize('local', [None, '', [], ['product_spec']])
def test_existing_empty_and_alias_task_declarations_remain_compatible(local):
    task_map = {'tasks': [{'task_id': 'T1', 'evidence_contract': {'required_plan_ports': local}}]}
    assert plan_input_declaration_errors(task_map, available_ports=['requirement_spec']) == []


@pytest.mark.parametrize('bind_local', [False, True])
def test_package_preparation_checks_task_local_bindings_before_admission(tmp_path, bind_local):
    state = tmp_path / 'state'
    artifacts = state / 'artifacts'
    artifacts.mkdir(parents=True)
    (artifacts / 'requirement.md').write_text('Deliver AC-1.\n')
    task_map = {'schema_version': 'task-map.v1', 'objective': 'Deliver AC-1',
                'tasks': [{'task_id': 'T1', 'acceptance_criteria': ['AC-1'],
                           'evidence_contract': {'required_plan_ports': ['task_detail_t1']}}]}
    (artifacts / 'task-map.json').write_text(json.dumps(task_map))
    body = {'schema_version': 'run-contract.v1', 'workflow': {'kind': 'prd'}}
    write_run_contract(state, {**body, 'contract_digest': stable_json_sha256(body)})
    payload = {'task_map_ref': 'state/artifacts/task-map.json',
               'prd_ref': 'state/artifacts/requirement.md'}
    if bind_local:
        payload['plan_ports'] = [{'logical_name': 'task_detail_t1', 'schema_version': 'v1',
                                  'body': {'task_id': 'T1', 'rule': 'retain owner constraints'}}]
    args = dict(state_dir=state, project_root=tmp_path, events=[], payload=payload,
                workflow_run_id='run', flow_kind='prd', producer_stage_id='plan', goal_id='GOAL-1',
                metadata={'artifact_package': {'mode': 'blocking'}})
    if not bind_local:
        with pytest.raises(PlanArtifactPackageError, match='task_detail_t1'):
            prepare_plan_artifact_package(**args)
    else:
        package, _, _ = prepare_plan_artifact_package(**args)
        assert 'task_detail_t1' in {p['logical_name'] for p in package['produced']}
        assert 'task_detail_t1' not in package['required_ports']


@pytest.mark.parametrize('bind_local', [False, True])
def test_pre_submit_checks_task_local_bindings_before_critic(tmp_path, bind_local):
    from tests.test_plan_candidate_preflight import _task_map, _source_index
    from zf.runtime.plan_candidate_preflight import evaluate_plan_candidate_preflight

    task_map = _task_map()
    task_map['tasks'][0]['evidence_contract'] = {'required_plan_ports': ['task_detail_t1']}
    report = {'task_map': task_map, 'source_index': _source_index(),
              'source_index_ref': 'inline:source-index', 'plan_md': '# Plan\nImplement TASK-1.'}
    if bind_local:
        report['plan_ports'] = [{'logical_name': 'task_detail_t1',
                                'body': {'task_id': 'TASK-1', 'rule': 'retain owner constraints'}}]
    result = evaluate_plan_candidate_preflight(
        state_dir=tmp_path / 'state', project_root=tmp_path, reports=[{'report': report}],
        manifest={'trigger_payload': {'flow_kind': 'prd', 'prd_ref': 'requirements.md'}},
        metadata={'flow_kind': 'prd'},
    )
    if bind_local:
        assert result['status'] == 'passed', result['errors']
    else:
        assert result['status'] == 'failed'
        assert any('task_detail_t1' in str(error) for error in result['errors'])
