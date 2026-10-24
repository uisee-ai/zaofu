"""Approval pins semantic task inputs, not refreshed document projections."""
from copy import deepcopy

import pytest

from zf.core.task.schema import Task, TaskContract
from zf.runtime.task_doc import apply_task_capsule_revisions
from zf.runtime.task_workflow_plans import task_workflow_binding_digest
from zf.runtime.workflow_anchor import bind_workflow_request_to_task


def test_request_binding_and_capsule_refresh_do_not_change_semantic_approval():
    task = Task(id='T1', title='Fix JSON', contract=TaskContract(
        behavior='Reject invalid JSON', scope=['parser.py'],
        evidence_contract={'channel_prd_digest': 'sha256:original', 'prd_revision': 1}))
    apply_task_capsule_revisions(task)
    before = task_workflow_binding_digest(task)
    bind_workflow_request_to_task(task, request_id='R1', request_revision=2,
                                 origin_binding_digest='sha256:origin')
    apply_task_capsule_revisions(task)
    task.contract.task_doc_ref = 'state/task_docs/T1/task.md'
    task.contract.acceptance_evidence = {'observed': 'receipt'}
    assert task_workflow_binding_digest(task) == before


@pytest.mark.parametrize('field,value', [
    ('behavior', 'Allow invalid JSON'), ('scope', ['**']),
    ('acceptance', 'weaker'), ('verification', 'true'),
    ('evidence_contract', {'channel_prd_digest': 'sha256:new', 'prd_revision': 2}),
    ('owner_role', 'other'), ('source_ref', 'different.md'),
])
def test_semantic_contract_changes_still_invalidate_approval(field, value):
    original = Task(id='T1', title='Fix JSON', contract=TaskContract(behavior='Reject invalid JSON'))
    changed = deepcopy(original)
    setattr(changed.contract, field, value)
    assert task_workflow_binding_digest(changed) != task_workflow_binding_digest(original)
