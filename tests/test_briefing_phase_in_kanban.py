"""Tests for R-TASK-STATE-AXIS-01: briefing _render_kanban shows phase.

When events.jsonl has stage-progress events for a task, the briefing's
kanban listing should include `phase=<derived>` so Layer 2 can see
where in the pipeline each task currently is.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator_briefing import _render_kanban


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "kanban.json").write_text("[]")
    EventLog(sd / "events.jsonl").append(
        ZfEvent(type="loop.started", actor="zf-cli")
    )
    return sd


def _add_task(state_dir: Path, **kwargs) -> None:
    TaskStore(state_dir / "kanban.json").add(Task(**kwargs))


def _emit(state_dir: Path, type_: str, task_id: str) -> None:
    EventLog(state_dir / "events.jsonl").append(
        ZfEvent(type=type_, actor="t", task_id=task_id, payload={})
    )


class TestKanbanPhaseRendering:
    def test_no_events_no_phase_tag(self, state_dir):
        _add_task(state_dir, id="T1", title="x", status="backlog")
        out = _render_kanban(state_dir)
        assert "T1" in out
        assert "phase=" not in out

    def test_dev_build_done_shows_phase(self, state_dir):
        _add_task(
            state_dir, id="T1", title="x", status="in_progress",
            assigned_to="review",
        )
        _emit(state_dir, "dev.build.done", "T1")
        out = _render_kanban(state_dir)
        assert "phase=build_done" in out

    def test_review_approved_shows_phase(self, state_dir):
        _add_task(
            state_dir, id="T1", title="x", status="in_progress",
            assigned_to="test",
        )
        _emit(state_dir, "dev.build.done", "T1")
        _emit(state_dir, "review.approved", "T1")
        out = _render_kanban(state_dir)
        assert "phase=review_approved" in out

    def test_unrelated_task_does_not_pollute(self, state_dir):
        _add_task(
            state_dir, id="T1", title="x", status="in_progress",
            assigned_to="dev-1",
        )
        _add_task(
            state_dir, id="T2", title="y", status="in_progress",
            assigned_to="review",
        )
        _emit(state_dir, "dev.build.done", "T2")
        out = _render_kanban(state_dir)
        # T1 has no events → no phase
        # T2 has dev.build.done → phase=build_done
        t1_line = next(l for l in out.splitlines() if "T1" in l)
        t2_line = next(l for l in out.splitlines() if "T2" in l)
        assert "phase=" not in t1_line
        assert "phase=build_done" in t2_line

    def test_no_kanban_returns_placeholder(self, tmp_path):
        sd = tmp_path / ".zf"
        sd.mkdir()
        # No kanban.json
        out = _render_kanban(sd)
        assert out == "_(no tasks)_"

    def test_empty_kanban_returns_placeholder(self, state_dir):
        # state_dir has empty []
        out = _render_kanban(state_dir)
        assert out == "_(no tasks)_"
