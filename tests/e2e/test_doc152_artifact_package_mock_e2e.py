"""Static artifact-package closure proof for design 152."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.call_result_runtime import (
    admit_runtime_call_result,
    mark_call_operation_started,
    prepare_call_operation,
)
from zf.runtime.plan_artifact_package import (
    PlanArtifactPackageError,
    admit_plan_artifact_package_for_payload,
    hydrate_plan_artifact_package,
    reduce_plan_artifact_packages,
)
from zf.runtime.product_delivery import ingest_task_map_to_kanban
from zf.runtime.run_contract import (
    stable_json_sha256,
    write_run_contract,
)
from zf.runtime.task_map_materialization import (
    commit_task_map_materialization,
    prepare_task_map_materialization,
)


WORKFLOW_RUN_ID = "run-doc152-mock"
TASK_ID = "DOC152-DELIVER-001"


def _write_json(project_root: Path, ref: str, payload: dict) -> str:
    path = project_root / ref
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return ref


def _task_map(*, generation: str, sentinel: str) -> dict:
    return {
        "schema_version": "task-map.v1",
        "feature_id": "DOC152-MOCK",
        "goal_claims": [{
            "goal_claim_id": f"CLAIM-{generation}",
            "text": sentinel,
            "mandatory": True,
        }],
        "tasks": [{
            "task_id": TASK_ID,
            "title": f"Deliver {sentinel}",
            "behavior": f"result.txt contains {sentinel}",
            "owner_role": "dev",
            "scope": ["result.txt"],
            "acceptance_criteria": [{
                "id": f"AC-{generation}",
                "text": f"result.txt contains {sentinel}",
            }],
            "goal_claim_ids": [f"CLAIM-{generation}"],
            "verification": f"grep -qx {sentinel} result.txt",
            "verification_tiers": ["runtime"],
        }],
    }


def _runtime(project_root: Path, state_dir: Path) -> SimpleNamespace:
    log = EventLog(state_dir / "events.jsonl")
    return SimpleNamespace(
        state_dir=state_dir,
        project_root=project_root,
        event_log=log,
        event_writer=EventWriter(log),
        config=SimpleNamespace(
            workflow=SimpleNamespace(
                flow_metadata={"result_protocol": {"mode": "blocking"}},
            ),
        ),
    )


def _package_payload(
    *,
    requirement_ref: str,
    task_map_ref: str,
    revision: str,
    generation: str,
    acceptance_matrix_ref: str = "",
    test_matrix_ref: str = "",
) -> dict:
    payload = {
        "workflow_run_id": WORKFLOW_RUN_ID,
        "stage_id": "prd-plan",
        "prd_ref": requirement_ref,
        "task_map_ref": task_map_ref,
        "plan_revision": revision,
        "task_map_generation": generation,
    }
    if acceptance_matrix_ref:
        payload["acceptance_matrix_ref"] = acceptance_matrix_ref
    if test_matrix_ref:
        payload["test_matrix_ref"] = test_matrix_ref
    return payload


def _admit_package(
    runtime: SimpleNamespace,
    *,
    payload: dict,
    source_event_id: str,
) -> dict:
    return admit_plan_artifact_package_for_payload(
        state_dir=runtime.state_dir,
        project_root=runtime.project_root,
        event_writer=runtime.event_writer,
        events=runtime.event_log.read_all(),
        payload=payload,
        workflow_run_id=WORKFLOW_RUN_ID,
        flow_kind="prd",
        producer_stage_id="prd-plan",
        goal_id="DOC152-MOCK",
        metadata={
            "artifact_package": {
                "mode": "blocking",
                "required_ports": [
                    "requirement_spec",
                    "goal_claim_set",
                    "task_map",
                    "planning_result",
                ],
            },
        },
        source_event_id=source_event_id,
        correlation_id=WORKFLOW_RUN_ID,
    )


def test_doc152_package_currentness_and_restart_closure(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    state_dir = project_root / ".zf-doc152"
    state_dir.mkdir(parents=True)
    requirement_ref = _write_json(project_root, "inputs/requirement.json", {
        "schema_version": "requirement-spec.v1",
        "acceptance": [{"id": "GOAL-152", "text": "deliver current sentinel"}],
    })
    acceptance_matrix_ref = _write_json(
        project_root,
        "inputs/acceptance-matrix.json",
        {"schema_version": "acceptance-matrix.v1", "rows": ["GOAL-152"]},
    )
    test_matrix_ref = _write_json(
        project_root,
        "inputs/test-matrix.json",
        {"schema_version": "test-matrix.v1", "commands": ["grep result.txt"]},
    )
    task_map_r0 = _task_map(generation="G0", sentinel="baseline")
    task_map_r2 = _task_map(generation="G2", sentinel="current-r2")
    task_map_r0_ref = _write_json(
        project_root, "inputs/task-map-r0.json", task_map_r0,
    )
    task_map_r2_ref = _write_json(
        project_root, "inputs/task-map-r2.json", task_map_r2,
    )
    contract_body = {
        "schema_version": "run-contract.v1",
        "workflow": {"kind": "prd"},
        "project": {"root": str(project_root), "state_dir": str(state_dir)},
    }
    contract_body["contract_digest"] = stable_json_sha256(contract_body)
    write_run_contract(state_dir, contract_body)
    runtime = _runtime(project_root, state_dir)

    baseline = _admit_package(
        runtime,
        payload=_package_payload(
            requirement_ref=requirement_ref,
            task_map_ref=task_map_r0_ref,
            revision="R0",
            generation="G0",
            acceptance_matrix_ref=acceptance_matrix_ref,
            test_matrix_ref=test_matrix_ref,
        ),
        source_event_id="evt-plan-r0",
    )
    old_call_payload = {
        "workflow_run_id": WORKFLOW_RUN_ID,
        "task_id": TASK_ID,
        "fanout_id": "fanout-r0",
        "child_id": "impl-r0",
        "stage_id": "prd-impl",
        "role_instance": "dev-lane-0",
        "task_map_generation": "G0",
        **{
            key: baseline[key]
            for key in (
                "plan_artifact_package_id",
                "plan_artifact_package_ref",
                "plan_artifact_package_digest",
                "run_contract_ref",
                "run_contract_digest",
            )
        },
    }
    old_operation = prepare_call_operation(
        runtime,
        payload=old_call_payload,
        operation_type="fanout_writer_child",
        operation_key="impl-r0",
        stage_id="prd-impl",
        task_id=TASK_ID,
        dispatch_id="attempt-r0",
        correlation_id=WORKFLOW_RUN_ID,
    )
    mark_call_operation_started(
        runtime,
        old_operation,
        task_id=TASK_ID,
        dispatch_id="attempt-r0",
        correlation_id=WORKFLOW_RUN_ID,
    )

    with pytest.raises(PlanArtifactPackageError):
        _admit_package(
            runtime,
            payload=_package_payload(
                requirement_ref=requirement_ref,
                task_map_ref="inputs/missing-task-map-r1.json",
                revision="R1",
                generation="G1",
            ),
            source_event_id="evt-plan-r1",
        )

    current = _admit_package(
        runtime,
        payload=_package_payload(
            requirement_ref=requirement_ref,
            task_map_ref=task_map_r2_ref,
            revision="R2",
            generation="G2",
        ),
        source_event_id="evt-plan-r2",
    )
    reduced = reduce_plan_artifact_packages(
        runtime.event_log.read_all(),
        workflow_run_id=WORKFLOW_RUN_ID,
    )
    assert reduced["current"]["plan_revision"] == "R2"
    assert [item["plan_revision"] for item in reduced["rejected"]] == ["R1"]
    package = hydrate_plan_artifact_package(
        state_dir,
        {
            "ref": current["plan_artifact_package_ref"],
            "sha256": current["plan_artifact_package_digest"],
        },
    )
    inherited = {
        item["logical_name"]: item
        for item in package["inherited"]
    }
    assert set(inherited) >= {"acceptance_matrix", "test_matrix"}
    assert {
        inherited["acceptance_matrix"]["source_package_ref"],
        inherited["test_matrix"]["source_package_ref"],
    } == {baseline["plan_artifact_package_ref"]}
    events = runtime.event_log.read_all()
    claim_index = next(
        index for index, event in enumerate(events)
        if event.type == "goal.claim_set.pinned"
        and event.payload.get("task_map_generation") == "G2"
    )
    package_index = next(
        index for index, event in enumerate(events)
        if event.type == "plan.artifact_package.admitted"
        and event.payload.get("plan_revision") == "R2"
    )
    assert claim_index < package_index

    delivery = ingest_task_map_to_kanban(
        state_dir,
        task_map_r2,
        source_refs={
            "task_map_ref": task_map_r2_ref,
            "plan_artifact_package_id": current["plan_artifact_package_id"],
            "plan_artifact_package_ref": current["plan_artifact_package_ref"],
            "plan_artifact_package_digest": current["plan_artifact_package_digest"],
            "goal_claim_set_ref": current["goal_claim_set_ref"],
            "goal_claim_set_digest": current["goal_claim_set_digest"],
            "run_contract_ref": current["run_contract_ref"],
            "run_contract_digest": current["run_contract_digest"],
        },
        task_map_ref=task_map_r2_ref,
        writer=runtime.event_writer,
        actor="orchestrator",
        correlation_id=WORKFLOW_RUN_ID,
    )
    assert delivery.passed is True
    task = TaskStore(state_dir / "kanban.json").get(TASK_ID)
    assert task is not None
    task_sources = task.contract.evidence_contract["source_refs"]
    assert task_sources["plan_artifact_package_id"] == current[
        "plan_artifact_package_id"
    ]
    assert task_sources["plan_artifact_package_ref"] == current[
        "plan_artifact_package_ref"
    ]
    assert task.contract.goal_claim_ids == ["CLAIM-G2"]

    stale_result = admit_runtime_call_result(
        runtime,
        ZfEvent(
            type="dev.build.done",
            actor="dev-lane-0",
            task_id=TASK_ID,
            correlation_id=WORKFLOW_RUN_ID,
            payload={
                **old_call_payload,
                "source_commit": "commit-r0",
                "target_commit": "commit-r0",
                "task_ref": f"refs/zf/tasks/{TASK_ID}",
                "summary": "late obsolete implementation",
            },
        ),
        mode="blocking",
        dispatch_correction=False,
    )
    assert stale_result.status == "superseded"
    assert "stale_plan_artifact_package" in {
        issue["code"] for issue in stale_result.issues
    }

    current_call_payload = {
        **old_call_payload,
        "fanout_id": "fanout-r2",
        "child_id": "impl-r2",
        "task_map_generation": "G2",
        **{
            key: current[key]
            for key in (
                "plan_artifact_package_id",
                "plan_artifact_package_ref",
                "plan_artifact_package_digest",
                "run_contract_ref",
                "run_contract_digest",
            )
        },
    }
    current_operation = prepare_call_operation(
        runtime,
        payload=current_call_payload,
        operation_type="fanout_writer_child",
        operation_key="impl-r2",
        stage_id="prd-impl",
        task_id=TASK_ID,
        dispatch_id="attempt-r2",
        correlation_id=WORKFLOW_RUN_ID,
    )
    mark_call_operation_started(
        runtime,
        current_operation,
        task_id=TASK_ID,
        dispatch_id="attempt-r2",
        correlation_id=WORKFLOW_RUN_ID,
    )
    admitted = admit_runtime_call_result(
        runtime,
        ZfEvent(
            type="dev.build.done",
            actor="dev-lane-0",
            task_id=TASK_ID,
            correlation_id=WORKFLOW_RUN_ID,
            payload={
                **current_call_payload,
                "source_commit": "commit-r2",
                "target_commit": "commit-r2",
                "task_ref": f"refs/zf/tasks/{TASK_ID}",
                "summary": "current implementation",
            },
        ),
        mode="blocking",
        dispatch_correction=False,
    )
    assert admitted.admitted is True

    recovery_tasks = [
        Task(
            id="DOC152-RECOVER-A",
            title="recover A",
            contract=TaskContract(behavior="A", verification="true"),
        ),
        Task(
            id="DOC152-RECOVER-B",
            title="recover B",
            blocked_by=["DOC152-RECOVER-A"],
            contract=TaskContract(behavior="B", verification="true"),
        ),
    ]
    plan, descriptor = prepare_task_map_materialization(
        state_dir=state_dir,
        tasks=recovery_tasks,
        task_map_ref=task_map_r2_ref,
        package_ref=current["plan_artifact_package_ref"],
        package_digest=current["plan_artifact_package_digest"],
        writer=runtime.event_writer,
        correlation_id=WORKFLOW_RUN_ID,
    )
    with pytest.raises(RuntimeError, match="injected materialization fault"):
        commit_task_map_materialization(
            state_dir=state_dir,
            plan=plan,
            descriptor=descriptor,
            writer=runtime.event_writer,
            fail_after_store_write=True,
        )

    restarted = _runtime(project_root, state_dir)
    closed = commit_task_map_materialization(
        state_dir=state_dir,
        plan=plan,
        descriptor=descriptor,
        writer=restarted.event_writer,
        correlation_id=WORKFLOW_RUN_ID,
    )
    assert closed["status"] == "committed"
    assert set(closed["task_ids"]) == {"DOC152-RECOVER-A", "DOC152-RECOVER-B"}
    restarted_reducer = reduce_plan_artifact_packages(
        restarted.event_log.read_all(),
        workflow_run_id=WORKFLOW_RUN_ID,
    )
    assert restarted_reducer["current"]["package_digest"] == current[
        "plan_artifact_package_digest"
    ]
    assert sum(
        event.type == "task_map.materialization.committed"
        and event.payload.get("materialization_plan_digest") == descriptor["sha256"]
        for event in restarted.event_log.read_all()
    ) == 1
