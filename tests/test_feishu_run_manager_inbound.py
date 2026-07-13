from __future__ import annotations

import json
from pathlib import Path

from zf.core.config.schema import (
    FeishuRouteConfig,
    IntegrationsConfig,
    RuntimeConfig,
    RuntimeRunManagerConfig,
    RuntimeRunManagerResidentAgentConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.integrations.feishu.run_manager_card import sync_run_manager_cards
from zf.integrations.feishu.run_manager_inbound import run_manager_inbound_reply
from zf.integrations.feishu.transport import FeishuWebhookEvent


def _writer(state_dir: Path) -> EventWriter:
    return EventWriter(EventLog(state_dir / "events.jsonl"))


def _config() -> ZfConfig:
    return ZfConfig(
        runtime=RuntimeConfig(
            run_manager=RuntimeRunManagerConfig(
                backend="fake",
                resident_agent=RuntimeRunManagerResidentAgentConfig(
                    enabled=True,
                    instance_id="rm-resident",
                ),
            ),
        ),
        integrations=IntegrationsConfig(
            feishu_routing={
                "oc_arch": FeishuRouteConfig(
                    target="run_manager",
                    default_member="run-manager",
                    backend="fake",
                ),
            },
        ),
    )


def _seed_card_context(state_dir: Path) -> str:
    writer = _writer(state_dir)
    writer.append(ZfEvent(type="human.escalation.sent", actor="run-manager", payload={
        "decision_token": "hdec-ctx",
        "run_id": "R1",
        "failure_class": "workflow_stall",
        "checkpoint_id": "ck-1",
        "safe_resume_action": "workflow_resume",
        "reason": "lane stalled",
    }))
    sent: list[dict] = []
    result = sync_run_manager_cards(
        state_dir,
        send_card=lambda card: (sent.append(card), "msg-card-1")[1],
        update_card=lambda _mid, _card: True,
        ledger={},
    )
    ledger_path = state_dir / "integrations" / "feishu" / "run_manager_ledger.json"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(
        json.dumps(result["ledger"], ensure_ascii=False),
        encoding="utf-8",
    )
    return "msg-card-1"


def test_run_manager_inbound_explain_resolves_card_context_without_tmux_handoff(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    card_message_id = _seed_card_context(state_dir)
    writer = _writer(state_dir)
    event = FeishuWebhookEvent(
        event_type="message",
        chat_id="oc_arch",
        user_id="ou_user",
        payload={
            "text": "解释一下这条决策",
            "message_id": "msg-user-1",
            "parent_message_id": card_message_id,
        },
    )

    result = run_manager_inbound_reply(state_dir, _config(), event, writer)

    assert result["status"] == "replied"
    events = EventLog(state_dir / "events.jsonl").read_all()
    assert [item for item in events if item.type == "run.manager.context.resolved"]
    generated = [
        item for item in events
        if item.type == "run.manager.explanation.generated"
    ]
    assert generated
    assert generated[-1].payload["decision_token"] == "hdec-ctx"
    assert not [item for item in events if item.type == "worker.reply.requested"]


def test_run_manager_inbound_explicit_handoff_requests_resident_worker_reply(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    card_message_id = _seed_card_context(state_dir)
    writer = _writer(state_dir)
    event = FeishuWebhookEvent(
        event_type="message",
        chat_id="oc_arch",
        user_id="ou_user",
        payload={
            "text": "请转交常驻监工处理",
            "message_id": "msg-user-2",
            "parent_message_id": card_message_id,
        },
    )

    result = run_manager_inbound_reply(state_dir, _config(), event, writer)

    assert result["status"] == "resident_handoff_requested"
    events = EventLog(state_dir / "events.jsonl").read_all()
    reply = [item for item in events if item.type == "worker.reply.requested"]
    assert reply
    assert reply[-1].payload["instance_id"] == "rm-resident"
    assert "workflow_stall" in json.dumps(reply[-1].payload, ensure_ascii=False)


def test_run_manager_inbound_diagnose_text_emits_agent_recommendation(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    card_message_id = _seed_card_context(state_dir)
    writer = _writer(state_dir)
    event = FeishuWebhookEvent(
        event_type="message",
        chat_id="oc_arch",
        user_id="ou_user",
        payload={
            "text": "诊断这个 blocked，看看为什么卡住",
            "message_id": "msg-user-3",
            "parent_message_id": card_message_id,
        },
    )

    result = run_manager_inbound_reply(state_dir, _config(), event, writer)

    assert result["status"] == "replied"
    events = EventLog(state_dir / "events.jsonl").read_all()
    rec = [item for item in events if item.type == "run.manager.agent.recommendation"]
    assert rec
    assert rec[-1].payload["recommended_route"] == "autoresearch"
    assert rec[-1].payload["decision_token"] == "hdec-ctx"
    assert not [item for item in events if item.type == "run.manager.explanation.requested"]
    assert not [item for item in events if item.type == "worker.reply.requested"]


def test_run_manager_inbound_plain_reply_to_card_gets_context(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    card_message_id = _seed_card_context(state_dir)
    writer = _writer(state_dir)
    event = FeishuWebhookEvent(
        event_type="message",
        chat_id="oc_arch",
        user_id="ou_user",
        payload={
            "text": "hi",
            "message_id": "msg-user-4",
            "parent_message_id": card_message_id,
        },
    )

    result = run_manager_inbound_reply(state_dir, _config(), event, writer)

    assert result["status"] == "replied"
    events = EventLog(state_dir / "events.jsonl").read_all()
    messages = [item for item in events if item.type == "channel.message.posted"]
    assert messages
    assert "Run Manager context" in messages[-1].payload["text"]
    assert "workflow_stall" in messages[-1].payload["text"]
    assert "ck-1" in messages[-1].payload["text"]
