from __future__ import annotations

from pathlib import Path

from zf.core.task.schema import Task, TaskContract
from zf.runtime.workflow_request_acceptance import (
    assert_task_workflow_input_coverage,
    inherit_task_acceptance,
)


def test_inherits_canonical_task_acceptance_when_request_omits_it() -> None:
    task = Task(
        id="TASK-1",
        title="delivery",
        contract=TaskContract(acceptance_criteria=[{
            "id": "AC-1",
            "statement": "The regression command passes.",
        }]),
    )

    parameters = inherit_task_acceptance({"target_ref": "HEAD"}, task)

    assert parameters["acceptance"] == ["The regression command passes."]


def test_explicit_request_acceptance_is_not_overwritten() -> None:
    task = Task(
        id="TASK-1",
        title="delivery",
        contract=TaskContract(acceptance_criteria=["canonical acceptance"]),
    )

    parameters = inherit_task_acceptance(
        {"acceptance": ["approved revision"]},
        task,
    )

    assert parameters["acceptance"] == ["approved revision"]


def test_source_ref_coverage_accepts_runtime_state_root_alias(tmp_path: Path) -> None:
    state_root = tmp_path / ".zf"
    source = state_root / "artifacts" / "issue.json"
    source.parent.mkdir(parents=True)
    source.write_text("{}", encoding="utf-8")
    from zf.runtime.workflow_request_acceptance import bind_task_workflow_inputs
    task = Task(
        id="TASK-1",
        title="Issue",
        contract=TaskContract(source_ref="artifacts/issue.json"),
    )
    _, binding, input_contract, _ = bind_task_workflow_inputs(
        {}, task, task_contract_digest="contract",
    )
    assert_task_workflow_input_coverage(
        binding=binding,
        input_contract=input_contract,
        source_ref=str(source),
        source_refs={
            "task_source_ref": input_contract["source_ref"],
            "task_contract_digest": "contract",
            "task_evidence_contract_digest": binding["task_evidence_contract_digest"],
        },
        artifact_refs=[input_contract["source_ref"]],
        acceptance=["exit_code=0"],
        constraints=[],
        scope=[],
        project_root=tmp_path / "project",
        reference_roots=(state_root,),
    )
