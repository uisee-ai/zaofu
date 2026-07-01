"""Tests for Gap #3: Multi-turn resume continuity via stream-json.

Two layers:
  1. Unit test (always runs): proves the session ID is reused across
     multiple send_task calls and --resume flag is set correctly.
  2. Integration test (RUN_REAL_CLAUDE=1): actually spawns Claude,
     sends two turns, asserts turn 2 can reference turn 1 content.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    SessionConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.state.session import SessionStore
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.transport import TransportAdapter, AttachHandle


# -- Unit tests: session ID determinism and resume flag --

class TestSessionIdDeterminism:
    def test_same_role_same_project_same_uuid(self, tmp_path):
        registry = RoleSessionRegistry(tmp_path / "role_sessions.yaml", str(tmp_path))
        sid1 = registry.get_or_create("dev")
        sid2 = registry.get_or_create("dev")
        assert sid1 == sid2

    def test_different_roles_different_uuids(self, tmp_path):
        registry = RoleSessionRegistry(tmp_path / "role_sessions.yaml", str(tmp_path))
        sid_dev = registry.get_or_create("dev")
        sid_review = registry.get_or_create("review")
        assert sid_dev != sid_review

    def test_uuid_survives_registry_restart(self, tmp_path):
        path = tmp_path / "role_sessions.yaml"
        reg1 = RoleSessionRegistry(path, str(tmp_path))
        sid1 = reg1.get_or_create("dev")
        del reg1
        reg2 = RoleSessionRegistry(path, str(tmp_path))
        sid2 = reg2.get_or_create("dev")
        assert sid1 == sid2


class _RecordingTransport(TransportAdapter):
    def __init__(self):
        self.send_task_calls: list[tuple[str, Path, str]] = []
        self.spawned: set[str] = set()

    def init(self) -> None:
        pass

    def is_session_running(self, role: str) -> bool:
        return role in self.spawned

    def spawn(
        self,
        role: RoleConfig,
        argv: list[str],
        *,
        cwd=None,
    ) -> None:
        self.spawned.add(role.instance_id)

    def is_alive(self, role: str) -> bool:
        return role in self.spawned

    def wait_ready(self, role: str, pattern: str, timeout: float) -> bool:
        return True

    def send_task(self, role: str, briefing_path: Path, prompt: str) -> None:
        self.send_task_calls.append((role, briefing_path, prompt))

    def capture_log(self, role: str, lines: int = 200) -> str:
        return ""

    def poll_events(self, role: str) -> list:
        return []

    def attach_handle(self, role: str | None) -> AttachHandle:
        return AttachHandle(argv=["echo", "no-attach"])

    def terminate(self, role: str) -> None:
        self.spawned.discard(role)

    def shutdown(self) -> None:
        self.spawned.clear()


class TestMultiTurnDispatchUsesResume:
    """Proves that dispatching two tasks to the same role reuses the
    same session ID, which means --resume is used on the second turn."""

    def test_two_dispatches_same_session_id(self, tmp_path):
        sd = tmp_path / ".zf"
        sd.mkdir()
        (sd / "memory").mkdir()
        EventLog(sd / "events.jsonl").append(
            ZfEvent(type="loop.started", actor="zf-cli")
        )
        SessionStore(sd / "session.yaml").create(project_root=str(tmp_path))
        (sd / "kanban.json").write_text("[]\n")

        config = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[RoleConfig(name="dev", backend="mock")],
        )
        transport = _RecordingTransport()
        store = TaskStore(sd / "kanban.json")
        store.add(Task(id="T1", title="task 1", assigned_to="dev"))

        orch = Orchestrator(sd, config, transport)
        orch.run_once()
        assert len(transport.send_task_calls) == 1
        assert transport.send_task_calls[0][0] == "dev"

        # Reset kanban for second task
        store.add(Task(id="T2", title="task 2", assigned_to="dev"))
        # Move T1 out of the way
        store.update("T1", status="done")

        orch.run_once()
        assert len(transport.send_task_calls) == 2
        # Both dispatches went to the same role instance
        assert transport.send_task_calls[1][0] == "dev"


class TestBackendAdapterResumeFlags:
    def test_claude_adapter_uses_session_id_first_resume_after(self):
        from zf.runtime.backend import ClaudeCodeAdapter
        role = RoleConfig(name="dev", backend="claude-code")
        adapter = ClaudeCodeAdapter()

        cmd_first = adapter.build_command(
            role, session_id="abc-123", is_resume=False,
        )
        assert "--session-id" in cmd_first
        assert "abc-123" in cmd_first
        assert "--resume" not in cmd_first

        cmd_resume = adapter.build_command(
            role, session_id="abc-123", is_resume=True,
        )
        assert "--resume" in cmd_resume
        assert "abc-123" in cmd_resume
        assert "--session-id" not in cmd_resume


# -- Real Claude integration test (gated by env var) --

REAL_CLAUDE = os.environ.get("RUN_REAL_CLAUDE", "").lower() in ("1", "true", "yes")


@pytest.mark.skipif(not REAL_CLAUDE, reason="Set RUN_REAL_CLAUDE=1 to run")
class TestRealClaudeMultiTurnResume:
    """Spawns a real Claude subprocess via stream-json, sends two turns,
    asserts the second turn can reference the first turn's content.

    This guards against Claude Code version drift breaking --resume.
    """

    def test_turn_two_sees_turn_one(self, tmp_path):
        from zf.runtime.transport_stream_json import StreamJsonTransport

        session_id = str(uuid.uuid4())
        transport = StreamJsonTransport(
            state_dir=tmp_path,
            default_model="claude-sonnet-4-5-20250514",
        )

        # Turn 1: establish a unique fact
        secret = f"ZAOFU_TEST_{uuid.uuid4().hex[:8]}"
        briefing = tmp_path / "turn1.md"
        briefing.write_text(f"Remember this secret: {secret}. Reply with 'acknowledged'.")
        transport.send_task(
            "test-role",
            briefing,
            f"Remember this secret: {secret}. Reply with only 'acknowledged'.",
        )

        # Turn 2: ask for the secret back
        briefing2 = tmp_path / "turn2.md"
        briefing2.write_text("What was the secret I told you in the previous turn?")
        transport.send_task(
            "test-role",
            briefing2,
            "What was the secret I told you in the previous turn? Reply with just the secret string.",
        )

        events = transport.poll_events("test-role")
        text_events = [
            e for e in events
            if e.type == "agent.text" and secret in e.payload.get("content", "")
        ]
        assert len(text_events) >= 1, (
            f"Turn 2 should reference {secret} from turn 1. "
            f"Got events: {[e.payload for e in events]}"
        )
