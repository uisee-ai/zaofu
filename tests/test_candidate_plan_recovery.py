"""A candidate reader's frozen evidence can authorize only a request for a new Plan."""

from types import SimpleNamespace

import pytest

from tests.test_orchestrator_agent_semantic_failure import _runtime, _submit_rework
from zf.core.config.schema import RoleConfig
from zf.core.events import ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.fanout_failure_findings import fanout_failure_recovery
from zf.runtime.candidate_rework import plan_candidate_rework
from zf.runtime.typed_result_admission import typed_result_admission_issues
from zf.runtime.orchestrator_agent_decision_apply import apply_orchestrator_agent_decision
from zf.runtime.orchestrator_agent_semantic_failure import (
    SEMANTIC_FAILURE_REQUESTED,
    SemanticFailureCheckpointError,
    request_semantic_failure_checkpoint,
)
from zf.runtime.task_contract_snapshot import (
    build_target_snapshot,
    snapshot_payload_fields,
    target_payload_fields,
    write_target_snapshot,
    write_task_contract_snapshot,
)
from zf.runtime.verification_result import normalize_candidate_blocker, VerificationResultError


def _incident(tmp_path, *, unknown_ac=False, blocked=False, expected_children=None):
    runtime = _runtime(tmp_path)
    runtime.config.roles.append(RoleConfig(name="verify", instance_id="verify-0", role_kind="reader"))
    package = runtime.event_log.read_all()[-1].payload
    run = package["workflow_run_id"]
    anchor, child_id = "TASK-1", "TASK-2"
    runtime.task_store.update(anchor, active_dispatch_id="")
    commit = "c" * 40

    def emit(kind, body, *, cause=None):
        return runtime.event_writer.append(
            ZfEvent(
                type=kind,
                origin="kernel",
                task_id=anchor,
                causation_id=cause,
                correlation_id=run,
                payload={"workflow_run_id": run, **body},
            )
        )

    identity = {
        "workflow_run_id": run,
        "task_id": anchor,
        "contract_revision": "revision-1",
        "plan_artifact_package_digest": package["package_digest"],
        "plan_artifact_package_ref": package["package_ref"],
        "task_map_generation": "g1",
    }
    candidate = emit(
        "candidate.ready",
        {
            **identity,
            "schema_version": "candidate-freeze-receipt.v1",
            "pdd_id": anchor,
            "candidate_ref": "refs/heads/candidate/demo",
            "candidate_head_commit": commit,
        },
    )
    binding = {"authority_scope": "candidate", "candidate_event_id": candidate.id}
    contract = {
        **identity,
        **binding,
        "schema_version": "task-contract-snapshot.v1",
        "task_ref": "task/demo",
        "base_commit": "b" * 40,
        "completed_task_ids": [child_id],
        "acceptance_criteria": [
            {
                "acceptance_id": "TASK-2/AC-5",
                "statement": "matches Owner protocol",
                "verification_owner": "candidate_verify",
                "verification_tier": "release",
                "source_task_id": child_id,
                "source_acceptance_id": "AC-5",
            }
        ],
    }
    descriptor = write_task_contract_snapshot(runtime.state_dir, contract)
    target = build_target_snapshot(descriptor, target_commit=commit, contract_snapshot=contract)
    target_ref = write_target_snapshot(runtime.state_dir, {**target, **binding})
    operation = {
        **identity,
        "operation_type": "fanout_reader_child",
        "operation_id": "op-candidate-verify",
        "stage_id": "candidate-verify",
        "role_instance": "verify-0",
        "active_attempt_id": "reader-attempt",
        "request_hash": "a" * 64,
    }
    emit("fanout.started", {"fanout_id": "verify-fanout", "trigger_event_id": candidate.id,
        **({"expected_children": expected_children, "stage_id": "candidate-verify"}
           if expected_children is not None else {})})
    emit("workflow.operation.requested", operation)
    verdict = "blocked" if blocked else "rejected"
    ac = "unmapped-ac" if unknown_ac else "TASK-2/AC-5"
    result = {
        **operation,
        **snapshot_payload_fields(descriptor),
        **target_payload_fields(target_ref),
        "attempt_id": "reader-attempt",
        "execution_status": "completed",
        "verdict": verdict,
        "schema_version": "verification-result.v1",
        "output_profile_id": "candidate-verify",
        "target_commit": commit,
        "requirement_results": [{"acceptance_id": ac, "status": "blocked" if blocked else "failed"}],
        "rework_items": (
            [{"acceptance_id": ac, "status": "blocked", "allowed_scope": ["plan_ports:task_map"]}]
            if blocked
            else []
        ),
    }
    normalize_candidate_blocker(result)
    ref = write_immutable_json_sidecar(
        runtime.state_dir,
        result,
        root="fixtures/control",
        kind="call_control_result",
        schema_version=result["schema_version"],
        created_by="test",
    )
    emit(
        "workflow.call.result.admitted", {**operation, "semantic_verdict": verdict, "control_result_ref": ref}
    )
    emit("workflow.operation.settled", operation)
    child = {
        **operation,
        "fanout_id": "verify-fanout",
        "child_id": "verify-0",
        "control_result_ref": ref,
        "control_result_schema": result["schema_version"],
        "verification_result": result,
    }
    emit("fanout.child.failed", child)
    aggregate = emit(
        "fanout.aggregate.completed", {**child, "status": "failed", "failed_children": ["verify-0"]}
    )
    failure = emit(
        "test.failed", {**identity, "pdd_id": anchor, "fanout_id": "verify-fanout"}, cause=aggregate.id
    )
    request = ZfEvent(
        type=SEMANTIC_FAILURE_REQUESTED,
        task_id=anchor,
        correlation_id=run,
        payload={
            "problem_class": "semantic",
            "workflow_run_id": run,
            "flow_kind": "prd",
            "target_operation_id": operation["operation_id"],
            "failure_event_ids": [failure.id],
        },
    )
    return runtime, request, candidate, child


@pytest.mark.parametrize("unknown_ac", [False, True])
def test_candidate_reader_replan_reaches_planner_with_frozen_scope(tmp_path, unknown_ac):
    runtime, request, candidate, _ = _incident(tmp_path, unknown_ac=unknown_ac)
    runtime.event_writer.append(
        ZfEvent(
            type="prd.plan.requested",
            origin="kernel",
            correlation_id=request.correlation_id,
            payload={
                "trace_id": request.correlation_id,
                "failed_task_ids": ["OLD-TASK"],
                "task_ids": ["OLD-TASK"],
                "input_task_ids": ["OLD-TASK"],
                "resume_scope": "failed_children_only",
            },
        )
    )
    prepared = request_semantic_failure_checkpoint(runtime, request)
    context = prepared.context.input_body["checkpoint_context"]
    assert context["target_role_instance"] == "verify-0"
    assert context["target_attempt_id"] == "reader-attempt"
    decision = _submit_rework(runtime, prepared, action="return_to_plan")
    outcome = apply_orchestrator_agent_decision(runtime, decision)
    assert outcome["applied"]
    apply_orchestrator_agent_decision(runtime, decision)
    events = runtime.event_log.read_all()
    markers = [e for e in events if e.type == "orchestrator.replan_requested"]
    triggers = [e for e in events if e.type == "prd.plan.requested" and e.payload.get("rework_of")]
    assert len(markers) == len(triggers) == 1
    for row in (markers[0], triggers[0]):
        assert row.payload["rework_of"] == request.payload["failure_event_ids"][0]
        assert row.payload["source_commit"] == candidate.payload["candidate_head_commit"]
        assert row.payload.get("task_ids", []) == ([] if unknown_ac else ["TASK-2"])
        assert "OLD-TASK" not in row.payload.get("input_task_ids", [])
    assert not any(
        e.type in {"task_map.ready", "plan.approved", "orchestrator.semantic.rework.requested"}
        for e in events
    )
    with pytest.raises(SemanticFailureCheckpointError, match="already replanned"):
        request_semantic_failure_checkpoint(runtime, request)


@pytest.mark.parametrize("action", ["rework", "rebind", "invalidate", "stale_candidate"])
def test_candidate_checkpoint_cannot_grant_writer_or_stale_authority(tmp_path, action):
    runtime, request, candidate, _ = _incident(tmp_path)
    prepared = request_semantic_failure_checkpoint(runtime, request)
    decision = _submit_rework(
        runtime, prepared, action="return_to_plan" if action == "stale_candidate" else action
    )
    if action == "stale_candidate":
        runtime.event_writer.append(
            ZfEvent(type="candidate.ready", origin="kernel", payload=candidate.payload)
        )
    outcome = apply_orchestrator_agent_decision(runtime, decision)
    assert not outcome["applied"]
    assert not any(
        e.type in {"prd.plan.requested", "orchestrator.semantic.rework.requested"}
        for e in runtime.event_log.read_all()
    )


@pytest.mark.parametrize("change", ["other_attempt", "other_run", "wrong_operation", "different_fanout"])
def test_candidate_checkpoint_rejects_unbound_failure(tmp_path, change):
    runtime, request, _, child = _incident(tmp_path)
    if change == "other_attempt":
        request.payload["active_attempt_id"] = "older"
    elif change == "other_run":
        request.payload["workflow_run_id"] = "another"
    elif change == "wrong_operation":
        request.payload["target_operation_id"] = "other"
    else:
        failure = runtime.event_log.read_all()[-1]
        bad = runtime.event_writer.append(
            ZfEvent(
                type=failure.type,
                origin="kernel",
                correlation_id=failure.correlation_id,
                causation_id=failure.causation_id,
                payload={**failure.payload, "fanout_id": child["fanout_id"] + "-other"},
            )
        )
        request.payload["failure_event_ids"] = [bad.id]
    with pytest.raises(SemanticFailureCheckpointError):
        request_semantic_failure_checkpoint(runtime, request)


def test_candidate_blocker_aggregate_binds_affected_child(tmp_path):
    runtime, _, candidate, child = _incident(tmp_path, blocked=True)
    owner = SimpleNamespace(
        state_dir=runtime.state_dir, event_log=runtime.event_log, _fanout_child_payloads=lambda _: [child]
    )
    manifest = {
        "fanout_id": child["fanout_id"],
        "children": [{"child_id": child["child_id"], "status": "failed"}],
    }
    actual = fanout_failure_recovery(owner, manifest)
    assert actual["candidate_event_id"] == candidate.id
    assert actual["failed_task_ids"] == ["TASK-2"]
    assert actual["recovery_action"] == "replan"
    failure = runtime.event_log.read_all()[-1]
    failure.payload.update(
        {**actual, "target_ref": candidate.payload["candidate_ref"], "failure_scope": "candidate"}
    )
    plans = plan_candidate_rework([*runtime.event_log.read_all()[:-1], failure], config=runtime.config)
    assert [(plan.action, plan.failed_task_ids) for plan in plans] == [("replan", ("TASK-2",))]
    (runtime.state_dir / child["control_result_ref"]["ref"]).write_text("{}")
    assert fanout_failure_recovery(owner, manifest)["recovery_action"] == "escalate"


@pytest.mark.parametrize(
    "verdict,item_status,ac",
    [
        ("rejected", "blocked", "A"),
        ("blocked", "incorrect", "A"),
        ("blocked", "blocked", "B"),
    ],
)
def test_plan_scope_requires_explicit_blocked_row(verdict, item_status, ac):
    result = {
        "output_profile_id": "candidate-verify",
        "execution_status": "completed",
        "verdict": verdict,
        "requirement_results": [{"status": "blocked", "acceptance_id": "A"}],
        "rework_items": [
            {"status": item_status, "acceptance_id": ac, "allowed_scope": ["plan_ports:task_map"]}
        ],
    }
    with pytest.raises(VerificationResultError):
        normalize_candidate_blocker(result)


@pytest.mark.parametrize("scope", ["plan_ports:invented", "plan_ports", "plan_ports:"])
def test_candidate_submit_rejects_undeclared_plan_port(tmp_path, scope):
    runtime, _, _, child = _incident(tmp_path, blocked=True)
    result = child["verification_result"]
    result["rework_items"][0]["allowed_scope"] = [scope]
    if scope == "plan_ports":
        # A previously admitted result can still be decoded from its original event.
        normalize_candidate_blocker(result)
    issues = typed_result_admission_issues(
        runtime.state_dir, schema_version=result["schema_version"], result=result, semantic_submit=True
    )
    assert [i["code"] for i in issues] == ["candidate_plan_port_invalid"]
    assert ("invented" if scope == "plan_ports:invented" else "plan_ports:task_map") in issues[0]["message"]


@pytest.mark.parametrize("change", ["current", "stale", "cross_run", "retry_same_candidate"])
def test_controlled_candidate_rework_rechecks_frozen_scope(tmp_path, change):
    from zf.runtime.control_actions import ControlledActionService

    runtime, _, candidate, child = _incident(tmp_path, blocked=True)
    owner = SimpleNamespace(
        state_dir=runtime.state_dir, event_log=runtime.event_log, _fanout_child_payloads=lambda _: [child]
    )
    manifest = {
        "fanout_id": child["fanout_id"],
        "children": [{"child_id": child["child_id"], "status": "failed"}],
    }
    recovery = fanout_failure_recovery(owner, manifest)
    original = runtime.event_log.read_all()[-1]
    failure = runtime.event_writer.append(
        ZfEvent(
            type=original.type,
            origin="kernel",
            causation_id=original.causation_id,
            correlation_id=original.correlation_id,
            payload={**original.payload, **recovery},
        )
    )
    if change == "stale":
        runtime.event_writer.append(ZfEvent(type=candidate.type, origin="kernel", payload=candidate.payload))
    service = ControlledActionService(
        runtime.state_dir, runtime.event_writer, config=runtime.config, actor="operator"
    )
    requested = runtime.event_writer.emit("control.action.requested", actor="operator", payload={})
    result = service.execute(
        action="candidate-rework-apply",
        requested_action="candidate-rework-apply",
        requested=requested,
        payload={
            "pdd_id": failure.payload["pdd_id"],
            "source_event_id": failure.id,
            "trace_id": "unrelated" if change == "cross_run" else failure.correlation_id,
            "failed_task_ids": ["WRONG"],
            "candidate_rework_action": "retrigger" if change == "retry_same_candidate" else "replan",
        },
    )
    assert bool(result["ok"]) == (change == "current")
    events = runtime.event_log.read_all()
    markers = [e for e in events if e.type == "orchestrator.replan_requested"]
    if change == "current":
        assert len(markers) == 1 and markers[0].payload["task_ids"] == ["TASK-2"]
        assert markers[0].payload["source_commit"] == candidate.payload["candidate_head_commit"]
    else:
        assert not markers
    assert not any(e.type == "task_map.ready" for e in events)
