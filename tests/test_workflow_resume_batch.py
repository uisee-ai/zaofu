from __future__ import annotations

from pathlib import Path

from zf.core.config.loader import load_config
from zf.core.events.model import ZfEvent
from zf.runtime.workflow_resume import build_workflow_resume_projection


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
