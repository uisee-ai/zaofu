"""End-to-end integration test for the three-layer architecture (E6).

Uses a scripted fake transport to simulate the Claude Code Orchestrator
(Layer 2) responding to events. Verifies the full chain:

    user → zf chat → user.message event
        → Layer 1 EventWatcher catches
        → Orchestrator._react_to_events sees layer2_active
        → _notify_orchestrator_agent dispatches via transport
        → fake transport simulates Layer 2 calling zf feature add + zf kanban add
        → Layer 1 agent.usage event recorded into cost tracker
        → kanban + feature_list reflect the new state
        → events.jsonl has the full chain
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zf.cli.main import main as cli_main
from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    SessionConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.feature.store import FeatureStore
from zf.core.task.store import TaskStore
from zf.core.state.session import SessionStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


class _ScriptedLayer2Transport(TmuxTransport):
    """Fake transport that simulates the Claude Code Orchestrator (Layer 2)
    making tool calls. When send_task('orchestrator', ...) is called, it runs
    the configured `script` callable which is given the briefing + state_dir
    and can mutate state directly (pretending to be tool_use calls)."""

    def __init__(self, state_dir: Path, script):
        super().__init__(TmuxSession(session_name="fake", dry_run=True))
        self.state_dir = state_dir
        self.script = script
        self.dispatches: list[tuple] = []

    def send_task(self, role_name: str, briefing_path: Path, prompt: str) -> None:
        self.dispatches.append((role_name, briefing_path, prompt))
        if role_name == "orchestrator":
            self.script(self.state_dir, briefing_path)


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "memory").mkdir()
    (state_dir / "logs").mkdir()
    EventLog(state_dir / "events.jsonl").append(
        ZfEvent(type="loop.started", actor="zf-cli")
    )
    SessionStore(state_dir / "session.yaml").create(project_root=str(tmp_path))
    (state_dir / "kanban.json").write_text("[]\n")
    return tmp_path


@pytest.fixture
def safe_team_config():
    return ZfConfig(
        project=ProjectConfig(name="test", state_dir=".zf"),
        session=SessionConfig(tmux_session="test"),
        roles=[
            RoleConfig(
                name="orchestrator",
                backend="claude-code",
                transport="stream-json",
                permission_mode="allowlist",
                allowed_tools=[
                    "Bash(zf feature *)",
                    "Bash(zf kanban *)",
                    "Bash(zf emit *)",
                ],
                stages=["meta"],
                triggers=["user.message", "dev.build.done"],
            ),
            RoleConfig(name="dev", backend="claude-code"),
        ],
    )


def test_zf_chat_triggers_full_layer2_round_trip(project: Path, safe_team_config):
    """The headline test: zf chat → Layer 1 → Layer 2 (fake) → state mutation."""
    # Step 1: human types `zf chat "implement OAuth"`
    cli_main(["chat", "implement OAuth login"])

    # Verify user.message event was written
    log = EventLog(project / ".zf" / "events.jsonl")
    user_msgs = [e for e in log.read_all() if e.type == "user.message"]
    assert len(user_msgs) == 1
    assert "OAuth" in user_msgs[0].payload["message"]

    # Step 2: the scripted "Layer 2" responds to the event by creating a
    # feature, decomposing into a task, and emitting an agent.usage event.
    def fake_orchestrator_agent(state_dir: Path, briefing_path: Path):
        # Read the briefing to verify it contained the trigger
        briefing = briefing_path.read_text()
        assert "user.message" in briefing
        assert "OAuth" in briefing or "## Trigger" in briefing

        # Simulate Layer 2 deciding to create a feature and a task.
        # In real life Claude Code would call `zf feature add` via tool_use;
        # here we shortcut and write directly to the state files.
        from zf.core.feature.schema import Feature
        from zf.core.task.schema import Task
        FeatureStore(state_dir / "feature_list.json").add(Feature(
            id="F-001",
            title="OAuth login",
            user_message="implement OAuth login",
            status="active",
        ))
        TaskStore(state_dir / "kanban.json").add(Task(
            id="T1",
            title="design OAuth flow",
            status="backlog",
        ))
        # Layer 2 also emits an agent.usage event for cost tracking
        EventLog(state_dir / "events.jsonl").append(ZfEvent(
            type="agent.usage",
            actor="orchestrator",
            payload={"usage": {"input_tokens": 1500, "output_tokens": 300}},
        ))

    transport = _ScriptedLayer2Transport(project / ".zf", fake_orchestrator_agent)
    orch = Orchestrator(project / ".zf", safe_team_config, transport)
    orch.run_once()  # Layer 1 reads tail, dispatches to Layer 2

    # Step 3: verify the full chain
    # 3a. Layer 2 was dispatched (and possibly Layer 1 also dispatched the
    # new dev task that the fake orchestrator created — that's OK)
    orch_dispatches = [d for d in transport.dispatches if d[0] == "orchestrator"]
    assert len(orch_dispatches) == 1

    # 3b. Feature was created (Layer 2 wrote it via fake tool calls)
    fs = FeatureStore(project / ".zf" / "feature_list.json")
    features = fs.list_all()
    assert len(features) == 1
    assert features[0].title == "OAuth login"

    # 3c. Task was created
    ts = TaskStore(project / ".zf" / "kanban.json")
    tasks = ts.list_all()
    assert len(tasks) == 1
    assert tasks[0].title == "design OAuth flow"

    # 3d. agent.usage event was recorded into cost tracker
    # (running another run_once picks up the agent.usage and applies housekeeping;
    #  this also regenerates progress.md so it now reflects post-Layer-2 state)
    orch.run_once()

    # 3e. progress.md was regenerated and reflects new state
    progress = (project / ".zf" / "progress.md").read_text()
    assert "OAuth login" in progress
    assert "design OAuth flow" in progress

    cost_jsonl = project / ".zf" / "cost.jsonl"
    assert cost_jsonl.exists()
    lines = [l for l in cost_jsonl.read_text().splitlines() if l.strip()]
    assert len(lines) >= 1
    entry = json.loads(lines[0])
    assert entry["role"] == "orchestrator"
    assert entry["input_tokens"] == 1500


def test_dev_build_done_triggers_layer2_decision(project: Path, safe_team_config):
    """A dev.build.done event with Layer 2 active does NOT auto-move the task.
    Layer 2 makes the decision."""
    from zf.core.task.schema import Task
    ts = TaskStore(project / ".zf" / "kanban.json")
    ts.add(Task(id="T1", title="x", status="in_progress", assigned_to="dev"))

    EventLog(project / ".zf" / "events.jsonl").append(
        ZfEvent(type="dev.build.done", actor="dev", task_id="T1")
    )

    decisions_seen = []

    def fake_orchestrator_agent(state_dir, briefing_path):
        decisions_seen.append(briefing_path.read_text())

    transport = _ScriptedLayer2Transport(project / ".zf", fake_orchestrator_agent)
    orch = Orchestrator(project / ".zf", safe_team_config, transport)
    orch.run_once()

    # Layer 2 was called
    assert len(transport.dispatches) == 1
    # Task is STILL in_progress (Layer 2 has not made a decision yet in this fake)
    task = ts.get("T1")
    assert task.status == "in_progress"


def test_event_offset_persisted_after_layer2_dispatch(project: Path, safe_team_config):
    """Restart safety: after Layer 2 dispatch, offset persists so restart
    does not re-deliver the same event."""
    EventLog(project / ".zf" / "events.jsonl").append(
        ZfEvent(type="user.message", actor="human", payload={"message": "hi"})
    )
    transport = _ScriptedLayer2Transport(project / ".zf", lambda *a, **k: None)
    orch1 = Orchestrator(project / ".zf", safe_team_config, transport)
    orch1.run_once()
    assert len(transport.dispatches) == 1

    # Restart: fresh orchestrator instance, same state
    transport2 = _ScriptedLayer2Transport(project / ".zf", lambda *a, **k: None)
    orch2 = Orchestrator(project / ".zf", safe_team_config, transport2)
    orch2.run_once()
    # Should NOT re-deliver the user.message
    assert len(transport2.dispatches) == 0
