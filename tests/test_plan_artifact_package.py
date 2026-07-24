from __future__ import annotations

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.plan_artifact_package import (
    PlanArtifactPackageError,
    build_plan_artifact_package,
    hydrate_plan_artifact_package,
    package_event_payload,
    reduce_plan_artifact_packages,
    write_plan_artifact_package,
)
from zf.runtime.run_contract import write_run_contract_snapshot


def _fixture_port(tmp_path, name, value):
    descriptor = write_immutable_json_sidecar(
        tmp_path,
        {"schema_version": f"{name}.v1", "value": value},
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


def _run_contract(tmp_path):
    contract = {
        "schema_version": "run-contract.v1",
        "workflow": {"kind": "prd"},
        "contract_digest": "",
    }
    from zf.runtime.run_contract import stable_json_sha256

    contract["contract_digest"] = stable_json_sha256({
        "schema_version": "run-contract.v1",
        "workflow": {"kind": "prd"},
    })
    return write_run_contract_snapshot(tmp_path, contract)


def _package(tmp_path, *, revision="r1", generation="g1", stage="plan-a", inherited=()):
    ports = [
        _fixture_port(tmp_path, "requirement_spec", revision),
        _fixture_port(tmp_path, "goal_claim_set", revision),
        _fixture_port(tmp_path, "task_map", revision),
        _fixture_port(tmp_path, "planning_result", revision),
    ]
    return build_plan_artifact_package(
        workflow_run_id="run-1",
        flow_kind="prd",
        producer_stage_id=stage,
        run_contract=_run_contract(tmp_path),
        plan_revision=revision,
        task_map_generation=generation,
        produced=ports,
        inherited=inherited,
        required_ports=[
            "requirement_spec",
            "goal_claim_set",
            "task_map",
            "planning_result",
        ],
    )


def test_package_body_is_immutable_and_hydrates_all_ports(tmp_path):
    package = _package(tmp_path)
    descriptor = write_plan_artifact_package(tmp_path, package)

    assert "package_id" not in package
    assert "status" not in package
    assert descriptor["package_id"].startswith("planpkg-")
    assert hydrate_plan_artifact_package(tmp_path, descriptor) == package
    assert write_plan_artifact_package(tmp_path, package)["ref"] == descriptor["ref"]


def test_reducer_uses_run_and_slot_not_producer_stage(tmp_path):
    first = _package(tmp_path, stage="plan-a")
    first_ref = write_plan_artifact_package(tmp_path, first)
    second = _package(tmp_path, revision="r2", generation="g2", stage="plan-b")
    second_ref = write_plan_artifact_package(tmp_path, second)
    events = [
        ZfEvent(
            type="plan.artifact_package.admitted",
            payload=package_event_payload(first, first_ref, status="admitted"),
        ),
        ZfEvent(
            type="plan.artifact_package.admitted",
            payload=package_event_payload(second, second_ref, status="admitted"),
        ),
    ]

    reduced = reduce_plan_artifact_packages(events, workflow_run_id="run-1")

    assert reduced["current"]["producer_stage_id"] == "plan-b"
    assert reduced["current"]["package_digest"] == second_ref["sha256"]
    assert reduced["history"][0]["package_digest"] == first_ref["sha256"]


def test_reducer_rejects_same_revision_with_different_digest(tmp_path):
    first = _package(tmp_path, revision="same", generation="g1")
    second = _package(tmp_path, revision="same", generation="g2")
    first_ref = write_plan_artifact_package(tmp_path, first)
    second_ref = write_plan_artifact_package(tmp_path, second)

    with pytest.raises(PlanArtifactPackageError, match="conflicting admitted"):
        reduce_plan_artifact_packages(
            [
                ZfEvent(
                    type="plan.artifact_package.admitted",
                    payload=package_event_payload(first, first_ref, status="admitted"),
                ),
                ZfEvent(
                    type="plan.artifact_package.admitted",
                    payload=package_event_payload(second, second_ref, status="admitted"),
                ),
            ],
            workflow_run_id="run-1",
        )


def test_inherited_port_requires_source_package_identity(tmp_path):
    inherited = _fixture_port(tmp_path, "acceptance_matrix", "matrix")
    with pytest.raises(PlanArtifactPackageError, match="source package identity"):
        build_plan_artifact_package(
            workflow_run_id="run-1",
            flow_kind="prd",
            producer_stage_id="prd-plan",
            run_contract=_run_contract(tmp_path),
            plan_revision="r2",
            task_map_generation="g2",
            produced=[
                _fixture_port(tmp_path, "requirement_spec", "r2"),
                _fixture_port(tmp_path, "goal_claim_set", "r2"),
                _fixture_port(tmp_path, "task_map", "r2"),
                _fixture_port(tmp_path, "planning_result", "r2"),
            ],
            inherited=[inherited],
        )
