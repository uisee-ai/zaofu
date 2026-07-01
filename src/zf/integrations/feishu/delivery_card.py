"""Channel delivery projector — agent Working/Done/Failed/Interrupted as one card.

feishu-C (design §6 P1.2): fold a single channel reply's lifecycle —
``channel.agent.reply.requested/started/failed/completed`` plus
``agent.session.run.cancelled`` — into ONE feishu card that updates in place
(update-message), instead of one message per event. All events share a stable
``request_id`` (the reply request key, also carried in the session-run base
payload), so that is the card key.

Screen-spam boundary (§5.1): ``agent.session.part.delta`` is deliberately NOT
projected — streaming deltas never touch the card; only lifecycle transitions
do. Pure functions; subscription/transport live in the sidecar.
"""

from __future__ import annotations

from typing import Any

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
    _WORKING: ("blue", "🟦 Working — agent 处理中"),
    "done": ("green", "✅ Done"),
    "failed": ("red", "❌ Failed"),
    "interrupted": ("grey", "⏹️ Interrupted"),
}


def build_delivery_card(state: dict[str, Any]) -> dict[str, Any]:
    """Render the current projected state of one reply into a feishu card.

    A Working card carries an Interrupt button (callback ``agent-cancel:<id>``,
    gated by feishu-B); terminal states drop the button.
    """
    request_id = str(state.get("request_id") or "")
    status = str(state.get("status") or _WORKING)
    template, headline = _HEADER.get(status, _HEADER[_WORKING])
    member = str(state.get("member_id") or "-")
    provider = str(state.get("provider") or state.get("backend") or "-")
    reason = str(state.get("reason") or "")
    body = (
        f"{headline}\n"
        f"member: {member}  provider: {provider}\n"
        f"request: {request_id}"
    )
    if reason and status in {"failed", "interrupted"}:
        body += f"\nreason: {reason}"
    elements: list[dict[str, Any]] = [
        {"tag": "div", "text": {"tag": "lark_md", "content": body}},
    ]
    if status == _WORKING and request_id:
        elements.append({"tag": "action", "actions": [{
            "tag": "button",
            "text": {"tag": "plain_text", "content": "⏹️ Interrupt"},
            "type": "danger",
            "value": {"action": f"agent-cancel:{request_id}"},
        }]})
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "Channel 回复"},
            "template": template,
        },
        "elements": elements,
        "_card_key": f"delivery-{request_id}",
    }


def _fold_states(events: list) -> dict[str, dict[str, Any]]:
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
            "member_id": payload.get("member_id") or (current or {}).get("member_id"),
            "provider": (
                payload.get("provider")
                or payload.get("backend")
                or (current or {}).get("provider")
            ),
            "reason": payload.get("reason") or (current or {}).get("reason"),
        }
    return states


def sync_delivery_cards(
    state_dir,
    *,
    send_card,
    update_card,
    ledger: dict | None = None,
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
    states = _fold_states(events)
    sent, updated = [], []
    for request_id, state in states.items():
        key = f"delivery-{request_id}"
        entry = ledger.get(key) or {}
        if not entry.get("message_id"):
            message_id = send_card(build_delivery_card(state))
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
    return {"sent": sent, "updated": updated, "ledger": ledger}


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

    ledger_path = (
        Path(state_dir) / "integrations" / "feishu" / "delivery_ledger.json"
    )
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ledger = {}

    def send_card(card: dict) -> str | None:
        if action_secret:
            attach_action_token(
                card,
                secret=action_secret,
                chat_id=receive_id,
                ttl_seconds=action_ttl_seconds,
                now=issued_at,
                key_version=action_key_version,
            )
        return transport.send_card(FeishuMessage(
            chat_id=receive_id,
            content=json.dumps(card, ensure_ascii=False),
            msg_type="interactive",
            receive_id_type=receive_id_type,
        ))

    def update_card(message_id: str, card: dict) -> bool:
        return transport.update_card(message_id, card)

    result = sync_delivery_cards(
        state_dir, send_card=send_card, update_card=update_card, ledger=ledger,
    )
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(
        json.dumps(result["ledger"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result
