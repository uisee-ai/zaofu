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


def test_force_gate_dispatch_routes_blocked_external_gate(tmp_path: Path) -> None:
    """FIX-2(bizsim r4):operator 显式强制时,blocked_external_gate 可借
    out-of-band dispatcher 推进;默认(无 force)保持 stalled 不误派。"""
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]")
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="T-GATE", title="x", status="in_progress"))

    blocking = ZfEvent(type="candidate.ready", actor="zf-cli", payload={})
    dispatched: list[str] = []

    checkpoint = _checkpoint("blocked_external_gate")
    checkpoint = WorkflowResumeCheckpoint(
        **{**checkpoint.to_dict(), "blocking_event_id": blocking.id},
    )

    # 默认:不派发,stalled。
    _apply_checkpoint(
        store, writer, checkpoint,
        events=[blocking], gate_dispatcher=lambda e: dispatched.append(e.id),
    )
    assert dispatched == []

    # operator 强制:走 out-of-band 派发,事件标注 operator 强制来源。
    result = _apply_checkpoint(
        store, writer, checkpoint,
        events=[blocking],
        gate_dispatcher=lambda e: dispatched.append(e.id),
        force_gate_dispatch=True,
    )
    assert dispatched == [blocking.id]
    assert result.applied is True
    applied = [
        e for e in log.read_all()
        if e.type == "workflow.resume.applied"
        and e.payload.get("mode") == "operator_forced_gate_dispatch"
    ]
    assert len(applied) == 1
