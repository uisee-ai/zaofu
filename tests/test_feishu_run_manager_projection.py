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


def test_escalation_sent_suppressed_when_card_first_enabled(tmp_path, monkeypatch):
    monkeypatch.delenv("ZF_RUN_MANAGER_CARD_FIRST", raising=False)
    router, t = _router(tmp_path)
    ev = ZfEvent(type="human.escalation.sent", actor="run-manager",
                 payload={"run_id": "R1", "failure_class": "worker_stuck"})
    assert router.route_event(ev) is False
    assert not t.sent_messages


def test_escalation_sent_routes_to_approval_when_projection_fallback_enabled(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ZF_RUN_MANAGER_CARD_FIRST", "0")
    router, t = _router(tmp_path)
    ev = ZfEvent(type="human.escalation.sent", actor="run-manager",
                 payload={"run_id": "R1", "failure_class": "worker_stuck"})
    assert router.route_event(ev) is True
    assert t.sent_messages[-1].chat_id == "oc_appr"


def test_escalation_sent_falls_back_to_owner_channel(tmp_path):
    import os

    old = os.environ.get("ZF_RUN_MANAGER_CARD_FIRST")
    os.environ["ZF_RUN_MANAGER_CARD_FIRST"] = "0"
    try:
        _assert_escalation_owner_fallback(tmp_path)
    finally:
        if old is None:
            os.environ.pop("ZF_RUN_MANAGER_CARD_FIRST", None)
        else:
            os.environ["ZF_RUN_MANAGER_CARD_FIRST"] = old


def _assert_escalation_owner_fallback(tmp_path):
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
