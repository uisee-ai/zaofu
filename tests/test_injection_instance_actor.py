"""Tests for G-INST-8: briefings teach workers to emit with instance_id actor.

Currently the briefing template hardcodes `--actor {role.name}`. For
multi-instance to be traceable in events.jsonl, each replica must emit
with its own `--actor {instance_id}` so downstream consumers can tell
dev-1's events from dev-2's.
"""

from __future__ import annotations

import pytest

from zf.core.config.schema import ProjectConfig, RoleConfig, SessionConfig, ZfConfig
from zf.core.task.schema import Task
from zf.runtime.injection import generate_task_briefing, _add_completion_protocol


@pytest.fixture
def minimal_config() -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[RoleConfig(name="dev", replicas=2)],
    )


class TestTaskBriefingUsesInstanceIdActor:
    def test_generate_task_briefing_uses_instance_id(self, minimal_config):
        # minimal_config expands to dev-1, dev-2
        dev1 = minimal_config.roles[0]
        assert dev1.instance_id == "dev-1"

        briefing = generate_task_briefing(
            minimal_config, dev1, Task(id="T1", title="x"),
        )
        assert "--actor dev-1" in briefing
        # Must not spill the un-qualified name in the emit command
        assert "--actor dev " not in briefing
        assert "--actor dev\n" not in briefing

    def test_two_replicas_emit_with_different_actors(self, minimal_config):
        dev1, dev2 = minimal_config.roles[0], minimal_config.roles[1]
        b1 = generate_task_briefing(minimal_config, dev1, Task(id="T1", title="x"))
        b2 = generate_task_briefing(minimal_config, dev2, Task(id="T2", title="y"))
        assert "--actor dev-1" in b1
        assert "--actor dev-2" in b2


class TestCompletionProtocolUsesInstanceId:
    def test_completion_protocol_uses_instance_id_actor(self):
        sections: list[str] = []
        role = RoleConfig(name="dev", instance_id="dev-1")
        _add_completion_protocol(sections, role, Task(id="T1", title="x"))
        blob = "\n".join(sections)
        assert "--actor dev-1" in blob

    def test_completion_protocol_legacy_single_instance(self):
        """Legacy config (no replicas) has instance_id == name, so
        `--actor dev` should still appear and stay backward-compatible."""
        sections: list[str] = []
        role = RoleConfig(name="dev")  # instance_id defaults to "dev"
        _add_completion_protocol(sections, role, Task(id="T1", title="x"))
        blob = "\n".join(sections)
        assert "--actor dev" in blob
