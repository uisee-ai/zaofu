from __future__ import annotations

from pathlib import Path

from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    WorkflowConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.measure_loop_projection import build_measure_loop_projection
from zf.runtime.task_pipeline_projection import build_task_pipeline_projection
from zf.web.projections.events import _trace_detail
from zf.web.projections.workflow_graph import _workflow_graph


def _event(
    event_id: str,
    event_type: str,
    *,
    task_id: str = "TASK-A",
    payload: dict | None = None,
) -> ZfEvent:
    return ZfEvent(
        id=event_id,
        type=event_type,
        ts=f"2026-08-03T00:00:{len(event_id):02d}+00:00",
        origin="kernel",
        task_id=task_id or None,
        payload=payload or {},
        correlation_id="run-1",
    )


def _policy() -> dict:
    worker = {"role": "impl-2", "capabilities": []}
    return {
        "profile_id": "task-pipeline-v4",
        "profile_digest": "profile-sha",
        "mode": "blocking",
        "max_active_task_pipelines": 2,
        "max_rework_attempts": 2,
        "pools": {
            "impl": {"capacity": 1, "worker_profiles": [worker]},
            "verify": {"capacity": 1, "worker_profiles": [worker]},
        },
        "backpressure": {
            "max_unverified_tasks": 2,
            "max_integration_queue": 2,
        },
        "integration_admission": {"default": "verify_admitted"},
    }


def _generation() -> ZfEvent:
    return _event(
        "generation",
        "task.pipeline.generation.admitted",
        task_id="",
        payload={
            "schema_version": "task-pipeline-generation.v1",
            "generation_id": "generation-1",
            "workflow_run_id": "run-1",
            "task_map_generation": "map-g1",
            "dispatch_base_commit": "base-sha",
            "task_ids": ["TASK-A"],
        },
    )


def _operation_events(*, terminal: bool = False) -> list[ZfEvent]:
    request = {
        "workflow_run_id": "run-1",
        "task_id": "TASK-A",
        "operation_id": "op-a-impl-1",
        "operation_type": "task-stage",
        "parent_stage_id": "impl",
        "request_hash": "request-sha",
        "task_pipeline_stage": "impl",
        "operation_generation": 1,
        "task_map_generation": "map-g1",
        "workspace_generation": 1,
        "placement_epoch": 2,
        "task_stage_session_binding": "binding-a-impl",
        "role_instance": "impl-2",
    }
    rows = [
        _event("operation-requested", "workflow.operation.requested", payload=request),
        _event(
            "operation-started",
            "workflow.operation.started",
            payload={
                "workflow_run_id": "run-1",
                "task_id": "TASK-A",
                "operation_id": "op-a-impl-1",
                "request_hash": "request-sha",
                "role_instance": "impl-2",
                "active_attempt_id": "attempt-a-1",
            },
        ),
    ]
    if terminal:
        rows.append(_event(
            "operation-settled",
            "workflow.operation.settled",
            payload={
                "workflow_run_id": "run-1",
                "task_id": "TASK-A",
                "operation_id": "op-a-impl-1",
                "request_hash": "request-sha",
                "admitted_call_result_ref": {
                    "ref": "artifacts/result.json",
                    "sha256": "result-sha",
                },
            },
        ))
    return rows


def _attempt(status: str) -> dict:
    return {
        "attempt_id": "attempt-a-1",
        "lease_id": "lease-a-1",
        "run_id": "run-1",
        "task_id": "TASK-A",
        "operation_id": "op-a-impl-1",
        "identity_version": "operation-v2",
        "dispatch_id": "dispatch-a-1",
        "role": "impl",
        "instance_id": "impl-2",
        "placement_epoch": 2,
        "ordinal": 1,
        "series": 1,
        "status": status,
    }


def _binding(status: str) -> dict:
    return {
        "binding_key": "binding-a-impl",
        "workflow_run_id": "run-1",
        "task_id": "TASK-A",
        "stage": "impl",
        "rework_affinity_id": "map-g1:impl",
        "session_id": "session-a-impl",
        "status": status,
        "current_role_instance": "impl-2",
        "current_placement_epoch": 2,
        "workspace_generation": 1,
        "placement_history": [{
            "placement_epoch": 1,
            "role_instance": "impl-1",
            "workspace_generation": 1,
        }, {
            "placement_epoch": 2,
            "role_instance": "impl-2",
            "workspace_generation": 1,
        }],
    }


def test_projection_replay_is_stable_and_worker_comes_from_operation() -> None:
    task = Task(
        id="TASK-A",
        title="Task A",
        status="in_progress",
        assigned_to="legacy-lane-9",
    )
    events = [_generation(), *_operation_events()]
    inputs = {
        "policy": _policy(),
        "tasks": [task],
        "events": events,
        "attempts": [_attempt("sent")],
        "session_bindings": {"binding-a-impl": _binding("active")},
    }

    first = build_task_pipeline_projection(**inputs)
    replay = build_task_pipeline_projection(**inputs)

    assert replay == first
    assert replay["projection_digest"] == first["projection_digest"]
    projected = first["tasks"][0]
    assert projected["current_worker"] == "impl-2"
    assert projected["current_worker_source"] == "workflow_operation"
    assert "legacy-lane-9" not in str(first)
    assert first["closure"]["status"] == "running"
    assert first["closure"]["active_operation_ids"] == ["op-a-impl-1"]
    assert first["sessions"][0]["placement_history"][0][
        "role_instance"
    ] == "impl-1"


def test_projection_prefers_current_task_over_archived_duplicate() -> None:
    current = Task(
        id="TASK-A",
        title="current external gate",
        status="backlog",
        contract=TaskContract(
            evidence_contract={
                "required_manual_evidence": "/tmp/ac8.json",
            },
            acceptance_criteria=[{
                "id": "AC8",
                "mandatory": True,
                "verification_owner": "human",
                "verification_tier": "manual_evidence",
            }],
        ),
    )
    archived = Task(
        id="TASK-A",
        title="stale archived copy",
        status="done",
    )

    projection = build_task_pipeline_projection(
        policy=_policy(),
        tasks=[current, archived],
        events=[_generation()],
    )

    assert projection["tasks"][0]["task_status"] == "backlog"
    assert projection["tasks"][0]["pipeline_stage"] == "external_gate_waiting"
    assert projection["queues"]["external_gate_waiting"] == ["TASK-A"]


def test_projection_distinguishes_task_run_and_operation_after_recovery_request():
    task = Task(id="TASK-A", title="Task A", status="in_progress")
    events = [_event("goal-start", "run.goal.started", payload={"run_id": "run-1"}),
              _generation(), *_operation_events(terminal=True),
              _event("goal-blocked", "run.goal.blocked", payload={"run_id": "run-1"}),
              _event("repair-applied", "run.manager.action.verify.passed",
                     payload={"workflow_run_id": "run-1"})]
    projection = build_task_pipeline_projection(policy=_policy(), tasks=[task], events=events)
    row = projection["tasks"][0]
    assert row["task_status"] == "in_progress"
    assert row["run_status"] == "blocked"
    assert row["run_status_event_id"] == "goal-blocked"
    assert row["last_operation_status"] == "settled"
    assert row["last_operation_event_id"] == "operation-settled"
    assert row["active_operation_count"] == 0
    assert not row["current_worker"]
    events.append(_event("goal-resumed", "run.goal.updated",
                         payload={"run_id": "run-1", "status": "active"}))
    resumed = build_task_pipeline_projection(policy=_policy(), tasks=[task], events=events)
    assert resumed["tasks"][0]["run_status"] == "active"
    assert task.status == "in_progress"  # Projection never updates TaskStore.


def test_projection_combines_concurrent_flow_policy_partitions() -> None:
    issue_policy = _policy()
    issue_policy.update({
        "profile_id": "issue-v4",
        "profile_digest": "i" * 64,
    })
    issue_policy["pools"]["impl"] = {
        "capacity": 1,
        "role_instances": ["fix-1"],
    }
    prd_policy = _policy()
    prd_policy.update({
        "profile_id": "prd-v4",
        "profile_digest": "p" * 64,
    })
    prd_policy["pools"]["impl"] = {
        "capacity": 1,
        "role_instances": ["dev-1"],
    }
    events = [
        _event(
            "generation-issue",
            "task.pipeline.generation.admitted",
            task_id="",
            payload={
                "schema_version": "task-pipeline-generation.v1",
                "generation_id": "generation-issue",
                "workflow_run_id": "run-issue",
                "task_map_generation": "map-issue",
                "flow_kind": "issue",
                "profile_id": "issue-v4",
                "profile_digest": "i" * 64,
                "task_ids": ["TASK-ISSUE"],
            },
        ),
        _event(
            "generation-prd",
            "task.pipeline.generation.admitted",
            task_id="",
            payload={
                "schema_version": "task-pipeline-generation.v1",
                "generation_id": "generation-prd",
                "workflow_run_id": "run-prd",
                "task_map_generation": "map-prd",
                "flow_kind": "prd",
                "profile_id": "prd-v4",
                "profile_digest": "p" * 64,
                "task_ids": ["TASK-PRD"],
            },
        ),
    ]

    projection = build_task_pipeline_projection(
        policy=None,
        policy_by_task={
            "TASK-ISSUE": issue_policy,
            "TASK-PRD": prd_policy,
        },
        tasks=[
            Task(id="TASK-ISSUE", title="Issue", status="backlog"),
            Task(id="TASK-PRD", title="PRD", status="backlog"),
        ],
        events=events,
    )

    assert projection["enabled"] is True
    assert projection["mode"] == "mixed"
    assert [row["profile_id"] for row in projection["policy_partitions"]] == [
        "issue-v4",
        "prd-v4",
    ]
    assert {
        row["task_id"]: row["role_instance"]
        for row in projection["dispatchable"]["impl"]
    } == {
        "TASK-ISSUE": "fix-1",
        "TASK-PRD": "dev-1",
    }
    assert {row["pipeline_stage"] for row in projection["tasks"]} == {
        "impl_ready",
    }
    assert projection["capacity"]["max_active_task_pipelines"] == 4
    assert projection["capacity"]["available_task_pipelines"] == 4
    assert projection["capacity"]["pools"]["impl"]["capacity"] == 2
    assert projection["occupancy"]["pools"]["impl"] == 0


def test_projection_exposes_task_ref_rejection_as_impl_rework_ready() -> None:
    result = _event(
        "typed-impl-result",
        "dev.build.done",
        payload={
            "workflow_run_id": "run-1",
            "task_map_generation": "map-g1",
            "task_pipeline_stage": "impl",
            "operation_generation": 1,
            "operation_id": "op-a-impl-1",
            "source_commit": "a" * 40,
        },
    )
    events = [
        _generation(),
        *_operation_events(),
        _event(
            "result-admitted",
            "workflow.call.result.admitted",
            payload={
                "workflow_run_id": "run-1",
                "task_id": "TASK-A",
                "operation_id": "op-a-impl-1",
                "request_hash": "request-sha",
                "semantic_verdict": "passed",
            },
        ),
        _event(
            "operation-settled",
            "workflow.operation.settled",
            payload={
                "workflow_run_id": "run-1",
                "task_id": "TASK-A",
                "operation_id": "op-a-impl-1",
                "request_hash": "request-sha",
                "admitted_call_result_ref": {
                    "ref": "artifacts/result.json",
                    "sha256": "result-sha",
                },
            },
        ),
        result,
        _event(
            "task-ref-rejected",
            "task.ref.rejected",
            payload={
                "trigger_event_id": result.id,
                "reason": "source commit changes outside task scope",
                "out_of_scope_files": ["app/server.test.mjs"],
            },
        ),
    ]

    policy = _policy()
    policy["pools"]["impl"]["role_instances"] = ["impl-2"]
    projection = build_task_pipeline_projection(
        policy=policy,
        tasks=[Task(id="TASK-A", title="Task A", status="in_progress")],
        events=events,
    )

    assert projection["tasks"][0]["pipeline_stage"] == "impl_rework_ready"
    assignment = projection["dispatchable"]["impl"][0]
    assert assignment["operation_generation"] == "2"
    assert assignment["impl_rework_request"]["event_id"] == (
        "task-ref-rejected"
    )


def test_terminal_projection_requires_no_active_residual_and_exact_freeze() -> None:
    task = Task(id="TASK-A", title="Task A", status="done")
    events = [
        _generation(),
        *_operation_events(terminal=True),
        _event(
            "candidate-ready",
            "candidate.ready",
            task_id="",
            payload={
                "workflow_run_id": "run-1",
                "task_map_generation": "map-g1",
                "candidate_generation": "candidate-g1",
                "candidate_head": "candidate-sha",
                "freeze_id": "freeze-1",
                "freeze_receipt_digest": "freeze-sha",
            },
        ),
    ]

    projection = build_task_pipeline_projection(
        policy=_policy(),
        tasks=[task],
        events=events,
        attempts=[_attempt("succeeded")],
        session_bindings={"binding-a-impl": _binding("archived")},
    )

    assert projection["tasks"][0]["pipeline_stage"] == "done"
    assert projection["tasks"][0]["current_worker"] == ""
    assert projection["operations"][0]["current_worker"] == ""
    assert projection["closure"] == {
        "status": "converged",
        "terminal_expected": True,
        "converged": True,
        "active_operation_ids": [],
        "active_attempt_ids": [],
        "active_session_binding_keys": [],
        "missing_candidate_freeze_generation_ids": [],
        "residuals": [],
    }


def test_trace_graph_and_loop_share_task_pipeline_operation_identity(
    tmp_path: Path,
) -> None:
    (tmp_path / "kanban.json").write_text("[]\n", encoding="utf-8")
    task = Task(
        id="TASK-A",
        title="Task A",
        status="in_progress",
        assigned_to="legacy-lane-9",
        contract=TaskContract(feature_id="F-1"),
    )
    TaskStore(tmp_path / "kanban.json").add(task)
    log = EventLog(tmp_path / "events.jsonl")
    for event in [_generation(), *_operation_events()]:
        log.append(event)
    config = ZfConfig(
        project=ProjectConfig(name="pipeline-projection", state_dir=str(tmp_path)),
        roles=[RoleConfig(
            name="impl",
            instance_id="impl-2",
            backend="mock",
        )],
        workflow=WorkflowConfig(flow_metadata={"task_pipeline": _policy()}),
    )

    trace = _trace_detail(tmp_path, "run-1", config=config)
    graph = _workflow_graph(tmp_path, config=config, force_recompute=True)
    loop = build_measure_loop_projection(
        tmp_path,
        config=config,
        project_root=tmp_path,
        project_id="project-1",
        feature_id="F-1",
        generated_at="2026-08-03T00:00:00+00:00",
    )

    assert trace["task_pipeline"]["operations"][0]["operation_id"] == (
        "op-a-impl-1"
    )
    assert trace["task_pipeline"]["operations"][0]["current_worker"] == (
        "impl-2"
    )
    assert graph["task_pipeline"]["projection_digest"]
    graph_task = next(
        node for node in graph["nodes"]
        if node["id"] == "task-pipeline-task:TASK-A"
    )
    assert graph_task["current_worker"] == "impl-2"
    assert "legacy-lane-9" not in str(graph["task_pipeline"])
    assert loop["task_pipeline"]["operation_ids"] == ["op-a-impl-1"]
    assert "task-pipeline-projection.v1" in loop["source_projection_refs"]
