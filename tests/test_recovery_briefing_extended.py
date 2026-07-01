"""Tests for G-RESUME-5: recovery briefing extensions + wire into respawn.

Adds 3 new sections (progress / instructions / causal chain) plus a
compact=True mode that trims memory to 3 days and events to 10. The
SpawnCoordinator calls build_recovery_briefing after respawn and
feeds the markdown back to the new CLI as the first user message via
transport.send_task.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.memory.store import MemoryStore
from zf.core.task.schema import Task, TaskContract
from zf.runtime.recovery import build_recovery_briefing


def _task():
    return Task(
        id="T1", title="Implement auth", status="in_progress",
        assigned_to="dev",
        contract=TaskContract(behavior="login works"),
    )


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "memory").mkdir()
    return sd


class TestProgressMdSection:
    def test_briefing_includes_progress_section(self, state_dir: Path):
        progress_path = state_dir / "progress.md"
        progress_path.write_text(
            "# Progress\n\n"
            "## Currently Active\n\n"
            "**Tasks**:\n"
            "- `T1` [in_progress] @dev — Implement auth\n\n"
            "## Completed\n\n"
            "_(none)_\n"
        )
        briefing = build_recovery_briefing(state_dir, "dev", _task())
        assert "## Currently Active (from progress.md)" in briefing
        assert "Implement auth" in briefing

    def test_briefing_progress_section_missing_file_graceful(self, state_dir):
        # No progress.md file
        briefing = build_recovery_briefing(state_dir, "dev", _task())
        assert "## Currently Active (from progress.md)" in briefing
        # Should have an inline hint, not crash
        assert "_(no progress.md yet)_" in briefing or "_(none)_" in briefing


class TestInstructionsSection:
    def test_briefing_includes_role_instructions_if_present(self, state_dir: Path):
        instructions_dir = state_dir / "instructions"
        instructions_dir.mkdir()
        (instructions_dir / "dev.md").write_text(
            "You are dev. Implement the task. Run pytest after every change."
        )
        briefing = build_recovery_briefing(state_dir, "dev", _task())
        assert "## Role Instructions" in briefing
        assert "Run pytest" in briefing

    def test_briefing_instructions_missing_is_graceful(self, state_dir: Path):
        briefing = build_recovery_briefing(state_dir, "dev", _task())
        # Either absent section or a "none" placeholder — just don't crash
        assert "dev" in briefing  # role appears somewhere


class TestCausationChainSection:
    def test_briefing_includes_causation_chain_for_task(self, state_dir: Path):
        log = EventLog(state_dir / "events.jsonl")
        e1 = ZfEvent(type="task.dispatched", actor="orchestrator", task_id="T1")
        log.append(e1)
        e2 = ZfEvent(
            type="dev.build.done", actor="dev",
            task_id="T1", causation_id=e1.id,
        )
        log.append(e2)
        briefing = build_recovery_briefing(state_dir, "dev", _task())
        assert "## Causal Chain" in briefing
        assert "task.dispatched" in briefing
        assert "dev.build.done" in briefing


class TestCompactMode:
    def test_compact_briefing_includes_all_sections_still(
        self, state_dir: Path
    ):
        briefing = build_recovery_briefing(state_dir, "dev", _task(), compact=True)
        for section in (
            "## Shared Memory",
            "## Role Memory",
            "## Current Task",
            "## Recent Events",
            "## Git State",
            "## Currently Active",
        ):
            assert section in briefing

    def test_compact_briefing_is_smaller_than_full(self, state_dir: Path):
        log = EventLog(state_dir / "events.jsonl")
        # Dump 50 events so normal-mode briefing has lots, compact has 10
        for i in range(50):
            log.append(ZfEvent(
                type="agent.text", actor="dev", task_id="T1",
                payload={"text": "x" * 80},
            ))
        full = build_recovery_briefing(state_dir, "dev", _task(), compact=False)
        compact = build_recovery_briefing(state_dir, "dev", _task(), compact=True)
        assert len(compact) < len(full)
