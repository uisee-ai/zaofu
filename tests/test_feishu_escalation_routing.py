"""Feishu projection routing for human escalation and triage-first events."""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.events.model import ZfEvent
from zf.integrations.feishu.projection import (
    ProjectionRouter,
    RoutingConfig,
    _MUST_PUSH,
    _ROUTING,
)
from zf.integrations.feishu.transport import FeishuMessage, MockFeishuTransport


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "kanban.json").write_text("[]\n")
    return sd


@pytest.fixture
def router(state_dir: Path):
    transport = MockFeishuTransport()
    routing = RoutingConfig(channels={
        "approval": "chat-approval",
        "alert": "chat-alert",
        "progress": "chat-progress",
    })
    return ProjectionRouter(transport, routing, state_dir), transport


class TestMustPushMembership:
    def test_human_escalate_in_must_push(self):
        assert "human.escalate" in _MUST_PUSH

    def test_worker_stuck_in_must_push(self):
        assert "worker.stuck" in _MUST_PUSH

    def test_human_escalate_routes_to_approval_channel(self):
        assert _ROUTING["human.escalate"] == "approval"

    def test_worker_stuck_routes_to_approval_channel(self):
        assert _ROUTING["worker.stuck"] == "approval"


class TestRoutingFire:
    def test_human_escalate_sent_to_approval_channel(self, router):
        r, transport = router
        ok = r.route_event(ZfEvent(
            type="human.escalate", actor="orchestrator", task_id="T1",
            payload={"reason": "dev blocked"},
        ))
        assert ok
        assert len(transport.sent_messages) == 1
        assert transport.sent_messages[0].chat_id == "chat-approval"
        assert "dev blocked" in transport.sent_messages[0].content

    def test_worker_stuck_is_not_direct_pushed_before_run_manager_triage(self, router):
        r, transport = router
        ok = r.route_event(ZfEvent(
            type="worker.stuck", actor="dev",
            payload={"role": "dev", "threshold_seconds": 300.0},
        ))
        assert not ok
        assert transport.sent_messages == []

    def test_budget_exceeded_is_not_direct_pushed_before_run_manager_triage(self, router):
        r, transport = router
        ok = r.route_event(ZfEvent(
            type="cost.budget.exceeded", actor="zf-cli",
            payload={"scope": "global", "budget_usd": 60.0, "current_usd": 96.0},
        ))
        assert not ok
        assert transport.sent_messages == []

    def test_non_must_push_event_not_routed(self, router):
        r, transport = router
        ok = r.route_event(ZfEvent(type="task.created", actor="zf-cli"))
        assert not ok
        assert transport.sent_messages == []

    def test_route_can_override_receive_id_type_per_channel(self, state_dir):
        transport = MockFeishuTransport()
        r = ProjectionRouter(
            transport,
            RoutingConfig(
                channels={
                    "approval": "ou-owner",
                    "progress": "oc-project",
                },
                receive_id_type="chat_id",
                receive_id_types={"approval": "open_id"},
            ),
            state_dir,
        )

        assert r.route_event(ZfEvent(type="human.escalate", actor="orchestrator"))
        assert r.route_event(ZfEvent(type="task.done", actor="judge", task_id="T1"))

        assert [message.chat_id for message in transport.sent_messages] == [
            "ou-owner",
            "oc-project",
        ]
        assert [message.receive_id_type for message in transport.sent_messages] == [
            "open_id",
            "chat_id",
        ]
