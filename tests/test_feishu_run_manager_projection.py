"""Run Manager run-level escalation + human-decision events project to Feishu
(backlog 2026-06-25 RM1): monitor execution status + surface "needs human"."""

from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.integrations.feishu.projection import ProjectionRouter, RoutingConfig
from zf.integrations.feishu.transport import MockFeishuTransport


def _router(tmp_path):
    (tmp_path / "kanban.json").write_text("[]\n")
    transport = MockFeishuTransport()
    router = ProjectionRouter(
        transport,
        RoutingConfig(channels={"approval": "oc_appr", "progress": "oc_prog",
                                "alert": "oc_alert"}),
        tmp_path)
    return router, transport


def test_escalation_sent_routes_to_approval(tmp_path):
    router, t = _router(tmp_path)
    ev = ZfEvent(type="human.escalation.sent", actor="run-manager",
                 payload={"run_id": "R1", "failure_class": "worker_stuck"})
    assert router.route_event(ev) is True
    assert t.sent_messages[-1].chat_id == "oc_appr"   # needs-human → approval channel


def test_escalation_sent_falls_back_to_owner_channel(tmp_path):
    (tmp_path / "kanban.json").write_text("[]\n")
    transport = MockFeishuTransport()
    router = ProjectionRouter(
        transport,
        RoutingConfig(channels={"owner": "oc_owner"}),
        tmp_path,
    )
    ev = ZfEvent(type="human.escalation.sent", actor="run-manager",
                 payload={"run_id": "R1", "failure_class": "worker_stuck"})

    assert router.route_event(ev) is True
    assert transport.sent_messages[-1].chat_id == "oc_owner"


def test_human_decision_applied_routes_to_progress(tmp_path):
    router, t = _router(tmp_path)
    ev = ZfEvent(type="run.manager.human_decision.applied", actor="run-manager",
                 payload={"run_id": "R1"})
    assert router.route_event(ev) is True
    assert t.sent_messages[-1].chat_id == "oc_prog"


def test_escalation_failed_routes_to_alert(tmp_path):
    router, t = _router(tmp_path)
    ev = ZfEvent(type="human.escalation.failed", actor="run-manager", payload={})
    assert router.route_event(ev) is True
    assert t.sent_messages[-1].chat_id == "oc_alert"


def test_unrelated_event_not_pushed(tmp_path):
    router, t = _router(tmp_path)
    ev = ZfEvent(type="some.random.event", actor="x", payload={})
    assert router.route_event(ev) is False
    assert not t.sent_messages
