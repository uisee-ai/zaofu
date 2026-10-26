from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from zf.core.config.schema import (
    GoalConfig,
    ProjectConfig,
    RoleConfig,
    WorkflowConfig,
    WorkflowOrchestrationConfig,
    WorkflowOrchestrationFlowPolicyConfig,
    ZfConfig,
)
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
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
from zf.runtime.orchestrator_agent_metrics import (
    build_orchestrator_agent_metrics,
)
from zf.runtime.orchestrator_agent_operations import (
    activate_orchestrator_agent_operation,
    prepared_operation_from_checkpoint_event,
)
from zf.runtime.orchestrator_agent_aggregation import (
    pre_closeout_checkpoint_state,
)
from zf.runtime.orchestrator_agent_plan_adoption import (
    plan_candidate_checkpoint_state,
)
from zf.runtime.orchestrator_agent_run_plan import (
    pre_impl_checkpoint_state,
)
from zf.runtime.orchestrator_agent_semantic_failure import (
    SEMANTIC_FAILURE_REQUESTED,
    request_semantic_failure_checkpoint,
)
from zf.runtime.owner_delivery_narrative import (
    apply_owner_delivery_narrative,
    prepare_owner_delivery_narrative_operation,
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
from zf.runtime.run_contract import (
    stable_json_sha256,
    write_run_contract_snapshot,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


class _RecordingTransport:
    def __init__(self) -> None:
        self.sent: list[tuple[str, Path, str, object]] = []

    def send_task(  # noqa: ANN001
        self,
        role_name,
        briefing_path,
        prompt,
        *,
        context=None,
    ) -> None:
        self.sent.append((role_name, briefing_path, prompt, context))

    def is_alive(self, role_name):  # noqa: ANN001
        return True

    def capture_log(self, role_name, lines=200):  # noqa: ANN001
        return ""

    def poll_events(self):
        return []


def _config(
    tmp_path: Path,
    state_dir: Path,
    *,
    checkpoints: list[str] | None = None,
    goal_enabled: bool = False,
) -> ZfConfig:
    enabled_checkpoints = checkpoints or ["plan_candidate"]
    return ZfConfig(
        project=ProjectConfig(
            name="oa-semantic-mock-e2e",
            workspace=str(tmp_path),
            state_dir=str(state_dir),
        ),
        goal=GoalConfig(enabled=goal_enabled),
        roles=[
            RoleConfig(
                name="orchestrator",
                instance_id="orchestrator",
                backend="mock",
                role_kind="reader",
                triggers=["orchestrator.semantic.checkpoint.requested"],
            ),
            RoleConfig(
                name="dev",
                instance_id="dev-a",
                backend="mock",
                role_kind="writer",
                publishes=["dev.build.done", "dev.blocked"],
            ),
        ],
        workflow=WorkflowConfig(orchestration=WorkflowOrchestrationConfig(
            mode="semantic_control",
            checkpoints=enabled_checkpoints,
            checkpoint_policies={
                checkpoint: "blocking" for checkpoint in enabled_checkpoints
            },
            max_plan_revisions=2,
            no_progress_limit=2,
        )),
    )


def _port(state_dir: Path, name: str) -> dict:
    descriptor = write_immutable_json_sidecar(
        state_dir,
        {"schema_version": f"{name}.v1", "revision": "r1"},
        root=f"fixtures/{name}",
        kind=name,
        schema_version=f"{name}.v1",
        created_by="mock-e2e",
    )
    return {
        "logical_name": name,
        "artifact_kind": name,
        "schema_version": f"{name}.v1",
        "producer_stage_id": "prd-plan",
        "ref": descriptor["ref"],
        "sha256": descriptor["sha256"],
    }


def _candidate(
    runtime: Orchestrator,
    *,
    flow_kind: str = "prd",
    run_id: str = "run-oa-mock-e2e",
) -> tuple[ZfEvent, SimpleNamespace]:
    run_contract = {
        "schema_version": "run-contract.v1",
        "workflow": {"kind": flow_kind},
    }
    run_contract["contract_digest"] = stable_json_sha256(run_contract)
    contract_ref = write_run_contract_snapshot(
        runtime.state_dir,
        run_contract,
    )
    ports = [
        _port(runtime.state_dir, name)
        for name in (
            "requirement_spec",
            "goal_claim_set",
            "task_map",
            "planning_result",
        )
    ]
    package = build_plan_artifact_package(
        workflow_run_id=run_id,
        flow_kind=flow_kind,
        producer_stage_id=f"{flow_kind}-plan",
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
        correlation_id=run_id,
        payload=package_event_payload(package, descriptor, status="admitted"),
    ))
    trigger = ZfEvent(
        id=f"evt-task-map-{flow_kind}-oa-mock-e2e",
        type="task_map.ready",
        correlation_id=run_id,
        payload={
            "workflow_run_id": run_id,
            "flow_kind": flow_kind,
            "goal_id": "GOAL-OA-MOCK-E2E",
            "feature_id": "GOAL-OA-MOCK-E2E",
            "plan_revision": "r1",
            "task_map_generation": "g1",
            "plan_artifact_package_id": descriptor["package_id"],
            "plan_artifact_package_ref": descriptor["ref"],
            "plan_artifact_package_digest": descriptor["sha256"],
        },
    )
    loaded = SimpleNamespace(
        workflow_run_id=run_id,
        flow_kind=flow_kind,
        feature_id="GOAL-OA-MOCK-E2E",
        pdd_id="GOAL-OA-MOCK-E2E",
        task_map_generation="g1",
        task_map_ref=next(
            port["ref"]
            for port in package["produced"]
            if port["logical_name"] == "task_map"
        ),
        plan_artifact_package_id=descriptor["package_id"],
        plan_artifact_package_ref=descriptor["ref"],
        plan_artifact_package_digest=descriptor["sha256"],
    )
    return trigger, loaded


def _adopt_decision(prepared) -> dict:  # noqa: ANN001
    identity = prepared.context.input_body["identity"]
    return {
        "schema_version": "orchestration-decision.v1",
        "execution_status": "completed",
        "identity": {
            "operation_id": prepared.operation_id,
            "workflow_run_id": prepared.workflow_run_id,
            "checkpoint": "plan_candidate",
            "input_digest": prepared.context.input_ref["sha256"],
            "effective_config_digest": prepared.context.effective_config_ref[
                "sha256"
            ],
            "plan_artifact_package_ref": identity[
                "plan_artifact_package_ref"
            ],
            "plan_artifact_package_digest": identity[
                "plan_artifact_package_digest"
            ],
            "task_map_generation": identity["task_map_generation"],
        },
        "decision": "adopt",
        "reason_codes": ["plan_is_complete"],
        "affected_work_units": [],
        "required_followup": "continue",
        "expected_outcome": "current plan enters writer graph",
        "confidence": 0.9,
    }


def _submit_result(
    runtime: Orchestrator,
    prepared,  # noqa: ANN001
    semantic_result: dict,
) -> ZfEvent:
    activate_orchestrator_agent_operation(
        runtime,
        prepared,
        dispatch_id=f"dispatch-{prepared.operation_id}",
        causation_id=str(
            prepared.context.input_body["identity"].get("source_event_id") or ""
        ),
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
        runtime.state_dir / "private/result-submit/roles/orchestrator.token"
    ).read_text(encoding="utf-8").strip()
    submitted = SemanticResultSubmitService(
        state_dir=runtime.state_dir,
        event_log=runtime.event_log,
        event_writer=runtime.event_writer,
    ).submit(
        operation_id=prepared.operation_id,
        semantic_result=semantic_result,
        role_instance="orchestrator",
        credential=token,
    )
    canonical = next(
        event
        for event in runtime.event_log.read_all()
        if event.id == submitted.canonical_event_id
    )
    return hydrate_profiled_control_result_event(runtime.state_dir, canonical)


def _pre_impl_decision(prepared) -> dict:  # noqa: ANN001
    identity = prepared.context.input_body["identity"]
    routed_source = next(
        source
        for source in prepared.context.source_manifest["sources"]
        if source["source_id"] == "plan-artifact-package"
    )
    return {
        "schema_version": "orchestration-decision.v1",
        "execution_status": "completed",
        "identity": {
            "operation_id": prepared.operation_id,
            "workflow_run_id": prepared.workflow_run_id,
            "checkpoint": "pre_impl",
            "input_digest": prepared.context.input_ref["sha256"],
            "effective_config_digest": prepared.context.effective_config_ref[
                "sha256"
            ],
            "plan_artifact_package_ref": identity[
                "plan_artifact_package_ref"
            ],
            "plan_artifact_package_digest": identity[
                "plan_artifact_package_digest"
            ],
            "task_map_generation": identity["task_map_generation"],
        },
        "decision": "adopt",
        "reason_codes": ["run_graph_is_actionable"],
        "affected_work_units": ["TASK-1"],
        "required_followup": "execute the admitted graph",
        "expected_outcome": "TASK-1 receives exact routed context",
        "confidence": 0.9,
        "run_plan": {
            "schema_version": "run-orchestration-plan.v1",
            "identity": {
                "operation_id": prepared.operation_id,
                "workflow_run_id": prepared.workflow_run_id,
                "goal_id": identity["goal_id"],
                "effective_config_digest": prepared.context.effective_config_ref[
                    "sha256"
                ],
                "run_contract_ref": identity["run_contract_ref"],
                "run_contract_digest": identity["run_contract_digest"],
                "plan_revision": 1,
            },
            "goal_model": {
                "objective": "deliver the semantic-control PRD fixture",
                "mandatory_claims": ["CLAIM-1"],
                "constraints": [],
                "assumptions": [],
                "exclusions": [],
            },
            "graph": {
                "work_units": [{"work_unit_id": "TASK-1"}],
                "edges": [],
                "barriers": [],
                "semantic_checkpoints": ["pre_closeout"],
            },
            "delegation": [{
                "work_unit_id": "TASK-1",
                "capability_refs": [],
                "preferred_role_refs": ["dev-a"],
                "skill_refs": [],
            }],
            "context_routes": [{
                "work_unit_id": "TASK-1",
                "required_sources": [{
                    "ref": routed_source["ref"],
                    "sha256": routed_source["sha256"],
                }],
                "return_policy": "selective",
            }],
            "quality": {},
            "control": {},
        },
    }


def _aggregation_decision(prepared) -> dict:  # noqa: ANN001
    identity = prepared.context.input_body["identity"]
    result_refs = [
        {
            "ref": str(item.get("ref") or ""),
            "sha256": str(item.get("sha256") or ""),
        }
        for item in prepared.context.input_body["aggregation_input_refs"]
    ]
    return {
        "schema_version": "orchestration-decision.v1",
        "execution_status": "completed",
        "identity": {
            "operation_id": prepared.operation_id,
            "workflow_run_id": prepared.workflow_run_id,
            "checkpoint": "pre_closeout",
            "input_digest": prepared.context.input_ref["sha256"],
            "effective_config_digest": prepared.context.effective_config_ref[
                "sha256"
            ],
            "plan_artifact_package_ref": identity[
                "plan_artifact_package_ref"
            ],
            "plan_artifact_package_digest": identity[
                "plan_artifact_package_digest"
            ],
            "task_map_generation": identity["task_map_generation"],
        },
        "decision": "aggregate",
        "reason_codes": ["mandatory_claims_have_results"],
        "affected_work_units": ["TASK-1"],
        "required_followup": "run the independent Kernel Goal gate",
        "expected_outcome": "Kernel independently evaluates completion",
        "confidence": 0.9,
        "aggregation_result": {
            "schema_version": "orchestration-result.v1",
            "identity": {
                "operation_id": prepared.operation_id,
                "workflow_run_id": prepared.workflow_run_id,
                "checkpoint": "pre_closeout",
            },
            "input_result_refs": result_refs,
            "selected_result_refs": result_refs,
            "rejected_result_refs": [],
            "unclosed_claim_ids": [],
            "provenance_map": [{"claim_id": "CLAIM-1"}],
            "remaining_uncertainty": [],
            "recommendation": "aggregate",
        },
    }


def _semantic_rebind_decision(prepared) -> dict:  # noqa: ANN001
    identity = prepared.context.input_body["identity"]
    target = prepared.context.input_body["checkpoint_context"]
    basis = prepared.context.source_manifest["sources"][0]
    return {
        "schema_version": "orchestration-decision.v1",
        "execution_status": "completed",
        "identity": {
            "operation_id": prepared.operation_id,
            "workflow_run_id": prepared.workflow_run_id,
            "checkpoint": "semantic_failure",
            "input_digest": prepared.context.input_ref["sha256"],
            "effective_config_digest": prepared.context.effective_config_ref[
                "sha256"
            ],
            "plan_artifact_package_ref": identity[
                "plan_artifact_package_ref"
            ],
            "plan_artifact_package_digest": identity[
                "plan_artifact_package_digest"
            ],
            "task_map_generation": identity["task_map_generation"],
        },
        "decision": "rebind",
        "reason_codes": ["capability_owner_mismatch"],
        "affected_work_units": ["TASK-1"],
        "required_followup": "rebind the exact failed work unit",
        "expected_outcome": "unaffected work remains untouched",
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
                "directive_id": "rebind-task-1",
                "action": "rebind",
                "target": {
                    "work_unit_id": "TASK-1",
                    "task_id": target["target_task_id"],
                    "stage_id": target["target_stage_id"],
                    "attempt_id": target["target_attempt_id"],
                    "role_instance": target["target_role_instance"],
                },
                "basis_refs": [{
                    "ref": basis["ref"],
                    "sha256": basis["sha256"],
                }],
                "required_actions": ["select the admitted capable role"],
                "reuse_refs": [{
                    "ref": basis["ref"],
                    "sha256": basis["sha256"],
                }],
                "invalidate_refs": [],
            }],
        },
    }


def test_mock_provider_plan_checkpoint_dispatch_admit_and_replay(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    transport = _RecordingTransport()
    runtime = Orchestrator(
        state_dir,
        _config(tmp_path, state_dir),
        transport,  # type: ignore[arg-type]
    )
    provision_role_submit_credential(state_dir, "orchestrator")
    provision_role_artifact_read_credential(
        state_dir,
        "orchestrator",
        role_name="orchestrator",
        provider="mock",
    )
    trigger, loaded = _candidate(runtime)

    initial = plan_candidate_checkpoint_state(
        runtime,
        stage_id="impl-writers",
        trigger_event=trigger,
        loaded=loaded,
        trace_id="run-oa-mock-e2e",
    )
    checkpoint = next(
        event
        for event in reversed(runtime.event_log.read_all())
        if event.type == "orchestrator.semantic.checkpoint.requested"
    )
    prepared = prepared_operation_from_checkpoint_event(runtime, checkpoint)
    assert prepared is not None

    runtime.run_once(events=[checkpoint])

    assert initial.blocking is True
    assert initial.satisfied is False
    assert len(transport.sent) == 1
    role, briefing_path, prompt, _context = transport.sent[0]
    assert role == "orchestrator"
    assert briefing_path.is_file()
    assert "Required Canonical Inputs" in briefing_path.read_text(
        encoding="utf-8"
    )
    assert "typed Orchestrator Agent semantic checkpoint" in prompt

    for source in prepared.context.source_manifest["sources"]:
        read_attempt_artifact(
            state_dir,
            manifest=prepared.context.source_manifest,
            source_id=source["source_id"],
            artifact_id=source["artifact_id"],
            actor="orchestrator",
            role="orchestrator",
            provider="mock",
        )
    token = (
        state_dir / "private/result-submit/roles/orchestrator.token"
    ).read_text(encoding="utf-8").strip()
    submitted = SemanticResultSubmitService(
        state_dir=state_dir,
        event_log=runtime.event_log,
        event_writer=runtime.event_writer,
    ).submit(
        operation_id=prepared.operation_id,
        semantic_result=_adopt_decision(prepared),
        role_instance="orchestrator",
        credential=token,
    )
    canonical = next(
        event
        for event in runtime.event_log.read_all()
        if event.id == submitted.canonical_event_id
    )
    outcome = apply_orchestrator_agent_decision(
        runtime,
        hydrate_profiled_control_result_event(state_dir, canonical),
    )
    resumed = plan_candidate_checkpoint_state(
        runtime,
        stage_id="impl-writers",
        trigger_event=trigger,
        loaded=loaded,
        trace_id="run-oa-mock-e2e",
    )

    assert outcome["status"] == "applied"
    assert resumed.satisfied is True
    assert any(
        event.type == "plan.approved"
        and event.payload.get("semantic_control") is True
        for event in runtime.event_log.read_all()
    )

    runtime.run_once(events=[checkpoint])
    assert len(transport.sent) == 1

    metrics = build_orchestrator_agent_metrics(runtime.event_log.read_all())
    assert metrics["summary"]["operation_count"] == 1
    assert metrics["summary"]["required_read_closure_rate"] == 1.0
    assert metrics["summary"]["normal_path_oa_turn_rate"] == 0.0


@pytest.mark.parametrize("flow_kind", ["issue", "refactor"])
def test_scoped_product_flow_task_map_runs_plan_checkpoint(
    tmp_path: Path,
    flow_kind: str,
) -> None:
    state_dir = tmp_path / f"state-{flow_kind}"
    state_dir.mkdir()
    config = _config(tmp_path, state_dir)
    config.workflow.orchestration = WorkflowOrchestrationConfig(
        mode="exception_advisor",
        flow_policies={
            flow_kind: WorkflowOrchestrationFlowPolicyConfig(
                mode="semantic_control",
                checkpoints=["plan_candidate"],
                checkpoint_policies={"plan_candidate": "blocking"},
            ),
        },
    )
    runtime = Orchestrator(
        state_dir,
        config,
        _RecordingTransport(),  # type: ignore[arg-type]
        project_root=tmp_path,
    )
    provision_role_submit_credential(state_dir, "orchestrator")
    provision_role_artifact_read_credential(
        state_dir,
        "orchestrator",
        role_name="orchestrator",
        provider="mock",
    )
    trigger, loaded = _candidate(
        runtime,
        flow_kind=flow_kind,
        run_id=f"run-{flow_kind}-oa-mock-e2e",
    )

    pending = plan_candidate_checkpoint_state(
        runtime,
        stage_id=f"{flow_kind}-impl",
        trigger_event=trigger,
        loaded=loaded,
        trace_id=loaded.workflow_run_id,
    )
    requested = next(
        event
        for event in reversed(runtime.event_log.read_all())
        if event.type == "orchestrator.semantic.checkpoint.requested"
    )
    prepared = prepared_operation_from_checkpoint_event(runtime, requested)
    assert prepared is not None
    outcome = apply_orchestrator_agent_decision(
        runtime,
        _submit_result(runtime, prepared, _adopt_decision(prepared)),
    )

    assert pending.blocking is True and pending.satisfied is False
    assert prepared.context.input_body["identity"]["task_map_generation"] == "g1"
    assert outcome["status"] == "applied"
    assert any(
        event.type == "plan.approved"
        and event.payload.get("semantic_control") is True
        for event in runtime.event_log.read_all()
    )


def test_full_prd_semantic_control_run_is_replay_safe_and_auditable(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state-full"
    state_dir.mkdir()
    checkpoints = [
        "pre_impl",
        "plan_candidate",
        "semantic_failure",
        "pre_closeout",
        "owner_delivery",
    ]
    config = _config(
        tmp_path,
        state_dir,
        checkpoints=checkpoints,
        goal_enabled=True,
    )
    runtime = Orchestrator(
        state_dir,
        config,
        _RecordingTransport(),  # type: ignore[arg-type]
        project_root=tmp_path,
    )
    provision_role_submit_credential(state_dir, "orchestrator")
    provision_role_artifact_read_credential(
        state_dir,
        "orchestrator",
        role_name="orchestrator",
        provider="mock",
    )
    trigger, loaded = _candidate(runtime)
    trigger = runtime.event_writer.append(trigger)
    runtime.event_writer.append(ZfEvent(
        type="run.goal.started",
        correlation_id=loaded.workflow_run_id,
        payload={
            "run_id": loaded.workflow_run_id,
            "goal_id": loaded.feature_id,
            "objective": "deliver the semantic-control PRD fixture",
        },
    ))

    pre_impl = pre_impl_checkpoint_state(
        runtime,
        stage_id="impl-writers",
        trigger_event=trigger,
        loaded=loaded,
        trace_id=loaded.workflow_run_id,
    )
    run_checkpoint = next(
        event
        for event in reversed(runtime.event_log.read_all())
        if event.type == "orchestrator.semantic.checkpoint.requested"
        and event.payload.get("checkpoint") == "pre_impl"
    )
    run_prepared = prepared_operation_from_checkpoint_event(
        runtime,
        run_checkpoint,
    )
    assert run_prepared is not None
    run_decision_event = _submit_result(
        runtime,
        run_prepared,
        _pre_impl_decision(run_prepared),
    )
    run_outcome = apply_orchestrator_agent_decision(
        runtime,
        run_decision_event,
    )

    plan = plan_candidate_checkpoint_state(
        runtime,
        stage_id="impl-writers",
        trigger_event=trigger,
        loaded=loaded,
        trace_id=loaded.workflow_run_id,
    )
    plan_checkpoint = next(
        event
        for event in reversed(runtime.event_log.read_all())
        if event.type == "orchestrator.semantic.checkpoint.requested"
        and event.payload.get("checkpoint") == "plan_candidate"
    )
    plan_prepared = prepared_operation_from_checkpoint_event(
        runtime,
        plan_checkpoint,
    )
    assert plan_prepared is not None
    plan_outcome = apply_orchestrator_agent_decision(
        runtime,
        _submit_result(runtime, plan_prepared, _adopt_decision(plan_prepared)),
    )

    runtime.task_store.add(Task(
        id="TASK-1",
        title="semantic graph target",
        status="in_progress",
        assigned_to="dev-a",
        active_dispatch_id="attempt-task-1",
        contract=TaskContract(
            feature_id=loaded.feature_id,
            phase="impl",
            owner_role="dev",
            owner_instance="dev-a",
            scope=["src/fixture.py"],
        ),
    ))
    failed_result = write_immutable_json_sidecar(
        state_dir,
        {"schema_version": "verification-result.v1", "status": "failed"},
        root="fixtures/full-semantic/failure",
        kind="verification_result",
        schema_version="verification-result.v1",
        created_by="mock-e2e",
    )
    failure = runtime.event_writer.append(ZfEvent(
        id="evt-full-semantic-failure",
        type="verify.failed",
        task_id="TASK-1",
        correlation_id=loaded.workflow_run_id,
        payload={
            "workflow_run_id": loaded.workflow_run_id,
            "task_map_generation": loaded.task_map_generation,
            "dispatch_id": "attempt-task-1",
            "stage_id": "verify",
            "failure_fingerprint": "owner-capability-mismatch",
            "result_refs": [failed_result],
        },
    ))
    recovery_ref = write_immutable_json_sidecar(
        state_dir,
        {
            "schema_version": "task-recovery-context.v1",
            "task_id": "TASK-1",
            "failure_event_ids": [failure.id],
        },
        root="fixtures/full-semantic/recovery",
        kind="recovery_context",
        schema_version="task-recovery-context.v1",
        created_by="mock-e2e",
    )
    semantic_request = runtime.event_writer.append(ZfEvent(
        type=SEMANTIC_FAILURE_REQUESTED,
        actor="run-manager",
        task_id="TASK-1",
        correlation_id=loaded.workflow_run_id,
        payload={
            "problem_class": "semantic",
            "workflow_run_id": loaded.workflow_run_id,
            "failure_fingerprint": "owner-capability-mismatch",
            "failure_event_ids": [failure.id],
            "trigger_event_type": "verify.failed",
            "recovery_context_ref": recovery_ref,
        },
    ))
    semantic_prepared = request_semantic_failure_checkpoint(
        runtime,
        semantic_request,
    )
    semantic_outcome = apply_orchestrator_agent_decision(
        runtime,
        _submit_result(
            runtime,
            semantic_prepared,
            _semantic_rebind_decision(semantic_prepared),
        ),
    )
    runtime.task_store.update(
        "TASK-1",
        status="done",
        active_dispatch_id="",
    )
    runtime.event_writer.append(ZfEvent(
        type="task.done",
        task_id="TASK-1",
        correlation_id=loaded.workflow_run_id,
        payload={"workflow_run_id": loaded.workflow_run_id},
    ))

    passed_result = write_immutable_json_sidecar(
        state_dir,
        {"schema_version": "verification-result.v1", "status": "passed"},
        root="fixtures/full-semantic/passed",
        kind="verification_result",
        schema_version="verification-result.v1",
        created_by="mock-e2e",
    )
    evidence_ref = write_immutable_json_sidecar(
        state_dir,
        {"schema_version": "evidence.v1", "status": "passed"},
        root="fixtures/full-semantic/evidence",
        kind="evidence",
        schema_version="evidence.v1",
        created_by="mock-e2e",
    )
    runtime.event_writer.append(ZfEvent(
        type="verify.passed",
        task_id="TASK-1",
        correlation_id=loaded.workflow_run_id,
        payload={
            "workflow_run_id": loaded.workflow_run_id,
            "task_map_generation": loaded.task_map_generation,
            "dispatch_id": "attempt-task-1",
            "target_commit": "a" * 40,
            "result_refs": [passed_result],
            "evidence_refs": [evidence_ref],
        },
    ))
    judge = runtime.event_writer.append(ZfEvent(
        type="judge.passed",
        correlation_id=loaded.workflow_run_id,
        payload={
            "workflow_run_id": loaded.workflow_run_id,
            "goal_id": loaded.feature_id,
            "task_map_generation": loaded.task_map_generation,
            "result_refs": [passed_result],
            "evidence_refs": [evidence_ref],
        },
    ))
    runtime._maybe_complete_run_goal(judge)
    assert not any(
        event.type == "run.goal.completion.claimed"
        for event in runtime.event_log.read_all()
    )
    closeout_checkpoint = next(
        event
        for event in reversed(runtime.event_log.read_all())
        if event.type == "orchestrator.semantic.checkpoint.requested"
        and event.payload.get("checkpoint") == "pre_closeout"
    )
    closeout_prepared = prepared_operation_from_checkpoint_event(
        runtime,
        closeout_checkpoint,
    )
    assert closeout_prepared is not None
    closeout_outcome = apply_orchestrator_agent_decision(
        runtime,
        _submit_result(
            runtime,
            closeout_prepared,
            _aggregation_decision(closeout_prepared),
        ),
    )
    runtime._maybe_complete_run_goal(judge)
    terminal = next(
        event
        for event in reversed(runtime.event_log.read_all())
        if event.type == "run.goal.completed"
    )

    dossier_fingerprint = "d" * 64
    receipt_fingerprint = "e" * 64
    dossier = {
        "schema_version": "goal-dossier.v1",
        "run_id": loaded.workflow_run_id,
        "goal_id": loaded.feature_id,
        "source_fingerprint": dossier_fingerprint,
        "terminal": {"event_id": terminal.id, "status": "completed"},
        "claim_to_evidence": {
            "claims": [{"claim_id": "CLAIM-1", "status": "closed"}],
            "rows": [{
                "goal_claim_id": "CLAIM-1",
                "task_ids": ["TASK-1"],
                "result_refs": [passed_result["ref"]],
                "evidence_refs": [evidence_ref["ref"]],
            }],
        },
        "task_contracts": [{"task_id": "TASK-1"}],
        "gaps": [],
        "results": [passed_result],
        "evidence_index": [evidence_ref],
    }
    projection_dir = state_dir / "projections/goals" / loaded.workflow_run_id
    projection_dir.mkdir(parents=True, exist_ok=True)
    dossier_path = projection_dir / "goal-dossier.v1.json"
    dossier_path.write_text(
        json.dumps(dossier, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    receipt = {
        "schema_version": "goal-completion-receipt.v1",
        "run_id": loaded.workflow_run_id,
        "source_fingerprint": receipt_fingerprint,
        "terminal_event_id": terminal.id,
    }
    receipt_path = projection_dir / "goal-completion-receipt.v1.json"
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    narrative_prepared = prepare_owner_delivery_narrative_operation(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
        event_log=runtime.event_log,
        writer=runtime.event_writer,
        terminal=terminal,
        dossier=dossier,
        dossier_path=dossier_path,
        receipt=receipt,
        receipt_path=receipt_path,
    )
    assert narrative_prepared is not None
    narrative_context = narrative_prepared.context.input_body[
        "checkpoint_context"
    ]
    narrative = {
        "schema_version": "owner-delivery-narrative.v1",
        "execution_status": "completed",
        "identity": {
            "operation_id": narrative_prepared.operation_id,
            "workflow_run_id": loaded.workflow_run_id,
            "terminal_event_id": narrative_context["terminal_event_id"],
            "terminal_event_type": narrative_context["terminal_event_type"],
            "dossier_ref": narrative_context["dossier_ref"],
            "dossier_source_fingerprint": narrative_context[
                "dossier_source_fingerprint"
            ],
            "completion_receipt_ref": narrative_context[
                "completion_receipt_ref"
            ],
            "completion_receipt_fingerprint": narrative_context[
                "completion_receipt_fingerprint"
            ],
        },
        "status": "completed",
        "executive_summary": "The PRD fixture closed with canonical evidence.",
        "delivered_outcomes": [{
            "claim_ids": ["CLAIM-1"],
            "task_ids": ["TASK-1"],
            "gap_ids": [],
            "result_refs": [passed_result],
            "evidence_refs": [evidence_ref],
            "narrative": "TASK-1 closes CLAIM-1 with admitted evidence.",
        }],
        "decisions_and_tradeoffs": ["Used one bounded rebind."],
        "remaining_risks": [],
        "recommended_next_actions": [],
    }
    narrative_outcome = apply_owner_delivery_narrative(
        runtime,
        _submit_result(runtime, narrative_prepared, narrative),
    )

    before_replay = list(runtime.event_log.read_all())
    restarted = Orchestrator(
        state_dir,
        config,
        _RecordingTransport(),  # type: ignore[arg-type]
        project_root=tmp_path,
    )
    assert pre_impl_checkpoint_state(
        restarted,
        stage_id="impl-writers",
        trigger_event=trigger,
        loaded=loaded,
        trace_id=loaded.workflow_run_id,
    ).satisfied is True
    assert pre_closeout_checkpoint_state(restarted, judge).satisfied is True
    replayed_outcome = apply_orchestrator_agent_decision(
        restarted,
        run_decision_event,
    )
    replayed_narrative = prepare_owner_delivery_narrative_operation(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
        event_log=restarted.event_log,
        writer=restarted.event_writer,
        terminal=terminal,
        dossier=dossier,
        dossier_path=dossier_path,
        receipt=receipt,
        receipt_path=receipt_path,
    )
    assert replayed_narrative is not None

    events = restarted.event_log.read_all()
    counts: dict[str, int] = {}
    for event in events:
        counts[event.type] = counts.get(event.type, 0) + 1
    metrics = build_orchestrator_agent_metrics(events)
    report = {
        "schema_version": "oa-semantic-control-mock-e2e-report.v1",
        "workflow_run_id": loaded.workflow_run_id,
        "flow_kind": "prd",
        "checkpoint_status": {
            "pre_impl": run_outcome["status"],
            "plan_candidate": plan_outcome["status"],
            "semantic_failure": semantic_outcome["status"],
            "pre_closeout": closeout_outcome["status"],
            "owner_delivery": narrative_outcome["status"],
        },
        "event_counts": counts,
        "required_read_closure_rate": metrics["summary"][
            "required_read_closure_rate"
        ],
        "replay": {
            "pre_impl_outcome_equal": replayed_outcome == run_outcome,
            "owner_operation_replay_hit": replayed_narrative.replay_hit,
            "event_count_before": len(before_replay),
            "event_count_after": len(events),
        },
    }
    report_ref = write_immutable_json_sidecar(
        state_dir,
        report,
        root="reports/oa-semantic-control-mock-e2e",
        kind="oa_semantic_control_mock_e2e_report",
        schema_version=report["schema_version"],
        created_by="mock-e2e",
    )

    assert pre_impl.blocking is True and pre_impl.satisfied is False
    assert plan.blocking is True and plan.satisfied is False
    assert run_outcome["status"] == "applied"
    assert plan_outcome["status"] == "applied"
    assert semantic_outcome["semantic_rework_event_ids"]
    assert closeout_outcome["status"] == "applied"
    assert narrative_outcome["status"] == "admitted"
    assert counts["run.goal.completion.claimed"] == 1
    assert counts["run.goal.completed"] == 1
    assert counts["orchestrator.run_plan.admitted"] == 1
    assert counts["orchestrator.pre_closeout.admitted"] == 1
    assert replayed_outcome == run_outcome
    assert replayed_narrative.replay_hit is True
    assert metrics["summary"]["required_read_closure_rate"] == 1.0
    assert hydrate_sidecar_ref(state_dir, report_ref).payload == report


def test_oa_adopt_does_not_replace_human_plan_approval(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    from zf.core.config.loader import load_config

    config_path = tmp_path / "zf.yaml"
    config_path.write_text(f"""project:
  name: approval-regression
  workspace: {tmp_path}
  state_dir: {state_dir}
roles:
  - name: orchestrator
    backend: mock
    role_kind: reader
    triggers: [orchestrator.semantic.checkpoint.requested]
workflow:
  plan_approval: true
  orchestration:
    flow_policies:
      prd:
        mode: semantic_control
        pilot_id: approval-regression
        checkpoints: [plan_candidate, pre_impl]
  kind_routes:
    prd:
      pattern_id: route
      default_tier: full
  stages:
    - id: route
      trigger: test.requested
      topology: fanout_reader
      roles: [orchestrator]
""")
    config = load_config(config_path)
    runtime = Orchestrator(state_dir, config, _RecordingTransport())
    provision_role_submit_credential(state_dir, "orchestrator")
    provision_role_artifact_read_credential(state_dir, "orchestrator", role_name="orchestrator", provider="mock")
    trigger, loaded = _candidate(runtime)
    gate = dict(stage_id="impl-writers", trigger_event=trigger, loaded=loaded, trace_id="run-oa-mock-e2e")
    assert not runtime._plan_approval_satisfied(**gate)
    checkpoint = next(e for e in runtime.event_log.read_all() if e.type == "orchestrator.semantic.checkpoint.requested")
    prepared = prepared_operation_from_checkpoint_event(runtime, checkpoint)
    event = _submit_result(runtime, prepared, _adopt_decision(prepared))
    apply_orchestrator_agent_decision(runtime, event)
    assert not runtime._plan_approval_satisfied(**gate)
    events = runtime.event_log.read_all()
    assert not any(e.type == "plan.approved" for e in events)
    requests = [e for e in events if e.type == "plan.approval.requested"]
    assert len(requests) == 1
    assert requests[0].payload["plan_id"] == trigger.id
    runtime.event_writer.append(ZfEvent(type="plan.approved", actor="zf-cli", payload={"plan_id": trigger.id, "semantic_control": True}))
    assert not runtime._plan_approval_satisfied(**gate)
    runtime.event_writer.append(ZfEvent(type="plan.approved", actor="operator", payload={"plan_id": trigger.id}))
    assert runtime._plan_approval_satisfied(**gate)
