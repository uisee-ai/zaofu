"""Acknowledged owner-card flip — projection-side card update (ack-flip task).

When ``runtime.attention.acknowledged`` lands (Feishu button or the Web
attention-ack action — one event contract), the originally delivered alert
card is updated in place to a green verdict card, replan-verdict family.
Idempotency rides a ledger file, NOT a new event type: flipping is cosmetic
projection, it must never feed the recovery chain (doc 121).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.integrations.feishu.transport import FeishuTransport
from zf.runtime.owner_visible_render import humanize_owner_title

OWNER_MESSAGE_DELIVERED = "owner.visible_message.delivered"

_FLIP_LEDGER_REL = Path("integrations") / "feishu" / "owner_card_flip_ledger.json"
_FLIP_ATTEMPT_CAP = 2  # a failing update is retried once, then abandoned — no storm


def _flip_card(title: str, *, operator: str, hhmm: str, task_id: str,
               attention_id: str) -> dict[str, Any]:
    """Green verdict card replacing an acknowledged alert (replan-verdict family)."""
    lines = [f"✅ 已确认 — {operator} {hhmm}"]
    human_title = humanize_owner_title(title) if title else ""
    if human_title:
        lines.append(human_title)
    if task_id:
        lines.append(f"任务 {task_id}")
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "green",
            "title": {"tag": "plain_text", "content": "✅ 已确认收到"},
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}},
            {"tag": "note", "elements": [{
                "tag": "plain_text", "content": f"attention {attention_id}"}]},
        ],
    }


def flip_acknowledged_owner_cards_once(
    *,
    event_log: EventLog,
    transport: FeishuTransport,
    target: str = "feishu",
    events: list[ZfEvent] | None = None,
) -> dict[str, Any]:
    """Update delivered alert cards whose attention was acknowledged.

    Join key: ``runtime.attention.acknowledged``.payload.attention_id (same
    event contract for the Feishu button and the Web attention-ack action) ×
    delivered receipts carrying ``feishu_message_id``. Idempotency rides a
    ledger file (the replan-card pattern), NOT a new event type — flipping is a
    projection concern, it must not feed the recovery chain (doc 121). Receipts
    written before feishu_message_id existed simply never flip (safe).
    """
    events = events if events is not None else event_log.read_all()
    acks: dict[str, ZfEvent] = {}
    for event in events:
        if event.type != "runtime.attention.acknowledged":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        attention_id = str(payload.get("attention_id") or "")
        if attention_id and attention_id not in acks:
            acks[attention_id] = event  # first ack wins
    if not acks:
        return {"flipped": [], "skipped": 0}

    ledger_path = Path(event_log.path).parent / _FLIP_LEDGER_REL
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ledger = {}

    flipped: list[str] = []
    skipped = 0
    for event in events:
        if event.type != OWNER_MESSAGE_DELIVERED:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if str(payload.get("target") or "") != target:
            continue
        feishu_mid = str(payload.get("feishu_message_id") or "")
        attention_id = str(payload.get("attention_id") or "")
        ack = acks.get(attention_id)
        if not feishu_mid or ack is None:
            skipped += 1
            continue
        entry = ledger.get(feishu_mid) or {}
        if entry.get("flipped") or int(entry.get("attempts") or 0) >= _FLIP_ATTEMPT_CAP:
            continue
        ack_payload = ack.payload if isinstance(ack.payload, dict) else {}
        operator = (
            str(ack_payload.get("operator") or "")
            or str(ack.actor or "").removeprefix("feishu:")[:12]
            or "operator"
        )
        hhmm = str(ack.ts)[11:16] if len(str(ack.ts)) >= 16 else ""
        card = _flip_card(
            str(payload.get("title") or ""),
            operator=operator, hhmm=hhmm,
            task_id=str(payload.get("task_id") or ""),
            attention_id=attention_id,
        )
        try:
            ok = transport.update_card(feishu_mid, card)
        except Exception:
            ok = False
        if ok:
            ledger[feishu_mid] = {"flipped": True, "attention_id": attention_id}
            flipped.append(feishu_mid)
        else:
            ledger[feishu_mid] = {
                "flipped": False, "attention_id": attention_id,
                "attempts": int(entry.get("attempts") or 0) + 1,
            }
    if flipped or any(not v.get("flipped") for v in ledger.values()):
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        ledger_path.write_text(
            json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"flipped": flipped, "skipped": skipped}
