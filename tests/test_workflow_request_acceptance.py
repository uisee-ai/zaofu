from __future__ import annotations

from pathlib import Path

from zf.core.task.schema import Task, TaskContract
from zf.runtime.channel_contract_artifacts import persist_channel_prd
from zf.runtime.workflow_request_acceptance import (
    bind_task_workflow_inputs,
    inherit_task_acceptance,
)
from zf.runtime.workflow_task_input_artifacts import materialize_channel_prd_reader_source


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


def test_channel_prd_lineage_is_inherited_into_workflow_source_refs() -> None:
    task = Task(
        id="TASK-CHANNEL-PRD",
        title="Deliver confirmed Channel PRD",
        contract=TaskContract(
            source_ref="channels/ch-prd/prd/r3.json",
            evidence_contract={
                "channel_id": "ch-prd",
                "thread_id": "main",
                "prd_revision": 3,
                "channel_prd_digest": "canonical-prd-sha",
                "readiness_ref": "channels/ch-prd/prd/r3-readiness.json",
                "readiness_digest": "readiness-sha",
            },
        ),
    )

    parameters, _binding, _contract, _overrides = bind_task_workflow_inputs(
        {"target_root": "."},
        task,
        task_contract_digest="task-contract-sha",
    )

    source_refs = parameters["source_refs"]
    assert source_refs["task_evidence_contract_digest"]
    assert {
        key: value
        for key, value in source_refs.items()
        if key != "task_evidence_contract_digest"
    } == {
        "task_source_ref": "channels/ch-prd/prd/r3.json",
        "task_contract_digest": "task-contract-sha",
        "channel_id": "ch-prd",
        "channel_thread_id": "main",
        "channel_prd_ref": "channels/ch-prd/prd/r3.json",
        "channel_prd_digest": "canonical-prd-sha",
        "channel_prd_revision": "3",
        "channel_prd_readiness_ref": "channels/ch-prd/prd/r3-readiness.json",
        "channel_prd_readiness_digest": "readiness-sha",
    }


def test_channel_prd_reader_source_preserves_canonical_body_and_digest(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    descriptor = persist_channel_prd(
        state, channel_id="ch-1", thread_id="main", revision=1,
        previous_ref="", previous_digest="",
        body={"title": "报名", "markdown": "# 报名\n\n最多 20 人。\n"},
        readiness_descriptor={}, created_by="test", source_event_id="source-1",
    )
    contract = {"source_ref": descriptor["ref"], "evidence_contract": {
        "channel_id": "ch-1", "channel_prd_digest": descriptor["sha256"]}}
    target = materialize_channel_prd_reader_source(
        state_dir=state, workflow_dir=tmp_path / "project" / "artifacts" / "workflow" / "req-1",
        input_contract=contract,
    )
    assert target.endswith("/artifacts/workflow/req-1/channel-prd-source.md")
    assert Path(target).read_text(encoding="utf-8") == "# 报名\n\n最多 20 人。\n"
    assert materialize_channel_prd_reader_source(
        state_dir=state, workflow_dir=tmp_path / "unused",
        input_contract={"source_ref": "docs/PRD.md"},
    ) == ""


def test_empty_constraints_do_not_invent_owner_approval_or_change_legacy_input_digest():
    task = Task(id="T-PLAIN", title="plain", contract=TaskContract())
    params, _, contract, _ = bind_task_workflow_inputs({}, task, task_contract_digest="legacy")
    assert "constraints" not in params
    assert "constraints" not in contract


def test_source_constraints_roundtrip_and_missing_coverage_is_rejected(tmp_path):
    import pytest
    from zf.runtime.workflow_request_acceptance import (
        assert_task_workflow_input_coverage, TaskWorkflowInputCoverageError,
    )
    task = Task(id="T-CONDITIONS", title="CLI", contract=TaskContract(
        constraints=["Plan 需审批后实施", "仅标准库"],
        explicit_non_goals=["不接入网络"],
    ))
    params, binding, contract, _ = bind_task_workflow_inputs({}, task, task_contract_digest="bound")
    assert params["constraints"] == ["Plan 需审批后实施", "仅标准库", "不接入网络"]
    args = dict(binding=binding, input_contract=contract, source_ref="",
                source_refs=params.get("source_refs", {}), artifact_refs=[],
                acceptance=params["acceptance"], scope=[], project_root=tmp_path)
    assert_task_workflow_input_coverage(**args, constraints=params["constraints"])
    with pytest.raises(TaskWorkflowInputCoverageError, match="constraints coverage"):
        assert_task_workflow_input_coverage(**args, constraints=["不接入网络"])
