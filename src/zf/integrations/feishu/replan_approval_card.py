"""Replan owner-decision card (报告场景 13) — outbound notify + deep link.

Mirrors plan_approval_card's outbound half for the replan owner-decision gate:
when a replan adoption is parked waiting on the owner
(``replan.adoption.awaiting_owner``), Feishu gets a summary card with a deep link
to the Web decision surface; the owner decides there (approve/defer/reject +
reason — too heavy for an inline button), and ``replan.owner_decision.*`` flips
the original card to the verdict. Feishu holds no truth; Web/CLI remain the
fallback decision surface.

Pure card builders + a sync pass; transport lives in push_replan_cards_once.
"""

from __future__ import annotations

from typing import Any


def build_replan_card(payload: dict[str, Any], *, web_base_url: str = "") -> dict[str, Any]:
    """replan.adoption.awaiting_owner → summary card with a deep link."""
    proposal_ref = str(payload.get("proposal_ref") or "")
    deep_link = (
        f"{web_base_url.rstrip('/')}/?page=inbox&replan={proposal_ref}"
        if web_base_url else ""
    )
    body = (
        "**待决策 replan**(owner 三选一)\n"
        f"proposal: {proposal_ref}\n"
        f"eval: {payload.get('eval_ref')}\n"
        f"task_map: {payload.get('candidate_task_map_ref') or '-'}"
    )
    elements: list[dict[str, Any]] = [
        {"tag": "div", "text": {"tag": "lark_md", "content": body}},
    ]
    if deep_link:
        elements.append({"tag": "action", "actions": [{
            "tag": "button",
            "text": {"tag": "plain_text", "content": "去 Web 决策"},
            "type": "primary",
            "url": deep_link,
        }]})
    elements.append({"tag": "note", "elements": [{
        "tag": "plain_text",
        "content": (
            f"proposal: {proposal_ref} — approve/defer/reject 在 Web "
            "(三选一需 reason,断网由 Web/CLI 兜底)"
        ),
    }]})
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "Replan 决策请求"},
            "template": "orange",
        },
        "elements": elements,
        "_card_key": f"replan-{proposal_ref}",
    }


_VERDICT_BODY = {
    "approved": ("green", "✅ 已批准 — replan 采纳,继续推进"),
    "deferred": ("grey", "⏸️ 已推迟 — replan 暂缓"),
    "rejected": ("red", "❌ 已驳回 — 维持原计划"),
}


def build_replan_verdict_update(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """replan.owner_decision.{approved,deferred,rejected} → original card update."""
    proposal_ref = str(payload.get("proposal_ref") or "")
    decision = event_type.rsplit(".", 1)[-1]
    template, body = _VERDICT_BODY.get(decision, ("grey", "replan 已决策"))
    reason = str(payload.get("reason") or "")
    if reason:
        body = f"{body}\nreason: {reason}"
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "Replan 决策结果"},
            "template": template,
        },
        "elements": [{"tag": "div", "text": {
            "tag": "lark_md", "content": f"{body}\nproposal: {proposal_ref}"}}],
        "_card_key": f"replan-{proposal_ref}",
    }


_DECISION_EVENTS = {
    "replan.owner_decision.approved",
    "replan.owner_decision.deferred",
    "replan.owner_decision.rejected",
}


def sync_replan_cards(state_dir, *, send_card, update_card,
                      ledger: dict | None = None, web_base_url: str = "") -> dict:
    """Send a replan card once per proposal; update it on the owner decision."""
    from pathlib import Path

    from zf.core.events.log import EventLog

    ledger = ledger if ledger is not None else {}
    try:
        events = EventLog(Path(state_dir) / "events.jsonl").read_all()
    except Exception:
        events = []
    requested: dict[str, dict] = {}
    verdicts: dict[str, tuple[str, dict]] = {}
    for event in events:
        payload = getattr(event, "payload", None)
        payload = payload if isinstance(payload, dict) else {}
        proposal_ref = str(payload.get("proposal_ref") or "")
        if not proposal_ref:
            continue
        etype = str(getattr(event, "type", "") or "")
        if etype == "replan.adoption.awaiting_owner":
            requested[proposal_ref] = payload
        elif etype in _DECISION_EVENTS:
            verdicts[proposal_ref] = (etype, payload)
    sent, updated = [], []
    for proposal_ref, payload in requested.items():
        key = f"replan-{proposal_ref}"
        entry = ledger.get(key) or {}
        if proposal_ref in verdicts:
            if entry.get("state") != "verdict" and entry.get("message_id"):
                etype, vp = verdicts[proposal_ref]
                update_card(entry["message_id"], build_replan_verdict_update(etype, vp))
                ledger[key] = {**entry, "state": "verdict"}
                updated.append(proposal_ref)
            continue
        if entry.get("message_id"):
            continue
        message_id = send_card(build_replan_card(payload, web_base_url=web_base_url))
        ledger[key] = {"message_id": str(message_id), "state": "pending"}
        sent.append(proposal_ref)
    return {"sent": sent, "updated": updated, "ledger": ledger}


def push_replan_cards_once(state_dir, transport, *, receive_id: str,
                           receive_id_type: str = "chat_id", web_base_url: str = "") -> dict:
    """Production caller: transport + persistent ledger, one replan sync pass."""
    import json
    from pathlib import Path

    from zf.integrations.feishu.transport import FeishuMessage

    ledger_path = Path(state_dir) / "integrations" / "feishu" / "replan_ledger.json"
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ledger = {}

    def send_card(card: dict) -> str | None:
        return transport.send_card(FeishuMessage(
            chat_id=receive_id, content=json.dumps(card, ensure_ascii=False),
            msg_type="interactive", receive_id_type=receive_id_type))

    def update_card(message_id: str, card: dict) -> bool:
        return transport.update_card(message_id, card)

    result = sync_replan_cards(state_dir, send_card=send_card, update_card=update_card,
                               ledger=ledger, web_base_url=web_base_url)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(json.dumps(result["ledger"], ensure_ascii=False, indent=2),
                           encoding="utf-8")
    return result
