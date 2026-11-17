from __future__ import annotations

from pathlib import Path

from zf.core.config.loader import load_config
from zf.core.events.model import ZfEvent
from zf.runtime.workflow_resume import build_workflow_resume_projection
from zf.runtime.workflow_resume_apply import _task_ids_from_failed_children


def _projection(tmp_path: Path, events: list[ZfEvent]) -> dict:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    config = load_config(Path("examples/prod/controller/prd-fanout-v3.yaml"))
    return build_workflow_resume_projection(
        state_dir,
        config,
        events=events,
        tasks=[],
    )


def test_fanout_cancelled_more_tasks_than_lanes_produces_batch_checkpoint(
    tmp_path: Path,
) -> None:
    projection = _projection(tmp_path, [
        ZfEvent(
            type="task_map.ready",
            id="evt-taskmap",
            actor="sim",
            correlation_id="trace-gap",
            payload={
                "pdd_id": "F-GAP",
                "task_map_ref": ".zf/artifacts/F-GAP/task_map.json",
            },
        ),
        ZfEvent(
            type="fanout.cancelled",
            id="evt-cancel",
            actor="zf-cli",
            correlation_id="trace-gap",
            payload={
                "fanout_id": "fanout-prd-lanes-impl-gap",
                "stage_id": "prd-lanes-impl",
                "pdd_id": "F-GAP",
                "task_map_ref": ".zf/artifacts/F-GAP/task_map.json",
                "reason": "writer fanout has more tasks than writer role instances",
            },
        ),
    ])

    assert projection["summary"]["batch_pending"] == 1
    checkpoint = projection["batch_checkpoints"][0]
    assert checkpoint["source_event_type"] == "fanout.cancelled"
    assert checkpoint["safe_resume_action"] == "trigger_rework"
    assert checkpoint["task_map_ref"] == ".zf/artifacts/F-GAP/task_map.json"


def test_fanout_cancelled_task_map_validation_remains_fail_closed(
    tmp_path: Path,
) -> None:
    projection = _projection(tmp_path, [
        ZfEvent(
            type="fanout.cancelled",
            id="evt-cancel",
            actor="zf-cli",
            correlation_id="trace-gap",
            payload={
                "fanout_id": "fanout-prd-lanes-impl-gap",
                "stage_id": "prd-lanes-impl",
                "pdd_id": "F-GAP",
                "task_map_ref": ".zf/artifacts/F-GAP/task_map.json",
                "reason": "task_map validation failed: missing tasks",
            },
        ),
    ])

    assert projection["summary"]["batch_pending"] == 0
    assert projection["batch_checkpoints"] == []


def test_pre_task_reader_failure_is_owned_by_stage_replan(
    tmp_path: Path,
) -> None:
    projection = _projection(tmp_path, [
        ZfEvent(
            type="fanout.started",
            id="evt-scan-started",
            actor="zf-cli",
            correlation_id="refactor-run",
            payload={
                "fanout_id": "fanout-flow-scan",
                "stage_id": "flow-scan",
                "topology": "fanout_reader",
            },
        ),
        ZfEvent(
            type="fanout.aggregate.completed",
            id="evt-scan-aggregate",
            actor="zf-cli",
            correlation_id="refactor-run",
            payload={
                "fanout_id": "fanout-flow-scan",
                "stage_id": "flow-scan",
                "status": "failed",
                "failure_event": "zaofu.refactor.scan.blocked",
                "failed_children": ["scan-contract", "scan-runtime"],
            },
        ),
        ZfEvent(
            type="zaofu.refactor.scan.blocked",
            id="evt-scan-blocked",
            actor="zf-cli",
            correlation_id="refactor-run",
            payload={
                "fanout_id": "fanout-flow-scan",
                "stage_id": "flow-scan",
                "status": "failed",
            },
            causation_id="evt-scan-aggregate",
        ),
    ])

    assert projection["summary"]["batch_pending"] == 0
    assert projection["batch_checkpoints"] == []


def test_structured_discovery_gap_owns_resume_with_candidate_refs(
    tmp_path: Path,
) -> None:
    fanout_id = "fanout-prd-post-verify-discovery"
    projection = _projection(tmp_path, [
        ZfEvent(
            type="fanout.started",
            id="evt-discovery-started",
            correlation_id="workflow-1",
            payload={
                "fanout_id": fanout_id,
                "stage_id": "prd-post-verify-discovery",
                "topology": "fanout_reader",
            },
        ),
        ZfEvent(
            type="fanout.aggregate.completed",
            id="evt-discovery-aggregate",
            correlation_id="workflow-1",
            payload={
                "fanout_id": fanout_id,
                "stage_id": "prd-post-verify-discovery",
                "status": "failed",
                "failure_event": "flow.discovery.failed",
                "pdd_id": "PRD-1",
                "task_map_ref": "artifacts/PRD-1/task_map.json",
                "candidate_ref": "candidate/PRD-1",
                "candidate_head_commit": "abc123",
                "failed_children": ["flow-discovery"],
            },
        ),
        ZfEvent(
            type="flow.discovery.failed",
            id="evt-discovery-failed",
            causation_id="evt-discovery-aggregate",
            correlation_id="semantic-replan-child",
            payload={
                "workflow_run_id": "workflow-1",
                "run_id": "workflow-1",
                "trace_id": "workflow-1",
                "fanout_id": fanout_id,
                "pdd_id": "PRD-1",
                "task_map_ref": "artifacts/PRD-1/task_map.json",
                "candidate_ref": "candidate/PRD-1",
                "candidate_head_commit": "abc123",
                "gap_tasks": [{"task_id": "PRD-1-ASSEMBLY-GAP"}],
            },
        ),
        ZfEvent(
            type="flow.gap_plan.ready",
            id="evt-gap-plan",
            causation_id="evt-discovery-failed",
            correlation_id="workflow-1",
            payload={"source_event_id": "evt-discovery-failed"},
        ),
        ZfEvent(
            type="task_map.amend.failed",
            id="evt-amend-failed",
            causation_id="evt-amend-requested",
            correlation_id="workflow-1",
            payload={
                "source_event_id": "evt-gap-plan",
                "reason": "replacement lost root_owner_class",
            },
        ),
    ])

    assert projection["summary"]["batch_pending"] == 0
    assert projection["batch_checkpoints"] == []
    assert projection["checkpoints"] == []


def test_scheduler_queue_timeout_produces_gap_only_resume_checkpoint(
    tmp_path: Path,
) -> None:
    projection = _projection(tmp_path, [
        ZfEvent(
            type="task_map.ready",
            id="evt-taskmap",
            actor="sim",
            correlation_id="trace-queue",
            payload={
                "pdd_id": "F-QUEUE",
                "task_map_ref": ".zf/artifacts/F-QUEUE/task_map.json",
                "source_commit": "base123",
                "target_ref": "main",
            },
        ),
        ZfEvent(
            type="fanout.started",
            id="evt-root-started",
            actor="zf-cli",
            correlation_id="trace-queue",
            payload={
                "fanout_id": "fanout-prd-lanes-impl-queue",
                "stage_id": "prd-lanes-impl",
                "pdd_id": "F-QUEUE",
                "task_map_ref": ".zf/artifacts/F-QUEUE/task_map.json",
                "target_ref": "main",
            },
        ),
        ZfEvent(
            type="fanout.cancelled",
            id="evt-cancel",
            actor="zf-cli",
            correlation_id="trace-queue",
            payload={
                "fanout_id": "fanout-prd-lanes-impl-queue",
                "stage_id": "prd-lanes-impl",
                "pdd_id": "F-QUEUE",
                "task_map_ref": ".zf/artifacts/F-QUEUE/task_map.json",
                "reason": "queued_wait_timeout",
                "failure_kind": "scheduler_queue_timeout",
                "queued_children": ["queued-F-QUEUE-ASSEMBLY-006-6"],
                "semantic_attempt_consumed": False,
            },
        ),
        ZfEvent(
            type="verify.passed",
            id="evt-unrelated-progress",
            actor="verify-lane-0",
            correlation_id="trace-queue",
            payload={
                "pdd_id": "F-QUEUE",
                "task_id": "F-QUEUE-ANALYTICS-004",
                "completed_task_ids": ["F-QUEUE-ANALYTICS-004"],
            },
        ),
        ZfEvent(
            type="candidate.ready",
            id="evt-unrelated-candidate",
            actor="zf-cli",
            correlation_id="trace-queue",
            payload={
                "fanout_id": "fanout-analytics-rework",
                "pdd_id": "F-QUEUE",
                "target_ref": "task/F-QUEUE-ANALYTICS-004",
                "candidate_ref": "candidate/F-QUEUE",
            },
        ),
    ])

    assert projection["summary"]["batch_pending"] == 1
    checkpoint = projection["batch_checkpoints"][0]
    assert checkpoint["safe_resume_action"] == "resume_queued_children"
    assert checkpoint["pending_children"] == [
        "queued-F-QUEUE-ASSEMBLY-006-6",
    ]
    assert checkpoint["target_ref"] == "main"


def test_candidate_quality_failure_routes_to_rework_not_candidate_reemit(
    tmp_path: Path,
) -> None:
    projection = _projection(tmp_path, [
        ZfEvent(
            type="integration.failed",
            id="evt-integration-quality",
            actor="zf-cli",
            correlation_id="trace-quality",
            payload={
                "fanout_id": "fanout-impl",
                "stage_id": "prd-lanes-impl",
                "pdd_id": "PRD-QUALITY",
                "task_map_ref": ".zf/artifacts/PRD-QUALITY/task_map.json",
                "candidate_ref": "candidate/PRD-QUALITY",
                "candidate_base_commit": "base123",
                "candidate_head_commit": "head456",
                "completed_task_ids": ["PRD-QUALITY-001"],
                "findings": [{
                    "finding_id": "candidate-quality-failed",
                    "category": "candidate_quality",
                    "message": "vitest is unavailable",
                }],
            },
        ),
    ])

    assert projection["summary"]["batch_pending"] == 1
    assert projection["batch_checkpoints"][0]["safe_resume_action"] == "trigger_rework"


def test_candidate_semantic_rejection_is_not_runtime_reemitted(
    tmp_path: Path,
) -> None:
    fanout_id = "fanout-candidate-verify"
    projection = _projection(tmp_path, [
        ZfEvent(
            type="candidate.ready",
            id="evt-candidate",
            actor="zf-cli",
            correlation_id="workflow-1",
            payload={
                "pdd_id": "PRD-1",
                "candidate_ref": "refs/heads/candidate/PRD-1",
                "candidate_head_commit": "abc123",
            },
        ),
        ZfEvent(
            type="fanout.started",
            id="evt-started",
            actor="zf-cli",
            correlation_id="workflow-1",
            payload={
                "fanout_id": fanout_id,
                "stage_id": "prd-lanes-verify",
                "topology": "fanout_reader",
                "trigger_event_id": "evt-candidate",
                "pdd_id": "PRD-1",
            },
        ),
        ZfEvent(
            type="fanout.child.failed",
            id="evt-child-failed",
            actor="zf-cli",
            correlation_id="workflow-1",
            payload={
                "fanout_id": fanout_id,
                "child_id": "verify-lane-2",
                "semantic_verdict": "rejected",
                "report": {
                    "schema_version": "verification-result.v1",
                    "execution_status": "completed",
                    "verdict": "rejected",
                    "verification_owner": "candidate_verify",
                    "requirement_results": [{
                        "acceptance_id": "AC25",
                        "status": "failed",
                        "findings": [{
                            "path": "src/styles/mobility.css",
                            "message": "inactive mode remains visible",
                        }],
                    }],
                },
            },
        ),
        ZfEvent(
            type="fanout.aggregate.completed",
            id="evt-aggregate",
            actor="zf-cli",
            correlation_id="workflow-1",
            payload={
                "fanout_id": fanout_id,
                "stage_id": "prd-lanes-verify",
                "pdd_id": "PRD-1",
                "status": "failed",
                "failure_event": "test.failed",
                "failed_children": ["verify-lane-2"],
                "findings": [{
                    "category": "runtime_failure",
                    "message": "semantic verdict: rejected",
                }],
            },
        ),
        ZfEvent(
            type="test.failed",
            id="evt-test-failed",
            actor="zf-cli",
            causation_id="evt-aggregate",
            correlation_id="workflow-1",
            payload={
                "fanout_id": fanout_id,
                "pdd_id": "PRD-1",
                "status": "failed",
            },
        ),
    ])

    assert projection["summary"]["batch_pending"] == 0
    assert projection["batch_checkpoints"] == []


def test_candidate_dependency_failure_does_not_reopen_quality_gate_owners(
    tmp_path: Path,
) -> None:
    projection = _projection(tmp_path, [
        ZfEvent(
            type="integration.failed",
            id="evt-integration-dependency",
            actor="zf-cli",
            correlation_id="trace-dependency",
            payload={
                "fanout_id": "fanout-impl",
                "stage_id": "prd-lanes-impl",
                "pdd_id": "PRD-DEPENDENCY",
                "task_map_ref": ".zf/artifacts/PRD-DEPENDENCY/task_map.json",
                "candidate_ref": "candidate/PRD-DEPENDENCY",
                "candidate_base_commit": "base123",
                "candidate_head_commit": "head456",
                "completed_task_ids": ["WU-01", "WU-02"],
                "failed_task_ids": ["WU-01", "WU-02"],
                "failed_children": [],
                "failure_scope": "candidate",
                "failure_class": "candidate_dependency_missing",
            },
        ),
    ])

    assert projection["summary"]["batch_pending"] == 1
    checkpoint = projection["batch_checkpoints"][0]
    assert checkpoint["safe_resume_action"] == "trigger_rework"
    assert checkpoint["failed_children"] == []


def test_integration_failure_task_ids_are_not_completed_dependencies(tmp_path: Path) -> None:
    projection = _projection(tmp_path, [
        ZfEvent(
            type="integration.failed", id="evt-integration-failed", actor="zf-cli",
            payload={
                "fanout_id": "fanout-impl", "stage_id": "prd-lanes-impl",
                "pdd_id": "PRD-1", "task_map_ref": ".zf/artifacts/task_map.json",
                "candidate_base_commit": "base123", "source_commit": "base123",
                "task_ids": ["TASK-A", "TASK-B"],
                "completed_task_ids": ["TASK-A"],
                "failed_children": ["dev-lane-1-TASK-B"],
            },
        ),
    ])
    checkpoint = projection["batch_checkpoints"][0]
    assert checkpoint["completed_task_ids"] == ["TASK-A"]
    assert checkpoint["failed_children"] == ["dev-lane-1-TASK-B"]


def test_failed_child_identity_prefers_manifest_task_id_over_transport_suffix(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    manifest = state_dir / "fanouts" / "fanout-impl" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        '{"children":[{"child_id":"queued-SIM1-T06-EVAL-6",'
        '"task_id":"SIM1-T06-EVAL"}]}\n',
        encoding="utf-8",
    )

    task_ids = _task_ids_from_failed_children(
        ["queued-SIM1-T06-EVAL-6"],
        state_dir=state_dir,
        fanout_id="fanout-impl",
    )

    assert task_ids == ["SIM1-T06-EVAL"]


def test_batch_checkpoint_is_recovered_by_later_aggregate_completion(
    tmp_path: Path,
) -> None:
    projection = _projection(tmp_path, [
        ZfEvent(
            type="task_map.ready",
            id="evt-taskmap",
            actor="sim",
            correlation_id="trace-notes",
            payload={
                "pdd_id": "NOTES",
                "task_map_ref": ".zf/artifacts/NOTES/task_map.json",
                "source_commit": "base",
            },
        ),
        ZfEvent(
            type="fanout.aggregate.completed",
            id="evt-agg-failed",
            actor="verify",
            correlation_id="trace-notes",
            payload={
                "fanout_id": "fanout-notes",
                "stage_id": "verify",
                "pdd_id": "NOTES",
                "task_map_ref": ".zf/artifacts/NOTES/task_map.json",
                "source_commit": "base",
                "status": "failed",
                "failed_children": ["NOTES-STORE-001"],
            },
        ),
        ZfEvent(
            type="lane.stage.completed",
            id="evt-lane-done",
            actor="verify-lane-0",
            correlation_id="trace-notes",
            payload={
                "fanout_id": "fanout-notes",
                "stage_id": "verify",
                "pdd_id": "NOTES",
                "task_map_ref": ".zf/artifacts/NOTES/task_map.json",
                "completed_task_ids": ["NOTES-STORE-001"],
                "status": "completed",
            },
        ),
    ])

    assert projection["summary"]["batch_pending"] == 0
    assert projection["batch_checkpoints"] == []
