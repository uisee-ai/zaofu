"""End-to-end: candidate.conflict appended to events.jsonl → Orchestrator
run_once → kernel auto-promotes it into a memory.note → MemoryStore writes
.zf/memory/shared.md → recovery briefing renderer sees the new entry.

This is the smoke test for `docs/impl/21-memory-store-wire-up.md`. If this
test passes, the closed loop from "kernel-level system event" to
"future-session briefing memory section" is end-to-end working without
worker cooperation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.config.schema import ProjectConfig, RoleConfig, SessionConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.memory.store import MemoryStore
from zf.core.state.session import SessionStore
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "memory").mkdir()
    EventLog(sd / "events.jsonl").append(
        ZfEvent(type="loop.started", actor="zf-cli")
    )
    SessionStore(sd / "session.yaml").create(project_root=str(tmp_path))
    (sd / "kanban.json").write_text("[]\n")
    return sd


@pytest.fixture
def orch(state_dir: Path) -> Orchestrator:
    config = ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[RoleConfig(name="dev", backend="mock")],
    )
    transport = TmuxTransport(TmuxSession(session_name="t", dry_run=True))
    return Orchestrator(state_dir, config, transport)


def test_candidate_conflict_lands_in_shared_memory_md(
    state_dir: Path, orch: Orchestrator
):
    """The full path: append candidate.conflict → run_once → assert
    .zf/memory/shared.md exists with [context] entry."""
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="candidate.conflict",
        actor="zf-cli",
        task_id="TASK-DEMO",
        payload={
            "branch": "candidate/F-demo",
            "conflict_files": ["packages/core/package.json"],
            "failed_task_id": "TASK-DEMO",
            "base_commit": "abc123def456",
        },
    ))

    orch.run_once()

    shared_mem = state_dir / "memory" / "shared.md"
    assert shared_mem.exists(), "shared.md should be created by auto-promote"
    text = shared_mem.read_text(encoding="utf-8")
    assert "type: context" in text
    assert "packages/core/package.json" in text
    assert "TASK-DEMO" in text


def test_dev_blocked_lands_in_shared_memory_md(
    state_dir: Path, orch: Orchestrator
):
    # dev.blocked is a worker lifecycle event; the kernel rejects it as
    # malformed when the task is unknown, so seed kanban first so the
    # event reaches _apply_housekeeping → auto-promote.
    ts = TaskStore(state_dir / "kanban.json")
    ts.add(Task(
        id="TASK-PIPE",
        title="pipe",
        contract=TaskContract(behavior="x"),
        assigned_to="dev-1",
    ))

    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="dev.blocked",
        actor="dev-1",
        task_id="TASK-PIPE",
        payload={"reason": "pnpm install needs network access"},
    ))

    orch.run_once()

    shared_mem = state_dir / "memory" / "shared.md"
    assert shared_mem.exists()
    text = shared_mem.read_text(encoding="utf-8")
    assert "type: fix" in text
    assert "TASK-PIPE" in text
    assert "pnpm install needs network access" in text


def test_promoted_note_visible_in_recovery_briefing(
    state_dir: Path, orch: Orchestrator, tmp_path: Path
):
    """Recovery briefing renderer reads MemoryStore. After auto-promote,
    the same content must surface in the briefing's Shared Memory section
    — that's the whole point of the wire-up.

    StalenessChecker filters entries whose content mentions paths that
    don't exist in the workspace, so we create the path before asserting
    the briefing contains it."""
    # StalenessChecker scans every slash-bearing word in the entry content;
    # create both the file path and the branch path so the entry survives
    # the stale filter when rendered into the briefing.
    pkg_json = tmp_path / "packages/state/package.json"
    pkg_json.parent.mkdir(parents=True, exist_ok=True)
    pkg_json.write_text("{}\n")
    branch_marker = tmp_path / "candidate/F-rb"
    branch_marker.parent.mkdir(parents=True, exist_ok=True)
    branch_marker.write_text("")

    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="candidate.conflict",
        task_id="TASK-RB",
        payload={
            "branch": "candidate/F-rb",
            "conflict_files": ["packages/state/package.json"],
            "failed_task_id": "TASK-RB",
            "base_commit": "feedfacebeef",
        },
    ))
    orch.run_once()

    from zf.runtime.recovery import build_recovery_briefing

    task = Task(
        id="TASK-NEXT",
        title="follow-up",
        contract=TaskContract(behavior="next round"),
    )
    briefing = build_recovery_briefing(
        state_dir=state_dir,
        role="dev",
        task=task,
        recent_events_limit=20,
        compact=False,
    )
    assert "Shared Memory" in briefing
    assert "packages/state/package.json" in briefing, (
        "promoted memory.note should appear in next session's recovery briefing"
    )


def test_replay_does_not_duplicate_promoted_note(
    state_dir: Path, orch: Orchestrator
):
    """Process-level dedupe: running run_once twice over the same backlog
    must not append a second copy of the auto-promoted memory.note."""
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="candidate.conflict",
        payload={"conflict_files": ["x"], "failed_task_id": "TASK-DUP"},
    ))

    orch.run_once()
    orch.run_once()
    orch.run_once()

    notes = [e for e in log.read_all() if e.type == "memory.note"]
    assert len(notes) == 1, f"expected 1 promoted note, got {len(notes)}"
