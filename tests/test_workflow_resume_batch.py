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
