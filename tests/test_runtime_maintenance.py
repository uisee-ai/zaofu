from __future__ import annotations

from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.maintenance import (
    create_checkpoint,
    enter_maintenance,
    exit_maintenance,
)


def test_enter_and_exit_maintenance_emit_events(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"

    current = enter_maintenance(
        state_dir,
        trigger_id="trig-1",
        reason="repair",
    )
    exit_maintenance(
        state_dir,
        repair_run_id="repair-1",
        validation_summary="passed",
    )

    assert current.exists()
    events = EventLog(state_dir / "events.jsonl").read_all()
    types = [event.type for event in events]
    assert "runtime.maintenance.entered" in types
    assert "dispatch.paused" in types
    assert "runtime.maintenance.exited" in types
    assert "dispatch.resumed" in types


def test_create_checkpoint_writes_resume_packet_and_event(tmp_path: Path) -> None:
    project = tmp_path / "project"
    state_dir = project / ".zf"
    project.mkdir()
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        type="worker.progress",
        actor="dev-1",
        task_id="TASK-1",
    ))

    checkpoint = create_checkpoint(
        state_dir,
        project_root=project,
        task_id="TASK-1",
        role="dev",
        assigned_worker="dev-1",
        last_progress="half done",
    )

    assert checkpoint.task_id == "TASK-1"
    assert checkpoint.last_event_id
    assert Path(checkpoint.resume_packet_path).exists()
    assert Path(checkpoint.dirty_diff_artifact).exists()
    events = EventLog(state_dir / "events.jsonl").read_all()
    assert events[-1].type == "worker.checkpointed"


def test_controlled_action_maintenance_prepare_pauses_and_checkpoints(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    state_dir = project / ".zf"
    project.mkdir()
    event_log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(event_log)
    event_log.append(ZfEvent(
        type="worker.progress",
        actor="dev-1",
        task_id="TASK-1",
    ))
    requested = writer.emit(
        "web.action.requested",
        actor="web",
        task_id="TASK-1",
        payload={"action": "maintenance-prepare"},
    )

    response = ControlledActionService(
        state_dir,
        writer,
        project_root=project,
        actor="web",
        source="maintenance",
        surface="web",
    ).execute(
        action="maintenance-prepare",
        requested_action="maintenance.prepare",
        requested=requested,
        payload={
            "trigger_id": "trig-1",
            "reason": "repair zaofu bug",
            "task_id": "TASK-1",
            "checkpoint": True,
            "role": "dev",
            "assigned_worker": "dev-1",
            "last_progress": "half done",
        },
    )

    assert response["ok"] is True
    assert response["status"] == "prepared"
    assert response["checkpoint_id"].startswith("ckpt-TASK-1-")
    assert Path(response["maintenance_current"]).exists()
    assert Path(response["checkpoint_path"]).exists()
    types = [event.type for event in event_log.read_all()]
    assert "runtime.maintenance.entered" in types
    assert "dispatch.paused" in types
    assert "worker.checkpointed" in types
    assert "runtime.action.completed" in types
    assert "web.action.completed" in types


def test_controlled_action_attention_lifecycle_emits_attention_event(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    event_log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(event_log)
    requested = writer.emit(
        "web.action.requested",
        actor="web",
        payload={"action": "attention-ack"},
    )

    response = ControlledActionService(
        state_dir,
        writer,
        actor="web",
        source="attention",
        surface="web",
    ).execute(
        action="attention-ack",
        requested_action="attention.ack",
        requested=requested,
        payload={
            "attention_id": "attn-1",
            "fingerprint": "autopilot:stuck",
            "reason": "operator reviewed",
        },
    )

    assert response["ok"] is True
    assert response["event_type"] == "runtime.attention.acknowledged"
    types = [event.type for event in event_log.read_all()]
    assert "runtime.attention.acknowledged" in types
    assert "runtime.action.completed" in types
    assert "web.action.completed" in types
