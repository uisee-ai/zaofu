"""Tests for the uniform recovery briefing assembler (B4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.memory.store import MemoryStore
from zf.core.state.git_state import GitState
from zf.core.task.schema import Task, TaskContract
from zf.runtime.git_capture import GitDiffContext
from zf.runtime.recovery import build_recovery_briefing


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "memory").mkdir()
    return sd


def _task() -> Task:
    return Task(
        id="T1",
        title="Implement auth module",
        status="in_progress",
        assigned_to="dev",
        contract=TaskContract(
            behavior="login + logout work",
            verification="pytest tests/test_auth.py",
            scope=["src/auth.py", "tests/test_auth.py"],
        ),
    )


def test_briefing_contains_five_sections(state_dir: Path):
    briefing = build_recovery_briefing(state_dir, role="dev", task=_task())
    # Five expected section headings
    assert "## Shared Memory" in briefing
    assert "## Role Memory" in briefing
    assert "## Current Task" in briefing
    assert "## Recent Events" in briefing
    assert "## Git State" in briefing


def test_briefing_includes_task_contract(state_dir: Path):
    briefing = build_recovery_briefing(state_dir, role="dev", task=_task())
    assert "T1" in briefing
    assert "Implement auth module" in briefing
    assert "login + logout work" in briefing
    assert "pytest tests/test_auth.py" in briefing
    assert "src/auth.py" in briefing


def test_recovery_briefing_points_to_task_doc_and_rich_contract(state_dir: Path):
    task = Task(
        id="T2",
        title="Resume rich contract",
        status="in_progress",
        assigned_to="dev",
        active_dispatch_id="disp-2",
        contract=TaskContract(
            behavior="resume from task.md",
            verification="pytest tests/test_recovery_briefing.py",
            spec_ref="docs/spec.md",
            plan_ref="docs/plan.md",
            tdd_ref="tests/test_recovery_briefing.py",
            acceptance_criteria=["task doc loaded before continuing"],
            evidence_contract={"required_events": ["dev.build.done"]},
        ),
    )

    briefing = build_recovery_briefing(state_dir, role="dev", task=task)

    assert "## Recovery Read Order" in briefing
    assert "task_doc:" in briefing
    assert "source_doc:" in briefing
    assert "progress_doc:" in briefing
    assert "source_revision:" in briefing
    assert "contract_revision:" in briefing
    assert "capsule_revision:" in briefing
    assert "Do not edit task.md to mark completion" in briefing
    assert (state_dir / "task_docs" / "T2" / "task.md").exists()
    assert "**spec_ref**: `docs/spec.md`" in briefing
    assert "**plan_ref**: `docs/plan.md`" in briefing
    assert "**tdd_ref**: `tests/test_recovery_briefing.py`" in briefing
    assert "task doc loaded before continuing" in briefing
    assert "required_events" in briefing


def test_briefing_includes_recent_events_for_role(state_dir: Path):
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(type="task.dispatched", actor="orchestrator", task_id="T1"))
    log.append(ZfEvent(type="dev.note", actor="dev", task_id="T1"))
    log.append(ZfEvent(type="other.event", actor="other", task_id="T2"))
    briefing = build_recovery_briefing(state_dir, role="dev", task=_task())
    # Events for T1 should appear, T2 should not
    assert "task.dispatched" in briefing
    assert "dev.note" in briefing
    assert "other.event" not in briefing


def test_briefing_includes_role_memory(state_dir: Path):
    mem = MemoryStore(state_dir / "memory")
    mem.add(role=None, mem_type="decision", content="use bcrypt for password hashing")
    mem.add(role="dev", mem_type="pattern", content="prefer composition over inheritance")
    briefing = build_recovery_briefing(state_dir, role="dev", task=_task())
    assert "bcrypt" in briefing
    assert "composition over inheritance" in briefing


def test_briefing_other_role_memory_not_included(state_dir: Path):
    mem = MemoryStore(state_dir / "memory")
    mem.add(role="review", mem_type="pattern", content="reject methods longer than 50 lines")
    briefing = build_recovery_briefing(state_dir, role="dev", task=_task())
    assert "50 lines" not in briefing


def test_briefing_handles_missing_memory_gracefully(state_dir: Path):
    # No memory files at all — must still produce a valid briefing
    briefing = build_recovery_briefing(state_dir, role="dev", task=_task())
    assert "## Shared Memory" in briefing
    assert "## Role Memory" in briefing


def test_briefing_includes_git_state_when_provided(state_dir: Path):
    git = GitState(branch="main", head="abc1234567" + "0" * 30, dirty_files=["src/x.py"])
    briefing = build_recovery_briefing(state_dir, role="dev", task=_task(), git_state=git)
    assert "main" in briefing
    assert "abc1234567" in briefing
    assert "src/x.py" in briefing


def test_briefing_handles_missing_git_state_gracefully(state_dir: Path):
    briefing = build_recovery_briefing(state_dir, role="dev", task=_task(), git_state=None)
    assert "## Git State" in briefing  # heading present
    assert "no git state" in briefing.lower()


def test_briefing_renders_git_diff_context(state_dir: Path):
    context = GitDiffContext(
        base_sha="aaa111",
        branch="dev",
        head="bbb222",
        commits=["bbb222 add feature"],
        files_touched=["src/x.py", "tests/test_x.py"],
        dirty_files=["src/x.py"],
        diff_stat="src/x.py | 2 ++",
    )
    briefing = build_recovery_briefing(
        state_dir,
        role="dev",
        task=_task(),
        git_context=context,
    )
    assert "**Base**: `aaa111`" in briefing
    assert "add feature" in briefing
    assert "src/x.py" in briefing
    assert "Diff stat" in briefing


def test_briefing_excludes_old_archived_memory(state_dir: Path):
    """G-MEM-4 tail: recovery should only inject the last 7 days of memory.
    A 30-day-old archived entry must NOT bleed into the briefing."""
    mem_dir = state_dir / "memory"
    mem_dir.mkdir(exist_ok=True)
    # Today's active entry — should appear
    mem = MemoryStore(mem_dir)
    mem.add(role="dev", mem_type="pattern", content="recent insight worth keeping")

    # Manually craft a 30-day-old archive file
    archive_dir = mem_dir / "dev"
    archive_dir.mkdir(parents=True, exist_ok=True)
    from datetime import datetime, timezone, timedelta
    old_date = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    old_iso = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    old_file = archive_dir / f"{old_date}.md"
    old_file.write_text(
        f"<!-- type: pattern; max_days: 60; last_updated: {old_iso} -->\n"
        "## ancient unused note\nancient unused note\n",
        encoding="utf-8",
    )

    briefing = build_recovery_briefing(state_dir, role="dev", task=_task())
    assert "recent insight worth keeping" in briefing
    assert "ancient unused note" not in briefing
