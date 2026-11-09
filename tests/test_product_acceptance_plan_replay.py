"""Writer admission must replay the PAS identity binding performed by Plan admission."""
from copy import deepcopy
import json

import pytest

from zf.runtime.plan_artifact_package import (
    PlanArtifactPackageError, admit_plan_artifact_package_for_payload,
)
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from tests.test_control_actions_recovery import _service
from tests.test_product_acceptance import _spec
from zf.runtime.run_contract import stable_json_sha256, write_run_contract


def _admit(tmp_path):
    service = _service(tmp_path)
    artifacts = service.state_dir / 'artifacts'
    artifacts.mkdir(exist_ok=True)
    (artifacts / 'requirement.md').write_text('Deliver AC-1.\n')
    (artifacts / 'map.json').write_text(json.dumps({
        'schema_version': 'task-map.v1', 'objective': 'Deliver AC-1',
        'tasks': [{'task_id': 'TASK-ASSEMBLY', 'acceptance_criteria': ['AC-1']}],
    }))
    contract = {'schema_version': 'run-contract.v1', 'workflow': {'kind': 'prd'}}
    write_run_contract(service.state_dir, {**contract, 'contract_digest': stable_json_sha256(contract)})
    spec = _spec()
    for key in ('workflow_run_id', 'task_map_generation', 'plan_revision'):
        spec.pop(key)
    payload = {'task_map_ref': 'artifacts/map.json', 'prd_ref': 'artifacts/requirement.md',
               'task_map_generation': 'g-new', 'plan_revision': 'r-new',
               'plan_ports': [{'logical_name': 'product_acceptance_spec', 'body': spec}]}
    kwargs = dict(state_dir=service.state_dir, project_root=tmp_path, event_writer=service.writer,
                  payload=payload, workflow_run_id='run-new', flow_kind='prd',
                  producer_stage_id='plan', goal_id='G',
                  metadata={'artifact_package': {'mode': 'blocking'}})
    admitted = admit_plan_artifact_package_for_payload(events=[], **kwargs)
    return service, payload, kwargs, admitted


@pytest.mark.parametrize('form', ['inline', 'file', 'descriptor'])
def test_writer_reuses_package_after_kernel_binds_pas_identity(tmp_path, form):
    service, payload, kwargs, admitted = _admit(tmp_path)
    original = deepcopy(payload)
    if form == 'file':
        path = service.state_dir / 'artifacts/unbound-pas.json'
        path.write_text(json.dumps(payload.pop('plan_ports')[0]['body']))
        payload['product_acceptance_spec_ref'] = 'artifacts/unbound-pas.json'
    elif form == 'descriptor':
        descriptor = write_immutable_json_sidecar(
            service.state_dir, payload['plan_ports'][0]['body'], root='fixtures/pas',
            kind='product_acceptance_spec', schema_version='product_acceptance_spec.v1',
            created_by='test',
        )
        payload['plan_ports'] = [{**descriptor, 'logical_name': 'product_acceptance_spec'}]
    before = service.writer.event_log.read_all()
    result = admit_plan_artifact_package_for_payload(events=before, **kwargs)
    assert result['plan_artifact_package_digest'] == admitted['plan_artifact_package_digest']
    assert service.writer.event_log.read_all() == before
    if form == 'inline':
        assert payload == original


@pytest.mark.parametrize('change', ['assertion', 'mandatory', 'entrypoint', 'qualification'])
def test_replay_still_rejects_changed_product_acceptance_body(tmp_path, change):
    service, payload, kwargs, admitted = _admit(tmp_path)
    spec = payload['plan_ports'][0]['body']
    if change == 'assertion':
        spec['user_journeys'][0]['assertions'][0]['statement'] = 'Different behavior'
    elif change == 'mandatory':
        spec['user_journeys'][0]['mandatory'] = False
    elif change == 'entrypoint':
        spec['entrypoints'][0]['start_command'] = 'different-command'
    else:
        spec['provider_qualification']['required_for_goal'] = True
    before = service.writer.event_log.read_all()
    frozen = (service.state_dir / admitted['plan_artifact_package_ref']).read_bytes()
    with pytest.raises(PlanArtifactPackageError, match='explicit plan port.*differs'):
        admit_plan_artifact_package_for_payload(events=before, **kwargs)
    assert service.writer.event_log.read_all() == before
    assert (service.state_dir / admitted['plan_artifact_package_ref']).read_bytes() == frozen
