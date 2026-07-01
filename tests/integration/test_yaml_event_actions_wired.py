"""P0-2 integration: YAML workflow.event_actions actually plug into the
orchestrator reactor, and custom events trigger registered actions.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.config.loader import load_config
from zf.core.config.schema import ProjectConfig, RoleConfig, WorkflowConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


def _make_config(event_actions: list[dict]) -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="test"),
        workflow=WorkflowConfig(event_actions=event_actions),
        roles=[
            RoleConfig(
                name="dev", backend="mock", stages=["implement"],
                publishes=["dev.build.done"],
            ),
        ],
    )


def test_builtin_handlers_registered_by_default(tmp_path):
    """Built-in registrations survive the refactor."""
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "events.jsonl").touch()

    config = _make_config(event_actions=[])
    transport = TmuxTransport(TmuxSession(session_name="t", dry_run=True))
    orch = Orchestrator(state_dir, config, transport)

    # Core built-ins registered
    handled = orch.event_registry.handled_events()
    for event in (
        "dev.build.done", "arch.proposal.done", "design.critique.done",
        "review.approved", "review.rejected", "review.suspended",
        "verify.passed", "verify.failed",
        "test.passed", "test.failed", "test.suspended",
        "judge.passed", "judge.failed",
        "dev.blocked", "gate.failed",
    ):
        assert event in handled


def test_yaml_event_action_appends_to_registry(tmp_path):
    """Custom YAML event_actions are registered alongside built-ins."""
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "events.jsonl").touch()

    config = _make_config(event_actions=[
        {
            "event": "custom.milestone",
            "actions": [
                {"type": "emit", "params": {"event": "custom.ack"}},
            ],
        },
    ])
    transport = TmuxTransport(TmuxSession(session_name="t", dry_run=True))
    orch = Orchestrator(state_dir, config, transport)

    assert "custom.milestone" in orch.event_registry.handled_events()
    # Built-in still present
    assert "dev.build.done" in orch.event_registry.handled_events()


def test_yaml_emit_action_fires_on_matching_event(tmp_path):
    """When a custom event arrives, YAML emit action produces the derived event."""
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    events_path = state_dir / "events.jsonl"
    events_path.touch()

    config = _make_config(event_actions=[
        {
            "event": "custom.trigger",
            "actions": [
                {"type": "emit", "params": {"event": "custom.notified"}},
            ],
        },
    ])
    transport = TmuxTransport(TmuxSession(session_name="t", dry_run=True))

    # Pre-create a task so the task_id gate doesn't skip this path
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="T1", title="x", status="in_progress"))

    orch = Orchestrator(state_dir, config, transport)

    # Emit the custom trigger
    trigger = ZfEvent(type="custom.trigger", actor="ext", task_id="T1")
    orch.event_log.append(trigger)

    # Run the orchestrator cycle — it should pick up custom.trigger
    # via registry and fire the emit action
    orch.run_once(events=[trigger])

    # Re-read events — derived event should be present
    all_events = list(EventLog(events_path).read_all())
    types = [e.type for e in all_events]
    assert "custom.notified" in types

    # Derived event should have causation_id pointing to the trigger
    derived = next(e for e in all_events if e.type == "custom.notified")
    assert derived.causation_id == trigger.id


def test_yaml_noop_registers_without_side_effect(tmp_path):
    """Noop action is a valid "observe but don't react" registration."""
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "events.jsonl").touch()

    config = _make_config(event_actions=[
        {"event": "my.observation", "actions": [{"type": "noop"}]},
    ])
    transport = TmuxTransport(TmuxSession(session_name="t", dry_run=True))
    orch = Orchestrator(state_dir, config, transport)

    assert "my.observation" in orch.event_registry.handled_events()
    # Fire — should not error, should not emit anything
    trigger = ZfEvent(type="my.observation", task_id="T1")
    orch.event_log.append(trigger)
    prior_count = len(list(orch.event_log.read_all()))
    orch.run_once(events=[trigger])
    after_count = len(list(orch.event_log.read_all()))
    # Only the trigger event exists, no derived events
    assert after_count == prior_count


def test_config_loader_parses_event_actions_from_yaml(tmp_path):
    """zf.yaml with workflow.event_actions loads into WorkflowConfig."""
    yaml_path = tmp_path / "zf.yaml"
    yaml_path.write_text("""
version: "1.0"
project:
  name: test
workflow:
  gan_rounds: 2
  event_actions:
    - event: custom.milestone
      actions:
        - type: emit
          params:
            event: task.notification
        - type: noop
roles:
  - name: dev
    publishes: [dev.build.done]
""")
    config = load_config(yaml_path)
    assert config.workflow.gan_rounds == 2
    assert len(config.workflow.event_actions) == 1
    entry = config.workflow.event_actions[0]
    assert entry["event"] == "custom.milestone"
    assert len(entry["actions"]) == 2
