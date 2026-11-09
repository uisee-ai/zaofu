"""Candidate input preflight respects proven historical replacement inputs."""
from copy import deepcopy

import pytest

from zf.runtime.plan_artifact_package import hydrate_plan_artifact_package
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.plan_candidate_preflight import evaluate_plan_candidate_preflight
from tests.test_task_plan_consumption import case  # noqa: F401
from tests.test_replacement_plan_inputs import _replacement


def _candidate(case, *, retained=False, fault=""):
    state, _, identity = _replacement(case)
    package = hydrate_plan_artifact_package(state, {'ref': identity['plan_artifact_package_ref'],
                                                   'sha256': identity['plan_artifact_package_digest']})
    ports = {p['logical_name']: p for g in ('produced', 'inherited') for p in package[g]}
    old_map = hydrate_sidecar_ref(state, ports['task_map']).payload
    task_map = deepcopy(old_map)
    if not retained:
        task_map = {'tasks': [{'task_id': 'NEXT', 'payload': {'supersedes_task_ids': ['NEW']},
                              'evidence_contract': {'supersedes_task_ids': ['NEW']}}],
                    'amend': {'kind': 'goal_gap', 'gap_task_ids': ['NEXT'], 'superseded_task_ids': ['NEW']}}
    else:
        # A later, unrelated amendment retains this exact existing task.
        task_map['amend'] = {'kind': 'goal_gap', 'gap_task_ids': ['OTHER'], 'superseded_task_ids': ['OLD-OTHER']}
    body = deepcopy(hydrate_sidecar_ref(state, ports['task_execution_input']).payload)
    trigger = {**identity, 'workflow_run_id': 'run', 'flow_kind': 'prd', 'prd_ref': 'requirements.md'}
    if fault == 'run': trigger['workflow_run_id'] = 'other'
    elif fault == 'generation': trigger['task_map_generation'] = 'other'
    elif fault == 'run_contract': trigger['run_contract_digest'] = 'other'
    elif fault == 'digest': trigger['plan_artifact_package_digest'] = '0' * 64
    elif fault == 'no_baseline': trigger.pop('plan_artifact_package_ref')
    elif fault == 'input_changed': body['shared']['rule'] = 'changed semantics'
    elif fault == 'task_changed': task_map['tasks'][0]['title'] = 'different retained task'
    elif fault == 'ambiguous': task_map['tasks'][0]['payload']['supersedes_task_ids'].append('OTHER')
    elif fault == 'invented': task_map['tasks'][0]['payload']['supersedes_task_ids'] = ['UNKNOWN']
    report = {'task_map': task_map, 'source_index': {'tasks': []}, 'source_index_ref': 'inline:source',
              'plan_md': '# Pending plan',
              'plan_ports': [{'logical_name': 'task_execution_input', 'body': body}]}
    return state, trigger, report


@pytest.mark.parametrize('retained', [False, True])
def test_actual_preflight_accepts_proven_replacement_partition(case, retained):
    state, trigger, report = _candidate(case, retained=retained)
    result = evaluate_plan_candidate_preflight(state_dir=state, project_root=state.parent,
        reports=[{'report': report}], manifest={'trigger_payload': trigger}, metadata={'flow_kind': 'prd'})
    # These compact fixtures are intentionally not full product plans: only
    # the input error must disappear; unrelated task/acceptance gates stay red.
    assert not [e for e in result['errors'] if e['code'] == 'task_scoped_input_invalid'], result['errors']
    assert result['status'] == 'failed'


@pytest.mark.parametrize('fault', ['run', 'generation', 'run_contract', 'digest', 'no_baseline', 'input_changed', 'task_changed', 'ambiguous', 'invented'])
def test_actual_preflight_rejects_unproven_inheritance(case, fault):
    state, trigger, report = _candidate(case, retained=fault == 'task_changed', fault=fault)
    result = evaluate_plan_candidate_preflight(state_dir=state, project_root=state.parent,
        reports=[{'report': report}], manifest={'trigger_payload': trigger}, metadata={'flow_kind': 'prd'})
    assert any(e['code'] == 'task_scoped_input_invalid' for e in result['errors'])
