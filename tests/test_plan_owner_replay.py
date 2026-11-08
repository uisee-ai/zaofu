"""Owner-bound requirement authority must be identical on first admission/replay."""
import json

import pytest

from tests.test_plan_requirement_amendment import bound_review, propose  # noqa: F401
from tests.test_plan_artifact_review import review  # noqa: F401
from zf.runtime.plan_artifact_package import PlanArtifactPackageError, admit_plan_artifact_package_for_payload
from zf.runtime.plan_requirement_amendment import amended_task_map_baseline
from zf.runtime.run_contract import stable_json_sha256, write_run_contract


@pytest.mark.parametrize('mode', ['ref', 'inline'])
def test_owner_bound_package_replays_old_transport_requirement(bound_review, mode):
    service, _, original, previous_map, _ = bound_review
    service.apply(propose(bound_review)['draft_ref'])
    path = service.state_dir / 'artifacts/new-task-map.json'
    path.write_text(json.dumps(amended_task_map_baseline(service.state_dir, 'run-1', previous_map)))
    contract = {'schema_version': 'run-contract.v1', 'workflow': {'kind': 'prd'}}
    write_run_contract(service.state_dir, {**contract, 'contract_digest': stable_json_sha256(contract)})
    payload = {'task_map_ref': str(path)}
    if mode == 'ref':
        payload['requirement_spec_ref'] = 'artifacts/requirement.json'
    else:
        payload['plan_ports'] = [{'logical_name':'requirement_spec','body':original}]
    def admit():
        return admit_plan_artifact_package_for_payload(
            state_dir=service.state_dir, project_root=service.project_root, event_writer=service.writer,
            events=service.writer.event_log.read_all(), workflow_run_id='run-1',
            flow_kind='prd', producer_stage_id='prd-plan', goal_id='goal',
            metadata={'artifact_package':{'mode':'blocking'}}, payload=payload,
            source_event_id='ready-1', correlation_id='run-1')
    first = admit()
    before = service.writer.event_log.read_all()
    second = admit()
    assert first['plan_artifact_package_digest'] == second['plan_artifact_package_digest']
    assert service.writer.event_log.read_all() == before
    # The owner override is specific to requirements, not a waiver for other inputs.
    payload['plan_ports'] = [*payload.get('plan_ports', []),
                             {'logical_name':'execution_spec','body':{'changed':True}}]
    with pytest.raises(PlanArtifactPackageError, match='explicit plan port'):
        admit()


def test_new_owner_revision_still_requires_new_plan_generation(bound_review):
    service, _, _, previous_map, _ = bound_review
    path = service.state_dir / 'artifacts/new-task-map.json'
    path.write_text(json.dumps(previous_map))
    contract = {'schema_version': 'run-contract.v1', 'workflow': {'kind': 'prd'}}
    write_run_contract(service.state_dir, {**contract, 'contract_digest': stable_json_sha256(contract)})
    def admit():
        return admit_plan_artifact_package_for_payload(
            state_dir=service.state_dir, project_root=service.project_root, event_writer=service.writer,
            events=service.writer.event_log.read_all(), workflow_run_id='run-1',
            flow_kind='prd', producer_stage_id='prd-plan', goal_id='goal',
            metadata={'artifact_package':{'mode':'blocking'}},
            payload={'task_map_ref':str(path),'requirement_spec_ref':'artifacts/requirement.json'},
            source_event_id='ready-1', correlation_id='run-1')
    admit()
    service.apply(propose(bound_review)['draft_ref'])
    with pytest.raises(PlanArtifactPackageError, match='requirement_spec.*differs'):
        admit()
