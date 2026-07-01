"""Tests for G-RECYCLE-7: new context events in wake_patterns + Feishu routing."""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path

import pytest

from zf.core.events.model import ZfEvent
from zf.core.verification.event_schema import (
    EventSchemaRegistry,
    context_event_schema_rules,
    fanout_request_schema_rules,
    progress_event_schema_rules,
)
from zf.integrations.feishu.projection import (
    ProjectionRouter,
    RoutingConfig,
    _MUST_PUSH,
    _ROUTING,
)
from zf.integrations.feishu.transport import MockFeishuTransport


class TestWakePatterns:
    def _wake_patterns(self) -> list[str]:
        from zf.runtime.wake_patterns import WAKE_PATTERNS
        return list(WAKE_PATTERNS)

    def test_context_warning_in_wake_patterns(self):
        assert "worker.context.warning" in self._wake_patterns()

    def test_context_critical_in_wake_patterns(self):
        assert "worker.context.critical" in self._wake_patterns()

    def test_recycling_in_wake_patterns(self):
        assert "worker.recycling" in self._wake_patterns()

    def test_recycled_in_wake_patterns(self):
        assert "worker.recycled" in self._wake_patterns()

    def test_recycle_failed_in_wake_patterns(self):
        assert "worker.recycle.failed" in self._wake_patterns()

    def test_worker_respawned_in_wake_patterns(self):
        assert "worker.respawned" in self._wake_patterns()


class TestMustPushMembership:
    def test_context_warning_must_push(self):
        assert "worker.context.warning" in _MUST_PUSH

    def test_context_critical_must_push(self):
        assert "worker.context.critical" in _MUST_PUSH

    def test_recycling_must_push(self):
        assert "worker.recycling" in _MUST_PUSH

    def test_recycled_must_push(self):
        assert "worker.recycled" in _MUST_PUSH

    def test_recycle_failed_must_push(self):
        assert "worker.recycle.failed" in _MUST_PUSH


class TestRoutingDestinations:
    def test_context_warning_routes_to_approval(self):
        assert _ROUTING["worker.context.warning"] == "approval"

    def test_context_critical_routes_to_approval(self):
        assert _ROUTING["worker.context.critical"] == "approval"

    def test_recycling_routes_to_progress(self):
        assert _ROUTING["worker.recycling"] == "progress"

    def test_recycled_routes_to_progress(self):
        assert _ROUTING["worker.recycled"] == "progress"

    def test_recycle_failed_routes_to_approval(self):
        assert _ROUTING["worker.recycle.failed"] == "approval"


class TestContextEventSchema:
    def test_context_event_schema_accepts_canonical_payload(self):
        registry = EventSchemaRegistry.from_dict(context_event_schema_rules())
        event = ZfEvent(
            type="worker.context.critical",
            actor="dev-1",
            task_id="TASK-1",
            payload={
                "task_id": "TASK-1",
                "dispatch_id": "disp-1",
                "role": "dev",
                "instance_id": "dev-1",
                "backend": "claude-code",
                "context_usage_ratio": 0.91,
                "session_ref": "session-1",
                "source": "session_reader",
                "reason": "hard_cap_exceeded",
            },
        )

        assert registry.validate(event) == []

    def test_context_event_schema_rejects_missing_task_dispatch(self):
        registry = EventSchemaRegistry.from_dict(context_event_schema_rules())
        event = ZfEvent(
            type="worker.context.warning",
            actor="dev-1",
            payload={
                "role": "dev",
                "instance_id": "dev-1",
                "backend": "claude-code",
                "context_usage_ratio": 0.61,
                "session_ref": "session-1",
                "source": "session_reader",
                "reason": "recycle_threshold_exceeded",
            },
        )

        fields = {violation.field_path for violation in registry.validate(event)}
        assert "payload.task_id" in fields
        assert "payload.dispatch_id" in fields

    def test_progress_event_schema_accepts_canonical_payload(self):
        registry = EventSchemaRegistry.from_dict(progress_event_schema_rules())
        event = ZfEvent(
            type="worker.progress",
            actor="dev-1",
            task_id="TASK-1",
            payload={
                "task_id": "TASK-1",
                "dispatch_id": "disp-1",
                "role": "dev",
                "instance_id": "dev-1",
                "phase": "implement",
                "message": "patching focused files",
                "source": "worker",
                "percent": 50,
            },
        )

        assert registry.validate(event) == []

    def test_fanout_request_schema_rejects_missing_scope(self):
        registry = EventSchemaRegistry.from_dict(fanout_request_schema_rules())
        event = ZfEvent(
            type="task.fanout.requested",
            actor="dev-1",
            task_id="TASK-1",
            payload={
                "task_id": "TASK-1",
                "dispatch_id": "disp-1",
                "requested_by": "dev-1",
                "reason": "needs independent review",
                "requested_specialists": ["review"],
                "expected_output": "review notes",
                "risk": "medium",
            },
        )

        fields = {violation.field_path for violation in registry.validate(event)}
        assert "payload.scope" in fields


@pytest.fixture
def router(tmp_path: Path):
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "kanban.json").write_text("[]\n")
    transport = MockFeishuTransport()
    routing = RoutingConfig(channels={
        "approval": "chat-approval",
        "progress": "chat-progress",
    })
    return ProjectionRouter(transport, routing, sd), transport


class TestLiveRouting:
    def test_context_warning_sent_to_approval(self, router):
        r, transport = router
        ok = r.route_event(ZfEvent(
            type="worker.context.warning",
            actor="dev-1",
            payload={"ratio": 0.62, "role": "dev"},
        ))
        assert ok
        assert len(transport.sent_messages) == 1
        assert transport.sent_messages[0].chat_id == "chat-approval"

    def test_recycling_sent_to_progress(self, router):
        r, transport = router
        ok = r.route_event(ZfEvent(
            type="worker.recycling",
            actor="dev-1",
            payload={"new_session": "abc"},
        ))
        assert ok
        assert transport.sent_messages[-1].chat_id == "chat-progress"
