"""End-to-end tests for housekeeping events through Orchestrator.run_once.

These cover the chain that sprint-memory-events.md asked for:

  zf emit memory.note  →  events.jsonl  →  EventWatcher  →  run_once
                       →  _react_to_events  →  _apply_housekeeping
                       →  MemoryStore.add  →  .zf/memory/<role>.md

(Same chain for task.contract.update → kanban.json contract field.)

The orchestrator is exercised in legacy mode (no Layer 2 role) so the
Python kernel handles everything.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.config.schema import ProjectConfig, RoleConfig, SessionConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.session import SessionStore
from zf.core.task.schema import Task
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
def config():
    return ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[RoleConfig(name="dev", backend="mock")],
    )


@pytest.fixture
def transport():
    return TmuxTransport(TmuxSession(session_name="t", dry_run=True))


class TestMemoryNoteEndToEnd:
    def test_emit_memory_note_writes_to_role_file(
        self, state_dir: Path, config, transport
    ):
        """Full chain: emit memory.note → run_once → housekeeping →
        .zf/memory/dev.md contains the content."""
        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(
            type="memory.note",
            actor="dev",
            payload={
                "mem_type": "decision",
                "content": "Use bcrypt for password hashing",
            },
        ))

        orch = Orchestrator(state_dir, config, transport)
        orch.run_once()

        dev_mem = state_dir / "memory" / "dev.md"
        assert dev_mem.exists()
        text = dev_mem.read_text(encoding="utf-8")
        assert "bcrypt" in text
        assert "type: decision" in text

    def test_emit_memory_note_no_actor_writes_shared(
        self, state_dir: Path, config, transport
    ):
        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(
            type="memory.note",
            actor=None,
            payload={
                "mem_type": "context",
                "content": "Project uses Python 3.12",
            },
        ))

        orch = Orchestrator(state_dir, config, transport)
        orch.run_once()

        shared_mem = state_dir / "memory" / "shared.md"
        assert shared_mem.exists()
        assert "Python 3.12" in shared_mem.read_text(encoding="utf-8")


class TestTaskContractUpdateEndToEnd:
    def test_emit_task_contract_update_lands_in_kanban(
        self, state_dir: Path, config, transport
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="Build OAuth"))

        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(
            type="task.contract.update",
            actor="orchestrator",
            task_id="T1",
            payload={
                "contract": {
                    "behavior": "User can sign in via Google OAuth",
                    "verification": "pytest tests/test_oauth.py",
                },
            },
        ))

        orch = Orchestrator(state_dir, config, transport)
        orch.run_once()

        task = store.get("T1")
        assert task is not None
        assert "Google OAuth" in task.contract.behavior
        assert "test_oauth.py" in task.contract.verification
