from __future__ import annotations

from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.handoff_summary import project_handoff_summary


def _state(tmp_path: Path) -> Path:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    return state_dir


def test_handoff_summary_combines_state_packet_and_resume_packet_without_writes(
    tmp_path: Path,
) -> None:
    state_dir = _state(tmp_path)
    task = Task(
        id="TASK-1",
        title="Implement queue recovery",
        status="in_progress",
        assigned_to="dev-1",
        active_dispatch_id="disp-1",
        contract=TaskContract(
            behavior="Queue recovery works after worker crash",
            owner_role="dev",
            owner_instance="dev-1",
            evidence_contract={"required_events": ["test.passed"]},
        ),
    )
    TaskStore(state_dir / "kanban.json").add(task)
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    writer.append(ZfEvent(
        id="evt-1",
        type="task.dispatched",
        actor="orchestrator",
        task_id="TASK-1",
        payload={"dispatch_id": "disp-1"},
    ))
    writer.append(ZfEvent(
        id="evt-2",
        type="dev.build.done",
        actor="dev-1",
        task_id="TASK-1",
        payload={"dispatch_id": "disp-1", "files_touched": ["src/runtime/queue.py"]},
    ))
    events = list(enumerate(EventLog(state_dir / "events.jsonl").read_all(), start=1))

    summary = project_handoff_summary(
        state_dir,
        "TASK-1",
        task=task,
        task_events=events,
    )

    assert summary["schema_version"] == "handoff-summary.v1"
    assert summary["task_id"] == "TASK-1"
    assert summary["objective"] == "Queue recovery works after worker crash"
    assert summary["current_stage"] == "static_gate"
    assert summary["owner"]["role"] == "dev"
    assert summary["next_required_event"] in {"static_gate.passed", "test.passed"}
    assert "src/runtime/queue.py" in summary["changed_files"]
    assert any(item.get("event_id") == "evt-2" for item in summary["completed"])
    assert summary["missing_evidence"]
    assert "evt-2" in summary["source_event_ids"]
    assert summary["quality"]["status"] == "needs_handoff_fix"
    assert {
        item["field"] for item in summary["quality"]["gaps"]
    } >= {"test_evidence_present", "risks_recorded"}
    assert not (state_dir / "resume_packets").exists()


def test_handoff_summary_quality_accepts_test_risk_and_next_action(
    tmp_path: Path,
) -> None:
    state_dir = _state(tmp_path)
    task = Task(
        id="TASK-2",
        title="Verify relay",
        status="in_progress",
        assigned_to="verify-1",
        active_dispatch_id="disp-2",
        contract=TaskContract(
            behavior="Relay has enough evidence for the next worker",
            owner_role="verify",
            owner_instance="verify-1",
            evidence_contract={"required_events": ["judge.passed"]},
        ),
    )
    TaskStore(state_dir / "kanban.json").add(task)
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    writer.append(ZfEvent(
        id="evt-1",
        type="task.dispatched",
        actor="orchestrator",
        task_id="TASK-2",
        payload={"dispatch_id": "disp-2"},
    ))
    writer.append(ZfEvent(
        id="evt-2",
        type="test.passed",
        actor="verify-1",
        task_id="TASK-2",
        payload={
            "dispatch_id": "disp-2",
            "changed_files": ["tests/test_relay.py"],
            "residual_risks": ["runner lifecycle not covered here"],
            "command": "uv run pytest tests/test_relay.py",
        },
    ))
    events = list(enumerate(EventLog(state_dir / "events.jsonl").read_all(), start=1))

    summary = project_handoff_summary(
        state_dir,
        "TASK-2",
        task=task,
        task_events=events,
    )

    assert summary["quality"]["status"] == "accepted"
    assert summary["quality"]["score"] == summary["quality"]["max_score"]
    assert summary["risks"] == ["runner lifecycle not covered here"]
    assert summary["next_required_event"] == "judge.passed"
