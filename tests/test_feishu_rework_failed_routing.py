"""Feishu: rework/failed/retry + autoresearch bug events route to channels."""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.events.model import ZfEvent
from zf.integrations.feishu.projection import ProjectionRouter, RoutingConfig
from zf.integrations.feishu.transport import MockFeishuTransport


@pytest.fixture
def router(tmp_path: Path) -> tuple[ProjectionRouter, MockFeishuTransport]:
    (tmp_path / "kanban.json").write_text("[]\n")
    transport = MockFeishuTransport()
    routing = RoutingConfig(channels={
        "alert": "ch_alert", "progress": "ch_progress", "approval": "ch_approval"})
    return ProjectionRouter(transport, routing, tmp_path), transport


def _route(router, transport, etype, **payload):
    transport.sent_messages.clear()
    ok = router.route_event(ZfEvent(type=etype, actor="orch", task_id="T1",
                                    payload=payload))
    return ok, (transport.sent_messages[0].chat_id if transport.sent_messages else "")


@pytest.mark.parametrize("etype,chat", [
    ("integration.failed", "ch_alert"),
    ("dev.failed", "ch_alert"),
    ("judge.failed", "ch_alert"),
    ("ship.failed", "ch_alert"),
    ("autoresearch.bug_candidate.created", "ch_alert"),
    ("task.rework.requested", "ch_progress"),
    ("task.retry_requested", "ch_progress"),
    ("worker.stuck.recovered", "ch_progress"),
    ("task.rework.capped", "ch_approval"),
    ("task.rework.blocked", "ch_approval"),
    ("ship.blocked", "ch_approval"),
    ("worker.stuck.recovery_failed", "ch_approval"),
    ("autoresearch.repair.closeout.required", "ch_approval"),
])
def test_rework_failed_events_route_to_expected_channel(router, etype, chat):
    r, t = router
    ok, got = _route(r, t, etype, reason="x")
    assert ok and got == chat


def test_message_carries_event_type_and_task(router):
    r, t = router
    r.route_event(ZfEvent(type="integration.failed", actor="o", task_id="T9",
                          payload={"reason": "merge conflict"}))
    body = t.sent_messages[0].content
    assert "integration.failed" in body and "T9" in body and "merge conflict" in body


def test_existing_pushes_not_regressed(router):
    r, t = router
    assert _route(r, t, "test.failed")[1] == "ch_alert"
    assert _route(r, t, "task.done")[1] == "ch_progress"
    assert _route(r, t, "human.escalate")[1] == "ch_approval"


def test_unlisted_event_not_pushed(router):
    r, t = router
    assert r.route_event(ZfEvent(type="some.random.event", actor="o")) is False
