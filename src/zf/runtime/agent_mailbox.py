"""Agent mailbox 状态机 — agent.message.*。

worker 间通信从 prompt 暗示升级为 mailbox 事件契约。**新命名空间**,
不改任何既有事件名(doc 90 §2.5 ABI 边界);channel.message.posted 的
桥接(posted→sent 映射)留给 channel bridge,本模块只折叠状态。

状态机:sent → delivered → read;sent → failed(终态)。
非法跳转忽略(append-only 流读侧宽进)。
"""

from __future__ import annotations

from typing import Any

MAILBOX_EVENTS = (
    "agent.message.sent",
    "agent.message.delivered",
    "agent.message.read",
    "agent.message.failed",
)

_NEXT = {
    "": {"agent.message.sent"},
    "sent": {"agent.message.delivered", "agent.message.failed"},
    "delivered": {"agent.message.read"},
    "read": set(),
    "failed": set(),
}

_STATE = {
    "agent.message.sent": "sent",
    "agent.message.delivered": "delivered",
    "agent.message.read": "read",
    "agent.message.failed": "failed",
}


def fold_mailbox(events: list[Any]) -> dict[str, dict]:
    """events → {message_id: {state, sender_run_id, recipients, subject,
    task_id, last_ts, history[]}}。"""
    box: dict[str, dict] = {}
    for event in events:
        etype = str(getattr(event, "type", "") or "")
        if etype not in _STATE:
            continue
        payload = getattr(event, "payload", None) or {}
        message_id = str(payload.get("message_id") or "")
        if not message_id:
            continue
        entry = box.setdefault(message_id, {
            "state": "",
            "sender_run_id": "",
            "recipients": [],
            "subject": "",
            "task_id": "",
            "last_ts": "",
            "history": [],
        })
        if etype not in _NEXT.get(entry["state"], set()):
            continue  # 非法跳转/重复:宽进忽略
        entry["state"] = _STATE[etype]
        entry["last_ts"] = str(getattr(event, "ts", "") or "")
        entry["history"].append(etype)
        if etype == "agent.message.sent":
            entry["sender_run_id"] = str(payload.get("sender_run_id") or "")
            entry["recipients"] = [
                str(r) for r in payload.get("recipient_run_ids") or []
            ]
            entry["subject"] = str(payload.get("subject") or "")
            entry["task_id"] = str(payload.get("task_id") or "")
    return box


def undelivered(box: dict[str, dict]) -> list[str]:
    """可重放清单:已 sent 未 delivered/failed 的 message_id(有序)。"""
    return sorted(
        mid for mid, entry in box.items() if entry["state"] == "sent"
    )
