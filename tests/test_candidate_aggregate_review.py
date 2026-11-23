"""First mixed Candidate failure reaches aggregate OA review through real RM."""
from dataclasses import replace

import pytest

from tests.test_candidate_plan_recovery import _incident
from tests.test_candidate_semantic_handoff import _peer_failure
from zf.core.events import ZfEvent
from zf.runtime.orchestrator_agent_semantic_failure import request_semantic_failure_checkpoint
from zf.runtime.run_manager import run_manager_tick, build_run_manager_projection, _action_payload


def _mixed(tmp_path, width=2):
    runtime, original, _, child = _incident(tmp_path, expected_children=[
        {"child_id": role, "role_instance": role} for role in ("verify-0", "verify-peer")])
    original, peer_ref = _peer_failure(runtime, original, child)
    if width > 2:
        from zf.runtime.call_result_envelope import write_immutable_json_sidecar
        from zf.runtime.sidecar_refs import hydrate_sidecar_ref
        base = hydrate_sidecar_ref(runtime.state_dir, peer_ref).payload
        for i in range(2, width):
            role = f"verify-peer-{i}"
            body = {**base, "role_instance": role, "operation_id": f"peer-operation-{i}",
                    "attempt_id": f"peer-attempt-{i}", "active_attempt_id": f"peer-attempt-{i}"}
            ref = write_immutable_json_sidecar(runtime.state_dir, body, root="fixtures/peer",
                kind="call_control_result", schema_version="verification-result.v1", created_by="test")
            op = {**child, **body, "child_id": role, "control_result_ref": ref, "verification_result": body}
            for kind in ("workflow.operation.requested", "workflow.call.result.admitted",
                         "workflow.operation.settled", "fanout.child.failed"):
                runtime.event_writer.append(ZfEvent(type=kind, origin="kernel", task_id="TASK-1",
                    correlation_id="run-semantic-1", payload={**op, "semantic_verdict": body["verdict"]}))
        roles = ["verify-0", "verify-peer", *[f"verify-peer-{i}" for i in range(2, width)]]
        aggregate = runtime.event_writer.append(ZfEvent(type="fanout.aggregate.completed", origin="kernel",
            correlation_id="run-semantic-1", payload={**child, "status": "failed", "failed_children": roles}))
        runtime.event_writer.append(ZfEvent(type="test.failed", origin="kernel", causation_id=aggregate.id,
            correlation_id="run-semantic-1", payload={"workflow_run_id": "run-semantic-1", "pdd_id": "TASK-1",
                "fanout_id": "verify-fanout", "task_map_generation": "g1"}))
    events = runtime.event_log.read_all()
    if width > 2:
        events = [replace(e, payload={**e.payload, "expected_children": [
            {"child_id": r, "role_instance": r} for r in roles]}) if e.type == "fanout.started" else e for e in events]
    failure = events[-1]
    # Production aggregate failures carry the anchor in payload, not event.task_id.
    failure = replace(failure, task_id=None, payload={**failure.payload, "failure_kind": "mixed"})
    # Isolated fixture log rewrite, never a live-state operation.
    runtime.event_log.path.write_text("")
    for e in [*events[:-1], failure]:
        runtime.event_writer.append(e)
    runtime.event_writer.append(ZfEvent(type="run.goal.started", origin="kernel",
        payload={"run_id": "run-semantic-1"}))
    return runtime, failure, peer_ref


@pytest.mark.parametrize("width", [2, 8])
def test_first_mixed_aggregate_rm_tick_requests_once_then_binds_every_member(tmp_path, width):
    runtime, failure, peer_ref = _mixed(tmp_path, width)
    projection = build_run_manager_projection(runtime.state_dir, config=runtime.config,
                                              project_root=runtime.project_root)
    actions = [a for a in projection["pending_actions"] if a.get("source_event_id") == failure.id]
    assert len(actions) == 1
    assert actions[0]["action"] == "orchestrator-rework-triage"
    assert actions[0]["failure_count"] == 1
    assert actions[0]["preflight"]["status"] == "passed"
    persisted = _action_payload(actions[0])
    assert persisted["recovery_scope"] == "candidate"
    for index in range(2):
        tick = run_manager_tick(state_dir=runtime.state_dir, writer=runtime.event_writer, config=runtime.config,
            project_root=runtime.project_root, event_log=runtime.event_log, spawn_repairs=False)
        if index == 0:
            assert tick.actions_applied == 1
            assert not any(e.type == "workflow.resume.control_action.result" for e in runtime.event_log.read_all())
    requests = [e for e in runtime.event_log.read_all() if e.type == "orchestrator.semantic.failure.requested"]
    assert len(requests) == 1
    assert requests[0].payload["recovery_scope"] == "candidate"
    assert not requests[0].payload.get("target_operation_id")
    prepared = request_semantic_failure_checkpoint(runtime, requests[0])
    assert prepared.context.input_body["checkpoint_context"]["candidate_failure_scope"] == "aggregate"
    sources = prepared.context.input_body["sources"]
    assert {s["source_id"] for s in sources} >= {"candidate-verification-verify-0", "candidate-verification-verify-peer"}
    assert any(s["sha256"] == peer_ref["sha256"] for s in sources)
    assert len([s for s in sources if s["source_id"].startswith("candidate-verification-")]) == width


@pytest.mark.parametrize("fault", ["disabled", "wrong_run", "tampered_member"])
def test_invalid_candidate_binding_never_proposes_semantic_review(tmp_path, fault):
    from zf.runtime.candidate_aggregate_review import pending_candidate_reviews
    runtime, failure, peer_ref = _mixed(tmp_path)
    events = runtime.event_log.read_all()
    if fault == "disabled":
        runtime.config.workflow.orchestration.checkpoints = []
    elif fault == "wrong_run":
        events = [replace(e, payload={**e.payload, "workflow_run_id": "other"}) if e.id == failure.id else e for e in events]
    else:
        (runtime.state_dir / peer_ref["ref"]).write_text("{}")
    actions = pending_candidate_reviews(state_dir=runtime.state_dir, config=runtime.config, events=events)
    if fault == "tampered_member":
        assert len(actions) == 1 and actions[0]["action"] == "diagnose-attention"
        from zf.runtime.candidate_aggregate_review import candidate_review_binding
        assert candidate_review_binding(runtime.state_dir, runtime.config, events, failure)["binding_error"]
    else:
        assert not actions


def test_requested_but_not_applied_is_not_duplicated_and_rejection_has_diagnosis(tmp_path):
    from zf.runtime.candidate_aggregate_review import pending_candidate_reviews
    runtime, failure, _ = _mixed(tmp_path)
    action = pending_candidate_reviews(state_dir=runtime.state_dir, config=runtime.config,
                                       events=runtime.event_log.read_all())[0]
    # Death after request publication and before RM action.applied.
    request = runtime.event_writer.append(ZfEvent(type="orchestrator.semantic.failure.requested",
        origin="kernel", task_id="TASK-1", payload=action))
    assert not pending_candidate_reviews(state_dir=runtime.state_dir, config=runtime.config,
                                         events=runtime.event_log.read_all())
    rejection = runtime.event_writer.append(ZfEvent(type="orchestrator.semantic.checkpoint.rejected",
        origin="kernel", causation_id=request.id, payload={"source_event_id": request.id, "reason": "missing_binding"}))
    actions = pending_candidate_reviews(state_dir=runtime.state_dir, config=runtime.config,
                                         events=runtime.event_log.read_all())
    assert len(actions) == 1
    assert actions[0]["action"] == "diagnose-attention"
    assert actions[0]["source_event_ids"] == [rejection.id]
    assert actions[0]["failure_event_ids"] == [failure.id]


def test_rejected_goal_binding_allows_one_fresh_candidate_review(tmp_path):
    from zf.runtime.candidate_aggregate_review import pending_candidate_reviews
    runtime, _, _ = _mixed(tmp_path)
    run_manager_tick(state_dir=runtime.state_dir, writer=runtime.event_writer, config=runtime.config,
        project_root=runtime.project_root, event_log=runtime.event_log, spawn_repairs=False)
    request = next(e for e in runtime.event_log.read_all() if e.type == "orchestrator.semantic.failure.requested")
    runtime.event_writer.append(ZfEvent(type="orchestrator.semantic.checkpoint.requested", origin="kernel",
        causation_id=request.id, payload={"operation_id": "old-review"}))
    runtime.event_writer.append(ZfEvent(type="run.goal.updated", origin="kernel",
        payload={"run_id": "run-semantic-1", "objective": "clarified objective"}))
    runtime.event_writer.append(ZfEvent(type="orchestrator.semantic.decision.rejected", origin="kernel",
        payload={"operation_id": "old-review", "reason": "execution_goal_changed"}))
    actions = pending_candidate_reviews(state_dir=runtime.state_dir, config=runtime.config,
                                        events=runtime.event_log.read_all())
    assert len(actions) == 1 and actions[0]["request_id"] != request.payload["request_id"]
