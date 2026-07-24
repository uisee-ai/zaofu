from __future__ import annotations

import json

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.artifact_query.service import ArtifactQueryService
from zf.runtime.artifact_query.store import projection_db_path
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.plan_artifact_package import (
    PlanArtifactPackageError,
    build_plan_artifact_package,
    hydrate_plan_artifact_package,
    package_event_payload,
    prepare_plan_artifact_package,
    reduce_plan_artifact_packages,
    required_plan_ports,
    write_plan_artifact_package,
)
from zf.runtime.run_contract import (
    stable_json_sha256,
    write_run_contract,
    write_run_contract_snapshot,
)


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


def test_task_map_required_ports_extend_profile_defaults_and_normalize_aliases() -> None:
    assert required_plan_ports(
        flow_kind="prd",
        metadata={"artifact_package": {"required_ports": ["task_map"]}},
        declared=["prd_ref", "acceptance_matrix"],
    ) == ["task_map", "requirement_spec", "acceptance_matrix"]
    with pytest.raises(PlanArtifactPackageError, match="must be a list"):
        required_plan_ports(flow_kind="prd", declared="task_map")
    with pytest.raises(PlanArtifactPackageError, match="duplicate"):
        required_plan_ports(
            flow_kind="prd",
            declared=["prd_ref", "requirement_spec"],
        )


def test_package_preparation_consumes_task_map_required_ports(tmp_path) -> None:
    project_root = tmp_path / "project"
    state_dir = project_root / ".zf"
    artifacts = state_dir / "artifacts"
    artifacts.mkdir(parents=True)
    requirement = artifacts / "requirement.md"
    requirement.write_text("Deliver AC-1.\n", encoding="utf-8")
    acceptance_matrix = artifacts / "acceptance-matrix.json"
    acceptance_matrix.write_text(
        json.dumps({
            "schema_version": "acceptance-matrix.v1",
            "status": "draft",
            "metadata": {
                "enrichment_contract": {
                    "status": "requires_scan_plan_enrichment",
                },
            },
            "rows": ["AC-1"],
        }),
        encoding="utf-8",
    )
    task_map = artifacts / "task-map.json"
    task_map.write_text(
        json.dumps({
            "schema_version": "task-map.v1",
            "objective": "Deliver AC-1",
            "required_plan_ports": [
                "requirement_spec",
                "goal_claim_set",
                "task_map",
                "planning_result",
                "acceptance_matrix",
            ],
            "tasks": [{
                "task_id": "T1",
                "acceptance_criteria": ["AC-1"],
            }],
        }),
        encoding="utf-8",
    )
    contract_body = {
        "schema_version": "run-contract.v1",
        "workflow": {"kind": "prd"},
    }
    write_run_contract(
        state_dir,
        {
            **contract_body,
            "contract_digest": stable_json_sha256(contract_body),
        },
    )

    base_payload = {
        "task_map_ref": ".zf/artifacts/task-map.json",
        "prd_ref": ".zf/artifacts/requirement.md",
        "acceptance_matrix_ref": ".zf/artifacts/acceptance-matrix.json",
    }
    with pytest.raises(PlanArtifactPackageError, match="is not ready"):
        prepare_plan_artifact_package(
            state_dir=state_dir,
            project_root=project_root,
            events=[],
            payload=base_payload,
            workflow_run_id="run-required-ports",
            flow_kind="prd",
            producer_stage_id="prd-plan",
            goal_id="GOAL-1",
            metadata={"artifact_package": {"mode": "blocking"}},
        )

    package, _, _ = prepare_plan_artifact_package(
        state_dir=state_dir,
        project_root=project_root,
        events=[],
        payload={
            **base_payload,
            "plan_ports": [{
                "logical_name": "acceptance_matrix",
                "schema_version": "acceptance-matrix.v1",
                "body": {
                    "schema_version": "acceptance-matrix.v1",
                    "status": "ready",
                    "metadata": {
                        "enrichment_contract": {"status": "fulfilled"},
                    },
                    "rows": ["AC-1"],
                },
            }],
        },
        workflow_run_id="run-required-ports",
        flow_kind="prd",
        producer_stage_id="prd-plan",
        goal_id="GOAL-1",
        metadata={"artifact_package": {"mode": "blocking"}},
    )

    assert package["required_ports"] == [
        "requirement_spec",
        "goal_claim_set",
        "task_map",
        "planning_result",
        "acceptance_matrix",
    ]
    acceptance_port = next(
        port for port in package["produced"]
        if port["logical_name"] == "acceptance_matrix"
    )
    assert acceptance_port["ref"].startswith("artifacts/plan-ports/")


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


def test_sqlite_advisory_projection_matches_canonical_reducer(tmp_path):
    first = _package(tmp_path, stage="plan-a")
    first_ref = write_plan_artifact_package(tmp_path, first)
    second = _package(
        tmp_path,
        revision="r2",
        generation="g2",
        stage="plan-b",
    )
    second_ref = write_plan_artifact_package(tmp_path, second)
    events = [
        ZfEvent(
            id="evt-package-r1",
            type="plan.artifact_package.admitted",
            payload=package_event_payload(first, first_ref, status="admitted"),
        ),
        ZfEvent(
            id="evt-package-r2",
            type="plan.artifact_package.admitted",
            payload=package_event_payload(second, second_ref, status="admitted"),
        ),
    ]
    log = EventLog(tmp_path / "events.jsonl")
    for event in events:
        log.append(event)
    canonical = reduce_plan_artifact_packages(
        events,
        workflow_run_id="run-1",
    )
    service = ArtifactQueryService(
        state_dir=tmp_path,
        project_root=tmp_path.parent,
    )

    advisory = service.plan_package_projection(
        "run-1",
        context=service.context(mode="advisory"),
    )
    assert advisory["current"] == canonical["current"]
    assert advisory["history"] == canonical["history"]
    assert advisory["is_derived_projection"] is True

    for path in projection_db_path(tmp_path).parent.glob("read_model.sqlite*"):
        path.unlink()
    replay = service.plan_package_projection(
        "run-1",
        context=service.context(mode="advisory"),
    )
    assert replay["current"] == canonical["current"]
    assert replay["history"] == canonical["history"]


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
