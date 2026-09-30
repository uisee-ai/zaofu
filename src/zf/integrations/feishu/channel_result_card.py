"""Exact-origin Feishu projection for Channel result receipts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from zf.core.events.log import EventLog
from zf.core.state.atomic_io import atomic_write_text
from zf.integrations.feishu.transport import FeishuMessage


_RECEIPT_EVENT = "channel.result.receipt.recorded"
_LEDGER_SCHEMA_VERSION = "feishu-channel-result-ledger.v1"
_STATUS_LABEL = {
    "available": "已就绪",
    "completed": "已完成",
    "confirmed": "已确认",
    "done": "已完成",
    "passed": "已通过",
    "blocked": "已阻塞",
    "failed": "未完成",
    "rejected": "未通过",
}
_KIND_LABEL = {
    "workflow_terminal": "工作流已完成",
    "task_created": "已创建任务",
    "task_terminal": "任务状态已更新",
    "goal_dossier": "已生成交付摘要",
}


def build_channel_result_card(receipt: dict[str, Any]) -> dict[str, Any]:
    """Render one read-only Channel result receipt."""

    status = str(receipt.get("status") or "available")
    template = (
        "green"
        if status in {"available", "completed", "confirmed", "done", "passed"}
        else "red"
        if status in {"blocked", "failed", "rejected"}
        else "blue"
    )
    status_label = _STATUS_LABEL.get(status, "状态已更新")
    kind_label = _KIND_LABEL.get(
        str(receipt.get("receipt_kind") or ""),
        "交付状态已更新",
    )
    task_id = str(receipt.get("task_id") or "")
    if task_id:
        body = (
            f"{kind_label}\n"
            f"任务：**{task_id}**\n"
            f"状态：{status_label}"
        )
    else:
        body = f"{kind_label}\n状态：{status_label}"
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "交付结果"},
            "template": template,
        },
        "elements": [
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": body},
            },
        ],
    }


def _receipt_rows(events: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in events:
        if str(getattr(event, "type", "") or "") != _RECEIPT_EVENT:
            continue
        payload = (
            event.payload if isinstance(getattr(event, "payload", None), dict) else {}
        )
        receipt_id = str(payload.get("receipt_id") or "").strip()
        origin = (
            payload.get("origin_binding")
            if isinstance(payload.get("origin_binding"), dict)
            else {}
        )
        if (
            not receipt_id
            or receipt_id in seen
            or str(origin.get("surface") or "") != "feishu"
            or not str(origin.get("chat_id") or "").strip()
            or not str(origin.get("origin_message_id") or "").strip()
        ):
            continue
        seen.add(receipt_id)
        rows.append({**payload, "origin_binding": dict(origin)})
    return rows


def sync_channel_result_cards(
    state_dir: Path,
    *,
    send_card: Callable[[str, str, dict[str, Any]], str | None],
    ledger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project each exact Feishu-origin receipt once."""

    ledger = ledger if isinstance(ledger, dict) else {}
    delivered = (
        ledger.get("delivered")
        if isinstance(ledger.get("delivered"), dict)
        else {}
    )
    sent: list[str] = []
    try:
        events = EventLog(Path(state_dir) / "events.jsonl").read_all()
    except Exception:
        events = []
    for receipt in _receipt_rows(events):
        receipt_id = str(receipt["receipt_id"])
        if receipt_id in delivered:
            continue
        origin = receipt["origin_binding"]
        message_id = send_card(
            str(origin["chat_id"]),
            str(origin["origin_message_id"]),
            build_channel_result_card(receipt),
        )
        delivered[receipt_id] = {
            "message_id": str(message_id or ""),
            "chat_id": str(origin["chat_id"]),
            "origin_message_id": str(origin["origin_message_id"]),
            "receipt_digest": str(receipt.get("receipt_digest") or ""),
        }
        sent.append(receipt_id)
    return {
        "sent": sent,
        "ledger": {
            "schema_version": _LEDGER_SCHEMA_VERSION,
            "delivered": delivered,
        },
    }


def push_channel_result_cards_once(
    state_dir: Path,
    transport: Any,
    *,
    receive_id_type: str = "chat_id",
) -> dict[str, Any]:
    """Run one restart-safe exact-origin Feishu receipt projection pass."""

    state_dir = Path(state_dir)
    ledger_path = (
        state_dir
        / "integrations"
        / "feishu"
        / "channel_result_ledger.json"
    )
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ledger = {}

    def send_card(
        chat_id: str,
        origin_message_id: str,
        card: dict[str, Any],
    ) -> str | None:
        return transport.send_card(
            FeishuMessage(
                chat_id=chat_id,
                thread_id=origin_message_id,
                content=json.dumps(card, ensure_ascii=False),
                msg_type="interactive",
                receive_id_type=receive_id_type,
            )
        )

    result = sync_channel_result_cards(
        state_dir,
        send_card=send_card,
        ledger=ledger,
    )
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        ledger_path,
        json.dumps(result["ledger"], ensure_ascii=False, indent=2) + "\n",
    )
    return result


__all__ = [
    "build_channel_result_card",
    "push_channel_result_cards_once",
    "sync_channel_result_cards",
]
