from __future__ import annotations

from pathlib import Path

from zf.core.events import EventWriter, ZfEvent
from zf.core.events.log import EventLog
from zf.integrations.feishu.projection import RoutingConfig
from zf.integrations.feishu.transport import FeishuMessage, MockFeishuTransport
from zf.runtime.owner_visible_delivery import (
    OWNER_MESSAGE_ATTEMPTED,
    OWNER_MESSAGE_DELIVERED,
    OWNER_MESSAGE_FAILED,
    OWNER_MESSAGE_SUPPRESSED,
    deliver_owner_visible_messages_once,
    project_owner_visible_inbox,
)
from zf.runtime.operator_inbox import build_operator_inbox


class _FailingTransport(MockFeishuTransport):
    def send_message(self, message: FeishuMessage) -> bool:
        raise RuntimeError("send timeout")


def _state(tmp_path: Path) -> tuple[EventLog, EventWriter]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    return log, EventWriter(log)


def _owner_message(log: EventLog) -> None:
    log.append(ZfEvent(
        type="owner.visible_message.requested",
        actor="zf-supervisor",
        task_id="TASK-1",
        payload={
            "message_id": "omsg-1",
            "decision_id": "dec-1",
            "attention_id": "attn-1",
            "severity": "high",
            "title": "worker stuck",
            "summary": "heartbeat is stale",
            "delivery_targets": ["web", "channel", "feishu"],
        },
    ))


def test_owner_visible_delivery_sends_feishu_receipt_once(tmp_path: Path) -> None:
    log, writer = _state(tmp_path)
    _owner_message(log)
    transport = MockFeishuTransport()
    routing = RoutingConfig(
        channels={"owner": "ou-owner"},
        receive_id_types={"owner": "open_id"},
    )

    first = deliver_owner_visible_messages_once(
        event_log=log,
        writer=writer,
        transport=transport,
        routing=routing,
    )
    second = deliver_owner_visible_messages_once(
        event_log=log,
        writer=writer,
        transport=transport,
        routing=routing,
    )

    assert first.delivered == 1
    assert first.failed == 0
    assert second.delivered == 0
    assert len(transport.sent_messages) == 1
    assert transport.sent_messages[0].chat_id == "ou-owner"
    assert transport.sent_messages[0].receive_id_type == "open_id"
    # backlog 2026-07-07-1315: friendly Chinese body, not a raw id/field dump.
    content = transport.sent_messages[0].content
    assert content.startswith("🟠")
    assert "/zf" not in content
    event_types = [event.type for event in log.read_all()]
    assert event_types.count(OWNER_MESSAGE_ATTEMPTED) == 1
    assert event_types.count(OWNER_MESSAGE_DELIVERED) == 1


def test_owner_visible_delivery_records_failed_receipt(tmp_path: Path) -> None:
    log, writer = _state(tmp_path)
    _owner_message(log)

    result = deliver_owner_visible_messages_once(
        event_log=log,
        writer=writer,
        transport=_FailingTransport(),
        routing=RoutingConfig(channels={"approval": "oc-alerts"}),
    )

    assert result.ok is False
    assert result.failed == 1
    events = log.read_all()
    failed = [event for event in events if event.type == OWNER_MESSAGE_FAILED][0]
    assert failed.payload["message_id"] == "omsg-1"
    assert failed.payload["reason"] == "send timeout"


def test_owner_visible_delivery_missing_route_falls_back_to_operator_inbox(
    tmp_path: Path,
) -> None:
    log, writer = _state(tmp_path)
    _owner_message(log)

    result = deliver_owner_visible_messages_once(
        event_log=log,
        writer=writer,
        transport=MockFeishuTransport(),
        routing=RoutingConfig(channels={}),
    )

    assert result.ok is False
    events = log.read_all()
    types = [event.type for event in events]
    assert OWNER_MESSAGE_FAILED in types
    assert "approval.requested" in types
    inbox = build_operator_inbox(tmp_path / ".zf", events)
    assert inbox["summary"]["action_required_pending"] == 1
    assert inbox["pending"][0]["approval_ref"] == "owner-visible:omsg-1"


def test_owner_visible_inbox_projects_pending_and_failed(tmp_path: Path) -> None:
    log, writer = _state(tmp_path)
    _owner_message(log)

    pending = project_owner_visible_inbox(events=log.read_all())

    assert pending["summary"]["pending"] == 1
    assert pending["pending"][0]["message_id"] == "omsg-1"
    assert pending["pending"][0]["title"] == "worker stuck"

    deliver_owner_visible_messages_once(
        event_log=log,
        writer=writer,
        transport=_FailingTransport(),
        routing=RoutingConfig(channels={"approval": "oc-alerts"}),
    )

    failed = project_owner_visible_inbox(events=log.read_all())
    assert failed["summary"]["pending"] == 0
    assert failed["summary"]["failed"] == 1
    assert failed["failed"][0]["last_error"] == "send timeout"


def test_owner_visible_delivery_suppresses_non_human_supervisor_feishu(tmp_path: Path) -> None:
    log, writer = _state(tmp_path)
    log.append(ZfEvent(
        type="owner.visible_message.requested",
        actor="zf-supervisor",
        task_id="TASK-2",
        payload={
            "message_id": "omsg-suppress",
            "source": "supervisor",
            "handled_by": "run-manager",
            "human_action_required": False,
            "severity": "high",
            "title": "fanout tail noise",
            "summary": "run manager can triage",
            "delivery_targets": ["web", "channel", "feishu"],
        },
    ))
    transport = MockFeishuTransport()

    result = deliver_owner_visible_messages_once(
        event_log=log,
        writer=writer,
        transport=transport,
        routing=RoutingConfig(channels={"approval": "oc-alerts"}),
    )

    assert result.ok is True
    assert result.attempted == 0
    assert result.delivered == 0
    assert result.skipped == 1
    assert transport.sent_messages == []
    event_types = [event.type for event in log.read_all()]
    assert OWNER_MESSAGE_SUPPRESSED in event_types
    inbox = project_owner_visible_inbox(events=log.read_all())
    assert inbox["summary"]["pending"] == 0
    assert inbox["recent"][-1]["status"] == "suppressed"


def test_owner_visible_delivery_formats_run_manager_restart_message(tmp_path: Path) -> None:
    log, writer = _state(tmp_path)
    log.append(ZfEvent(
        type="owner.visible_message.requested",
        actor="zf-run-manager",
        task_id="TASK-3",
        payload={
            "message_id": "omsg-rm",
            "source": "run-manager",
            "handled_by": "run-manager",
            "human_action_required": True,
            "severity": "high",
            "title": "Source repair closeout requires operator merge",
            "summary": "restart only at a checkpoint boundary",
            "restart_strategy": "operator_approved",
            "delivery_targets": ["feishu"],
        },
    ))
    transport = MockFeishuTransport()

    result = deliver_owner_visible_messages_once(
        event_log=log,
        writer=writer,
        transport=transport,
        routing=RoutingConfig(channels={"approval": "oc-alerts"}),
    )

    assert result.delivered == 1
    assert len(transport.sent_messages) == 1
    content = transport.sent_messages[0].content
    # backlog 2026-07-07-1315: friendly Chinese, severity emoji, human-action
    # prompt — no field dump, no "[ZaoFu ...]" header, no "/zf" CLI line.
    assert content.startswith("🟠")
    assert "需要你的确认后才能继续。" in content
    assert "restart_strategy:" not in content
    assert "/zf" not in content


def _requested(log: EventLog, *, fingerprint: str, summary: str = "",
               title: str = "", text: str = "", severity: str = "warn",
               human: bool = False) -> None:
    """A supervisor owner-visible request targeting feishu (backlog 2026-07-07-1315)."""
    log.append(ZfEvent(
        type="owner.visible_message.requested",
        actor="zf-supervisor",
        task_id="TASK-9",
        payload={
            "message_id": f"omsg-{fingerprint}",
            "source": "supervisor",
            "human_action_required": human,
            "severity": severity,
            "title": title,
            "summary": summary,
            "text": text,
            "fingerprint": fingerprint,
            "delivery_targets": ["feishu"],
        },
    ))


def test_owner_visible_reason_code_is_humanized(tmp_path: Path) -> None:
    log, writer = _state(tmp_path)
    _requested(log, fingerprint="fp-1", summary="recycle_threshold_exceeded",
               severity="warn", human=True)
    transport = MockFeishuTransport()

    result = deliver_owner_visible_messages_once(
        event_log=log, writer=writer, transport=transport,
        routing=RoutingConfig(channels={"approval": "oc-alerts"}),
    )

    assert result.delivered == 1
    content = transport.sent_messages[0].content
    assert "反复重启" in content            # plain Chinese explanation
    assert "recycle_threshold_exceeded" not in content   # not the raw code
    assert "/zf" not in content              # no CLI instruction


def test_owner_visible_empty_message_is_suppressed(tmp_path: Path) -> None:
    log, writer = _state(tmp_path)
    # Generic title + no summary/text -> nothing to show.
    _requested(log, fingerprint="fp-empty", title="Runtime escalated to human",
               human=True)
    transport = MockFeishuTransport()

    result = deliver_owner_visible_messages_once(
        event_log=log, writer=writer, transport=transport,
        routing=RoutingConfig(channels={"approval": "oc-alerts"}),
    )

    assert transport.sent_messages == []
    assert result.delivered == 0
    suppressed = [e for e in log.read_all() if e.type == OWNER_MESSAGE_SUPPRESSED]
    assert any(e.payload.get("reason") == "empty_owner_message" for e in suppressed)


def test_owner_visible_duplicate_content_is_folded(tmp_path: Path) -> None:
    log, writer = _state(tmp_path)
    # Same signal, DISTINCT fingerprints (the real recycle ×9 shape).
    for i in range(3):
        _requested(log, fingerprint=f"fp-{i}",
                   summary="recycle_threshold_exceeded", human=True)
    transport = MockFeishuTransport()

    result = deliver_owner_visible_messages_once(
        event_log=log, writer=writer, transport=transport,
        routing=RoutingConfig(channels={"approval": "oc-alerts"}),
    )

    assert result.delivered == 1
    assert len(transport.sent_messages) == 1
    suppressed = [e for e in log.read_all()
                  if e.type == OWNER_MESSAGE_SUPPRESSED
                  and e.payload.get("reason") == "duplicate_owner_message"]
    assert len(suppressed) == 2
