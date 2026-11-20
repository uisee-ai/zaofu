from copy import deepcopy

import pytest

from tests.test_plan_acceptance_rework import _prepare, _port
from zf.runtime.plan_artifact_package import prepare_plan_artifact_package
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_scoped_plan_inputs import task_input_paths, current_plan_port_sources


def test_packaged_acceptance_identity_remains_readable_without_rewriting_artifact(tmp_path):
    args, _, _, previous = _prepare(tmp_path)
    state = args['state_dir']
    args['metadata']['artifact_package']['required_ports'] = ['product_acceptance_spec']
    spec = hydrate_sidecar_ref(state, _port(previous)).payload
    for field in ('workflow_run_id', 'flow_kind', 'plan_revision', 'task_map_generation'):
        spec.pop(field)
    spec['task_input_policy'] = {
        'schema_version': 'task-input-policy.v1',
        'shared_paths': [f'$.{key}' for key in spec if key != 'user_journeys'],
        'tasks': [{'task_id': 'T1', 'paths': ['$.user_journeys[0]']}],
    }
    package, descriptor, _ = prepare_plan_artifact_package(events=[], payload={
        'task_map_ref': 'r1.json', 'task_map_generation': 'g1',
        'requirement_spec_ref': 'requirement.md',
        'plan_ports': [{'logical_name': 'product_acceptance_spec', 'body': spec}],
    }, **args)
    port = _port(package)
    body = hydrate_sidecar_ref(state, port).payload
    before = deepcopy(body)
    paths = task_input_paths(body, task_id='T1')
    assert {'$.workflow_run_id', '$.flow_kind', '$.plan_revision', '$.task_map_generation'} <= set(paths)
    assert '$.user_journeys[0]' in paths
    assert task_input_paths(body, task_id='', candidate=True) == ['$']
    sources = current_plan_port_sources(
        state_dir=state, workflow_run_id='run-1', task_id='T1',
        allow_without_task=True, currentness={
            'task_map_generation': 'g1', 'plan_artifact_package_ref': descriptor['ref'],
            'plan_artifact_package_digest': descriptor['sha256'],
        },
    )
    assert next(p for p in sources if p['artifact_id'] == 'product_acceptance_spec')['allowed_paths'] == paths
    assert body == before == hydrate_sidecar_ref(state, port).payload


@pytest.mark.parametrize('change', ['semantic', 'other_schema', 'object_identity'])
def test_only_pas_scalar_identity_is_implicitly_shared(change):
    body = {
        'schema_version': 'product_acceptance_spec.v1', 'workflow_run_id': 'run',
        'plan_revision': 'rev', 'task_map_generation': 'gen', 'flow_kind': 'prd',
        'user_journeys': [{'assertion': 'revoked access denied'}],
        'task_input_policy': {'schema_version': 'task-input-policy.v1',
            'shared_paths': ['$.schema_version'],
            'tasks': [{'task_id': 'T1', 'paths': ['$.user_journeys']}]},
    }
    if change == 'semantic':
        body['important_business_rules'] = ['must preserve']
    elif change == 'other_schema':
        body['schema_version'] = 'some-other.v1'
    else:
        body['plan_revision'] = {'hidden_rules': ['must preserve']}
    with pytest.raises(ValueError, match='unassigned content|identity'):
        task_input_paths(body, task_id='T1')
