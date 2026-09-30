"""Generic Feishu cards for Kanban Agent controlled-action proposals."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from zf.core.events.log import EventLog
from zf.runtime.kanban_proposals import (
    PROPOSAL_EVENT_TYPES,
    PROPOSAL_RESOLVED_EVENT_TYPES,
    pending_kanban_proposals,
)


def build_kanban_proposal_card(item: dict[str, Any]) -> dict[str, Any]:
    proposal_event_id = str(item.get("proposal_event_id") or "")
    proposal_id = str(item.get("proposal_id") or proposal_event_id)
    action = str(item.get("action") or "")
    reason = str(item.get("reason") or "")
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    payload_text = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    if len(payload_text) > 1800:
        payload_text = payload_text[:1797] + "..."
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "Kanban Agent 提案"},
            "template": "orange",
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        f"**action:** `{action}`\n"
                        f"**reason:** {reason or '(未提供)'}\n"
                        f"**proposal:** `{proposal_id}`\n"
                        f"**revision:** `{int(item.get('revision') or 1)}`\n"
                        f"**digest:** `{str(item.get('proposal_digest') or '')[:16]}`"
                    ),
                },
            },
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"```json\n{payload_text}\n```",
                },
            },
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "批准执行"},
                        "type": "primary",
                        "value": {
                            "action": (
                                "kanban-proposal-approve:"
                                f"{proposal_event_id}"
                            ),
                        },
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "忽略"},
                        "type": "default",
                        "value": {
                            "action": (
                                "kanban-proposal-dismiss:"
                                f"{proposal_event_id}"
                            ),
                        },
                    },
                ],
            },
        ],
        "_card_key": f"kanban-proposal-{proposal_id}",
        "_target_chat_id": _proposal_target(item),
        "_target_thread_id": _proposal_thread(item),
    }


def build_kanban_proposal_result_card(
    item: dict[str, Any],
    *,
    resolution: str,
) -> dict[str, Any]:
    approved = resolution == "executed"
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "Kanban Agent 提案结果"},
            "template": "green" if approved else "grey",
        },
        "elements": [{
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": (
                    f"{'已批准执行' if approved else '已忽略'}\n"
                    f"action: `{str(item.get('action') or '')}`\n"
                    f"proposal: `{str(item.get('proposal_id') or '')}`"
                ),
            },
        }],
        "_card_key": (
            f"kanban-proposal-"
            f"{str(item.get('proposal_id') or item.get('proposal_event_id') or '')}"
        ),
        "_target_chat_id": _proposal_target(item),
        "_target_thread_id": _proposal_thread(item),
    }


def sync_kanban_proposal_cards(
    state_dir: Path,
    *,
    send_card,
    update_card,
    ledger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ledger = ledger if ledger is not None else {}
    events = EventLog(Path(state_dir) / "events.jsonl").read_all()
    pending = pending_kanban_proposals(events)
    pending_by_id = {
        str(item.get("proposal_id") or item.get("proposal_event_id") or ""): item
        for item in pending
    }
    all_items = _proposal_items(events)
    sent: list[str] = []
    updated: list[str] = []
    for proposal_id, item in all_items.items():
        key = f"kanban-proposal-{proposal_id}"
        entry = ledger.get(key) if isinstance(ledger.get(key), dict) else {}
        current = pending_by_id.get(proposal_id)
        if current is not None:
            if not entry.get("message_id"):
                message_id = send_card(build_kanban_proposal_card(current))
                ledger[key] = {
                    "message_id": str(message_id or ""),
                    "state": "pending",
                    "proposal_event_id": str(
                        current.get("proposal_event_id") or ""
                    ),
                    "proposal_digest": str(
                        current.get("proposal_digest") or ""
                    ),
                    "revision": int(current.get("revision") or 1),
                }
                sent.append(proposal_id)
            elif (
                str(entry.get("proposal_event_id") or "")
                != str(current.get("proposal_event_id") or "")
                or str(entry.get("proposal_digest") or "")
                != str(current.get("proposal_digest") or "")
                or int(entry.get("revision") or 1)
                != int(current.get("revision") or 1)
            ):
                update_card(
                    str(entry["message_id"]),
                    build_kanban_proposal_card(current),
                )
                ledger[key] = {
                    **entry,
                    "state": "pending",
                    "proposal_event_id": str(
                        current.get("proposal_event_id") or ""
                    ),
                    "proposal_digest": str(
                        current.get("proposal_digest") or ""
                    ),
                    "revision": int(current.get("revision") or 1),
                }
                updated.append(proposal_id)
            continue
        if entry.get("message_id") and entry.get("state") == "pending":
            resolution = _proposal_resolution(events, item)
            update_card(
                str(entry["message_id"]),
                build_kanban_proposal_result_card(
                    item,
                    resolution=resolution,
                ),
            )
            ledger[key] = {**entry, "state": resolution}
            updated.append(proposal_id)
    return {
        "sent": sent,
        "updated": updated,
        "ledger": ledger,
    }


def push_kanban_proposal_cards_once(
    state_dir: Path,
    transport,
    *,
    receive_id: str,
    receive_id_type: str = "chat_id",
    action_secret: bytes | None = None,
    action_ttl_seconds: int = 86400,
    action_key_version: str = "1",
    now: float | None = None,
) -> dict[str, Any]:
    import time

    from zf.core.state.atomic_io import atomic_write_text
    from zf.integrations.feishu.callback_token import attach_action_token
    from zf.integrations.feishu.transport import FeishuMessage

    issued_at = time.time() if now is None else now
    ledger_path = (
        Path(state_dir)
        / "integrations"
        / "feishu"
        / "kanban_proposal_ledger.json"
    )
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ledger = {}

    def target_for(card: dict[str, Any]) -> str:
        return str(card.get("_target_chat_id") or receive_id)

    def thread_for(card: dict[str, Any]) -> str:
        return str(card.get("_target_thread_id") or "")

    def prepare_card(card: dict[str, Any]) -> dict[str, Any]:
        target = target_for(card)
        card = {key: value for key, value in card.items() if not key.startswith("_")}
        if action_secret:
            attach_action_token(
                card,
                secret=action_secret,
                chat_id=target,
                ttl_seconds=action_ttl_seconds,
                now=issued_at,
                key_version=action_key_version,
            )
        return card

    def send_card(card: dict[str, Any]) -> str | None:
        target = target_for(card)
        thread_id = thread_for(card)
        card = prepare_card(card)
        return transport.send_card(FeishuMessage(
            chat_id=target,
            thread_id=thread_id,
            content=json.dumps(card, ensure_ascii=False),
            msg_type="interactive",
            receive_id_type=receive_id_type,
        ))

    def update_card(message_id: str, card: dict[str, Any]) -> bool:
        return transport.update_card(message_id, prepare_card(card))

    result = sync_kanban_proposal_cards(
        state_dir,
        send_card=send_card,
        update_card=update_card,
        ledger=ledger,
    )
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        ledger_path,
        json.dumps(result["ledger"], ensure_ascii=False, indent=2) + "\n",
    )
    return result


def _proposal_items(events) -> dict[str, dict[str, Any]]:
    items: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.type not in PROPOSAL_EVENT_TYPES:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        proposal = payload.get("proposal") if isinstance(payload.get("proposal"), dict) else {}
        if not proposal:
            continue
        proposal_id = str(proposal.get("proposal_id") or event.id)
        item = {
            **proposal,
            "proposal_event_id": event.id,
            "proposal_id": proposal_id,
            "refs": payload.get("refs") or {},
        }
        prior = items.get(proposal_id)
        if prior is None or int(item.get("revision") or 1) >= int(
            prior.get("revision") or 1
        ):
            items[proposal_id] = item
    return items


def _proposal_resolution(events, item: dict[str, Any]) -> str:
    proposal_id = str(item.get("proposal_id") or "")
    proposal_event_id = str(item.get("proposal_event_id") or "")
    for event in reversed(events):
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.type in PROPOSAL_RESOLVED_EVENT_TYPES and (
            str(payload.get("proposal_id") or "") == proposal_id
            or str(payload.get("proposal_event_id") or "") == proposal_event_id
        ):
            return str(payload.get("resolution") or "dismissed")
        if event.type == "task.created":
            request = payload.get("request") if isinstance(payload.get("request"), dict) else {}
            if str(request.get("proposal_event_id") or "") == proposal_event_id:
                return "executed"
    return "dismissed"


def _proposal_target(item: dict[str, Any]) -> str:
    refs = item.get("refs") if isinstance(item.get("refs"), dict) else {}
    feishu = refs.get("feishu") if isinstance(refs.get("feishu"), dict) else {}
    return str(feishu.get("chat_id") or "")


def _proposal_thread(item: dict[str, Any]) -> str:
    refs = item.get("refs") if isinstance(item.get("refs"), dict) else {}
    feishu = refs.get("feishu") if isinstance(refs.get("feishu"), dict) else {}
    return str(
        feishu.get("root_message_id")
        or feishu.get("parent_message_id")
        or feishu.get("message_id")
        or ""
    )
