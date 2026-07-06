"""needs_gate_dispatch 无可执行路由时的可见性(r5 SCENE-001 正向通路半步)。"""

from __future__ import annotations

from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.workflow_resume import WorkflowResumeCheckpoint
from zf.runtime.workflow_resume_apply import _apply_checkpoint
from zf.core.task.store import TaskStore
from zf.core.task.schema import Task


def _checkpoint(action: str) -> WorkflowResumeCheckpoint:
    return WorkflowResumeCheckpoint(
        task_id="T-GATE",
        last_trusted_event_id="",
        last_completed_stage="workflow.child",
        expected_next_stage="avbs-impl:aggregate",
        expected_next_role="",
        blocking_event_id="",
        safe_resume_action=action,
        idempotency_key="wfres-test",
    )


def test_gate_dispatch_without_dispatcher_emits_unroutable(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]")
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="T-GATE", title="x", status="in_progress"))

    result = _apply_checkpoint(
        store, writer, _checkpoint("needs_gate_dispatch"),
        events=[], gate_dispatcher=None,
    )
    assert result.applied is True  # stalled 记录照常
    types = [e.type for e in log.read_all()]
    assert "workflow.resume.gate_unroutable" in types


def test_blocked_external_gate_does_not_emit(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]")
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    store = TaskStore(state_dir / "kanban.json")

    _apply_checkpoint(
        store, writer, _checkpoint("blocked_external_gate"),
        events=[], gate_dispatcher=None,
    )
    assert "workflow.resume.gate_unroutable" not in [e.type for e in log.read_all()]
