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
from zf.runtime.artifact_query.handoff import (
    CanonicalHandoffResolver,
    build_handoff_authority_contract,
)
from zf.runtime.artifact_read_capability import (
    provision_role_artifact_read_credential,
)
from zf.runtime.artifact_read_ledger import (
    canonical_required_reads,
    read_attempt_artifact,
    seal_read_ledger,
    validate_required_reads,
)
from zf.runtime.call_result_adapters import hydrate_profiled_control_result_event
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.orchestrator_agent_decision_apply import (
    apply_orchestrator_agent_decision,
)
from zf.runtime.orchestrator_agent_operations import (
    activate_orchestrator_agent_operation,
    prepared_operation_from_checkpoint_event,
)
from zf.runtime.orchestrator_agent_run_plan import (
    current_run_plan_admission,
    pre_impl_checkpoint_state,
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
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


RUN_ID = "pre-impl-plan-1"
GOAL_ID = "GOAL-START-1"
GENERATION = "generation-1"


def _config() -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="oa-pre-impl", workspace="."),
        workflow=WorkflowConfig(orchestration=WorkflowOrchestrationConfig(
            mode="semantic_control",
            checkpoints=["pre_impl"],
            checkpoint_policies={"pre_impl": "blocking"},
        )),
        roles=[
            RoleConfig(
                name="orchestrator",
                instance_id="orchestrator",
                role_kind="reader",
                backend="mock",
            ),
            RoleConfig(
                name="dev",
                instance_id="dev-1",
                role_kind="writer",
                backend="mock",
                stages=["implementation"],
            ),
        ],
    )


def _port(state_dir: Path, name: str, *, revision: str = "1") -> dict:
    descriptor = write_immutable_json_sidecar(
        state_dir,
        {
            "schema_version": f"{name}.v1",
            "goal_id": GOAL_ID,
            "plan_revision": revision,
        },
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


def _runtime(tmp_path: Path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    runtime = SimpleNamespace(
        state_dir=state_dir,
        project_root=tmp_path,
        config=_config(),
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
    provision_role_artifact_read_credential(
        state_dir,
        "dev-1",
        role_name="dev",
        provider="mock",
    )
    contract = {
        "schema_version": "run-contract.v1",
        "workflow": {"kind": "prd"},
    }
    contract["contract_digest"] = stable_json_sha256(contract)
    contract_ref = write_run_contract_snapshot(state_dir, contract)
    ports = [
        _port(state_dir, name)
        for name in (
            "requirement_spec",
            "goal_claim_set",
            "task_map",
            "planning_result",
        )
    ]
    package = build_plan_artifact_package(
        workflow_run_id=RUN_ID,
        flow_kind="prd",
        producer_stage_id="prd-plan",
        run_contract=contract_ref,
        plan_revision="1",
        task_map_generation=GENERATION,
        produced=ports,
        required_ports=[port["logical_name"] for port in ports],
    )
    package_ref = write_plan_artifact_package(state_dir, package)
    runtime.event_writer.append(ZfEvent(
        type="plan.artifact_package.admitted",
        actor="zf-cli",
        correlation_id=RUN_ID,
        payload=package_event_payload(package, package_ref, status="admitted"),
    ))
    trigger = ZfEvent(
        id="evt-task-map-pre-impl",
        type="task_map.ready",
        correlation_id=RUN_ID,
        payload={
            "workflow_run_id": RUN_ID,
            "goal_id": GOAL_ID,
            "feature_id": GOAL_ID,
            "task_map_generation": GENERATION,
            "plan_artifact_package_id": package_ref["package_id"],
            "plan_artifact_package_ref": package_ref["ref"],
            "plan_artifact_package_digest": package_ref["sha256"],
        },
    )
    loaded = SimpleNamespace(
        workflow_run_id=RUN_ID,
        feature_id=GOAL_ID,
        pdd_id=GOAL_ID,
        task_map_generation=GENERATION,
        task_map_ref=next(
            port["ref"] for port in ports if port["logical_name"] == "task_map"
        ),
        plan_artifact_package_id=package_ref["package_id"],
        plan_artifact_package_ref=package_ref["ref"],
        plan_artifact_package_digest=package_ref["sha256"],
    )
    return runtime, trigger, loaded, package, package_ref


def _revised_candidate(
    runtime,
    *,
    prior_package: dict,
    prior_ref: dict,
    revision: str,
    generation: str,
):
    ports = [
        _port(runtime.state_dir, name, revision=revision)
        for name in (
            "requirement_spec",
            "goal_claim_set",
            "task_map",
            "planning_result",
        )
    ]
    package = build_plan_artifact_package(
        workflow_run_id=RUN_ID,
        flow_kind="prd",
        producer_stage_id="prd-plan",
        run_contract={
            "ref": prior_package["run_contract_ref"],
            "sha256": prior_package["run_contract_sha256"],
            "contract_digest": prior_package["run_contract_digest"],
        },
        plan_revision=revision,
        task_map_generation=generation,
        produced=ports,
        required_ports=[port["logical_name"] for port in ports],
        supersedes=prior_ref,
    )
    package_ref = write_plan_artifact_package(runtime.state_dir, package)
    runtime.event_writer.append(ZfEvent(
        type="plan.artifact_package.admitted",
        actor="zf-cli",
        correlation_id=RUN_ID,
        payload=package_event_payload(package, package_ref, status="admitted"),
    ))
    trigger = ZfEvent(
        id=f"evt-task-map-pre-impl-{generation}",
        type="task_map.ready",
        correlation_id=RUN_ID,
        payload={
            "workflow_run_id": RUN_ID,
            "goal_id": GOAL_ID,
            "feature_id": GOAL_ID,
            "task_map_generation": generation,
            "plan_artifact_package_id": package_ref["package_id"],
            "plan_artifact_package_ref": package_ref["ref"],
            "plan_artifact_package_digest": package_ref["sha256"],
        },
    )
    loaded = SimpleNamespace(
        workflow_run_id=RUN_ID,
        feature_id=GOAL_ID,
        pdd_id=GOAL_ID,
        task_map_generation=generation,
        task_map_ref=next(
            port["ref"] for port in ports if port["logical_name"] == "task_map"
        ),
        plan_artifact_package_id=package_ref["package_id"],
        plan_artifact_package_ref=package_ref["ref"],
        plan_artifact_package_digest=package_ref["sha256"],
    )
    return trigger, loaded, package, package_ref


def _checkpoint(runtime, trigger, loaded):
    state = pre_impl_checkpoint_state(
        runtime,
        stage_id="implementation",
        trigger_event=trigger,
        loaded=loaded,
        trace_id=RUN_ID,
    )
    requested = next(
        event for event in reversed(runtime.event_log.read_all())
        if event.type == "orchestrator.semantic.checkpoint.requested"
    )
    prepared = prepared_operation_from_checkpoint_event(runtime, requested)
    assert prepared is not None
    return state, prepared


def _decision(prepared, *, role_ref: str = "dev-1", source: dict | None = None):
    identity = prepared.context.input_body["identity"]
    route_source = source or next(
        item for item in prepared.context.source_manifest["sources"]
        if item["source_id"] == "plan-port-requirement_spec"
    )
    return {
        "schema_version": "orchestration-decision.v1",
        "execution_status": "completed",
        "identity": {
            "operation_id": prepared.operation_id,
            "workflow_run_id": RUN_ID,
            "checkpoint": "pre_impl",
            "input_digest": prepared.context.input_ref["sha256"],
            "effective_config_digest": prepared.context.effective_config_ref["sha256"],
            "plan_artifact_package_ref": identity["plan_artifact_package_ref"],
            "plan_artifact_package_digest": identity[
                "plan_artifact_package_digest"
            ],
            "task_map_generation": identity["task_map_generation"],
        },
        "decision": "adopt",
        "reason_codes": ["goal_and_graph_are_actionable"],
        "affected_work_units": ["discovery"],
        "required_followup": "dispatch admitted graph",
        "expected_outcome": "bounded run graph starts",
        "confidence": 0.9,
        "run_plan": {
            "schema_version": "run-orchestration-plan.v1",
            "identity": {
                "operation_id": prepared.operation_id,
                "workflow_run_id": RUN_ID,
                "goal_id": GOAL_ID,
                "plan_revision": 1,
                "effective_config_digest": prepared.context.effective_config_ref[
                    "sha256"
                ],
                "run_contract_ref": identity["run_contract_ref"],
                "run_contract_digest": identity["run_contract_digest"],
            },
            "goal_model": {
                "objective": "Deliver the admitted product goal",
                "mandatory_claims": ["CLAIM-1"],
                "constraints": [],
                "assumptions": [],
                "exclusions": [],
            },
            "graph": {
                "work_units": [{
                    "work_unit_id": "discovery",
                    "stage_ids": ["discovery"],
                }],
                "edges": [],
                "barriers": [],
                "semantic_checkpoints": [],
            },
            "delegation": [{
                "work_unit_id": "discovery",
                "capability_refs": ["writer"],
                "preferred_role_refs": [role_ref],
                "skill_refs": [],
            }],
            "context_routes": [{
                "work_unit_id": "discovery",
                "required_sources": [{
                    "ref": route_source["ref"],
                    "sha256": route_source["sha256"],
                }],
                "return_policy": "selective",
            }],
            "quality": {},
            "control": {},
        },
    }


def _submit(runtime, prepared, decision: dict) -> ZfEvent:
    activate_orchestrator_agent_operation(
        runtime,
        prepared,
        dispatch_id="dispatch-pre-impl",
        causation_id="evt-task-map-pre-impl",
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
        semantic_result=decision,
        role_instance="orchestrator",
        credential=token,
    )
    event = next(
        item for item in runtime.event_log.read_all()
        if item.id == submitted.canonical_event_id
    )
    return hydrate_profiled_control_result_event(runtime.state_dir, event)


def test_pre_impl_admits_execution_plan_and_routes_required_context(
    tmp_path: Path,
) -> None:
    runtime, trigger, loaded, _package, package_ref = _runtime(tmp_path)
    state, prepared = _checkpoint(runtime, trigger, loaded)

    assert state.blocking is True
    assert state.satisfied is False
    outcome = apply_orchestrator_agent_decision(
        runtime,
        _submit(runtime, prepared, _decision(prepared)),
    )

    assert outcome["status"] == "applied"
    admitted = current_run_plan_admission(
        runtime.event_log.read_all(),
        workflow_run_id=RUN_ID,
        plan_artifact_package_ref=package_ref["ref"],
        plan_artifact_package_digest=package_ref["sha256"],
        task_map_generation=GENERATION,
    )
    execution_plan = hydrate_sidecar_ref(
        runtime.state_dir, admitted["execution_plan_ref"]
    ).payload
    assert execution_plan["schema_version"] == "orchestrator-execution-plan.v1"
    assert execution_plan["effective_config_ref"]["sha256"] == (
        execution_plan["identity"]["effective_config_digest"]
    )
    admitted_event = next(
        event for event in runtime.event_log.read_all()
        if event.type == "orchestrator.run_plan.admitted"
    )
    assert admitted_event.payload["effective_config_digest"] == (
        execution_plan["identity"]["effective_config_digest"]
    )
    assert execution_plan["context_routes"][0]["required_sources"][0][
        "source_id"
    ] == "plan-port-requirement_spec"

    payload = {
        "output_profile_id": "workflow-read",
        "stage_id": "discovery",
        "plan_artifact_package_id": package_ref["package_id"],
        "plan_artifact_package_ref": package_ref["ref"],
        "plan_artifact_package_digest": package_ref["sha256"],
        "task_map_generation": GENERATION,
    }
    payload["handoff_authority_contract"] = build_handoff_authority_contract(
        payload,
        output_profile_id="workflow-read",
        stage_id="discovery",
        operation_type="stage_call",
    )
    manifest, _descriptor = CanonicalHandoffResolver(
        state_dir=runtime.state_dir,
        project_root=runtime.project_root,
        config=runtime.config,
    ).resolve_payload(
        payload=payload,
        workflow_run_id=RUN_ID,
        task_id="",
        attempt_id="attempt-discovery",
        dispatch_id="dispatch-discovery",
    )

    route_sources = [
        source for source in manifest["sources"]
        if source["source_id"].startswith("oa-route-")
    ]
    assert len(route_sources) == 1
    assert route_sources[0]["ref"] == execution_plan["context_routes"][0][
        "required_sources"
    ][0]["ref"]
    required = canonical_required_reads(
        manifest,
        output_profile_id="workflow-read",
    )
    assert route_sources[0]["source_id"] in {
        row["source_id"] for row in required
    }


def test_revised_generation_is_the_only_downstream_read_authority(
    tmp_path: Path,
) -> None:
    runtime, trigger_v1, loaded_v1, package_v1, package_ref_v1 = _runtime(
        tmp_path
    )
    _state_v1, prepared_v1 = _checkpoint(runtime, trigger_v1, loaded_v1)
    applied_v1 = apply_orchestrator_agent_decision(
        runtime,
        _submit(runtime, prepared_v1, _decision(prepared_v1)),
    )
    assert applied_v1["status"] == "applied"
    old_route_ref = next(
        source["ref"]
        for source in prepared_v1.context.source_manifest["sources"]
        if source["source_id"] == "plan-port-requirement_spec"
    )
    runtime.event_writer.append(ZfEvent(
        type="plan.rejected",
        actor="orchestrator",
        correlation_id=RUN_ID,
        causation_id=trigger_v1.id,
        payload={
            "workflow_run_id": RUN_ID,
            "plan_artifact_package_ref": package_ref_v1["ref"],
            "plan_artifact_package_digest": package_ref_v1["sha256"],
            "task_map_generation": GENERATION,
            "reason": "semantic revision required",
        },
    ))

    trigger_v2, loaded_v2, _package_v2, package_ref_v2 = _revised_candidate(
        runtime,
        prior_package=package_v1,
        prior_ref=package_ref_v1,
        revision="2",
        generation="generation-2",
    )
    _state_v2, prepared_v2 = _checkpoint(runtime, trigger_v2, loaded_v2)
    applied_v2 = apply_orchestrator_agent_decision(
        runtime,
        _submit(runtime, prepared_v2, _decision(prepared_v2)),
    )
    assert applied_v2["status"] == "applied"
    new_route_ref = next(
        source["ref"]
        for source in prepared_v2.context.source_manifest["sources"]
        if source["source_id"] == "plan-port-requirement_spec"
    )
    assert new_route_ref != old_route_ref

    payload = {
        "output_profile_id": "workflow-read",
        "stage_id": "discovery",
        "plan_artifact_package_id": package_ref_v2["package_id"],
        "plan_artifact_package_ref": package_ref_v2["ref"],
        "plan_artifact_package_digest": package_ref_v2["sha256"],
        "task_map_generation": "generation-2",
    }
    payload["handoff_authority_contract"] = build_handoff_authority_contract(
        payload,
        output_profile_id="workflow-read",
        stage_id="discovery",
        operation_type="stage_call",
    )
    manifest, _descriptor = CanonicalHandoffResolver(
        state_dir=runtime.state_dir,
        project_root=runtime.project_root,
        config=runtime.config,
    ).resolve_payload(
        payload=payload,
        workflow_run_id=RUN_ID,
        task_id="",
        attempt_id="attempt-discovery-generation-2",
        dispatch_id="dispatch-discovery-generation-2",
    )
    required = canonical_required_reads(
        manifest,
        output_profile_id="workflow-read",
    )
    required_refs = {
        source["ref"]
        for source in manifest["sources"]
        if source["source_id"] in {
            row["source_id"] for row in required
        }
    }
    assert new_route_ref in required_refs
    assert old_route_ref not in required_refs
    assert manifest["task_map_generation"] == "generation-2"
    assert manifest["plan_artifact_package_ref"] == package_ref_v2["ref"]

    for row in required:
        read_attempt_artifact(
            runtime.state_dir,
            manifest=manifest,
            source_id=row["source_id"],
            artifact_id=row["artifact_id"],
            json_path=row.get("json_path") or "$",
            actor="dev-1",
            role="dev",
            provider="mock",
        )
    ledger = seal_read_ledger(
        runtime.state_dir,
        "attempt-discovery-generation-2",
    )
    assert validate_required_reads(
        runtime.state_dir,
        policy={
            "attempt_id": "attempt-discovery-generation-2",
            "required_reads": required,
        },
        ledger_descriptor=ledger,
    ) == []


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("unknown_role", "delegation role 'missing-role' is unknown"),
        ("capability", "delegation capabilities do not match"),
        ("outside_source", "context route source is outside"),
        ("identity", "run_plan identity mismatch for goal_id"),
    ],
)
def test_pre_impl_rejects_unbounded_delegation_or_context(
    tmp_path: Path,
    mutation: str,
    reason: str,
) -> None:
    runtime, trigger, loaded, _package, _package_ref = _runtime(tmp_path)
    _state, prepared = _checkpoint(runtime, trigger, loaded)
    decision = _decision(prepared)
    if mutation == "unknown_role":
        decision["run_plan"]["delegation"][0]["preferred_role_refs"] = [
            "missing-role"
        ]
    elif mutation == "capability":
        decision["run_plan"]["delegation"][0]["capability_refs"] = [
            "reader"
        ]
    elif mutation == "outside_source":
        decision["run_plan"]["context_routes"][0]["required_sources"] = [{
            "ref": "artifacts/outside.json",
            "sha256": "f" * 64,
        }]
    else:
        decision["run_plan"]["identity"]["goal_id"] = "GOAL-OTHER"

    outcome = apply_orchestrator_agent_decision(
        runtime,
        _submit(runtime, prepared, decision),
    )

    assert outcome["status"] == "rejected"
    assert reason in outcome["reason"]
    assert not any(
        event.type == "orchestrator.run_plan.admitted"
        for event in runtime.event_log.read_all()
    )
