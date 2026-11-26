"""Full Critic reviews the signed initial selection in its current response."""

from copy import deepcopy

import pytest

from tests.test_plan_review_comparisons import read_current_review_inputs
from tests.test_result_submit import _running_plan_synth_operation
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.plan_execution_review import attach_execution_review, review_execution
from zf.runtime.candidate_rework_evidence import plan_rejection_feedback
from zf.core.events import ZfEvent
from zf.runtime.plan_readonly_acceptance_review import (
    ACCEPTANCE_EVIDENCE_PATH,
    INITIAL_ACCEPTANCE,
    READONLY_SCOPE,
)
from zf.runtime.plan_review_claims import POLICY
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def stock_case(tmp_path, owner, *, partial_feedback=False):
    state = tmp_path / "project/.zf"

    def source(sid, body, *, kind="test"):
        ref = write_immutable_json_sidecar(
            state,
            body,
            root="review-inputs",
            kind=kind,
            schema_version="test.v1",
            created_by="fixture",
        )
        return {
            **ref,
            "source_id": sid,
            "artifact_id": sid + ".json",
            "allowed_paths": ["$"],
        }

    task = {
        "task_id": "catalog",
        "execution_mode": "runtime_only",
        "runtime_only": True,
        "write_free": True,
        "allowed_paths": [],
        "verification_read_paths": ["catalog.json"],
        "acceptance_criteria": [
            {
                "id": "catalog-reconciled",
                "statement": "Published records agree.",
                "verification_owner": "candidate_verify",
                "verification_command_ids": ["local-probe"],
            }
        ],
        "validation": {
            "commands": [
                {
                    "id": "local-probe",
                    "owner": "task_verify",
                    "command": "inspect catalog",
                }
            ]
        },
    }
    raw = {"tasks": [task]}
    review = review_execution(
        raw,
        ports={},
        policy={"effective_policy_digest": "policy"},
        metadata={"task_pipeline": {"mode": "blocking"}},
    )
    child = source("child-artifact-planner-1", raw)
    obligation = "Inspect the inventory locally. Final acceptance belongs to the frozen Candidate."
    requirement = source("requirement", {"rule": obligation})
    input_refs = [child, requirement]
    reason = "The initial inventory audit must close a mandatory local AC."
    run = "inventory-run"
    decision = ZfEvent(
        type="plan.rejected",
        actor="operator",
        origin="kernel",
        payload={"workflow_run_id": run, "reason": reason},
    )
    instructions = plan_rejection_feedback(decision.payload)
    context = {
        "operator_feedback": ["Start from the previous warehouse package."],
        "rework_source": "plan.rejected",
        "rework_feedback": [*instructions, "Historical smoke check"],
        "rework_of": decision.id,
    }
    trigger = ZfEvent(
        type="inventory.scan.completed",
        actor="zf-cli",
        origin="kernel",
        payload={"workflow_run_id": run, **context},
    )
    frozen_context = (
        {**context, "rework_feedback": ["Incomplete projection"]} if partial_feedback else context
    )
    feedback = source("plan-rework-context", frozen_context, kind="plan_rework_context")
    if owner:
        input_refs.append(feedback)
    payload = {"workflow_run_id": run, "input_refs": input_refs, "plan_review_evidence_policy": POLICY}
    attach_execution_review(
        state_dir=state,
        payload=payload,
        review=review,
        source_event_id=trigger.id,
        read_events=lambda: [decision, trigger],
    )
    inputs = payload["input_refs"]
    summary = next(r for r in inputs if r["source_id"] == "plan-execution-review")
    checks = hydrate_sidecar_ref(state, summary).payload["execution_comparisons"]
    contract = source(
        "plan-synth-contract",
        {"child_bindings": [{"child_id": "planner"}], "source_bindings": inputs},
    )
    runtime, operation, svc, token = _running_plan_synth_operation(
        tmp_path,
        verdict_only=True,
        extra_payload={
            **payload,
            "input_refs": [*inputs, contract],
            "plan_synth_contract_ref": contract["ref"],
            "plan_synth_contract_digest": contract["sha256"],
            "reviewed_plan_candidate_digest": contract["sha256"],
        },
    )

    def cite(ref, path, observed):
        return {
            **{k: ref[k] for k in ("artifact_id", "source_id", "sha256")},
            "json_path": path,
            "observed_value": observed,
        }

    basis = {
        "obligation": obligation,
        "requirement_evidence": [cite(requirement, "$.rule", obligation)],
        "current_evidence": [
            cite(
                child,
                "$.tasks[0].acceptance_criteria[0].verification_owner",
                "candidate_verify",
            )
        ],
    }
    fresh = deepcopy(basis)
    if owner:
        for i, note in enumerate(instructions):
            fresh["requirement_evidence"].append(
                {
                    **cite(feedback, f"$.rework_feedback[{i}]", note),
                    "initial_acceptance_requirement": "requires_nonempty" if i == 0 else "unrelated",
                    "rationale": "Requires a local mandatory result."
                    if i == 0
                    else "This addresses exported documentation.",
                }
            )
    else:
        fresh["requirement_evidence"][0].update(
            initial_acceptance_requirement="allows_empty",
            rationale="The local probe is preliminary; final acceptance belongs to Candidate.",
        )
    data = checks[READONLY_SCOPE]["facts"][INITIAL_ACCEPTANCE]
    rows = [
        {
            "comparison": "readonly_pipeline_entry",
            "task_ids": ["catalog"],
            "disposition": "no_repair",
            "basis": basis,
            "conclusion": "Task starts at Verify.",
            "runtime_evidence": cite(
                summary,
                "$.execution_comparisons.readonly_pipeline_entry.facts.entries[0].entry_mode",
                "verify_only",
            ),
        },
        {
            "comparison": READONLY_SCOPE,
            "task_ids": ["catalog"],
            "disposition": "no_repair",
            "basis": fresh,
            "conclusion": "The initial route selected the local probe and zero mandatory ACs.",
            "runtime_evidence": cite(summary, ACCEPTANCE_EVIDENCE_PATH, data),
        },
    ]
    result = {
        "status": "completed",
        "recommendation": "approve",
        "summary": "Inventory review",
        "report": {
            "status": "passed",
            "recommendation": "approve",
            "summary": "Inventory review",
            "findings": [],
            "gates": [],
            "execution_assessments": rows,
        },
    }
    args = {
        "operation_id": operation.operation_id,
        "role_instance": "plan-critic",
        "credential": token,
    }
    read_current_review_inputs(runtime, operation.operation_id)
    return runtime, svc, args, result, summary, review


@pytest.mark.parametrize("owner", [False, True])
def test_stock_full_critic_binds_the_obligation_to_empty_selection(tmp_path, owner):
    _, svc, args, result, _, _ = stock_case(tmp_path, owner)

    def validate():
        return svc.validate_plan_candidate(**args, semantic_result=result)

    assessment = result["report"]["execution_assessments"][1]
    if owner:
        er = validate()["errors"]
        assert any("nonempty initial acceptance" in e["message"] for e in er), er
        declaration = assessment["basis"]["requirement_evidence"].pop()
        assert any("each exact assigned Owner" in e["message"] for e in validate()["errors"])
        assessment["basis"]["requirement_evidence"].append(declaration)
        assessment["disposition"] = "repair_required"
        result.update(recommendation="reject")
        result["report"].update(
            status="failed",
            recommendation="reject",
            fix_items=[
                {
                    "id": "LOCAL-AC",
                    "comparison": READONLY_SCOPE,
                    "task_id": "catalog",
                    "observed_gap": "Local mandatory criterion not selected.",
                    "required_change": "Align the declared local criterion.",
                    "done_when": "Compiled initial selection contains the required criterion.",
                    "basis": assessment["basis"],
                }
            ],
        )
    assert validate()["status"] == "passed", validate()
    svc.submit(**args, semantic_result=result)


def test_signed_owner_decision_with_incomplete_feedback_requires_resolution(tmp_path):
    runtime, svc, args, result, summary, _ = stock_case(tmp_path, True, partial_feedback=True)
    spec = hydrate_sidecar_ref(runtime.state_dir, summary).payload["execution_comparisons"][READONLY_SCOPE]
    assert spec["initial_acceptance_review"]["unresolved_owner_decision"]
    assert "owner_feedback" not in spec["initial_acceptance_review"]
    row = result["report"]["execution_assessments"][1]
    base = row["basis"]["requirement_evidence"][0]
    row["basis"]["requirement_evidence"] = [
        {
            **base,
            "initial_acceptance_requirement": "allows_empty",
            "rationale": "The base rule allows a local inventory probe.",
        }
    ]
    errors = svc.validate_plan_candidate(**args, semantic_result=result)["errors"]
    assert any("current Owner decision cannot be reconciled" in e["message"] for e in errors), errors


@pytest.mark.parametrize(
    "mutation",
    ["command_only", "unrelated_only", "unknown", "wrong_value", "missing_reason"],
)
def test_cannot_bypass_the_scoped_evidence_and_consistency_check(tmp_path, mutation):
    _, svc, args, result, _, _ = stock_case(tmp_path, False)
    row = result["report"]["execution_assessments"][1]
    item = row["basis"]["requirement_evidence"][0]
    evidence = row["runtime_evidence"]
    if mutation == "command_only":
        evidence.update(
            json_path="$.execution_comparisons.readonly_command_scope.facts.entries[0].command_ids",
            observed_value=["local-probe"],
        )
    elif mutation == "unrelated_only":
        item["initial_acceptance_requirement"] = "unrelated"
    elif mutation == "unknown":
        item["initial_acceptance_requirement"] = "unclear"
    elif mutation == "wrong_value":
        evidence["observed_value"]["without_mandatory_acceptance_count"] = 0
    else:
        item.pop("rationale")
    assert svc.validate_plan_candidate(**args, semantic_result=result)["errors"]


def test_new_full_review_only_and_legacy_wrc_replay(tmp_path):
    from tests.test_call_result_runtime import _runtime
    from zf.runtime.call_result_runtime import prepare_call_operation
    from zf.runtime.workflow_operation import WorkflowOperationError

    runtime = _runtime(tmp_path)
    state = runtime.state_dir
    requirement = {
        **write_immutable_json_sidecar(
            state,
            {"rule": "Inspect stock locally."},
            root="requirements",
            kind="requirement",
            schema_version="inventory.v1",
            created_by="test",
        ),
        "source_id": "requirement",
        "artifact_id": "inventory.json",
        "allowed_paths": ["$"],
    }
    row = {
        "task_id": "stock",
        "execution_mode": "runtime_only",
        "runtime_only": True,
        "write_free": True,
        "allowed_paths": [],
        "verification_read_paths": ["counts.json"],
        "acceptance_criteria": [
            {
                "id": "inventory",
                "statement": "Counts agree",
                "verification_owner": "candidate_verify",
                "verification_command_ids": ["probe"],
            }
        ],
        "validation": {"commands": [{"id": "probe", "owner": "task_verify", "command": "probe-count"}]},
    }
    tasks = [{**row, "task_id": f"stock-{i}"} for i in range(20)]
    tasks.append({**row, "task_id": "pending", "acceptance_criteria": [], "validation": {}})
    review = review_execution(
        {"tasks": tasks},
        ports={},
        policy={"effective_policy_digest": "policy"},
        metadata={"task_pipeline": {"mode": "blocking"}},
    )
    base = {
        "workflow_run_id": "run",
        "fanout_id": "fanout",
        "role_instance": "plan-critic",
        "output_profile_id": "plan-synth",
        "canonical_success_event": "fanout.synth.completed",
        "canonical_failure_event": "fanout.synth.completed",
    }

    def projection(*, new=True, authority=True, view=review, **extra):
        payload = {
            **base,
            "input_refs": [deepcopy(requirement)] if authority else [],
            **extra,
        }
        if new:
            payload["plan_review_evidence_policy"] = POLICY
        attach_execution_review(state_dir=state, payload=payload, review=view, source_event_id="test")
        payload["plan_review_evidence_policy"] = POLICY
        src = next(s for s in payload["input_refs"] if s["source_id"] == "plan-execution-review")
        return payload, src

    def comparison(src):
        return hydrate_sidecar_ref(state, src).payload["execution_comparisons"][READONLY_SCOPE]

    for opts in (
        {"authority": False},
        {"diagnosis_review": {"mode": "isolated"}},
        {"review_scope_ref": "scoped"},
        {"view": {**review, "plan_review": {"mode": "scoped"}}},
    ):
        assert "initial_acceptance_review" not in comparison(projection(**opts)[1])
    old, old_ref = projection(new=False)
    fresh, fresh_ref = projection()
    fresh_sha = fresh_ref["sha256"]
    assert old_ref["sha256"] != fresh_sha
    assert "initial_acceptance_review" not in comparison(old_ref)
    summary = comparison(fresh_ref)["facts"][INITIAL_ACCEPTANCE]
    assert (
        summary["resolved_acceptance_task_count"],
        summary["without_mandatory_acceptance_count"],
        summary["unresolved_task_count"],
    ) == (20, 20, 1)
    assert (
        summary["sample_unresolved_tasks"] == ["pending"]
        and len(summary["sample_without_mandatory_acceptance"]) == 16
    )

    def prepare(payload, key, attempt):
        return prepare_call_operation(
            runtime,
            operation_type="fanout_synth",
            operation_key=key,
            stage_id="plan",
            task_id="",
            dispatch_id=attempt,
            payload=payload,
        )

    prior = prepare(old, "existing", "attempt-1")
    replay = prepare(fresh, "existing", "attempt-1")
    assert replay.replay_hit and replay.request_hash == prior.request_hash
    assert (
        next(s for s in fresh["input_refs"] if s["source_id"] == "plan-execution-review")["sha256"]
        == old_ref["sha256"]
    )
    new, _ = projection()
    issued = prepare(new, "new", "attempt-2")
    assert not issued.replay_hit and any(s.get("sha256") == fresh_sha for s in new["input_refs"])
    changed = deepcopy(review)
    changed["verification"]["task_requirements"][0]["initial_readonly_verify"]["mandatory_acceptance_ids"] = [
        "inventory"
    ]
    with pytest.raises(WorkflowOperationError, match="diverged"):
        prepare(projection(view=changed)[0], "existing", "attempt-1")
