"""Feishu cards for channel discussion open questions (doc 122 §8, P0-3).

Pure projection sidecar, same shape as run_manager_card: fold kernel-owned
events into cards, push idempotently via a ledger, and translate signed
button clicks back into ONE event (`channel.question.resolved`). The kernel
never depends on this module; it is an owner-notification edge.

v1 interaction: two signed buttons per question —
  adopt   -> resolve as `answered` with the asker's embedded suggestion
  oos     -> resolve as `out_of_scope`
Free-form answers stay on the Web/CLI surface (a card hint says so); the
card is the pager, not the whole conversation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

ADOPT_COMMAND = "channel-question-adopt"
OOS_COMMAND = "channel-question-oos"
QUESTION_COMMANDS = {ADOPT_COMMAND, OOS_COMMAND}


def fold_open_questions(events: list) -> dict[str, dict[str, Any]]:
    """{question_id: question} for every question not yet resolved/merged."""
    questions: dict[str, dict[str, Any]] = {}
    for event in events:
        etype = str(getattr(event, "type", "") or "")
        payload = getattr(event, "payload", None) or {}
        if not isinstance(payload, dict):
            continue
        question_id = str(payload.get("question_id") or "")
        if not question_id:
            continue
        if etype == "channel.question.opened":
            questions[question_id] = {
                "question_id": question_id,
                "channel_id": str(payload.get("channel_id") or ""),
                "thread_id": str(payload.get("thread_id") or "main"),
                "question": str(payload.get("question") or ""),
                "category": str(payload.get("category") or ""),
                "asked_by": str(payload.get("asked_by") or ""),
                "status": "open",
            }
        elif etype == "channel.question.resolved" and question_id in questions:
            resolution = str(payload.get("resolution") or "")
            resolved_by = str(payload.get("resolved_by") or "")
            # mirror the kernel's owner-only gate approximately: the fold
            # cannot see channel members here, so only trust resolutions the
            # projection accepted — detect via the paired rejection event.
            questions[question_id]["status"] = "resolved"
            questions[question_id]["resolution"] = resolution
            questions[question_id]["resolved_by"] = resolved_by
        elif etype == "channel.question.resolve.rejected" and question_id in questions:
            if questions[question_id].get("status") == "resolved":
                questions[question_id]["status"] = "open"
        elif etype == "channel.question.merged" and question_id in questions:
            questions[question_id]["status"] = "merged"
    return questions


def extract_suggestion(question: str) -> str:
    """Participants embed '建议:X' per the discussion skill; lift it if present."""
    for marker in ("建议:", "建议:", "suggestion:", "Suggestion:"):
        if marker in question:
            return question.split(marker, 1)[1].strip()[:300]
    return ""


def build_question_card(question: dict[str, Any], *, state: str = "open") -> dict[str, Any]:
    question_id = str(question.get("question_id") or "")
    body = (
        f"**需求澄清提问**\n"
        f"channel: `{question.get('channel_id')}`  thread: `{question.get('thread_id')}`\n"
        f"提问人: `{question.get('asked_by')}`  类别: `{question.get('category') or '-'}`\n\n"
        f"{question.get('question')}"
    )
    suggestion = extract_suggestion(str(question.get("question") or ""))
    elements: list[dict[str, Any]] = [
        {"tag": "div", "text": {"tag": "lark_md", "content": body}},
    ]
    if state == "open":
        actions = []
        if suggestion:
            actions.append(_button("采纳建议", "primary", f"{ADOPT_COMMAND}:{question_id}"))
        actions.append(_button("标记 Out of Scope", "danger", f"{OOS_COMMAND}:{question_id}"))
        elements.append({"tag": "action", "actions": actions})
        elements.append({
            "tag": "note",
            "elements": [{
                "tag": "plain_text",
                "content": "详答请在 Web channel 或 CLI 回答(zf emit channel.question.resolved)",
            }],
        })
    else:
        elements.append({
            "tag": "note",
            "elements": [{
                "tag": "plain_text",
                "content": f"已处理: {question.get('resolution') or state} by {question.get('resolved_by') or '-'}",
            }],
        })
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "Channel 讨论提问"},
            "template": "blue" if state == "open" else "green",
        },
        "elements": elements,
        "_card_key": f"channel-question-{question_id}",
    }


def sync_channel_question_cards(
    state_dir,
    *,
    send_card,
    update_card,
    ledger: dict | None = None,
) -> dict:
    """Send one card per open question; flip it to a receipt when resolved."""
    from zf.core.events.log import EventLog

    state_dir = Path(state_dir)
    ledger = dict(ledger or {})
    try:
        events = EventLog(state_dir / "events.jsonl").read_all()
    except Exception:
        events = []
    sent: list[str] = []
    updated: list[str] = []
    for question_id, question in fold_open_questions(events).items():
        key = f"channel-question-{question_id}"
        status = str(question.get("status") or "open")
        entry = ledger.get(key)
        if entry is None:
            if status != "open":
                continue  # resolved before ever paged — no card needed
            card = build_question_card(question, state="open")
            message_id = send_card(card)
            ledger[key] = {"message_id": message_id, "state": "open"}
            sent.append(question_id)
            continue
        if status != "open" and entry.get("state") == "open":
            update_card(str(entry["message_id"]), build_question_card(question, state=status))
            entry["state"] = status
            updated.append(question_id)
    return {"sent": sent, "updated": updated, "ledger": ledger}


def push_channel_question_cards_once(
    state_dir,
    transport,
    *,
    receive_id: str,
    receive_id_type: str = "chat_id",
    action_secret: bytes | None = None,
    action_ttl_seconds: int = 86400,
    action_key_version: str = "1",
) -> dict:
    import json

    from zf.integrations.feishu.callback_token import attach_action_token
    from zf.integrations.feishu.transport import FeishuMessage

    state_dir = Path(state_dir)
    ledger_path = state_dir / "integrations" / "feishu" / "question_ledger.json"
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ledger = {}

    def _prepare(card: dict) -> dict:
        card = {k: v for k, v in card.items() if not k.startswith("_")}
        if action_secret:
            card = attach_action_token(
                card,
                secret=action_secret,
                ttl_seconds=action_ttl_seconds,
                key_version=action_key_version,
            )
        return card

    def send_card(card: dict) -> str:
        return transport.send_card(FeishuMessage(
            receive_id=receive_id,
            receive_id_type=receive_id_type,
            content=_prepare(card),
        ))

    def update_card(message_id: str, card: dict):
        return transport.update_card(message_id, _prepare(card), 1)

    result = sync_channel_question_cards(
        state_dir, send_card=send_card, update_card=update_card, ledger=ledger,
    )
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(
        json.dumps(result["ledger"], ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return result


def handle_question_decision(
    *,
    command: str,
    question_id: str,
    state_dir,
    writer,
    user_id: str = "",
) -> dict[str, Any]:
    """Feishu button click -> one channel.question.resolved event.

    The clicking Feishu user is the owner surface (not a channel agent
    member), so the kernel's owner-only `answered` gate passes.
    """
    from zf.core.events.log import EventLog

    events = EventLog(Path(state_dir) / "events.jsonl").read_all()
    question = fold_open_questions(events).get(question_id)
    if not question:
        return {"ok": False, "reason": "unknown_question", "question_id": question_id}
    if question.get("status") != "open":
        return {"ok": True, "reason": "already_resolved", "question_id": question_id}
    if command == ADOPT_COMMAND:
        suggestion = extract_suggestion(str(question.get("question") or ""))
        resolution = "answered"
        answer = f"(owner 采纳提问内嵌建议) {suggestion}" if suggestion else "(owner 采纳建议)"
        extra: dict[str, Any] = {"answer": answer}
    elif command == OOS_COMMAND:
        resolution = "out_of_scope"
        extra = {"risk_note": "owner 经飞书卡划出范围"}
    else:
        return {"ok": False, "reason": "unknown_command", "command": command}
    event = writer.emit(
        "channel.question.resolved",
        actor=f"feishu:{user_id or 'owner'}",
        correlation_id=str(question.get("channel_id") or "") or None,
        payload={
            "channel_id": question.get("channel_id"),
            "thread_id": question.get("thread_id"),
            "question_id": question_id,
            "resolution": resolution,
            "resolved_by": f"feishu:{user_id or 'owner'}",
            "source": "feishu",
            **extra,
        },
    )
    return {"ok": True, "resolution": resolution, "event_id": event.id, "question_id": question_id}


def _button(text: str, typ: str, action: str) -> dict[str, Any]:
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": text},
        "type": typ,
        "value": {"action": action},
    }
