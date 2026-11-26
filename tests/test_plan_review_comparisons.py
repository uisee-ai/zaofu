"""A full Critic accounts for frozen discrepancies without manufacturing a defect."""

from copy import deepcopy

import pytest

from tests.test_result_submit import _running_plan_synth_operation
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.plan_execution_review import attach_execution_review, review_execution
from zf.runtime.plan_review_claims import POLICY
from zf.runtime.result_submit import ResultSubmitError
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import reduce_workflow_operations


def case(tmp_path, *, mode="full", legacy=False):
    state = tmp_path / "project/.zf"

    def source(name, body):
        ref = write_immutable_json_sidecar(
            state,
            body,
            root="review-inputs",
            kind="test",
            schema_version="test.v1",
            created_by="test",
        )
        return {
            **ref,
            "source_id": name,
            "artifact_id": name + ".json",
            "allowed_paths": ["$"],
        }

    task_map = {
        "tasks": [
            {
                "task_id": "A",
                "allowed_paths": [],
                "validation": {
                    "commands": [
                        {
                            "id": "browser",
                            "owner": "candidate_verify",
                            "command": "build && browser",
                        }
                    ]
                },
            }
        ]
    }
    policy = {
        "host_verification_commands": [
            "build",
            "build && browser",
            "test-all",
            "optional-probe",
        ],
        "effective_policy_digest": "p",
    }
    review = review_execution(
        task_map,
        ports={},
        policy=policy,
        metadata={"plan_review": {"mode": mode}, "task_pipeline": {"mode": "blocking"}},
    )
    assert review["execution_comparisons"]["standard_with_empty_write_scope"][
        "task_ids"
    ] == ["A"]
    selections = review["execution_comparisons"]["candidate_command_scope"]
    assert set(selections["host_permitted_only"]) == {
        "build",
        "test-all",
        "optional-probe",
    }
    assert set(selections["candidate_commands"]) == {"build && browser"}
    if legacy:
        review.pop("execution_comparisons")
    extra = {}
    attach_execution_review(
        state_dir=state, payload=extra, review=review, source_event_id="plan"
    )
    current = source("child-artifact-planner-1", task_map)
    requirement = source(
        "requirement",
        {
            "rule": "The authorized noop producer uses the standard entry. Only build && browser is required."
        },
    )
    bindings = [current, requirement, *extra["input_refs"]]
    contract = source(
        "plan-synth-contract",
        {"child_bindings": [{"child_id": "planner"}], "source_bindings": bindings},
    )
    runtime, prepared, svc, token = _running_plan_synth_operation(
        tmp_path,
        verdict_only=True,
        extra_payload={
            "plan_review_evidence_policy": POLICY,
            "plan_synth_contract_ref": contract["ref"],
            "plan_synth_contract_digest": contract["sha256"],
            "reviewed_plan_candidate_digest": contract["sha256"],
            "input_refs": [*bindings, contract],
        },
    )
    result = {
        "status": "completed",
        "recommendation": "approve",
        "summary": "All earlier issues are fixed.",
        "report": {
            "recommendation": "approve",
            "status": "passed",
            "summary": "All earlier issues are fixed.",
            "findings": [],
            "gates": [],
        },
    }

    def cite(ref, path, value):
        return {
            **{k: ref[k] for k in ("artifact_id", "source_id", "sha256")},
            "json_path": path,
            "observed_value": value,
        }

    authority = cite(
        requirement,
        "$.rule",
        "The authorized noop producer uses the standard entry. Only build && browser is required.",
    )
    args = {
        "operation_id": prepared.operation_id,
        "role_instance": "plan-critic",
        "credential": token,
    }
    assessments = [
        {
            "comparison": "standard_with_empty_write_scope",
            "task_ids": ["A"],
            "conclusion": "A needs no mutation.",
            "basis": {
                "obligation": "Use the authorized standard entry",
                "requirement_evidence": [authority],
                "current_evidence": [cite(current, "$.tasks[0].allowed_paths", [])],
            },
        },
        {
            "comparison": "candidate_command_scope",
            "conclusion": "Register the composite browser probe; the owner did not require independent build, broad tests or optional probe.",
            "basis": {
                "obligation": "Verify the required browser probe",
                "requirement_evidence": [authority],
                "current_evidence": [
                    cite(
                        current,
                        "$.tasks[0].validation.commands[0].command",
                        "build && browser",
                    )
                ],
            },
        },
    ]
    return runtime, svc, args, result, assessments


def read_current_review_inputs(runtime, operation_id):
    from zf.runtime.artifact_read_ledger import read_attempt_artifact

    op = reduce_workflow_operations(runtime.event_log.read_all())[operation_id]
    request = hydrate_sidecar_ref(runtime.state_dir, op["request_ref"]).payload[
        "request"
    ]
    digest = request["source_manifest_digest"]
    manifest = hydrate_sidecar_ref(
        runtime.state_dir,
        {
            "ref": f"artifacts/attempts/{request['active_attempt_id']}/source-manifests/{digest}.json",
            "sha256": digest,
        },
    ).payload
    for row in request["required_reads"]:
        read_attempt_artifact(
            runtime.state_dir,
            manifest=manifest,
            actor="plan-critic",
            role="plan-critic",
            source_id=row["source_id"],
            artifact_id=row["artifact_id"],
            json_path=row["json_path"],
        )


def test_fresh_full_approval_must_close_the_exact_comparisons(tmp_path):
    runtime, svc, args, result, assessments = case(tmp_path)
    assert any(
        e["field"] == "report.execution_assessments"
        for e in svc.validate_plan_candidate(**args, semantic_result=result)["errors"]
    )
    with pytest.raises(ResultSubmitError):
        svc.submit(**args, semantic_result=result)
    wrong = deepcopy(assessments)
    wrong[0]["task_ids"] = ["other"]
    wrong[1]["basis"]["current_evidence"][0]["observed_value"] = "test-all"
    result["report"]["execution_assessments"] = wrong
    errors = svc.validate_plan_candidate(**args, semantic_result=result)["errors"]
    assert {e["field"] for e in errors} >= {
        "report.execution_assessments[0].task_ids",
        "report.execution_assessments[1].basis.current_evidence[0]",
    }
    result["report"]["execution_assessments"] = assessments
    read_current_review_inputs(runtime, args["operation_id"])
    assert (
        svc.validate_plan_candidate(**args, semantic_result=result)["status"]
        == "passed"
    )
    svc.submit(**args, semantic_result=result)
    op = reduce_workflow_operations(runtime.event_log.read_all())[args["operation_id"]]
    body = hydrate_sidecar_ref(
        runtime.state_dir, op["admitted_control_result_ref"]
    ).payload
    assert (
        body["execution_assessments"] == assessments
        and body["findings"] == []
        and body["gates"] == []
    )


@pytest.mark.parametrize("mode,legacy", [("full", True), ("targeted", False)])
def test_old_and_targeted_requests_keep_their_review_contract(tmp_path, mode, legacy):
    runtime, svc, args, result, _ = case(tmp_path, mode=mode, legacy=legacy)
    read_current_review_inputs(runtime, args["operation_id"])
    assert (
        svc.validate_plan_candidate(**args, semantic_result=result)["status"]
        == "passed"
    )
