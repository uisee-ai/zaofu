"""Operator repair and replay fences for the first independent Critic dispatch."""

import pytest

from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.fanout import FanoutManifestProjector
from zf.runtime.fanout_identity import fanout_current_status
from zf.runtime.workflow_operation import operation_request_hash


@pytest.fixture
def checkpoint(tmp_path, monkeypatch):
    from zf.runtime import diagnosis_critic_handoff

    writer = EventWriter(EventLog(tmp_path / "events.jsonl"))

    def event(kind, id, *, cause=None, **data):
        return writer.append(
            ZfEvent(
                type=kind,
                id=id,
                actor="zf-cli",
                origin="kernel",
                causation_id=cause,
                correlation_id="run",
                payload={"workflow_run_id": "run", **data},
            )
        )

    event("run.started", "started", run_id="run")
    target = {"task_map_generation": "generation", "plan_artifact_package_digest": "p" * 64}
    event(
        "candidate.ready",
        "freeze",
        schema_version="candidate-freeze-receipt.v1",
        candidate_head_commit="commit",
        **target,
    )
    scope = {
        **target,
        "workflow_run_id": "run",
        "resume_scope": "candidate_diagnosis_only",
        "replan_classification": "oa_semantic_return_to_plan",
        "source_commit": "commit",
        "candidate_event_id": "freeze",
        "pdd_id": "T",
        "rework_of": "original-failure",
        "orchestrator_decision_ref": {"ref": "oa", "sha256": "x"},
    }
    event("scan.completed", "trigger", **scope)
    carrier = {
        "stage_id": "plan",
        "topology": "fanout_reader",
        "pdd_id": "T",
        "trigger_event_id": "trigger",
        "trigger_payload": scope,
        "children": [{"child_id": "planner", "role_instance": "planner"}],
        "aggregate": {"mode": "wait_for_all", "synth_role": "critic", "failure_event": "plan.failed"},
    }
    event("fanout.started", "old-start", cause="trigger", fanout_id="old", **carrier)
    event(
        "fanout.child.completed",
        "child",
        fanout_id="old",
        child_id="planner",
        role_instance="planner",
        status="completed",
        result_event_id="producer",
    )
    event("fanout.synth.completed", "failure", fanout_id="old", status="failed", role_instance="critic")
    event(
        "fanout.aggregate.completed",
        "aggregate",
        fanout_id="old",
        status="failed",
        synth_event_id="failure",
    )
    event("plan.failed", "stage-failure", cause="failure", fanout_id="old")
    successor = {**scope, "rework_of": "stage-failure"}
    event("scan.completed", "retry", cause="stage-failure", **successor)
    event(
        "fanout.started",
        "new-start",
        cause="retry",
        fanout_id="wrong",
        **{**carrier, "trigger_event_id": "retry", "trigger_payload": successor},
    )
    event("fanout.aggregate.completed", "ended", fanout_id="wrong", status="failed", failure_kind="infra")
    # The producer admission's signature and retained freeze have independent contract tests.
    monkeypatch.setattr(
        diagnosis_critic_handoff,
        "admitted_unissued_planner",
        lambda *a, **kw: {
            "planner_admitted_event_id": "admitted",
            "planner_result_ref": {"ref": "control.json", "sha256": "digest"},
            "planner_request_hash": "signed",
        },
    )
    service = ControlledActionService(tmp_path, writer, actor="operator", source="operator", surface="cli")
    request = ZfEvent(type="control.action.requested", actor="operator", id="owner", correlation_id="run")
    params = {
        "retry_critic_handoff_event_id": "failure",
        "planner_admitted_event_id": "admitted",
        "planner_control_sha256": "digest",
        "workflow_run_id": "run",
        "candidate_event_id": "freeze",
        "approval_ref": "approval",
    }
    return service, request, params, event


def apply(service, req, params):
    return service.execute(
        action="stage-replan-new-generation",
        requested_action="stage-replan-new-generation",
        requested=req,
        payload=params,
    )


def test_retires_only_empty_successor_and_never_reopens_twice(checkpoint):
    service, req, params, _ = checkpoint
    assert not fanout_current_status(service.writer.event_log.read_all(), "old").current
    result = apply(service, req, params)
    assert result["ok"] and not result["replayed"], result.get("reason")
    again = apply(service, req, params)
    assert again["replayed"] and again["critic_handoff_event_id"] == result["critic_handoff_event_id"]
    assert not apply(service, req, {**params, "approval_ref": "different"})["ok"]
    rows = service.writer.event_log.read_all()
    assert fanout_current_status(rows, "old").current and not fanout_current_status(rows, "wrong").current
    state = FanoutManifestProjector(service.state_dir).rebuild("old", rows)
    assert (
        state["trigger_event_id"] == "trigger"
        and state["critic_handoff_retry_event_id"] == result["critic_handoff_event_id"]
    )
    assert state["aggregate"]["status"] == "started" and not state.get("synth")
    assert state["children"][0]["result_event_id"] == "producer"


@pytest.mark.parametrize(
    "fault",
    [
        "new-wrc",
        "planner-admitted",
        "new-dispatch",
        "foreign-successor",
        "freeze-changed",
        "scope-change",
        "rm",
        "wrong-admission",
        "wrong-digest",
    ],
)
def test_never_revives_an_outdated_or_different_review(checkpoint, fault):
    service, req, params, event = checkpoint
    if fault == "new-wrc":
        # An operation can be real even when it was never dispatched and its event has no inline role/fanout.
        envelope = {"request": {"fanout_id": "wrong", "role_instance": "reviewer-alias"}}
        ref = write_immutable_json_sidecar(
            service.state_dir,
            envelope,
            root="requests",
            kind="request",
            schema_version="request.v1",
            created_by="test",
        )
        event(
            "workflow.operation.requested",
            "issued",
            operation_id="new",
            request_ref=ref,
            request_hash=operation_request_hash(envelope),
        )
    elif fault == "planner-admitted":
        event("workflow.call.result.admitted", "unexpected-admission", operation_id="unknown", role_instance="planner")
    elif fault == "new-dispatch":
        event("fanout.child.dispatched", "dispatched", fanout_id="wrong", child_id="planner")
    elif fault == "foreign-successor":
        event("fanout.started", "foreign", fanout_id="foreign", stage_id="plan")
    elif fault == "freeze-changed":
        event(
            "candidate.ready",
            "other-freeze",
            schema_version="candidate-freeze-receipt.v1",
            candidate_head_commit="other",
        )
    elif fault == "scope-change":
        params["operator_feedback"] = ["change contract"]
    elif fault == "rm":
        service.actor = "run-manager"
    elif fault == "wrong-admission":
        params["planner_admitted_event_id"] = "another"
    else:
        params["planner_control_sha256"] = "another"
    assert not apply(service, req, params)["ok"]
    assert not any(
        e.payload.get("recovery_kind") == "diagnosis_critic_handoff_retry"
        for e in service.writer.event_log.read_all()
    )
