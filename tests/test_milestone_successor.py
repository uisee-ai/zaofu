import pytest
from types import SimpleNamespace
from zf.core.events import ZfEvent
from zf.runtime.call_result_runtime import prepare_call_operation
from zf.runtime.orchestrator_agent_aggregation import stage_barrier_checkpoint_state
from zf.runtime.orchestrator_agent_operations import (
    prepared_operation_from_checkpoint_event,
)
from zf.runtime.orchestrator_agent_transport import (
    dispatch_orchestrator_agent_operation,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from tests.test_candidate_verification_authority import _seed_frozen_candidate
from tests.test_orchestrator_agent_aggregation import (
    _runtime,
    _seed_run,
    _result,
    _apply,
)
from zf.runtime.artifact_read_ledger import read_attempt_artifact


@pytest.mark.parametrize("replace", [False, True])
def test_fresh_oa_gets_frozen_evidence_and_old_target_cannot_advance(
    tmp_path, monkeypatch, replace
):
    runtime, state = _runtime(tmp_path, checkpoint="stage_barrier")
    runtime.config.workflow.flow_metadata["milestone_handoff"] = True
    package = _seed_run(runtime)
    candidate = _seed_frozen_candidate(
        runtime,
        run_id=package["workflow_run_id"],
        generation=package["task_map_generation"],
    )
    payload = {
        "workflow_run_id": package["workflow_run_id"],
        "task_id": "FLOW-ANCHOR",
        "fanout_id": "fanout-candidate-verify",
        "child_id": "verify-lane-0",
        "role_instance": "verify-lane-0",
        "stage_id": "issue-lanes-verify",
        "output_profile_id": "candidate-verify",
        "artifact_package_mode": "shadow",
        "target_commit": candidate["candidate_head"],
        "canonical_success_event": "test.passed",
        "canonical_failure_event": "test.failed",
    }
    prepare_call_operation(
        runtime,
        payload=payload,
        operation_type="fanout_reader_child",
        operation_key="verify-lane-0",
        stage_id="issue-lanes-verify",
        task_id="FLOW-ANCHOR",
        dispatch_id="dispatch-candidate-verify",
        causation_id="evt-candidate-ready",
    )
    result = _result(runtime)
    edge = runtime.event_writer.append(
        ZfEvent(
            type="fanout.aggregate.completed",
            origin="kernel",
            correlation_id=package["workflow_run_id"],
            payload={**package, "status": "completed", "result_refs": [result]},
        )
    )
    stage_barrier_checkpoint_state(runtime, edge)
    event = next(
        e
        for e in runtime.event_log.read_all()
        if e.type == "orchestrator.semantic.checkpoint.requested"
    )
    prepared = prepared_operation_from_checkpoint_event(runtime, event)
    context = prepared.context
    assert "milestone-successor" in {
        r["source_id"] for r in context.read_policy["required_reads"]
    }
    ref = context.input_body["milestone_successor_ref"]
    body = hydrate_sidecar_ref(state, ref).payload
    assert body["target_commit"] == candidate["candidate_head"]
    assert (
        body["reviews"][0]["readiness"]["verdict"] == "pending"
        and not body["authorizes_execution"]
    )
    starts = []
    sent = []

    def reboot(role, **kwargs):
        assert kwargs["force_fresh_session"] and not kwargs["inject_idle_prompt"]
        starts.append(role.instance_id)
        return SimpleNamespace(action="respawn")

    monkeypatch.setattr(runtime, "_respawn_instance", reboot)
    monkeypatch.setattr(
        runtime, "_send_transport_task", lambda *args: sent.append(args)
    )
    for _ in range(2):
        dispatch_orchestrator_agent_operation(runtime, runtime.config.roles[0], event)
    assert starts == ["orchestrator"] and len(sent) == 1
    for source in context.source_manifest["sources"]:
        read_attempt_artifact(
            state,
            manifest=context.source_manifest,
            source_id=source["source_id"],
            artifact_id=source["artifact_id"],
            actor="orchestrator",
            role="orchestrator",
            provider="mock",
        )
    if replace:
        old = next(
            e for e in runtime.event_log.read_all() if e.type == "candidate.ready"
        )
        runtime.event_writer.append(
            ZfEvent(
                type=old.type,
                origin="kernel",
                payload={**old.payload, "target_commit": "d" * 40},
            )
        )
    outcome = _apply(runtime, prepared, recommendation="aggregate")
    if replace:
        assert not outcome.get("applied")
    else:
        assert outcome["status"] == "applied"
        assert stage_barrier_checkpoint_state(runtime, edge).satisfied
