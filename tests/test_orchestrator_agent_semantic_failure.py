from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    SessionConfig,
    WorkflowConfig,
    WorkflowAdmissionReplanConfig,
    WorkflowOrchestrationConfig,
    WorkflowOrchestrationFlowPolicyConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.artifact_read_capability import (
    provision_role_artifact_read_credential,
)
from zf.runtime.artifact_read_ledger import read_attempt_artifact
from zf.runtime.call_result_adapters import hydrate_profiled_control_result_event
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.orchestrator_agent_decision_apply import (
    apply_orchestrator_agent_decision,
)
from zf.runtime.orchestrator_agent_operations import (
    activate_orchestrator_agent_operation,
)
from zf.runtime.orchestrator_agent_semantic_failure import (
    SEMANTIC_FAILURE_REQUESTED,
    SemanticFailureCheckpointError,
    request_semantic_failure_checkpoint,
    semantic_failure_request_type,
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
from zf.runtime.rework_feedback import (
    descriptor_from_payload,
    hydrate_rework_feedback,
)
from zf.runtime.run_contract import stable_json_sha256, write_run_contract_snapshot
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


def _config(*, enabled: bool = True) -> ZfConfig:
    policy = WorkflowOrchestrationConfig()
    if enabled:
        policy = WorkflowOrchestrationConfig(
            mode="semantic_control",
            checkpoints=["semantic_failure"],
            checkpoint_policies={"semantic_failure": "blocking"},
        )
    return ZfConfig(
        project=ProjectConfig(name="oa-semantic", workspace="."),
        session=SessionConfig(tmux_session="oa-semantic-test"),
        workflow=WorkflowConfig(
            orchestration=policy,
            admission_replan=WorkflowAdmissionReplanConfig(
                enabled=True,
                resynth_trigger="prd.plan.requested",
            ),
        ),
        roles=[
            RoleConfig(
                name="orchestrator",
                instance_id="orchestrator",
                role_kind="reader",
                backend="mock",
            ),
            RoleConfig(
                name="dev",
                instance_id="dev-a",
                role_kind="writer",
                backend="mock",
                publishes=["dev.build.done", "dev.blocked"],
            ),
            RoleConfig(
                name="dev",
                instance_id="dev-b",
                role_kind="writer",
                backend="mock",
                publishes=["dev.build.done", "dev.blocked"],
            ),
        ],
    )


def _port(state_dir: Path, name: str) -> dict:
    descriptor = write_immutable_json_sidecar(
        state_dir,
        {"schema_version": f"{name}.v1", "task_ids": ["TASK-1", "TASK-2"]},
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


def _admit_package(runtime) -> None:
    contract = {
        "schema_version": "run-contract.v1",
        "workflow": {"kind": "prd"},
    }
    contract["contract_digest"] = stable_json_sha256(contract)
    contract_ref = write_run_contract_snapshot(runtime.state_dir, contract)
    ports = [
        _port(runtime.state_dir, name)
        for name in ("goal_claim_set", "task_map", "planning_result")
    ]
    package = build_plan_artifact_package(
        workflow_run_id="run-semantic-1",
        flow_kind="prd",
        producer_stage_id="prd-plan",
        run_contract=contract_ref,
        plan_revision="r1",
        task_map_generation="g1",
        produced=ports,
        required_ports=[port["logical_name"] for port in ports],
    )
    descriptor = write_plan_artifact_package(runtime.state_dir, package)
    runtime.event_writer.append(ZfEvent(
        type="plan.artifact_package.admitted",
        actor="zf-cli",
        correlation_id="run-semantic-1",
        payload=package_event_payload(package, descriptor, status="admitted"),
    ))


def _runtime(tmp_path: Path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "memory").mkdir()
    (state_dir / "logs").mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="TASK-1",
        title="repair target",
        status="in_progress",
        assigned_to="dev-a",
        active_dispatch_id="lease-task-1",
        contract=TaskContract(
            feature_id="PRD-1",
            phase="impl",
            owner_role="dev",
            owner_instance="dev-a",
            scope=["src/a.py"],
        ),
    ))
    store.add(Task(
        id="TASK-2",
        title="unaffected lane",
        status="in_progress",
        assigned_to="dev-b",
        active_dispatch_id="lease-task-2",
        contract=TaskContract(
            feature_id="PRD-1",
            phase="impl",
            owner_role="dev",
            owner_instance="dev-b",
            scope=["src/b.py"],
        ),
    ))
    runtime = SimpleNamespace(
        state_dir=state_dir,
        project_root=tmp_path,
        config=_config(),
        event_log=log,
        event_writer=EventWriter(log),
        task_store=store,
    )
    provision_role_submit_credential(state_dir, "orchestrator")
    provision_role_artifact_read_credential(
        state_dir,
        "orchestrator",
        role_name="orchestrator",
        provider="mock",
    )
    _admit_package(runtime)
    return runtime


def _semantic_request(runtime) -> ZfEvent:
    failure_result = write_immutable_json_sidecar(
        runtime.state_dir,
        {"schema_version": "verification-result.v1", "status": "failed"},
        root="fixtures/results",
        kind="verification_result",
        schema_version="verification-result.v1",
        created_by="test",
    )
    failure = runtime.event_writer.append(ZfEvent(
        id="evt-semantic-failure",
        type="verify.failed",
        actor="verify-a",
        task_id="TASK-1",
        correlation_id="run-semantic-1",
        payload={
            "dispatch_id": "lease-task-1",
            "stage_id": "verify",
            "failure_fingerprint": "coverage-conflict-1",
            "result_refs": [failure_result],
            "reason": "coverage and ownership conflict",
        },
    ))
    recovery = write_immutable_json_sidecar(
        runtime.state_dir,
        {
            "schema_version": "task-recovery-context.v1",
            "task_id": "TASK-1",
            "failure_event_ids": [failure.id],
        },
        root="fixtures/recovery",
        kind="recovery_context",
        schema_version="task-recovery-context.v1",
        created_by="test",
    )
    return runtime.event_writer.append(ZfEvent(
        id="evt-semantic-request",
        type=SEMANTIC_FAILURE_REQUESTED,
        actor="run-manager",
        task_id="TASK-1",
        correlation_id="run-semantic-1",
        payload={
            "problem_class": "semantic",
            "workflow_run_id": "run-semantic-1",
            "failure_fingerprint": "coverage-conflict-1",
            "failure_event_ids": [failure.id],
            "trigger_event_type": "verify.failed",
            "recovery_context_ref": recovery,
        },
    ))


def _submit_rework(
    runtime,
    prepared,
    *,
    action: str = "rework",
    outside_ref: bool = False,
) -> ZfEvent:
    activate_orchestrator_agent_operation(
        runtime,
        prepared,
        dispatch_id="dispatch-oa-semantic",
        causation_id="evt-semantic-request",
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
    identity = prepared.context.input_body["identity"]
    target = prepared.context.input_body["checkpoint_context"]
    basis = prepared.context.source_manifest["sources"][0]
    decision = {
        "schema_version": "orchestration-decision.v1",
        "execution_status": "completed",
        "identity": {
            "operation_id": prepared.operation_id,
            "workflow_run_id": prepared.workflow_run_id,
            "checkpoint": "semantic_failure",
            "input_digest": prepared.context.input_ref["sha256"],
            "effective_config_digest": prepared.context.effective_config_ref["sha256"],
            "plan_artifact_package_ref": identity["plan_artifact_package_ref"],
            "plan_artifact_package_digest": identity["plan_artifact_package_digest"],
            "task_map_generation": identity["task_map_generation"],
        },
        "decision": action,
        "reason_codes": ["coverage_ownership_conflict"],
        "affected_work_units": ["TASK-1"],
        "required_followup": "repair the exact ownership gap",
        "expected_outcome": "TASK-1 closes its coverage gap",
        "confidence": 0.9,
        "delta": {
            "schema_version": "orchestration-delta.v1",
            "identity": {
                "operation_id": prepared.operation_id,
                "workflow_run_id": prepared.workflow_run_id,
                "checkpoint": "semantic_failure",
                "input_digest": prepared.context.input_ref["sha256"],
            },
            "directives": [{
                "directive_id": "directive-task-1",
                "action": action,
                "target": {
                    "task_id": target["target_task_id"],
                    "stage_id": target["target_stage_id"],
                    "attempt_id": target["target_attempt_id"],
                    "role_instance": target["target_role_instance"],
                },
                "basis_refs": [{
                    "ref": (
                        "artifacts/outside-operation.json"
                        if outside_ref else basis["ref"]
                    ),
                    "sha256": ("f" * 64 if outside_ref else basis["sha256"]),
                }],
                "required_actions": ["resolve the coverage ownership conflict"],
                "reuse_refs": (
                    [{"ref": basis["ref"], "sha256": basis["sha256"]}]
                    if action in {"rework", "rebind", "return_to_plan"}
                    else []
                ),
                "invalidate_refs": (
                    [{"ref": basis["ref"], "sha256": basis["sha256"]}]
                    if action == "invalidate" else []
                ),
            }],
        },
    }
    token = (
        runtime.state_dir / "private/result-submit/roles/orchestrator.token"
    ).read_text(encoding="utf-8").strip()
    submitted = SemanticResultSubmitService(
        state_dir=runtime.state_dir,
        event_log=runtime.event_log,
        event_writer=runtime.event_writer,
    ).submit(
        operation_id=prepared.operation_id,
        semantic_result=decision,
        role_instance="orchestrator",
        credential=token,
    )
    event = next(
        item for item in runtime.event_log.read_all()
        if item.id == submitted.canonical_event_id
    )
    return hydrate_profiled_control_result_event(runtime.state_dir, event)


def test_semantic_checkpoint_is_explicit_and_requires_exact_sources(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    request = _semantic_request(runtime)
    prepared = request_semantic_failure_checkpoint(runtime, request)

    source_ids = {
        source["source_id"] for source in prepared.context.source_manifest["sources"]
    }
    assert "semantic-failure-input" in source_ids
    assert "task-recovery-context" in source_ids
    assert "plan-artifact-package" in source_ids
    assert prepared.context.input_body["checkpoint_context"] == {
        "failure_fingerprint": "coverage-conflict-1",
        "target_task_id": "TASK-1",
        "target_stage_id": "impl",
        "target_attempt_id": "lease-task-1",
        "target_role_instance": "dev-a",
    }
    with pytest.raises(SemanticFailureCheckpointError, match="not_semantic"):
        request_semantic_failure_checkpoint(runtime, ZfEvent(
            type="verify.failed",
            task_id="TASK-1",
        ))


def test_admitted_directive_dispatches_only_exact_target_with_bound_feedback(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    prepared = request_semantic_failure_checkpoint(runtime, _semantic_request(runtime))
    outcome = apply_orchestrator_agent_decision(
        runtime,
        _submit_rework(runtime, prepared),
    )
    rework_event = next(
        event for event in runtime.event_log.read_all()
        if event.type == "orchestrator.semantic.rework.requested"
    )

    assert outcome["applied"] is True
    assert rework_event.task_id == "TASK-1"
    assert rework_event.payload["target_role_instance"] == "dev-a"
    orchestrator = Orchestrator(
        runtime.state_dir,
        runtime.config,
        TmuxTransport(TmuxSession(session_name="oa-semantic-test", dry_run=True)),
        project_root=tmp_path,
    )
    dispatched = orchestrator._dispatch_rework(
        orchestrator.task_store.get("TASK-1"),
        rework_event,
    )

    assert dispatched == "dev"
    requests = [
        event for event in runtime.event_log.read_all()
        if event.type == "task.rework.requested"
    ]
    assert len(requests) == 1
    assert requests[0].task_id == "TASK-1"
    assert requests[0].payload["assignee"] == "dev-a"
    assert not any(event.task_id == "TASK-2" for event in requests)
    feedback = hydrate_rework_feedback(
        runtime.state_dir,
        descriptor_from_payload(requests[0].payload),
        expected_task_id="TASK-1",
    )
    assert feedback["semantic_target"]["role_instance"] == "dev-a"
    assert feedback["orchestrator_decision_ref"]
    assert feedback["orchestration_delta_ref"]
    assert feedback["reuse_refs"]
    assert feedback["invalidate_refs"] == []
    unaffected = orchestrator.task_store.get("TASK-2")
    assert unaffected.active_dispatch_id == "lease-task-2"
    assert unaffected.status == "in_progress"


def test_stale_target_attempt_fails_closed_before_dispatch(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    prepared = request_semantic_failure_checkpoint(runtime, _semantic_request(runtime))
    apply_orchestrator_agent_decision(runtime, _submit_rework(runtime, prepared))
    rework_event = next(
        event for event in runtime.event_log.read_all()
        if event.type == "orchestrator.semantic.rework.requested"
    )
    runtime.task_store.update("TASK-1", active_dispatch_id="lease-new")
    orchestrator = Orchestrator(
        runtime.state_dir,
        runtime.config,
        TmuxTransport(TmuxSession(session_name="oa-semantic-test", dry_run=True)),
        project_root=tmp_path,
    )

    assert orchestrator._dispatch_rework(
        orchestrator.task_store.get("TASK-1"),
        rework_event,
    ) is None
    assert any(
        event.type == "orchestrator.semantic.rework.rejected"
        and event.payload.get("reason") == "target_attempt_stale"
        for event in runtime.event_log.read_all()
    )
    assert not any(
        event.type == "task.rework.requested"
        for event in runtime.event_log.read_all()
    )


def test_run_manager_switches_request_type_only_for_semantic_pilot() -> None:
    assert semantic_failure_request_type(_config()) == SEMANTIC_FAILURE_REQUESTED
    assert semantic_failure_request_type(_config(enabled=False)) == (
        "orchestrator.rework.triage.requested"
    )

    scoped = _config(enabled=False)
    scoped.workflow.orchestration.flow_policies["prd"] = (
        WorkflowOrchestrationFlowPolicyConfig(
            mode="semantic_control",
            checkpoints=["semantic_failure"],
            checkpoint_policies={"semantic_failure": "blocking"},
        )
    )
    assert semantic_failure_request_type(
        scoped,
        flow_kind="prd",
    ) == SEMANTIC_FAILURE_REQUESTED
    assert semantic_failure_request_type(
        scoped,
        flow_kind="issue",
    ) == "orchestrator.rework.triage.requested"


@pytest.mark.parametrize("action", ["rebind", "invalidate"])
def test_targeted_graph_delta_reuses_existing_rework_feedback_path(
    tmp_path: Path,
    action: str,
) -> None:
    runtime = _runtime(tmp_path)
    prepared = request_semantic_failure_checkpoint(runtime, _semantic_request(runtime))
    outcome = apply_orchestrator_agent_decision(
        runtime,
        _submit_rework(runtime, prepared, action=action),
    )
    request = next(
        event for event in runtime.event_log.read_all()
        if event.type == "orchestrator.semantic.rework.requested"
    )

    assert outcome["applied"] is True
    assert request.task_id == "TASK-1"
    assert request.payload["semantic_action"] == action
    orchestrator = Orchestrator(
        runtime.state_dir,
        runtime.config,
        TmuxTransport(TmuxSession(session_name="oa-semantic-test", dry_run=True)),
        project_root=tmp_path,
    )
    assert orchestrator._dispatch_rework(
        orchestrator.task_store.get("TASK-1"),
        request,
    ) == "dev"
    task_requests = [
        event for event in runtime.event_log.read_all()
        if event.type == "task.rework.requested"
    ]
    assert [event.task_id for event in task_requests] == ["TASK-1"]
    feedback = hydrate_rework_feedback(
        runtime.state_dir,
        descriptor_from_payload(task_requests[0].payload),
        expected_task_id="TASK-1",
    )
    assert feedback["semantic_action"] == action
    assert bool(feedback["reuse_refs"]) is (action == "rebind")
    assert bool(feedback["invalidate_refs"]) is (action == "invalidate")
    assert orchestrator.task_store.get("TASK-2").active_dispatch_id == (
        "lease-task-2"
    )


def test_return_to_plan_emits_one_marker_and_existing_resynth_trigger(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    prepared = request_semantic_failure_checkpoint(runtime, _semantic_request(runtime))

    outcome = apply_orchestrator_agent_decision(
        runtime,
        _submit_rework(runtime, prepared, action="return_to_plan"),
    )

    events = runtime.event_log.read_all()
    markers = [
        event for event in events
        if event.type == "orchestrator.replan_requested"
    ]
    resynth = [event for event in events if event.type == "prd.plan.requested"]
    assert outcome["applied"] is True
    assert len(markers) == 1
    assert len(resynth) == 1
    assert markers[0].task_id == "TASK-1"
    assert markers[0].payload["task_ids"] == ["TASK-1"]
    assert markers[0].payload["orchestration_delta_ref"]
    assert resynth[0].payload["rework_of"] == markers[0].payload["rework_of"]
    assert not any(
        event.type == "orchestrator.semantic.rework.requested"
        for event in events
    )
    assert not any(event.type == "task_map.ready" for event in events)


def test_graph_delta_rejects_ref_outside_operation_manifest(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    prepared = request_semantic_failure_checkpoint(runtime, _semantic_request(runtime))

    outcome = apply_orchestrator_agent_decision(
        runtime,
        _submit_rework(runtime, prepared, outside_ref=True),
    )

    assert outcome["status"] == "rejected"
    assert "directive_ref_outside_operation" in outcome["reason"]
    assert not any(
        event.type in {
            "orchestrator.semantic.rework.requested",
            "orchestrator.replan_requested",
        }
        for event in runtime.event_log.read_all()
    )


def test_graph_delta_rejects_target_that_became_stale_before_apply(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    prepared = request_semantic_failure_checkpoint(runtime, _semantic_request(runtime))
    decision_event = _submit_rework(runtime, prepared, action="rebind")
    runtime.task_store.update("TASK-1", active_dispatch_id="lease-new")

    outcome = apply_orchestrator_agent_decision(runtime, decision_event)

    assert outcome["status"] == "rejected"
    assert outcome["reason"] == "target_attempt_stale"
    assert not any(
        event.type == "orchestrator.semantic.rework.requested"
        for event in runtime.event_log.read_all()
    )


def test_graph_delta_rejects_role_removed_after_checkpoint(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    prepared = request_semantic_failure_checkpoint(runtime, _semantic_request(runtime))
    decision_event = _submit_rework(runtime, prepared, action="rebind")
    runtime.config.roles = [
        role for role in runtime.config.roles if role.instance_id != "dev-a"
    ]

    outcome = apply_orchestrator_agent_decision(runtime, decision_event)

    assert outcome["status"] == "rejected"
    assert outcome["reason"] == "target_role_missing"


def test_return_to_plan_respects_semantic_revision_budget(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime.config.workflow.orchestration.max_plan_revisions = 0
    prepared = request_semantic_failure_checkpoint(runtime, _semantic_request(runtime))

    outcome = apply_orchestrator_agent_decision(
        runtime,
        _submit_rework(runtime, prepared, action="return_to_plan"),
    )

    assert outcome["status"] == "escalated"
    assert outcome["applied"] is False
    assert not any(
        event.type == "orchestrator.replan_requested"
        for event in runtime.event_log.read_all()
    )


@pytest.mark.parametrize("mutation,reason", [
    ({"workflow_run_id": "other-run"}, "current_plan_package_missing"),
    ({"task_map_generation": "obsolete"}, "target_generation_stale"),
])
def test_semantic_failure_never_falls_back_to_another_run_or_generation(tmp_path, mutation, reason):
    runtime = _runtime(tmp_path)
    event = _semantic_request(runtime)
    event.payload.update(mutation)
    with pytest.raises(SemanticFailureCheckpointError, match=reason):
        request_semantic_failure_checkpoint(runtime, event)


def test_replan_replay_preserves_unaffected_and_completed_tasks(tmp_path):
    runtime = _runtime(tmp_path)
    runtime.task_store.add(Task(id="TASK-DONE", status="done"))
    before = {key: runtime.task_store.get(key) for key in ("TASK-2", "TASK-DONE")}
    prepared = request_semantic_failure_checkpoint(runtime, _semantic_request(runtime))
    event = _submit_rework(runtime, prepared, action="return_to_plan")
    first = apply_orchestrator_agent_decision(runtime, event)
    assert apply_orchestrator_agent_decision(runtime, event) == first
    assert before == {key: runtime.task_store.get(key) for key in before}
    events = runtime.event_log.read_all()
    assert len([row for row in events if row.type == "prd.plan.requested"]) == 1
    assert not any(row.type in {"task_map.ready", "task.pipeline.generation.admitted"} for row in events)
