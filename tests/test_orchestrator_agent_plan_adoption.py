from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    WorkflowConfig,
    WorkflowOrchestrationConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.artifact_read_capability import (
    provision_role_artifact_read_credential,
)
from zf.runtime.artifact_read_ledger import read_attempt_artifact
from zf.runtime.call_result_adapters import hydrate_profiled_control_result_event
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.orchestrator_agent_decision_apply import (
    apply_orchestrator_agent_decision,
)
from zf.runtime.orchestrator_agent_operations import (
    activate_orchestrator_agent_operation,
)
from zf.runtime.orchestrator_agent_plan_adoption import (
    plan_candidate_checkpoint_state,
)
from zf.runtime.plan_artifact_package import (
    build_plan_artifact_package,
    package_event_payload,
    write_plan_artifact_package,
)
from zf.runtime.result_submit import (
    SemanticResultSubmitService,
    provision_role_submit_credential,
)
from zf.runtime.run_contract import stable_json_sha256, write_run_contract_snapshot


def _config(policy: str = "blocking") -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="oa-plan", workspace="."),
        workflow=WorkflowConfig(orchestration=WorkflowOrchestrationConfig(
            mode="semantic_control",
            checkpoints=["plan_candidate"],
            checkpoint_policies={"plan_candidate": policy},
            max_plan_revisions=2,
            no_progress_limit=2,
        )),
        roles=[RoleConfig(name="orchestrator", instance_id="orchestrator")],
    )


def _port(state_dir: Path, name: str, revision: str) -> dict:
    descriptor = write_immutable_json_sidecar(
        state_dir,
        {"schema_version": f"{name}.v1", "revision": revision},
        root=f"fixtures/{name}",
        kind=name,
        schema_version=f"{name}.v1",
        created_by="test",
    )
    return {
        "logical_name": name,
        "artifact_kind": name,
        "schema_version": f"{name}.v1",
        "producer_stage_id": "prd-plan",
        "ref": descriptor["ref"],
        "sha256": descriptor["sha256"],
    }


def _package(state_dir: Path, *, revision: str, generation: str):
    contract = {
        "schema_version": "run-contract.v1",
        "workflow": {"kind": "prd"},
    }
    contract["contract_digest"] = stable_json_sha256(contract)
    contract_ref = write_run_contract_snapshot(state_dir, contract)
    ports = [
        _port(state_dir, name, revision)
        for name in (
            "requirement_spec",
            "goal_claim_set",
            "task_map",
            "planning_result",
        )
    ]
    body = build_plan_artifact_package(
        workflow_run_id="run-plan-1",
        flow_kind="prd",
        producer_stage_id="prd-plan",
        run_contract=contract_ref,
        plan_revision=revision,
        task_map_generation=generation,
        produced=ports,
        required_ports=[port["logical_name"] for port in ports],
    )
    return body, write_plan_artifact_package(state_dir, body)


def _runtime(tmp_path: Path, *, policy: str = "blocking"):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    runtime = SimpleNamespace(
        state_dir=state_dir,
        project_root=tmp_path,
        config=_config(policy),
        event_log=log,
        event_writer=EventWriter(log),
    )
    provision_role_submit_credential(state_dir, "orchestrator")
    provision_role_artifact_read_credential(
        state_dir,
        "orchestrator",
        role_name="orchestrator",
        provider="mock",
    )
    return runtime


def _candidate(runtime, *, revision: str = "r1", generation: str = "g1"):
    package, descriptor = _package(
        runtime.state_dir,
        revision=revision,
        generation=generation,
    )
    runtime.event_writer.append(ZfEvent(
        type="plan.artifact_package.admitted",
        actor="zf-cli",
        correlation_id="run-plan-1",
        payload=package_event_payload(package, descriptor, status="admitted"),
    ))
    payload = {
        "workflow_run_id": "run-plan-1",
        "plan_revision": revision,
        "task_map_generation": generation,
        "plan_artifact_package_id": descriptor["package_id"],
        "plan_artifact_package_ref": descriptor["ref"],
        "plan_artifact_package_digest": descriptor["sha256"],
    }
    trigger = ZfEvent(
        id=f"evt-task-map-{revision}",
        type="task_map.ready",
        correlation_id="run-plan-1",
        payload=payload,
    )
    loaded = SimpleNamespace(
        workflow_run_id="run-plan-1",
        task_map_generation=generation,
        task_map_ref=next(
            port["ref"] for port in package["produced"]
            if port["logical_name"] == "task_map"
        ),
        plan_artifact_package_id=descriptor["package_id"],
        plan_artifact_package_ref=descriptor["ref"],
        plan_artifact_package_digest=descriptor["sha256"],
    )
    return trigger, loaded


def _checkpoint(runtime, trigger, loaded):
    state = plan_candidate_checkpoint_state(
        runtime,
        stage_id="impl-writers",
        trigger_event=trigger,
        loaded=loaded,
        trace_id="run-plan-1",
    )
    from zf.runtime.orchestrator_agent_operations import (
        prepared_operation_from_checkpoint_event,
    )

    requested = next(
        event for event in reversed(runtime.event_log.read_all())
        if event.type == "orchestrator.semantic.checkpoint.requested"
    )
    prepared = prepared_operation_from_checkpoint_event(runtime, requested)
    assert prepared is not None
    return state, prepared


def _decision(prepared, *, action: str = "adopt") -> dict:
    identity = prepared.context.input_body["identity"]
    body = {
        "schema_version": "orchestration-decision.v1",
        "execution_status": "completed",
        "identity": {
            "operation_id": prepared.operation_id,
            "workflow_run_id": prepared.workflow_run_id,
            "checkpoint": "plan_candidate",
            "input_digest": prepared.context.input_ref["sha256"],
            "effective_config_digest": prepared.context.effective_config_ref["sha256"],
            "plan_artifact_package_ref": identity["plan_artifact_package_ref"],
            "plan_artifact_package_digest": identity["plan_artifact_package_digest"],
            "task_map_generation": identity["task_map_generation"],
        },
        "decision": action,
        "reason_codes": ["plan_is_complete"],
        "summary": "The current Plan package covers the admitted goal.",
        "affected_work_units": [],
        "required_followup": "continue" if action == "adopt" else "revise plan",
        "expected_outcome": "writer graph is current",
        "confidence": 0.9,
    }
    if action == "revise":
        body["delta"] = {
            "schema_version": "orchestration-delta.v1",
            "identity": {
                "operation_id": prepared.operation_id,
                "workflow_run_id": prepared.workflow_run_id,
                "checkpoint": "plan_candidate",
                "input_digest": prepared.context.input_ref["sha256"],
            },
            "directives": [{
                "directive_id": "dir-revise-plan",
                "action": "revise",
                "basis_refs": [],
            }],
        }
    return body


def _submit(runtime, prepared, *, action: str = "adopt") -> ZfEvent:
    activate_orchestrator_agent_operation(
        runtime,
        prepared,
        dispatch_id="dispatch-plan-review",
        causation_id="evt-task-map-r1",
    )
    for source in prepared.context.source_manifest["sources"]:
        read_attempt_artifact(
            runtime.state_dir,
            manifest=prepared.context.source_manifest,
            source_id=source["source_id"],
            artifact_id=source["artifact_id"],
            actor="orchestrator",
            role="orchestrator",
            provider="mock",
        )
    token = (
        runtime.state_dir
        / "private/result-submit/roles/orchestrator.token"
    ).read_text(encoding="utf-8").strip()
    outcome = SemanticResultSubmitService(
        state_dir=runtime.state_dir,
        event_log=runtime.event_log,
        event_writer=runtime.event_writer,
    ).submit(
        operation_id=prepared.operation_id,
        semantic_result=_decision(prepared, action=action),
        role_instance="orchestrator",
        credential=token,
    )
    event = next(
        item for item in runtime.event_log.read_all()
        if item.id == outcome.canonical_event_id
    )
    return hydrate_profiled_control_result_event(runtime.state_dir, event)


def test_blocking_plan_waits_for_current_adopt_then_resumes(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    trigger, loaded = _candidate(runtime)
    state, prepared = _checkpoint(runtime, trigger, loaded)

    assert state.blocking is True
    assert state.satisfied is False
    outcome = apply_orchestrator_agent_decision(
        runtime,
        _submit(runtime, prepared),
    )
    resumed = plan_candidate_checkpoint_state(
        runtime,
        stage_id="impl-writers",
        trigger_event=trigger,
        loaded=loaded,
        trace_id="run-plan-1",
    )

    assert outcome["applied"] is True
    assert resumed.satisfied is True
    assert any(
        event.type == "plan.approved"
        and event.payload.get("semantic_control") is True
        for event in runtime.event_log.read_all()
    )


def test_equivalent_plan_candidate_event_reuses_applied_operation(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    trigger, loaded = _candidate(runtime)
    _state, prepared = _checkpoint(runtime, trigger, loaded)
    apply_orchestrator_agent_decision(runtime, _submit(runtime, prepared))
    replay = ZfEvent(
        id="evt-task-map-r1-replay",
        type="task_map.ready",
        correlation_id="run-plan-1",
        payload=dict(trigger.payload),
    )

    resumed = plan_candidate_checkpoint_state(
        runtime,
        stage_id="impl-writers",
        trigger_event=replay,
        loaded=loaded,
        trace_id="run-plan-1",
    )

    assert resumed.satisfied is True
    assert resumed.operation_id == prepared.operation_id
    assert not any(
        event.type == "workflow.operation.blocked"
        and event.payload.get("reason") == "request_hash_divergence"
        for event in runtime.event_log.read_all()
    )


def test_equivalent_plan_candidate_event_reuses_pending_operation(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    trigger, loaded = _candidate(runtime)
    first = plan_candidate_checkpoint_state(
        runtime,
        stage_id="impl-writers",
        trigger_event=trigger,
        loaded=loaded,
        trace_id="run-plan-1",
    )
    replay = ZfEvent(
        id="evt-task-map-r1-pending-replay",
        type="task_map.ready",
        correlation_id="run-plan-1",
        payload=dict(trigger.payload),
    )

    second = plan_candidate_checkpoint_state(
        runtime,
        stage_id="impl-writers",
        trigger_event=replay,
        loaded=loaded,
        trace_id="run-plan-1",
    )

    assert second.operation_id == first.operation_id
    assert second.status == "requested"
    assert sum(
        event.type == "workflow.operation.requested"
        and event.payload.get("operation_id") == first.operation_id
        for event in runtime.event_log.read_all()
    ) == 1
    assert not any(
        event.type == "workflow.operation.blocked"
        and event.payload.get("reason") == "request_hash_divergence"
        for event in runtime.event_log.read_all()
    )


def test_same_generation_and_digest_ignore_display_revision_replay(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    trigger, loaded = _candidate(runtime, revision="r1", generation="g1")
    first = plan_candidate_checkpoint_state(
        runtime,
        stage_id="impl-writers",
        trigger_event=trigger,
        loaded=loaded,
        trace_id="run-plan-1",
    )
    replay_payload = {**trigger.payload, "plan_revision": "display-r2"}
    replay = ZfEvent(
        id="evt-task-map-display-r2",
        type="task_map.ready",
        correlation_id="run-plan-1",
        payload=replay_payload,
    )

    second = plan_candidate_checkpoint_state(
        runtime,
        stage_id="impl-writers",
        trigger_event=replay,
        loaded=loaded,
        trace_id="run-plan-1",
    )

    assert second.operation_id == first.operation_id
    assert sum(
        event.type == "workflow.operation.requested"
        for event in runtime.event_log.read_all()
    ) == 1


def test_plan_candidate_digest_change_mints_new_operation_identity(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    trigger, loaded = _candidate(runtime, revision="r1", generation="g1")
    first, _prepared = _checkpoint(runtime, trigger, loaded)
    changed_trigger, changed_loaded = _candidate(
        runtime,
        revision="r1",
        generation="g2",
    )

    second = plan_candidate_checkpoint_state(
        runtime,
        stage_id="impl-writers",
        trigger_event=changed_trigger,
        loaded=changed_loaded,
        trace_id="run-plan-1",
    )

    assert second.operation_id != first.operation_id
    assert second.status == "requested"
    from zf.runtime.workflow_operation import load_workflow_operation

    previous = load_workflow_operation(runtime.event_log, first.operation_id)
    assert previous is not None
    assert previous["status"] == "superseded"
    assert second.operation_id in previous["reason"]
    assert not any(
        event.type == "workflow.operation.blocked"
        and event.payload.get("reason") == "request_hash_divergence"
        for event in runtime.event_log.read_all()
    )


def test_shadow_decision_never_applies_plan_state(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, policy="shadow")
    trigger, loaded = _candidate(runtime)
    state, prepared = _checkpoint(runtime, trigger, loaded)

    outcome = apply_orchestrator_agent_decision(
        runtime,
        _submit(runtime, prepared),
    )

    assert state.blocking is False
    required_ids = {
        row["source_id"]
        for row in prepared.context.read_policy["required_reads"]
    }
    assert {
        "checkpoint-pack",
        "effective-config-summary",
        "plan-artifact-package",
        "run-contract",
        "plan-port-requirement_spec",
        "plan-port-goal_claim_set",
        "plan-port-task_map",
        "plan-port-planning_result",
    } <= required_ids
    assert "effective-config" not in required_ids
    assert "checkpoint-input" not in required_ids
    assert outcome["status"] == "shadowed"
    assert not any(
        event.type in {"plan.approved", "plan.rejected"}
        for event in runtime.event_log.read_all()
    )


def test_revise_returns_to_plan_without_modifying_task_map(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    trigger, loaded = _candidate(runtime)
    _state, prepared = _checkpoint(runtime, trigger, loaded)

    outcome = apply_orchestrator_agent_decision(
        runtime,
        _submit(runtime, prepared, action="revise"),
    )

    assert outcome["decision"] == "revise"
    rejected = [
        event for event in runtime.event_log.read_all()
        if event.type == "plan.rejected"
    ]
    assert len(rejected) == 1
    assert rejected[0].payload["plan_id"] == trigger.id
    assert not any(event.type == "task_map.ready" for event in runtime.event_log.read_all())


def test_stale_plan_package_decision_is_rejected(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    trigger, loaded = _candidate(runtime)
    _state, prepared = _checkpoint(runtime, trigger, loaded)
    decision_event = _submit(runtime, prepared)
    _candidate(runtime, revision="r2", generation="g2")

    outcome = apply_orchestrator_agent_decision(runtime, decision_event)

    assert outcome["status"] == "rejected"
    assert outcome["reason"] == "plan_package_stale"
    assert not any(event.type == "plan.approved" for event in runtime.event_log.read_all())


def test_repeated_plan_revision_opens_bounded_breaker(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    outcomes = []

    for index in range(1, 4):
        trigger, loaded = _candidate(
            runtime,
            revision=f"r{index}",
            generation=f"g{index}",
        )
        _state, prepared = _checkpoint(runtime, trigger, loaded)
        outcomes.append(apply_orchestrator_agent_decision(
            runtime,
            _submit(runtime, prepared, action="revise"),
        ))

    assert [outcome["status"] for outcome in outcomes] == [
        "applied",
        "applied",
        "escalated",
    ]
    assert len([
        event for event in runtime.event_log.read_all()
        if event.type == "plan.rejected"
    ]) == 2
    assert any(
        event.type == "human.escalate"
        and "breaker" in str(event.payload.get("reason"))
        for event in runtime.event_log.read_all()
    )


@pytest.mark.parametrize("action", ["adopt", "revise"])
def test_owner_rejection_invalidates_late_plan_review(tmp_path: Path, action: str) -> None:
    runtime = _runtime(tmp_path)
    trigger, loaded = _candidate(runtime)
    _state, prepared = _checkpoint(runtime, trigger, loaded)
    decision_event = _submit(runtime, prepared, action=action)
    runtime.event_writer.append(ZfEvent(
        type="plan.rejected", actor="operator", origin="kernel",
        correlation_id="run-plan-1", payload={"plan_id": trigger.id, "reason": "new owner scope"},
    ))
    before = len(runtime.event_log.read_all())

    outcome = apply_orchestrator_agent_decision(runtime, decision_event)

    assert outcome["status"] == "rejected"
    assert outcome["reason"] == "plan_candidate_rejected"
    assert not any(event.type in {"plan.approved", "plan.rejected", "human.escalate"}
                   for event in runtime.event_log.read_all()[before:])


@pytest.mark.parametrize("plan_id", ["other-candidate", ""])
def test_unrelated_rejection_does_not_invalidate_plan_review(tmp_path: Path, plan_id: str) -> None:
    runtime = _runtime(tmp_path)
    trigger, loaded = _candidate(runtime)
    _state, prepared = _checkpoint(runtime, trigger, loaded)
    decision_event = _submit(runtime, prepared)
    runtime.event_writer.append(ZfEvent(
        type="plan.rejected", actor="operator", origin="kernel",
        correlation_id="run-plan-1", payload={"plan_id": plan_id},
    ))

    assert apply_orchestrator_agent_decision(runtime, decision_event)["status"] == "applied"
