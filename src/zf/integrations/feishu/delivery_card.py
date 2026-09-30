"""Channel delivery projector — readable agent reply cards.

feishu-C (design §6 P1.2): fold a single channel reply's lifecycle —
``channel.agent.reply.requested/started/failed/completed`` plus
``agent.session.run.cancelled`` — into ONE feishu card that updates in place
(update-message), instead of one message per event. All events share a stable
``request_id`` (the reply request key, also carried in the session-run base
payload), so that is the card key.

The Channel sidecar owns the full assistant text.  This projector uses that
canonical body only for a non-streaming reply's terminal card; stream replies
remain owned by ``stream_card`` and are skipped here.
"""

from __future__ import annotations

from typing import Any

from zf.runtime.provider_capabilities import provider_capability_for_backend
from zf.integrations.feishu.reply_details import reply_details

# event type → projected card state. Terminal states win over "working".
_WORKING = "working"
_STATE_BY_EVENT = {
    "channel.agent.reply.requested": _WORKING,
    "channel.agent.reply.started": _WORKING,
    "channel.agent.reply.completed": "done",
    "channel.agent.reply.failed": "failed",
    "agent.session.run.cancelled": "interrupted",
}
_TERMINAL = {"done", "failed", "interrupted"}
_HEADER = {
    _WORKING: ("blue", "正在处理"),
    "done": ("green", "已回复"),
    "failed": ("red", "未完成"),
    "interrupted": ("grey", "已停止"),
}


def build_delivery_card(state: dict[str, Any]) -> dict[str, Any]:
    """Render the current projected state of one reply into a feishu card.

    A Working card carries an Interrupt button (callback ``agent-cancel:<id>``,
    gated by feishu-B); terminal states drop the button.
    """
    request_id = str(state.get("request_id") or "")
    status = str(state.get("status") or _WORKING)
    template, headline = _HEADER.get(status, _HEADER[_WORKING])
    display_name = str(state.get("display_name") or "ZaoFu Agent")
    if status == "done":
        body = str(state.get("reply_text") or "回复已完成，但未返回可展示的内容。")
    elif status == "failed":
        body = "本次回复未能完成。请重试，或在 ZaoFu 的运行记录中查看详情。"
    elif status == "interrupted":
        body = "本次回复已停止。"
    else:
        body = "正在分析这条消息。"
    elements: list[dict[str, Any]] = [
        {"tag": "div", "text": {"tag": "lark_md", "content": body}},
    ]
    if status == "done" and state.get("reply_text"):
        elements = [reply_details("回复详情", elements)]
    if status == _WORKING and request_id:
        elements.append({"tag": "action", "actions": [{
            "tag": "button",
            "text": {"tag": "plain_text", "content": "停止回复"},
            "type": "danger",
            "value": {"action": f"agent-cancel:{request_id}"},
        }]})
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "title": {
                "tag": "plain_text",
                "content": f"{display_name} · {headline}",
            },
            "template": template,
        },
        "elements": elements,
    }


def _fold_states(events: list, *, member: str = "") -> dict[str, dict[str, Any]]:
    """Reduce the event stream to {request_id: latest-projected-state}."""
    states: dict[str, dict[str, Any]] = {}
    for event in events:
        etype = str(getattr(event, "type", "") or "")
        new_state = _STATE_BY_EVENT.get(etype)
        if new_state is None:
            continue  # part.delta and everything else: no card mutation
        payload = getattr(event, "payload", None)
        payload = payload if isinstance(payload, dict) else {}
        request_id = str(payload.get("request_id") or "")
        if not request_id:
            continue
        current = states.get(request_id)
        # Terminal state is sticky: once Done/Failed/Interrupted, ignore later
        # working transitions (out-of-order safety).
        if current is not None and current["status"] in _TERMINAL:
            continue
        states[request_id] = {
            "request_id": request_id,
            "status": new_state,
            "channel_id": payload.get("channel_id") or (current or {}).get("channel_id"),
            "thread_id": payload.get("thread_id") or (current or {}).get("thread_id"),
            "member_id": (
                payload.get("target_member_id")
                or (current or {}).get("member_id")
                or payload.get("member_id")
            ),
            "backend": (
                payload.get("backend")
                or (current or {}).get("backend")
                or ""
            ),
            "provider": (
                payload.get("provider")
                or (current or {}).get("provider")
                or ""
            ),
        }
    if member:
        states = {
            request_id: state
            for request_id, state in states.items()
            if str(state.get("member_id") or "") == member
        }
    return states


def _expects_stream(state: dict[str, Any]) -> bool:
    """Return whether the request's configured backend is stream-capable.

    The first ``agent.session.part.delta`` can arrive several seconds after the
    reply lifecycle begins.  Delivery must not create a Working card during
    that window, because Stream owns the same reply once the first delta lands.
    A terminal stream-capable request without deltas is still eligible for the
    readable Delivery fallback below.
    """
    if str(state.get("status") or "") in _TERMINAL:
        return False
    for backend in (state.get("backend"), state.get("provider")):
        name = str(backend or "").strip()
        if not name:
            continue
        try:
            if bool(provider_capability_for_backend(name).get("streaming")):
                return True
        except (TypeError, ValueError):
            continue
    return False


def sync_delivery_cards(
    state_dir,
    *,
    send_card,
    update_card,
    ledger: dict | None = None,
    skip_request_ids: set[str] | None = None,
    member: str = "",
) -> dict:
    """Send a Working card once per reply; update it in place on terminal state.

    ``ledger`` is the caller-held {card_key: {message_id, status}} dict
    (idempotent across ticks). Feishu unreachable is the caller's to catch —
    the Web Channel timeline stays the source of truth.
    """
    from pathlib import Path

    from zf.core.events.log import EventLog

    ledger = ledger if ledger is not None else {}
    try:
        events = EventLog(Path(state_dir) / "events.jsonl").read_all()
    except Exception:
        events = []
    from zf.integrations.feishu.channel_reply_presentation import (
        collect_channel_reply_presentations,
    )

    states = _fold_states(events, member=member)
    presentations = collect_channel_reply_presentations(
        Path(state_dir), events, member=member
    )
    stream_request_ids = {
        str(getattr(event, "payload", {}).get("request_id") or "")
        for event in events
        if str(getattr(event, "type", "") or "") == "agent.session.part.delta"
        and isinstance(getattr(event, "payload", None), dict)
    }
    skipped = set(skip_request_ids or ()) | stream_request_ids
    for request_id, state in states.items():
        presentation = presentations.get(request_id)
        if presentation is None:
            continue
        state.update({
            "display_name": presentation.display_name,
            "reply_text": presentation.text,
            "origin_chat_id": presentation.chat_id,
            "origin_thread_id": presentation.thread_id,
        })
    sent, updated, skipped_ids = [], [], []
    for request_id, state in states.items():
        if request_id in skipped:
            skipped_ids.append(request_id)
            continue
        if _expects_stream(state):
            # The stream projector owns the first visible card for native
            # stream-json backends.  Do not race it with a Delivery Working
            # card before the first delta is persisted.
            skipped_ids.append(request_id)
            continue
        key = f"delivery-{request_id}"
        entry = ledger.get(key) or {}
        if not entry.get("message_id"):
            message_id = send_card(build_delivery_card(state), state)
            ledger[key] = {"message_id": str(message_id), "status": state["status"]}
            sent.append(request_id)
            continue
        if (
            state["status"] in _TERMINAL
            and entry.get("status") != state["status"]
        ):
            update_card(entry["message_id"], build_delivery_card(state))
            ledger[key] = {**entry, "status": state["status"]}
            updated.append(request_id)
    return {
        "sent": sent,
        "updated": updated,
        "skipped": skipped_ids,
        "ledger": ledger,
    }


def push_delivery_cards_once(
    state_dir,
    transport,
    *,
    receive_id: str,
    receive_id_type: str = "chat_id",
    action_secret: bytes | None = None,
    action_ttl_seconds: int = 86400,
    action_key_version: str = "1",
    now: float | None = None,
    skip_request_ids: set[str] | None = None,
    member: str = "",
) -> dict:
    """Production caller: build send/update closures from a feishu transport +
    a persistent ledger and run one delivery-projection pass. Idempotent across
    ticks via the on-disk ledger; feishu errors propagate to the caller.

    When ``action_secret`` is set, the Interrupt button is signed (feishu-A2)."""
    import json
    import time
    from pathlib import Path

    from zf.integrations.feishu.callback_token import attach_action_token
    from zf.integrations.feishu.transport import FeishuMessage

    issued_at = time.time() if now is None else now

    suffix = f"-{member}" if member else ""
    ledger_path = (
        Path(state_dir) / "integrations" / "feishu"
        / f"delivery_ledger{suffix}.json"
    )

    def read_ledger(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    scoped_ledger_missing = bool(member) and not ledger_path.exists()
    ledger = read_ledger(ledger_path)
    if scoped_ledger_missing:
        # Preserve the old projection idempotency record when upgrading a
        # shared state dir to per-Bot ledgers. This is derived state only; it
        # prevents a restart from posting historical Delivery cards again.
        ledger = read_ledger(ledger_path.with_name("delivery_ledger.json"))

    def send_card(card: dict, state: dict) -> str | None:
        chat_id = str(state.get("origin_chat_id") or receive_id)
        thread_id = str(state.get("origin_thread_id") or "") or None
        if action_secret:
            attach_action_token(
                card,
                secret=action_secret,
                chat_id=chat_id,
                ttl_seconds=action_ttl_seconds,
                now=issued_at,
                key_version=action_key_version,
            )
        return transport.send_card(FeishuMessage(
            chat_id=chat_id,
            thread_id=thread_id,
            content=json.dumps(card, ensure_ascii=False),
            msg_type="interactive",
            receive_id_type=receive_id_type,
        ))

    def update_card(message_id: str, card: dict) -> bool:
        return transport.update_card(message_id, card)

    result = sync_delivery_cards(
        state_dir,
        send_card=send_card,
        update_card=update_card,
        ledger=ledger,
        skip_request_ids=skip_request_ids,
        member=member,
    )
    from zf.core.state.atomic_io import atomic_write_text

    atomic_write_text(
        ledger_path,
        json.dumps(result["ledger"], ensure_ascii=False, indent=2),
    )
    return result
