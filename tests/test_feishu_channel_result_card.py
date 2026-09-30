"""Exact-origin Feishu projection for Channel result receipts."""

from __future__ import annotations

import json
from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.integrations.feishu.channel_result_card import (
    push_channel_result_cards_once,
    sync_channel_result_cards,
)
from zf.integrations.feishu.transport import MockFeishuTransport


def _receipt(
    state_dir: Path,
    *,
    receipt_id: str = "receipt-1",
    chat_id: str = "oc-product",
    origin_message_id: str = "om-root",
) -> None:
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    writer.emit(
        "channel.result.receipt.recorded",
        actor="test",
        correlation_id="ch-product",
        payload={
            "schema_version": "channel-result-receipt.v1",
            "channel_id": "ch-product",
            "thread_id": "om-root",
            "receipt_id": receipt_id,
            "receipt_kind": "workflow_terminal",
            "status": "completed",
            "source_event_id": "evt-source",
            "source_event_type": "run.goal.completed",
            "receipt_ref": f"channels/ch-product/receipts/{receipt_id}.json",
            "receipt_digest": "a" * 64,
            "artifact_ref": "artifacts/goal-dossier.json",
            "artifact_digest": "b" * 64,
            "revision": 1,
            "idempotency_key": receipt_id,
            "task_id": "TASK-1",
            "workflow_run_id": "run-1",
            "origin_binding": {
                "schema_version": "channel-origin-binding.v1",
                "surface": "feishu",
                "channel_id": "ch-product",
                "thread_id": "om-root",
                "chat_id": chat_id,
                "origin_message_id": origin_message_id,
            },
            "source": "runtime",
        },
    )


def test_sync_uses_exact_chat_and_origin_message_once(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    _receipt(state_dir)
    sent: list[tuple[str, str, dict]] = []
    ledger: dict = {}

    first = sync_channel_result_cards(
        state_dir,
        send_card=lambda chat, origin, card: (
            sent.append((chat, origin, card)),
            "om-result",
        )[1],
        ledger=ledger,
    )
    second = sync_channel_result_cards(
        state_dir,
        send_card=lambda chat, origin, card: (
            sent.append((chat, origin, card)),
            "om-duplicate",
        )[1],
        ledger=first["ledger"],
    )

    assert first["sent"] == ["receipt-1"]
    assert second["sent"] == []
    assert len(sent) == 1
    assert sent[0][0:2] == ("oc-product", "om-root")
    rendered = json.dumps(sent[0][2], ensure_ascii=False)
    assert "任务：**TASK-1**" in rendered
    assert "状态：已完成" in rendered
    assert "交付结果" in rendered
    for internal in ("artifact", "receipt", "run-1", "kind:", "status:"):
        assert internal not in rendered


def test_push_persists_ledger_and_replies_to_exact_message(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    _receipt(state_dir)
    transport = MockFeishuTransport()

    first = push_channel_result_cards_once(state_dir, transport)
    second = push_channel_result_cards_once(state_dir, transport)

    assert first["sent"] == ["receipt-1"]
    assert second["sent"] == []
    assert len(transport.sent_messages) == 1
    assert transport.sent_messages[0].chat_id == "oc-product"
    assert transport.sent_messages[0].thread_id == "om-root"
    assert (
        state_dir
        / "integrations"
        / "feishu"
        / "channel_result_ledger.json"
    ).exists()


def test_non_feishu_or_incomplete_origin_is_not_rerouted(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    _receipt(state_dir, receipt_id="receipt-feishu")
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    writer.emit(
        "channel.result.receipt.recorded",
        actor="test",
        correlation_id="ch-web",
        payload={
            "schema_version": "channel-result-receipt.v1",
            "channel_id": "ch-web",
            "thread_id": "main",
            "receipt_id": "receipt-web",
            "receipt_kind": "task_created",
            "status": "available",
            "source_event_id": "evt-web",
            "source_event_type": "task.created",
            "receipt_ref": "channels/ch-web/receipts/r.json",
            "receipt_digest": "c" * 64,
            "artifact_ref": "event:evt-web",
            "artifact_digest": "d" * 64,
            "revision": 1,
            "idempotency_key": "receipt-web",
            "origin_binding": {
                "surface": "channel",
                "channel_id": "ch-web",
                "thread_id": "main",
            },
            "source": "runtime",
        },
    )
    sent: list[tuple[str, str]] = []

    result = sync_channel_result_cards(
        state_dir,
        send_card=lambda chat, origin, card: (
            sent.append((chat, origin)),
            "om-result",
        )[1],
    )

    assert result["sent"] == ["receipt-feishu"]
    assert sent == [("oc-product", "om-root")]
