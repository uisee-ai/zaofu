from __future__ import annotations

import json
from pathlib import Path

from zf.core.config.schema import (
    ProjectConfig,
    QualityGateConfig,
    RoleConfig,
    SessionConfig,
    WorkflowConfig,
    WorkflowDagConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.workflow_resume import (
    TASK_REF_REPAIR_REQUESTED_EVENT,
    WORKFLOW_RESUME_APPLIED_EVENT,
    WORKFLOW_RESUME_PLANNED_EVENT,
    WorkflowBatchResumeCheckpoint,
    apply_workflow_resume,
    build_workflow_resume_checkpoints,
    build_workflow_resume_projection,
)
from zf.runtime.workflow_resume_apply import _apply_batch_checkpoint


def _state(tmp_path: Path) -> tuple[Path, TaskStore, EventLog]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    return state_dir, TaskStore(state_dir / "kanban.json"), EventLog(
        state_dir / "events.jsonl"
    )


def _lane_config() -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="resume-test"),
        session=SessionConfig(tmux_session="resume-test"),
        roles=[
            RoleConfig(
                name="dev-lane-3",
                backend="mock",
                publishes=["dev.build.done", "dev.failed"],
            ),
            RoleConfig(
                name="review-lane-3",
                backend="mock",
                triggers=["static_gate.passed"],
                publishes=["review.approved", "review.rejected"],
            ),
            RoleConfig(
                name="verify-lane-3",
                backend="mock",
                triggers=["review.approved"],
                publishes=["verify.passed", "verify.failed"],
            ),
        ],
        workflow=WorkflowConfig(
            dag=WorkflowDagConfig(
                enabled=True,
                graph_review_test_judge_reconcile=True,
            ),
            rework_routing={
                "review.rejected": "dev-lane-3",
                "verify.failed": "dev-lane-3",
            },
        ),
    )


def _gate_only_config() -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="resume-gate-only"),
        session=SessionConfig(tmux_session="resume-gate-only"),
        roles=[
            RoleConfig(
                name="dev-lane-3",
                backend="mock",
                publishes=["dev.build.done", "dev.failed"],
            ),
        ],
        workflow=WorkflowConfig(
            dag=WorkflowDagConfig(
                enabled=True,
                graph_static_gate_action=True,
            ),
        ),
        quality_gates={
            "static": QualityGateConfig(enabled=True, required_checks=["true"]),
        },
    )


def test_resume_dispatches_static_gate_to_same_review_lane(tmp_path: Path) -> None:
    state_dir, store, log = _state(tmp_path)
    store.add(Task(
        id="CJMIN-GATEWAY-001",
        title="gateway",
        status="in_progress",
        assigned_to="dev-lane-3",
    ))
    dev_done = ZfEvent(
        type="dev.build.done",
        actor="dev-lane-3",
        task_id="CJMIN-GATEWAY-001",
        payload={"dispatch_id": "disp-dev"},
    )
    gate = ZfEvent(
        type="static_gate.passed",
        actor="zf-cli",
        task_id="CJMIN-GATEWAY-001",
        payload={"trigger_event_id": dev_done.id},
    )
    log.append(ZfEvent(
        type="task.dispatched",
        actor="orchestrator",
        task_id="CJMIN-GATEWAY-001",
        payload={"assignee": "dev-lane-3", "dispatch_id": "disp-dev"},
    ))
    log.append(dev_done)
    log.append(ZfEvent(
        type="task.ref.updated",
        actor="zf-cli",
        task_id="CJMIN-GATEWAY-001",
        payload={"trigger_event_id": dev_done.id},
    ))
    log.append(gate)

    checkpoints = build_workflow_resume_checkpoints(state_dir, _lane_config())
    result = apply_workflow_resume(state_dir, _lane_config())

    task = store.get("CJMIN-GATEWAY-001")
    events = log.read_all()
    assigned = [
        event for event in events
        if event.type == "task.assigned"
        and event.payload.get("source") == "workflow_resume"
    ]
    dispatched = [
        event for event in events
        if event.type == "task.dispatched"
        and event.payload.get("source") == "workflow_resume"
    ]
    assert checkpoints[0].safe_resume_action == "needs_stage_dispatch"
    assert checkpoints[0].expected_next_role == "review-lane-3"
    assert result["applied"] == 1
    assert task is not None
    assert task.status == "review"
    assert task.assigned_to == "review-lane-3"
    assert len(assigned) == 1
    assert assigned[0].payload["trigger_event_id"] == gate.id
    assert len(dispatched) == 1
    assert dispatched[0].payload["trigger_event_id"] == gate.id
    assert [
        event for event in events
        if event.type == WORKFLOW_RESUME_PLANNED_EVENT
        and event.task_id == "CJMIN-GATEWAY-001"
        and event.payload.get("idempotency_key") == checkpoints[0].idempotency_key
    ]
    assert [
        event for event in events
        if event.type == WORKFLOW_RESUME_APPLIED_EVENT
        and event.task_id == "CJMIN-GATEWAY-001"
        and event.payload.get("idempotency_key") == checkpoints[0].idempotency_key
    ]

    second = apply_workflow_resume(state_dir, _lane_config())

    assert second["applied"] == 0
    assert second["no_op_reason"] == "no pending resume action"
    assert len([
        event for event in log.read_all()
        if event.type == "task.assigned"
        and event.payload.get("source") == "workflow_resume"
    ]) == 1
    assert len([
        event for event in log.read_all()
        if event.type == "task.dispatched"
        and event.payload.get("source") == "workflow_resume"
    ]) == 1


def test_resume_marks_stale_worker_registry_without_blocking_dispatch(
    tmp_path: Path,
) -> None:
    state_dir, store, log = _state(tmp_path)
    missing_workdir = tmp_path / "missing-worker"
    (state_dir / "role_sessions.yaml").write_text(
        "project_root: /old/project\n"
        "roles:\n"
        "  dev-lane-3: 11111111-1111-1111-1111-111111111111\n"
        "instance_meta:\n"
        "  dev-lane-3:\n"
        f"    workdir_path: {missing_workdir}\n",
        encoding="utf-8",
    )
    store.add(Task(
        id="CJMIN-GATEWAY-001",
        title="gateway",
        status="in_progress",
        assigned_to="dev-lane-3",
    ))
    gate = ZfEvent(
        type="static_gate.passed",
        actor="zf-cli",
        task_id="CJMIN-GATEWAY-001",
    )
    log.append(gate)

    projection = build_workflow_resume_projection(state_dir, _lane_config())
    result = apply_workflow_resume(state_dir, _lane_config())

    stale = projection["worker_registry"]["stale"]
    task = store.get("CJMIN-GATEWAY-001")
    assert projection["summary"]["stale_workers"] == 1
    assert stale[0]["instance_id"] == "dev-lane-3"
    assert stale[0]["is_task_truth"] is False
    assert stale[0]["reasons"][0]["code"] == "workdir_missing"
    assert result["applied"] == 1
    assert task is not None
    assert task.assigned_to == "review-lane-3"


def test_resume_projection_exposes_integration_failed_batch_checkpoint(
    tmp_path: Path,
) -> None:
    state_dir, _store, log = _state(tmp_path)
    aggregate = ZfEvent(
        type="fanout.aggregate.completed",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-impl-r37",
            "stage_id": "cj-min-slice-implementation",
            "status": "failed",
            "failure_event": "integration.failed",
            "pdd_id": "CJMIN-R37",
            "feature_id": "CJMIN-R37",
            "candidate_ref": "cand/CJMIN-R37",
            "completed_task_ids": ["CJMIN-PI-CORE-001", "CJMIN-GATEWAY-001"],
            "failed_children": ["dev-lane-0-CJMIN-ASSEMBLY-001"],
        },
    )
    failed = ZfEvent(
        type="integration.failed",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-impl-r37",
            "pdd_id": "CJMIN-R37",
            "candidate_ref": "cand/CJMIN-R37",
            "candidate_head_commit": "7e6585c",
            "reason": "candidate integration failed",
        },
        causation_id=aggregate.id,
    )
    log.append(aggregate)
    log.append(failed)

    projection = build_workflow_resume_projection(state_dir, _lane_config())

    checkpoints = projection["batch_checkpoints"]
    assert projection["summary"]["batch_pending"] == 1
    assert len(checkpoints) == 1
    checkpoint = checkpoints[0]
    assert checkpoint["safe_resume_action"] == "repair_failed_children"
    assert checkpoint["fanout_id"] == "fanout-impl-r37"
    assert checkpoint["candidate_ref"] == "cand/CJMIN-R37"
    assert checkpoint["candidate_head_commit"] == "7e6585c"
    assert checkpoint["completed_task_ids"] == [
        "CJMIN-PI-CORE-001",
        "CJMIN-GATEWAY-001",
    ]
    assert checkpoint["failed_children"] == ["dev-lane-0-CJMIN-ASSEMBLY-001"]
    assert checkpoint["evidence_event_ids"] == [aggregate.id, failed.id]


def test_resume_apply_requeues_only_failed_batch_children(tmp_path: Path) -> None:
    state_dir, _store, log = _state(tmp_path)
    task_map = ZfEvent(
        type="task_map.ready",
        actor="zf-cli",
        payload={
            "pdd_id": "CJMIN-R37",
            "feature_id": "CJMIN-R37",
            "trace_id": "trace-r37",
            "task_map_ref": ".zf/artifacts/CJMIN-R37/task_map.json",
            "source_index_ref": ".zf/artifacts/CJMIN-R37/source_index.json",
            "source_commit": "base123",
            "candidate_base_commit": "base123",
            "target_ref": "dev",
        },
        correlation_id="trace-r37",
    )
    aggregate = ZfEvent(
        type="fanout.aggregate.completed",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-impl-r37",
            "stage_id": "cj-min-slice-implementation",
            "status": "failed",
            "failure_event": "integration.failed",
            "pdd_id": "CJMIN-R37",
            "feature_id": "CJMIN-R37",
            "candidate_ref": "cand/CJMIN-R37",
            "candidate_base_commit": "base123",
            "candidate_head_commit": "head456",
            "completed_task_ids": ["CJMIN-GATEWAY-001"],
            "failed_children": ["dev-lane-0-CJMIN-ASSEMBLY-001"],
        },
        correlation_id="trace-r37",
    )
    failed = ZfEvent(
        type="integration.failed",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-impl-r37",
            "pdd_id": "CJMIN-R37",
            "reason": "assembly failed",
        },
        causation_id=aggregate.id,
        correlation_id="trace-r37",
    )
    log.append(task_map)
    log.append(aggregate)
    log.append(failed)
    dispatched: list[ZfEvent] = []

    result = apply_workflow_resume(
        state_dir,
        _lane_config(),
        gate_dispatcher=dispatched.append,
    )
    second = apply_workflow_resume(
        state_dir,
        _lane_config(),
        gate_dispatcher=dispatched.append,
    )

    events = log.read_all()
    requeued = [
        event for event in events
        if event.type == "task_map.ready"
        and event.payload.get("source") == "workflow_resume_batch"
    ]
    assert result["applied"] == 1
    assert result["batch_results"][0]["applied"] is True
    assert second["applied"] == 0
    assert len(requeued) == 1
    assert requeued[0].payload["resume_scope"] == "failed_children_only"
    assert requeued[0].payload["task_ids"] == ["CJMIN-ASSEMBLY-001"]
    assert requeued[0].payload["candidate_head_commit"] == "head456"
    assert requeued[0].payload["upstream_fanout_id"] == "fanout-impl-r37"
    assert requeued[0].payload["operator_recovery"] == {
        "upstream_fanout_id": "fanout-impl-r37",
        "source": "workflow_resume_batch",
    }
    assert dispatched == [requeued[0]]


def test_resume_apply_reader_child_failure_uses_fanout_manifest_task_id(
    tmp_path: Path,
) -> None:
    state_dir, _store, log = _state(tmp_path)
    fanout_dir = state_dir / "fanouts" / "fanout-verify-r4"
    fanout_dir.mkdir(parents=True)
    (fanout_dir / "manifest.json").write_text(
        json.dumps({
            "fanout_id": "fanout-verify-r4",
            "topology": "fanout_reader",
            "children": [{
                "child_id": "verify-lane-4-dashboard-product",
                "role_instance": "verify-lane-4",
                "payload": {
                    "task_id": "CANGJIE-DASHBOARD-PRODUCT-001",
                    "affinity_tag": "dashboard-product",
                },
            }],
        }),
        encoding="utf-8",
    )
    task_map_path = state_dir / "artifacts" / "plan" / "task_map.json"
    task_map_path.parent.mkdir(parents=True)
    task_map_path.write_text(
        json.dumps({
            "schema_version": "task-map.v1",
            "tasks": [{
                "task_id": "CANGJIE-DASHBOARD-PRODUCT-001",
                "affinity_tag": "dashboard-product",
            }],
        }),
        encoding="utf-8",
    )
    log.append(ZfEvent(
        type="fanout.aggregate.completed",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-verify-r4",
            "stage_id": "cangjie-candidate-verification",
            "status": "failed",
            "pdd_id": "CANGJIE-R4",
            "task_map_ref": str(task_map_path),
            "source_commit": "base123",
            "candidate_ref": "cand/CANGJIE-R4",
            "candidate_base_commit": "base123",
            "candidate_head_commit": "head456",
            "failed_children": ["verify-lane-4-dashboard-product"],
        },
        correlation_id="trace-r4",
    ))
    dispatched: list[ZfEvent] = []

    result = apply_workflow_resume(
        state_dir,
        _lane_config(),
        gate_dispatcher=dispatched.append,
    )

    requeued = [
        event for event in log.read_all()
        if event.type == "task_map.ready"
        and event.payload.get("source") == "workflow_resume_batch"
    ]
    assert result["applied"] == 1
    assert len(requeued) == 1
    assert requeued[0].payload["resume_scope"] == "failed_children_only"
    assert requeued[0].payload["task_ids"] == [
        "CANGJIE-DASHBOARD-PRODUCT-001",
    ]
    assert requeued[0].payload["failed_children"] == [
        "verify-lane-4-dashboard-product",
    ]
    assert dispatched == [requeued[0]]


def test_resume_apply_reader_child_failure_uses_task_map_affinity_hint(
    tmp_path: Path,
) -> None:
    state_dir, _store, log = _state(tmp_path)
    task_map_path = state_dir / "artifacts" / "plan" / "task_map.json"
    task_map_path.parent.mkdir(parents=True)
    task_map_path.write_text(
        json.dumps({
            "schema_version": "task-map.v1",
            "tasks": [{
                "task_id": "CANGJIE-DASHBOARD-PRODUCT-001",
                "affinity_tag": "dashboard-product",
            }],
        }),
        encoding="utf-8",
    )
    log.append(ZfEvent(
        type="fanout.aggregate.completed",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-verify-r4",
            "stage_id": "cangjie-candidate-verification",
            "status": "failed",
            "pdd_id": "CANGJIE-R4",
            "task_map_ref": str(task_map_path),
            "source_commit": "base123",
            "candidate_ref": "cand/CANGJIE-R4",
            "candidate_base_commit": "base123",
            "candidate_head_commit": "head456",
            "failed_children": ["verify-lane-4-dashboard-product"],
        },
        correlation_id="trace-r4",
    ))
    dispatched: list[ZfEvent] = []

    result = apply_workflow_resume(
        state_dir,
        _lane_config(),
        gate_dispatcher=dispatched.append,
    )

    requeued = [
        event for event in log.read_all()
        if event.type == "task_map.ready"
        and event.payload.get("source") == "workflow_resume_batch"
    ]
    assert result["applied"] == 1
    assert len(requeued) == 1
    assert requeued[0].payload["resume_scope"] == "failed_children_only"
    assert requeued[0].payload["task_ids"] == [
        "CANGJIE-DASHBOARD-PRODUCT-001",
    ]
    assert dispatched == [requeued[0]]


def test_resume_apply_reader_child_failure_requeues_all_tasks(
    tmp_path: Path,
) -> None:
    state_dir, _store, log = _state(tmp_path)
    task_map = ZfEvent(
        type="task_map.ready",
        actor="zf-cli",
        payload={
            "pdd_id": "CJMIN-R37",
            "feature_id": "CJMIN-R37",
            "trace_id": "trace-r37",
            "task_map_ref": ".zf/artifacts/CJMIN-R37/task_map.json",
            "source_index_ref": ".zf/artifacts/CJMIN-R37/source_index.json",
            "source_commit": "base123",
            "candidate_base_commit": "base123",
            "target_ref": "dev",
        },
        correlation_id="trace-r37",
    )
    aggregate = ZfEvent(
        type="fanout.aggregate.completed",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-final-judge-r37",
            "stage_id": "final-judge",
            "status": "failed",
            "failure_event": "judge.failed",
            "pdd_id": "CJMIN-R37",
            "feature_id": "CJMIN-R37",
            "candidate_ref": "cand/CJMIN-R37",
            "candidate_base_commit": "base123",
            "candidate_head_commit": "head456",
            "failed_children": ["judge-refactor"],
        },
        correlation_id="trace-r37",
    )
    failed = ZfEvent(
        type="judge.failed",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-final-judge-r37",
            "pdd_id": "CJMIN-R37",
            "reason": "candidate judge failed",
        },
        causation_id=aggregate.id,
        correlation_id="trace-r37",
    )
    log.append(task_map)
    log.append(aggregate)
    log.append(failed)
    dispatched: list[ZfEvent] = []

    result = apply_workflow_resume(
        state_dir,
        _lane_config(),
        gate_dispatcher=dispatched.append,
    )

    events = log.read_all()
    requeued = [
        event for event in events
        if event.type == "task_map.ready"
        and event.payload.get("source") == "workflow_resume_batch"
    ]
    assert result["applied"] == 1
    assert len(requeued) == 1
    assert requeued[0].payload["resume_scope"] == "all_tasks_rework"
    assert "task_ids" not in requeued[0].payload
    assert requeued[0].payload["failed_children"] == ["judge-refactor"]
    assert dispatched == [requeued[0]]


def test_resume_invalidates_empty_task_map_cancelled_attempt(
    tmp_path: Path,
) -> None:
    state_dir, _store, log = _state(tmp_path)
    task_map = ZfEvent(
        type="task_map.ready",
        actor="zf-cli",
        payload={
            "pdd_id": "CJMIN-R37",
            "feature_id": "CJMIN-R37",
            "trace_id": "trace-r37",
            "task_map_ref": ".zf/artifacts/CJMIN-R37/task_map.json",
            "source_index_ref": ".zf/artifacts/CJMIN-R37/source_index.json",
            "source_commit": "base123",
            "candidate_base_commit": "base123",
            "target_ref": "dev",
        },
        correlation_id="trace-r37",
    )
    aggregate = ZfEvent(
        type="fanout.aggregate.completed",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-final-judge-r37",
            "stage_id": "final-judge",
            "status": "failed",
            "failure_event": "judge.failed",
            "pdd_id": "CJMIN-R37",
            "feature_id": "CJMIN-R37",
            "candidate_ref": "cand/CJMIN-R37",
            "candidate_base_commit": "base123",
            "candidate_head_commit": "head456",
            "failed_children": ["judge-refactor"],
        },
        correlation_id="trace-r37",
    )
    failed = ZfEvent(
        type="judge.failed",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-final-judge-r37",
            "pdd_id": "CJMIN-R37",
            "reason": "candidate judge failed",
        },
        causation_id=aggregate.id,
        correlation_id="trace-r37",
    )
    log.append(task_map)
    log.append(aggregate)
    log.append(failed)

    first = apply_workflow_resume(state_dir, _lane_config())
    resume_event = [
        event for event in log.read_all()
        if event.type == "task_map.ready"
        and event.payload.get("source") == "workflow_resume_batch"
    ][0]
    log.append(ZfEvent(
        type="fanout.cancelled",
        actor="zf-cli",
        payload={
            "stage_id": "slice-implementation",
            "trigger_event_id": resume_event.id,
            "reason": "writer fanout task_map has no tasks",
        },
        causation_id=resume_event.id,
        correlation_id="trace-r37",
    ))

    second = apply_workflow_resume(state_dir, _lane_config())

    assert first["applied"] == 1
    assert second["applied"] == 1
    assert len([
        event for event in log.read_all()
        if event.type == "task_map.ready"
        and event.payload.get("source") == "workflow_resume_batch"
    ]) == 2


def test_resume_apply_reemits_candidate_ready_with_same_head(
    tmp_path: Path,
) -> None:
    state_dir, _store, log = _state(tmp_path)
    task_map = ZfEvent(
        type="task_map.ready",
        actor="zf-cli",
        payload={
            "pdd_id": "CJMIN-R37",
            "feature_id": "CJMIN-R37",
            "trace_id": "trace-r37",
            "task_map_ref": ".zf/artifacts/CJMIN-R37/task_map.json",
            "source_index_ref": ".zf/artifacts/CJMIN-R37/source_index.json",
            "source_commit": "base123",
            "candidate_base_commit": "base123",
            "target_ref": "dev",
        },
        correlation_id="trace-r37",
    )
    aggregate = ZfEvent(
        type="fanout.aggregate.completed",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-impl-r37",
            "stage_id": "cj-min-slice-implementation",
            "status": "failed",
            "failure_event": "integration.failed",
            "pdd_id": "CJMIN-R37",
            "feature_id": "CJMIN-R37",
            "candidate_ref": "cand/CJMIN-R37",
            "candidate_base_commit": "base123",
            "candidate_head_commit": "head456",
            "completed_task_ids": ["CJMIN-GATEWAY-001", "CJMIN-PROVIDER-001"],
        },
        correlation_id="trace-r37",
    )
    failed = ZfEvent(
        type="integration.failed",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-impl-r37",
            "pdd_id": "CJMIN-R37",
            "reason": "candidate.ready event was skipped",
        },
        causation_id=aggregate.id,
        correlation_id="trace-r37",
    )
    log.append(task_map)
    log.append(aggregate)
    log.append(failed)
    dispatched: list[ZfEvent] = []

    result = apply_workflow_resume(
        state_dir,
        _lane_config(),
        gate_dispatcher=dispatched.append,
    )
    second = apply_workflow_resume(
        state_dir,
        _lane_config(),
        gate_dispatcher=dispatched.append,
    )

    ready = [
        event for event in log.read_all()
        if event.type == "candidate.ready"
        and event.payload.get("source") == "workflow_resume_batch"
    ]
    assert result["applied"] == 1
    assert second["applied"] == 0
    assert len(ready) == 1
    assert ready[0].payload["candidate_ref"] == "cand/CJMIN-R37"
    assert ready[0].payload["candidate_head_commit"] == "head456"
    assert ready[0].payload["diff_ref"] == "base123..head456"
    assert ready[0].payload["upstream_fanout_id"] == "fanout-impl-r37"
    assert ready[0].payload["operator_recovery"] == {
        "upstream_fanout_id": "fanout-impl-r37",
        "source": "workflow_resume_batch",
    }
    assert ready[0].payload["completed_task_ids"] == [
        "CJMIN-GATEWAY-001",
        "CJMIN-PROVIDER-001",
    ]
    assert dispatched == [ready[0]]


def test_resume_projection_suppresses_stale_candidate_reemit_checkpoint(
    tmp_path: Path,
) -> None:
    state_dir, _store, log = _state(tmp_path)
    log.append(ZfEvent(
        type="integration.failed",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-impl-r40",
            "pdd_id": "CANGJIE-R40",
            "candidate_ref": "cand/CANGJIE-R40",
            "candidate_base_commit": "base123",
            "candidate_head_commit": "oldhead",
            "completed_task_ids": ["CANGJIE-GATEWAY-001"],
            "reason": "candidate.ready event was skipped",
        },
    ))
    log.append(ZfEvent(
        type="candidate.quality.passed",
        actor="zf-cli",
        payload={
            "pdd_id": "CANGJIE-R40",
            "branch": "cand/CANGJIE-R40",
            "commit": "newhead",
            "status": "passed",
        },
    ))
    log.append(ZfEvent(
        type="candidate.ready",
        actor="operator",
        payload={
            "fanout_id": "fanout-impl-r40",
            "pdd_id": "CANGJIE-R40",
            "candidate_ref": "cand/CANGJIE-R40",
            "candidate_base_commit": "base123",
            "candidate_head_commit": "newhead",
            "source": "operator_candidate_rebuild",
        },
    ))

    projection = build_workflow_resume_projection(state_dir, _lane_config())

    assert projection["summary"]["batch_pending"] == 0
    assert projection["batch_checkpoints"] == []


def test_resume_projection_treats_resume_checkpoint_ref_as_recovered(
    tmp_path: Path,
) -> None:
    state_dir, _store, log = _state(tmp_path)
    failed = ZfEvent(
        type="fanout.aggregate.completed",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-verify-r40",
            "stage_id": "cangjie-candidate-verification",
            "status": "failed",
            "pdd_id": "CANGJIE-R40",
            "candidate_ref": "cand/CANGJIE-R40",
            "candidate_base_commit": "base123",
            "candidate_head_commit": "oldhead",
            "failed_children": ["verify-lane-2-assembly-2"],
        },
    )
    log.append(failed)
    checkpoint = build_workflow_resume_projection(
        state_dir,
        _lane_config(),
    )["batch_checkpoints"][0]
    log.append(ZfEvent(
        type="candidate.ready",
        actor="operator",
        payload={
            "fanout_id": "fanout-verify-r40",
            "pdd_id": "CANGJIE-R40",
            "candidate_ref": "cand/CANGJIE-R40",
            "candidate_base_commit": "base123",
            "candidate_head_commit": "newhead",
            "source": "operator_candidate_rebuild",
            "resume_checkpoint_ref": checkpoint["checkpoint_id"],
            "idempotency_key": (
                checkpoint["checkpoint_id"]
                + ":operator-candidate-rebuild:newhead"
            ),
        },
    ))

    projection = build_workflow_resume_projection(state_dir, _lane_config())

    assert projection["summary"]["batch_pending"] == 0
    assert projection["batch_checkpoints"] == []


def test_resume_apply_rejects_stale_candidate_reemit_checkpoint(
    tmp_path: Path,
) -> None:
    state_dir, _store, log = _state(tmp_path)
    failed = ZfEvent(
        type="integration.failed",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-impl-r40",
            "pdd_id": "CANGJIE-R40",
            "candidate_ref": "cand/CANGJIE-R40",
            "candidate_base_commit": "base123",
            "candidate_head_commit": "oldhead",
            "completed_task_ids": ["CANGJIE-GATEWAY-001"],
        },
    )
    quality = ZfEvent(
        type="candidate.quality.passed",
        actor="zf-cli",
        payload={
            "pdd_id": "CANGJIE-R40",
            "branch": "cand/CANGJIE-R40",
            "commit": "newhead",
            "status": "passed",
        },
    )
    log.append(failed)
    log.append(quality)
    checkpoint = WorkflowBatchResumeCheckpoint(
        checkpoint_id="wfres-stale",
        source_event_id=failed.id,
        source_event_type="integration.failed",
        blocking_event_id=failed.id,
        safe_resume_action="reemit_candidate_ready",
        pdd_id="CANGJIE-R40",
        feature_id="CANGJIE-R40",
        fanout_id="fanout-impl-r40",
        stage_id="cangjie-slice-implementation",
        trace_id="trace-r40",
        candidate_ref="cand/CANGJIE-R40",
        candidate_base_commit="base123",
        candidate_head_commit="oldhead",
        completed_task_ids=["CANGJIE-GATEWAY-001"],
    )

    result = _apply_batch_checkpoint(
        writer=EventWriter(log),
        checkpoint=checkpoint,
        state_dir=state_dir,
        events=log.read_all(),
    )

    event_types = [event.type for event in log.read_all()]
    assert result.applied is False
    assert result.reason.startswith("rejected: stale batch checkpoint")
    assert "candidate.ready" not in event_types
    assert event_types[-1] == "workflow.resume.rejected"


def test_resume_apply_filters_batch_checkpoint_id(tmp_path: Path) -> None:
    state_dir, _store, log = _state(tmp_path)
    task_map = ZfEvent(
        type="task_map.ready",
        actor="zf-cli",
        payload={
            "pdd_id": "CJMIN-R37",
            "feature_id": "CJMIN-R37",
            "trace_id": "trace-r37",
            "task_map_ref": ".zf/artifacts/CJMIN-R37/task_map.json",
            "source_index_ref": ".zf/artifacts/CJMIN-R37/source_index.json",
            "source_commit": "base123",
            "candidate_base_commit": "base123",
            "target_ref": "dev",
        },
        correlation_id="trace-r37",
    )
    old_failed = ZfEvent(
        type="fanout.aggregate.completed",
        id="evt-old-fanout",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-old",
            "stage_id": "cj-min-slice-implementation",
            "status": "failed",
            "pdd_id": "CJMIN-R37",
            "candidate_ref": "cand/CJMIN-R37",
            "candidate_base_commit": "base123",
            "candidate_head_commit": "oldhead",
            "failed_children": ["dev-lane-0-CJMIN-ASSEMBLY-001"],
        },
        correlation_id="trace-r37",
    )
    current_failed = ZfEvent(
        type="fanout.aggregate.completed",
        id="evt-current-fanout",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-current",
            "stage_id": "cj-min-slice-implementation",
            "status": "failed",
            "pdd_id": "CJMIN-R37",
            "candidate_ref": "cand/CJMIN-R37",
            "candidate_base_commit": "base123",
            "candidate_head_commit": "newhead",
            "failed_children": [
                "dev-lane-2-CJMIN-STATE-CONFIG-SESSION-001",
            ],
        },
        correlation_id="trace-r37",
    )
    log.append(task_map)
    log.append(old_failed)
    log.append(current_failed)
    projection = build_workflow_resume_projection(state_dir, _lane_config())
    current = [
        item for item in projection["batch_checkpoints"]
        if item["fanout_id"] == "fanout-current"
    ][0]

    result = apply_workflow_resume(
        state_dir,
        _lane_config(),
        checkpoint_id=current["checkpoint_id"],
    )

    requeued = [
        event for event in log.read_all()
        if event.type == "task_map.ready"
        and event.payload.get("source") == "workflow_resume_batch"
    ]
    assert result["applied"] == 1
    assert result["checkpoint_id"] == current["checkpoint_id"]
    assert len(requeued) == 1
    assert requeued[0].payload["task_ids"] == [
        "CJMIN-STATE-CONFIG-SESSION-001",
    ]
    assert requeued[0].payload["candidate_head_commit"] == "newhead"


def test_resume_apply_batch_checkpoint_uses_operator_task_map_override(
    tmp_path: Path,
) -> None:
    state_dir, _store, log = _state(tmp_path)
    original_task_map = state_dir / "artifacts" / "plan" / "task_map.json"
    override_task_map = (
        state_dir / "artifacts" / "workflow-resume" / "operator" / "task_map.json"
    )
    original_task_map.parent.mkdir(parents=True)
    override_task_map.parent.mkdir(parents=True)
    original_task_map.write_text(
        json.dumps({"schema_version": "task-map.v1", "tasks": []}),
        encoding="utf-8",
    )
    override_task_map.write_text(
        json.dumps({
            "schema_version": "task-map.v1",
            "tasks": [{
                "task_id": "CJMIN-PACKAGING-DOCKER-SECURITY-001",
                "allowed_paths": [
                    "packages/security/test/security.test.ts",
                ],
            }],
        }),
        encoding="utf-8",
    )
    log.append(ZfEvent(
        type="fanout.aggregate.completed",
        id="evt-current-fanout",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-current",
            "stage_id": "cj-min-slice-implementation",
            "status": "failed",
            "pdd_id": "CJMIN-R37",
            "task_map_ref": str(original_task_map),
            "source_commit": "base123",
            "candidate_ref": "cand/CJMIN-R37",
            "candidate_base_commit": "base123",
            "candidate_head_commit": "head456",
            "failed_children": [
                "queued-CJMIN-PACKAGING-DOCKER-SECURITY-001-8",
            ],
        },
        correlation_id="trace-r37",
    ))
    checkpoint = build_workflow_resume_projection(
        state_dir,
        _lane_config(),
    )["batch_checkpoints"][0]

    result = apply_workflow_resume(
        state_dir,
        _lane_config(),
        checkpoint_id=checkpoint["checkpoint_id"],
        override_task_map_ref=str(override_task_map),
    )

    requeued = [
        event for event in log.read_all()
        if event.type == "task_map.ready"
        and event.payload.get("source") == "workflow_resume_batch"
    ]
    assert result["applied"] == 1
    assert requeued[0].payload["task_map_ref"] == str(override_task_map)
    assert requeued[0].payload["task_ids"] == [
        "CJMIN-PACKAGING-DOCKER-SECURITY-001",
    ]
    assert requeued[0].payload["task_map_repair"] == {
        "kind": "operator_task_map_override",
        "original_task_map_ref": str(original_task_map),
        "repaired_task_map_ref": str(override_task_map),
    }


def test_resume_apply_rejects_missing_operator_task_map_override(
    tmp_path: Path,
) -> None:
    state_dir, _store, log = _state(tmp_path)
    task_map_path = state_dir / "artifacts" / "plan" / "task_map.json"
    task_map_path.parent.mkdir(parents=True)
    task_map_path.write_text(
        json.dumps({"schema_version": "task-map.v1", "tasks": []}),
        encoding="utf-8",
    )
    log.append(ZfEvent(
        type="fanout.aggregate.completed",
        id="evt-current-fanout",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-current",
            "stage_id": "cj-min-slice-implementation",
            "status": "failed",
            "pdd_id": "CJMIN-R37",
            "task_map_ref": str(task_map_path),
            "source_commit": "base123",
            "candidate_ref": "cand/CJMIN-R37",
            "candidate_base_commit": "base123",
            "candidate_head_commit": "head456",
            "failed_children": ["dev-lane-0-CJMIN-ASSEMBLY-001"],
        },
        correlation_id="trace-r37",
    ))
    checkpoint = build_workflow_resume_projection(
        state_dir,
        _lane_config(),
    )["batch_checkpoints"][0]

    result = apply_workflow_resume(
        state_dir,
        _lane_config(),
        checkpoint_id=checkpoint["checkpoint_id"],
        override_task_map_ref=str(state_dir / "missing-task-map.json"),
    )

    event_types = [event.type for event in log.read_all()]
    assert result["applied"] == 0
    assert result["rejected"] == 1
    assert "task_map.ready" not in event_types
    assert "workflow.resume.planned" not in event_types
    assert event_types[-1] == "workflow.resume.rejected"


def test_resume_apply_retries_after_task_map_validation_cancel(
    tmp_path: Path,
) -> None:
    state_dir, _store, log = _state(tmp_path)
    task_map_path = state_dir / "artifacts" / "plan" / "task_map.json"
    task_map_path.parent.mkdir(parents=True)
    task_map_path.write_text(
        json.dumps({
            "schema_version": "task-map.v1",
            "tasks": [{
                "task_id": "CJMIN-ASSEMBLY-001",
                "title": "assembly",
                "wave": 0,
                "verification": "pnpm --filter ./packages/** run typecheck",
            }],
        }),
        encoding="utf-8",
    )
    aggregate = ZfEvent(
        type="fanout.aggregate.completed",
        id="evt-current-fanout",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-current",
            "stage_id": "cj-min-slice-implementation",
            "status": "failed",
            "pdd_id": "CJMIN-R37",
            "task_map_ref": str(task_map_path),
            "source_commit": "base123",
            "candidate_ref": "cand/CJMIN-R37",
            "candidate_base_commit": "base123",
            "candidate_head_commit": "head456",
            "failed_children": ["dev-lane-0-CJMIN-ASSEMBLY-001"],
        },
        correlation_id="trace-r37",
    )
    log.append(aggregate)
    projection = build_workflow_resume_projection(state_dir, _lane_config())
    checkpoint = projection["batch_checkpoints"][0]
    log.append(ZfEvent(
        type="task_map.ready",
        actor="zf-cli",
        payload={
            "source": "workflow_resume_batch",
            "resume_checkpoint_ref": checkpoint["checkpoint_id"],
            "idempotency_key": checkpoint["checkpoint_id"],
            "task_map_ref": str(task_map_path),
        },
    ))
    log.append(ZfEvent(
        type="fanout.cancelled",
        actor="zf-cli",
        payload={
            "stage_id": "cj-min-slice-implementation",
            "reason": (
                "writer fanout task_map validation failed: "
                "CJMIN-ASSEMBLY-001.verification must quote shell glob "
                "filter arguments"
            ),
        },
    ))

    result = apply_workflow_resume(
        state_dir,
        _lane_config(),
        checkpoint_id=checkpoint["checkpoint_id"],
    )

    requeued = [
        event for event in log.read_all()
        if event.type == "task_map.ready"
        and event.payload.get("source") == "workflow_resume_batch"
    ]
    latest = requeued[-1]
    repaired_ref = Path(latest.payload["task_map_ref"])
    repaired = json.loads(repaired_ref.read_text(encoding="utf-8"))
    original = json.loads(task_map_path.read_text(encoding="utf-8"))
    assert result["applied"] == 1
    assert len(requeued) == 2
    assert repaired_ref != task_map_path
    assert latest.payload["task_map_repair"]["original_task_map_ref"] == str(
        task_map_path
    )
    assert repaired["tasks"][0]["verification"] == (
        "pnpm --filter './packages/**' run typecheck"
    )
    assert original["tasks"][0]["verification"] == (
        "pnpm --filter ./packages/** run typecheck"
    )


def test_resume_projection_preserves_integration_context_on_human_escalate(
    tmp_path: Path,
) -> None:
    state_dir, _store, log = _state(tmp_path)
    failed = ZfEvent(
        type="integration.failed",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-impl-r37",
            "pdd_id": "CJMIN-R37",
            "candidate_ref": "cand/CJMIN-R37",
            "candidate_head_commit": "7e6585c",
            "completed_task_ids": ["CJMIN-GATEWAY-001"],
            "failed_children": ["dev-lane-0-CJMIN-ASSEMBLY-001"],
            "reason": "candidate rework exhausted",
        },
    )
    escalated = ZfEvent(
        type="human.escalate",
        actor="zf-cli",
        payload={
            "pdd_id": "CJMIN-R37",
            "rework_of": failed.id,
            "rework_source": "integration.failed",
            "rework_attempt": 3,
        },
        causation_id=failed.id,
    )
    log.append(failed)
    log.append(escalated)

    projection = build_workflow_resume_projection(state_dir, _lane_config())

    checkpoints = projection["batch_checkpoints"]
    assert len(checkpoints) == 1
    checkpoint = checkpoints[0]
    assert checkpoint["source_event_type"] == "human.escalate"
    assert checkpoint["source_event_id"] == failed.id
    assert checkpoint["blocking_event_id"] == escalated.id
    assert checkpoint["escalated"] is True
    assert checkpoint["safe_resume_action"] == "repair_failed_children"
    assert checkpoint["fanout_id"] == "fanout-impl-r37"
    assert checkpoint["candidate_head_commit"] == "7e6585c"
    assert checkpoint["failed_children"] == ["dev-lane-0-CJMIN-ASSEMBLY-001"]
    assert checkpoint["evidence_event_ids"] == [failed.id, escalated.id]


def test_resume_routes_design_critique_to_next_planning_role(
    tmp_path: Path,
) -> None:
    state_dir, store, log = _state(tmp_path)
    cfg = ZfConfig(
        project=ProjectConfig(name="resume-design"),
        session=SessionConfig(tmux_session="resume-design"),
        roles=[
            RoleConfig(
                name="critic",
                backend="mock",
                publishes=["design.critique.done"],
            ),
            RoleConfig(
                name="refactor-plan-synth",
                backend="mock",
                triggers=["design.critique.done"],
                publishes=["zaofu.refactor.plan.ready"],
            ),
        ],
        workflow=WorkflowConfig(
            dag=WorkflowDagConfig(
                enabled=True,
                graph_review_test_judge_reconcile=True,
            ),
        ),
    )
    store.add(Task(
        id="CJMIN-STATE-001",
        title="state",
        status="in_progress",
        assigned_to="critic",
    ))
    critique = ZfEvent(
        type="design.critique.done",
        actor="critic",
        task_id="CJMIN-STATE-001",
        payload={"verdict": "approved"},
    )
    log.append(critique)

    result = apply_workflow_resume(state_dir, cfg)

    task = store.get("CJMIN-STATE-001")
    assert result["applied"] == 1
    assert task is not None
    assert task.assigned_to == "refactor-plan-synth"
    assert any(
        event.type == "task.assigned"
        and event.payload.get("assignee") == "refactor-plan-synth"
        and event.payload.get("trigger_event_id") == critique.id
        for event in log.read_all()
    )


def test_resume_task_ref_rejection_requests_repair_not_review(
    tmp_path: Path,
) -> None:
    state_dir, store, log = _state(tmp_path)
    store.add(Task(
        id="CJMIN-PROVIDER-001",
        title="provider",
        status="in_progress",
        assigned_to="dev-lane-3",
    ))
    dev_done = ZfEvent(
        type="dev.build.done",
        actor="dev-lane-3",
        task_id="CJMIN-PROVIDER-001",
    )
    rejected = ZfEvent(
        type="task.ref.rejected",
        actor="zf-cli",
        task_id="CJMIN-PROVIDER-001",
        payload={
            "trigger_event_id": dev_done.id,
            "reason": "source_commit changes outside task contract scope",
        },
    )
    log.append(dev_done)
    log.append(rejected)

    result = apply_workflow_resume(state_dir, _lane_config())

    events = log.read_all()
    assert result["applied"] == 1
    assert any(event.type == TASK_REF_REPAIR_REQUESTED_EVENT for event in events)
    assert not [
        event for event in events
        if event.type == "task.assigned"
        and event.payload.get("assignee") == "review-lane-3"
    ]

    second = apply_workflow_resume(state_dir, _lane_config())

    assert second["applied"] == 0


def test_resume_ignores_superseded_task_ref_rejection(tmp_path: Path) -> None:
    state_dir, store, log = _state(tmp_path)
    store.add(Task(
        id="CJMIN-PI-CORE-001",
        title="pi",
        status="in_progress",
        assigned_to="dev-lane-3",
    ))
    old_done = ZfEvent(
        type="dev.build.done",
        actor="dev-lane-3",
        task_id="CJMIN-PI-CORE-001",
    )
    old_rejected = ZfEvent(
        type="task.ref.rejected",
        actor="zf-cli",
        task_id="CJMIN-PI-CORE-001",
        payload={
            "trigger_event_id": old_done.id,
            "reason": "old dirty worktree",
        },
        causation_id=old_done.id,
    )
    newer_done = ZfEvent(
        type="dev.build.done",
        actor="dev-lane-3",
        task_id="CJMIN-PI-CORE-001",
    )
    log.append(old_done)
    log.append(old_rejected)
    log.append(newer_done)
    log.append(ZfEvent(
        type="task.ref.updated",
        actor="zf-cli",
        task_id="CJMIN-PI-CORE-001",
        payload={"trigger_event_id": newer_done.id},
        causation_id=newer_done.id,
    ))

    checkpoints = build_workflow_resume_checkpoints(state_dir, _lane_config())

    assert not [
        checkpoint for checkpoint in checkpoints
        if checkpoint.safe_resume_action == "needs_task_ref_repair"
    ]


def test_resume_gate_dispatch_is_done_after_fanout_child_result(
    tmp_path: Path,
) -> None:
    state_dir, store, log = _state(tmp_path)
    cfg = _lane_config()
    cfg.quality_gates = {
        "static": QualityGateConfig(enabled=True, required_checks=["true"]),
    }
    store.add(Task(
        id="CJMIN-GATEWAY-001",
        title="gateway",
        status="in_progress",
        assigned_to="dev-lane-3",
    ))
    dev_done = ZfEvent(
        type="dev.build.done",
        actor="dev-lane-3",
        task_id="CJMIN-GATEWAY-001",
        payload={"dispatch_id": "disp-dev"},
    )
    log.append(dev_done)
    log.append(ZfEvent(
        type="task.ref.updated",
        actor="zf-cli",
        task_id="CJMIN-GATEWAY-001",
        payload={"trigger_event_id": dev_done.id},
        causation_id=dev_done.id,
    ))
    log.append(ZfEvent(
        type="fanout.child.failed",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-impl",
            "child_id": "dev-lane-3-CJMIN-GATEWAY-001",
            "task_id": "CJMIN-GATEWAY-001",
            "reason": "stale_task_map",
        },
        causation_id=dev_done.id,
    ))

    checkpoints = build_workflow_resume_checkpoints(state_dir, cfg)

    assert all(
        checkpoint.safe_resume_action != "needs_gate_dispatch"
        for checkpoint in checkpoints
    )


def test_resume_gate_dispatch_is_done_after_static_gate_upstream_child_result(
    tmp_path: Path,
) -> None:
    state_dir, store, log = _state(tmp_path)
    cfg = _gate_only_config()
    store.add(Task(
        id="CJMIN-WEBTUI-001",
        title="webtui",
        status="in_progress",
        assigned_to="dev-lane-3",
    ))
    dev_done = ZfEvent(
        type="dev.build.done",
        actor="dev-lane-3",
        task_id="CJMIN-WEBTUI-001",
        payload={"dispatch_id": "disp-dev"},
    )
    gate = ZfEvent(
        type="static_gate.passed",
        actor="zf-cli",
        task_id="CJMIN-WEBTUI-001",
        payload={"trigger_event_id": dev_done.id},
        causation_id=dev_done.id,
    )
    log.append(dev_done)
    log.append(ZfEvent(
        type="task.ref.updated",
        actor="zf-cli",
        task_id="CJMIN-WEBTUI-001",
        payload={"trigger_event_id": dev_done.id},
        causation_id=dev_done.id,
    ))
    log.append(gate)
    log.append(ZfEvent(
        type="fanout.child.failed",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-impl",
            "child_id": "dev-lane-3-CJMIN-WEBTUI-001",
            "task_id": "CJMIN-WEBTUI-001",
            "reason": "stale_task_map",
        },
        causation_id=dev_done.id,
    ))

    checkpoints = build_workflow_resume_checkpoints(state_dir, cfg)

    assert len(checkpoints) == 1
    assert checkpoints[0].safe_resume_action == "no_action"
    assert checkpoints[0].last_trusted_event_id == gate.id


def test_resume_review_rejection_requests_rework_once(tmp_path: Path) -> None:
    state_dir, store, log = _state(tmp_path)
    store.add(Task(
        id="CJMIN-GATEWAY-001",
        title="gateway",
        status="review",
        assigned_to="review-lane-3",
    ))
    rejected = ZfEvent(
        type="review.rejected",
        actor="review-lane-3",
        task_id="CJMIN-GATEWAY-001",
        payload={"reason": "missing regression evidence"},
    )
    log.append(rejected)

    result = apply_workflow_resume(state_dir, _lane_config())

    task = store.get("CJMIN-GATEWAY-001")
    events = log.read_all()
    rework = [
        event for event in events
        if event.type == "task.rework.requested"
        and event.payload.get("source") == "workflow_resume"
    ]
    assert result["applied"] == 1
    assert task is not None
    assert task.status == "in_progress"
    assert task.assigned_to == "dev-lane-3"
    assert len(rework) == 1
    assert rework[0].payload["trigger_event_id"] == rejected.id

    second = apply_workflow_resume(state_dir, _lane_config())

    assert second["applied"] == 0
    assert len([
        event for event in log.read_all()
        if event.type == "task.rework.requested"
        and event.payload.get("source") == "workflow_resume"
    ]) == 1
