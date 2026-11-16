"""Adding optional conditions must not invalidate existing request/capsule bindings."""
from dataclasses import asdict

from zf.core.task.authority import CONTRACT_METADATA_FIELDS, contract_authority_payload
from zf.core.task.schema import Task, TaskContract, task_contract_identity_payload
from zf.runtime.task_doc import compute_task_capsule_revisions, _canonical_contract_payload
from zf.runtime.task_workflow_plans import task_workflow_binding_digest
from zf.runtime.orchestrator_agent_execution_policy import task_execution_binding
from zf.runtime.run_contract import stable_json_sha256


def test_empty_conditions_preserve_legacy_authority_capsule_and_execution_payloads():
    task = Task(id='T-LEGACY', key='legacy', title='Existing request', contract=TaskContract(behavior='preserve'))
    legacy = asdict(task.contract)
    legacy.pop('constraints')
    assert task_contract_identity_payload(task.contract) == legacy
    assert contract_authority_payload(task.contract) == {k:v for k,v in legacy.items() if k not in CONTRACT_METADATA_FIELDS}
    assert _canonical_contract_payload(task)['contract'] == {k:v for k,v in legacy.items() if k not in CONTRACT_METADATA_FIELDS}
    assert task_execution_binding(task)['contract_digest'] == stable_json_sha256(legacy)
    # Frozen before this field was introduced; changing defaults needs its own migration.
    assert task_workflow_binding_digest(task) == 'sha256:7b629a138a0ff24811c161c2506bfc2e9068eb8e35acca0a21981a137d3b192c'


def test_conditions_remain_identity_bearing_for_all_consumers():
    task = Task(id='T', title='CLI')
    before = (task_workflow_binding_digest(task), compute_task_capsule_revisions(task), task_execution_binding(task))
    task.contract.constraints = ['Plan approval before implementation']
    after = (task_workflow_binding_digest(task), compute_task_capsule_revisions(task), task_execution_binding(task))
    assert all(a != b for a,b in zip(before, after))
    assert contract_authority_payload(task.contract)['constraints'] == task.contract.constraints
