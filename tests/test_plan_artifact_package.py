from __future__ import annotations

import json
from types import SimpleNamespace

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
    plan_artifact_package_binding,
    prepare_plan_artifact_package,
    reduce_plan_artifact_packages,
    required_plan_ports,
    write_plan_artifact_package,
)
from zf.runtime.plan_artifact_package_runtime import (
    admit_synthesized_plan_package,
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


def test_package_binding_recovers_generation_pinned_goal_claim_set() -> None:
    package = {
        "workflow_run_id": "run-1",
        "task_map_generation": "generation-1",
        "package_id": "planpkg-current",
        "package_ref": "artifacts/plan-packages/current.json",
        "package_digest": "package-digest",
    }
    events = [ZfEvent(
        type="goal.claim_set.pinned",
        payload={
            "workflow_run_id": "run-1",
            "task_map_generation": "generation-1",
            "goal_claim_set_ref": "artifacts/claims/current.json",
            "goal_claim_set_digest": "claims-digest",
        },
    )]

    assert plan_artifact_package_binding(events, package) == {
        "task_map_generation": "generation-1",
        "plan_artifact_package_id": "planpkg-current",
        "plan_artifact_package_ref": "artifacts/plan-packages/current.json",
        "plan_artifact_package_digest": "package-digest",
        "goal_claim_set_ref": "artifacts/claims/current.json",
        "goal_claim_set_digest": "claims-digest",
    }


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


def test_refactor_plan_synth_admits_package_before_bridge(monkeypatch) -> None:
    admitted = []

    def _fake_admit(runtime, **kwargs):
        admitted.append(kwargs)
        return {
            "plan_artifact_package_id": "planpkg-refactor",
            "plan_artifact_package_ref": "artifacts/package.json",
            "plan_artifact_package_digest": "package-sha",
        }

    monkeypatch.setattr(
        "zf.runtime.plan_artifact_package_runtime._admit",
        _fake_admit,
    )
    final_status, recommendation, payload = admit_synthesized_plan_package(
        SimpleNamespace(config=SimpleNamespace(workflow=SimpleNamespace())),
        event=ZfEvent(
            type="fanout.synth.completed",
            correlation_id="refactor-run",
        ),
        manifest={"pdd_id": "REFACTOR-1"},
        stage_id="flow-plan",
        trace_id="refactor-run",
        success_event="zaofu.refactor.plan.ready",
        final_status="completed",
        recommendation="approve",
        artifact_payload={
            "flow_kind": "refactor",
            "goal_id": "refactor-run",
            "task_map_ref": "artifacts/task-map.json",
        },
    )

    assert final_status == "completed"
    assert recommendation == "approve"
    assert payload["plan_artifact_package_id"] == "planpkg-refactor"
    assert admitted[0]["producer_stage_id"] == "flow-plan"
    assert admitted[0]["workflow_run_id"] == "refactor-run"
    assert admitted[0]["goal_id"] == "refactor-run"


def test_plan_package_rejects_critic_digest_from_another_candidate() -> None:
    runtime = SimpleNamespace(
        config=SimpleNamespace(workflow=SimpleNamespace()),
        _contract_failure_payload=lambda payload, message: {
            **payload,
            "contract_failure": message,
        },
    )

    status, recommendation, payload = admit_synthesized_plan_package(
        runtime,
        event=ZfEvent(
            type="fanout.synth.completed",
            payload={
                "plan_synth_contract_digest": "a" * 64,
                "reviewed_plan_candidate_digest": "b" * 64,
            },
        ),
        manifest={"pdd_id": "PRD-1"},
        stage_id="prd-plan",
        trace_id="run-plan-digest",
        success_event="task_map.ready",
        final_status="completed",
        recommendation="approve",
        artifact_payload={"task_map_ref": "artifacts/task-map.json"},
    )

    assert status == "failed"
    assert recommendation == "reject"
    assert payload["failure_class"] == "plan_candidate_review_identity"
    assert payload["attempt_domain"] == "protocol_repair"
    assert payload["semantic_rework_cost"] == 0


def test_replan_synth_drops_inherited_package_identity_for_new_task_map(
    monkeypatch,
) -> None:
    admitted = []

    def _fake_admit(runtime, **kwargs):
        admitted.append(kwargs)
        return {
            "plan_artifact_package_id": "planpkg-new",
            "plan_artifact_package_ref": "artifacts/package-new.json",
            "plan_artifact_package_digest": "package-new-sha",
            "task_map_generation": "generation-new",
        }

    monkeypatch.setattr(
        "zf.runtime.plan_artifact_package_runtime._admit",
        _fake_admit,
    )
    final_status, recommendation, payload = admit_synthesized_plan_package(
        SimpleNamespace(config=SimpleNamespace(workflow=SimpleNamespace())),
        event=ZfEvent(
            type="fanout.synth.completed",
            correlation_id="prd-run",
        ),
        manifest={
            "pdd_id": "PRD-1",
            "workflow_run_id": "prd-run",
            "trigger_payload": {
                "workflow_run_id": "prd-run",
                "task_map_ref": "artifacts/task-map-old.json",
                "task_map_generation": "generation-old",
                "plan_revision": "revision-old",
                "plan_artifact_package_id": "planpkg-old",
                "plan_artifact_package_ref": "artifacts/package-old.json",
                "plan_artifact_package_digest": "package-old-sha",
                "goal_claim_set_ref": "artifacts/claims-old.json",
                "goal_claim_set_digest": "claims-old-sha",
                "artifact_package_status": "admitted",
            },
        },
        stage_id="prd-plan",
        trace_id="stall:prd-run:prd-plan:fanout-old:event-old",
        success_event="task_map.ready",
        final_status="completed",
        recommendation="approve",
        artifact_payload={
            "flow_kind": "prd",
            "task_map_ref": "artifacts/task-map-new.json",
            # Flow identity projection can copy stale values into the result;
            # the changed immutable Task Map ref remains authoritative.
            "task_map_generation": "generation-old",
            "plan_artifact_package_id": "planpkg-old",
        },
    )

    assert final_status == "completed"
    assert recommendation == "approve"
    assert payload["plan_artifact_package_id"] == "planpkg-new"
    assert admitted[0]["workflow_run_id"] == "prd-run"
    admission_payload = admitted[0]["payload"]
    assert admission_payload["task_map_ref"] == "artifacts/task-map-new.json"
    for key in _TASK_MAP_GENERATION_IDENTITY_KEYS_FOR_TEST:
        assert key not in admission_payload


_TASK_MAP_GENERATION_IDENTITY_KEYS_FOR_TEST = (
    "task_map_generation",
    "task_map_digest",
    "plan_revision",
    "plan_artifact_package_id",
    "plan_artifact_package_ref",
    "plan_artifact_package_digest",
    "goal_claim_set_ref",
    "goal_claim_set_digest",
    "artifact_package_status",
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
            "acceptance_matrix_ref": "missing/child-worktree/acceptance.json",
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


def test_package_preparation_materializes_declared_exact_ref_port(tmp_path) -> None:
    project_root = tmp_path / "project"
    state_dir = project_root / ".zf"
    artifacts = state_dir / "artifacts"
    artifacts.mkdir(parents=True)
    requirement = artifacts / "requirement.md"
    requirement.write_text("Preserve behavior.\n", encoding="utf-8")
    review = artifacts / "review.md"
    review.write_text("# Review\n\nReady.\n", encoding="utf-8")
    task_map = artifacts / "task-map.json"
    task_map.write_text(
        json.dumps({
            "schema_version": "task-map.v1",
            "objective": "Preserve behavior",
            "required_plan_ports": [
                "requirement_spec",
                "goal_claim_set",
                "task_map",
                "planning_result",
                "review_artifact",
            ],
            "tasks": [{
                "task_id": "T1",
                "acceptance_criteria": ["Behavior is preserved"],
            }],
        }),
        encoding="utf-8",
    )
    contract_body = {
        "schema_version": "run-contract.v1",
        "workflow": {"kind": "refactor"},
    }
    write_run_contract(
        state_dir,
        {
            **contract_body,
            "contract_digest": stable_json_sha256(contract_body),
        },
    )

    package, _, _ = prepare_plan_artifact_package(
        state_dir=state_dir,
        project_root=project_root,
        events=[],
        payload={
            "task_map_ref": ".zf/artifacts/task-map.json",
            "requirement_spec_ref": ".zf/artifacts/requirement.md",
            "review_artifact_ref": ".zf/artifacts/review.md",
        },
        workflow_run_id="run-custom-port",
        flow_kind="refactor",
        producer_stage_id="refactor-plan",
        goal_id="GOAL-1",
        metadata={"artifact_package": {"mode": "blocking"}},
    )

    review_port = next(
        port for port in package["produced"]
        if port["logical_name"] == "review_artifact"
    )
    assert review_port["ref"] == "artifacts/review.md"
    assert review_port["sha256"]
    assert all(
        port["producer_stage_id"] == "refactor-plan"
        for port in package["produced"]
    )


def test_issue_requirement_snapshot_adapts_to_issue_spec_port(tmp_path) -> None:
    project_root = tmp_path / "project"
    state_dir = project_root / ".zf"
    artifacts = state_dir / "artifacts"
    artifacts.mkdir(parents=True)
    requirement = artifacts / "issue.json"
    requirement.write_text(
        json.dumps({"schema_version": "workflow-requirement.v1"}),
        encoding="utf-8",
    )
    task_map = artifacts / "task-map.json"
    task_map.write_text(
        json.dumps({
            "schema_version": "task-map.v1",
            "objective": "Fix portable E2E",
            "tasks": [{
                "task_id": "ISSUE-1",
                "acceptance_criteria": ["E2E runs in Docker"],
            }],
        }),
        encoding="utf-8",
    )
    contract_body = {
        "schema_version": "run-contract.v1",
        "workflow": {"kind": "issue"},
    }
    write_run_contract(
        state_dir,
        {
            **contract_body,
            "contract_digest": stable_json_sha256(contract_body),
        },
    )

    package, _, _ = prepare_plan_artifact_package(
        state_dir=state_dir,
        project_root=project_root,
        events=[],
        payload={
            "task_map_ref": ".zf/artifacts/task-map.json",
            "requirement_spec_ref": ".zf/artifacts/issue.json",
            "objective_ref": "HEAD",
            "issue_ref": "HEAD",
            "source_refs": {"source_ref": "HEAD"},
        },
        workflow_run_id="issue-run",
        flow_kind="issue",
        producer_stage_id="issue-triage",
        goal_id="ISSUE-1",
        metadata={"artifact_package": {"mode": "blocking"}},
    )

    assert "issue_spec" in package["required_ports"]
    issue_spec = next(
        port for port in package["produced"]
        if port["logical_name"] == "issue_spec"
    )
    assert issue_spec["source_logical_name"] == "requirement_spec"
    assert issue_spec["adapter_version"] == "issue-spec-requirement-adapter.v1"
    assert not any(port["ref"] == "HEAD" for port in package["produced"])


def test_replan_package_rejects_silent_mandatory_claim_loss(tmp_path) -> None:
    project_root = tmp_path / "project"
    state_dir = project_root / ".zf"
    artifacts = state_dir / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "requirement.md").write_text(
        "The command must remain available.\n",
        encoding="utf-8",
    )
    task_map_path = artifacts / "task-map-r1.json"
    first_map = {
        "schema_version": "task-map.v1",
        "goal_claims": [{
            "goal_claim_id": "CLAIM-KEEP",
            "text": "The command remains available.",
            "mandatory": True,
        }],
        "tasks": [{
            "task_id": "T1",
            "goal_claim_ids": ["CLAIM-KEEP"],
            "acceptance_criteria": [{
                "id": "AC-1",
                "text": "The command exits successfully.",
            }],
        }],
    }
    task_map_path.write_text(json.dumps(first_map), encoding="utf-8")
    contract = {
        "schema_version": "run-contract.v1",
        "workflow": {"kind": "prd"},
    }
    write_run_contract(
        state_dir,
        {**contract, "contract_digest": stable_json_sha256(contract)},
    )
    args = {
        "state_dir": state_dir,
        "project_root": project_root,
        "payload": {
            "task_map_ref": ".zf/artifacts/task-map-r1.json",
            "requirement_spec_ref": ".zf/artifacts/requirement.md",
        },
        "workflow_run_id": "run-claim-continuity",
        "flow_kind": "prd",
        "producer_stage_id": "prd-plan",
        "goal_id": "GOAL-CLAIM-CONTINUITY",
        "metadata": {"artifact_package": {"mode": "blocking"}},
    }
    first, descriptor, _claim = prepare_plan_artifact_package(
        events=[],
        **args,
    )
    admitted = ZfEvent(
        type="plan.artifact_package.admitted",
        correlation_id="run-claim-continuity",
        payload=package_event_payload(first, descriptor, status="admitted"),
    )
    second_map = dict(first_map)
    second_map["goal_claims"] = []
    second_path = artifacts / "task-map-r2.json"
    second_path.write_text(json.dumps(second_map), encoding="utf-8")
    second_args = {
        **args,
        "payload": {
            **args["payload"],
            "task_map_ref": ".zf/artifacts/task-map-r2.json",
        },
    }

    with pytest.raises(
        PlanArtifactPackageError,
        match="mandatory goal claim continuity failed: CLAIM-KEEP",
    ):
        prepare_plan_artifact_package(events=[admitted], **second_args)


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


def test_reducer_quarantines_package_from_aborted_plan_admission(tmp_path):
    first = _package(tmp_path, revision="r1", generation="g1")
    aborted = _package(tmp_path, revision="r1", generation="g2")
    first_ref = write_plan_artifact_package(tmp_path, first)
    aborted_ref = write_plan_artifact_package(tmp_path, aborted)
    reduced = reduce_plan_artifact_packages(
        [
            ZfEvent(
                id="evt-package-r1",
                type="plan.artifact_package.admitted",
                payload=package_event_payload(first, first_ref, status="admitted"),
            ),
            ZfEvent(
                id="evt-package-aborted",
                type="plan.artifact_package.admitted",
                payload={
                    **package_event_payload(aborted, aborted_ref, status="admitted"),
                    "source_event_id": "task-map-aborted",
                },
            ),
            ZfEvent(
                id="evt-admission-cancelled",
                type="fanout.cancelled",
                payload={
                    "trigger_event_id": "task-map-aborted",
                    "failure_scope": "plan_admission",
                },
            ),
        ],
        workflow_run_id="run-1",
    )

    assert reduced["current"]["package_digest"] == first_ref["sha256"]
    assert reduced["rejected"][0]["event_id"] == "evt-package-aborted"
    assert reduced["rejected"][0]["reason"] == "aborted_plan_admission"


def test_reducer_quarantines_late_conflicting_replay_of_same_generation(
    tmp_path,
):
    first = _package(tmp_path, revision="r1", generation="g1", stage="plan-a")
    second = _package(tmp_path, revision="r2", generation="g2", stage="plan-b")
    stale = _package(
        tmp_path,
        revision="r1",
        generation="g1",
        stage="plan-replay",
    )
    first_ref = write_plan_artifact_package(tmp_path, first)
    second_ref = write_plan_artifact_package(tmp_path, second)
    stale_ref = write_plan_artifact_package(tmp_path, stale)

    reduced = reduce_plan_artifact_packages(
        [
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
            ZfEvent(
                id="evt-package-r1-replay",
                type="plan.artifact_package.admitted",
                payload=package_event_payload(stale, stale_ref, status="admitted"),
            ),
        ],
        workflow_run_id="run-1",
    )

    assert reduced["current"]["package_digest"] == second_ref["sha256"]
    assert [row["package_digest"] for row in reduced["history"]] == [
        first_ref["sha256"],
    ]
    assert reduced["rejected"][0]["event_id"] == "evt-package-r1-replay"
    assert reduced["rejected"][0]["reason"] == (
        "immutable_generation_replay_conflict"
    )
    assert reduced["freshness"]["admitted_count"] == 2


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
