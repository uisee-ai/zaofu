"""Run Manager Feishu cards: live run status + human-decision actions.

Pure projection helpers. The sidecar reads kernel-owned state/events and sends
or updates Feishu cards; button callbacks write intent events through
``zf cli feishu`` and Run Manager consumes those events on the next tick.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any


def build_run_manager_status_card(projection: dict[str, Any]) -> dict[str, Any]:
    """Render one live status card for the current Run Manager projection."""
    summary = projection.get("summary") if isinstance(projection.get("summary"), dict) else {}
    monitor = projection.get("monitor") if isinstance(projection.get("monitor"), dict) else {}
    status_explain = (
        projection.get("status_explain")
        if isinstance(projection.get("status_explain"), dict)
        else {}
    )
    completion = (
        projection.get("completion_profile")
        if isinstance(projection.get("completion_profile"), dict)
        else {}
    )
    pending_human = completion.get("pending_human_decisions")
    pending_human_count = len(pending_human) if isinstance(pending_human, list) else 0
    body = (
        "**Run Manager 状态**\n"
        f"goal: `{summary.get('goal_status') or '-'}`  "
        f"completion: `{summary.get('completion_status') or '-'}`\n"
        f"monitor: `{monitor.get('state') or '-'}`  "
        f"wait: `{status_explain.get('wait_reason') or monitor.get('next_wait') or '-'}`\n"
        f"pending_actions: `{summary.get('pending_actions') or 0}`  "
        f"blocked: `{summary.get('blocked_actions') or 0}`  "
        f"human: `{pending_human_count}`"
    )
    generated_at = str(projection.get("generated_at") or "")
    if generated_at:
        body += f"\ngenerated_at: `{generated_at}`"
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "Run Manager"},
            "template": _status_template(summary, status_explain, pending_human_count),
        },
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": body}}],
        "_card_key": "run-manager-status",
    }


def build_run_manager_escalation_card(
    event_payload: dict[str, Any],
    *,
    state: str = "pending",
    decision: str = "",
) -> dict[str, Any]:
    """Render a human escalation card.

    Pending cards carry three signed-capable buttons:
    approve controlled action, ask Autoresearch to diagnose, or safe halt.
    Resolved cards drop buttons and become a receipt update.
    """
    token = _decision_token_from_payload(event_payload)
    failure_class = str(event_payload.get("failure_class") or "-")
    checkpoint = str(event_payload.get("checkpoint_id") or "-")
    fingerprint = str(event_payload.get("fingerprint") or "")
    reason = str(event_payload.get("reason") or event_payload.get("message") or "")
    run_id = str(event_payload.get("run_id") or event_payload.get("pdd_id") or "-")
    safe_resume = str(event_payload.get("safe_resume_action") or "")
    task_id = str(event_payload.get("task_id") or "")

    body = (
        f"**需要人工决策**\n"
        f"run: `{run_id}`  failure: `{failure_class}`\n"
        f"checkpoint: `{checkpoint}`"
    )
    if task_id:
        body += f"\ntask: `{task_id}`"
    if safe_resume:
        body += f"\nsafe_resume_action: `{safe_resume}`"
    if fingerprint:
        body += f"\nfingerprint: `{fingerprint}`"
    if reason:
        body += f"\nreason: {reason}"
    if token:
        body += f"\ndecision_token: `{token}`"
    if state != "pending":
        body += f"\nresult: `{decision or state}`"

    elements: list[dict[str, Any]] = [
        {"tag": "div", "text": {"tag": "lark_md", "content": body}},
    ]
    if state == "pending" and token:
        elements.append({
            "tag": "action",
            "actions": [
                _button("批准并执行", "primary", f"human-decision-approve:{token}"),
                _button("转 Autoresearch", "default", f"human-decision-diagnose:{token}"),
                _button("安全暂停", "danger", f"human-decision-halt:{token}"),
            ],
        })

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "Run Manager 人工决策"},
            "template": "orange" if state == "pending" else _resolved_template(state),
        },
        "elements": elements,
        "_card_key": f"run-manager-escalation-{token}",
    }


def sync_run_manager_cards(
    state_dir,
    *,
    send_card,
    update_card,
    ledger: dict | None = None,
) -> dict:
    """Send/update Run Manager status and human-decision cards idempotently."""
    from zf.core.events.log import EventLog

    state_dir = Path(state_dir)
    ledger = ledger if ledger is not None else {}
    events = []
    try:
        events = EventLog(state_dir / "events.jsonl").read_all()
    except Exception:
        events = []

    escalation_sent: list[str] = []
    escalation_updated: list[str] = []
    for token, item in _fold_escalations(events).items():
        key = f"run-manager-escalation-{token}"
        entry = ledger.get(key) or {}
        state = str(item.get("state") or "pending")
        decision = str(item.get("decision") or "")
        card = build_run_manager_escalation_card(
            item.get("payload") or {},
            state=state,
            decision=decision,
        )
        if not entry.get("message_id"):
            message_id = send_card(card)
            ledger[key] = {
                "message_id": str(message_id),
                "state": state,
                "decision": decision,
            }
            escalation_sent.append(token)
            continue
        if entry.get("state") != state or entry.get("decision") != decision:
            update_card(str(entry["message_id"]), card)
            ledger[key] = {**entry, "state": state, "decision": decision}
            escalation_updated.append(token)

    status_sent = False
    status_updated = False
    projection = _load_run_manager_projection(state_dir)
    if projection:
        digest = _status_digest(projection)
        key = "run-manager-status"
        entry = ledger.get(key) or {}
        if not entry.get("message_id"):
            message_id = send_card(build_run_manager_status_card(projection))
            ledger[key] = {"message_id": str(message_id), "digest": digest}
            status_sent = True
        elif entry.get("digest") != digest:
            update_card(str(entry["message_id"]), build_run_manager_status_card(projection))
            ledger[key] = {**entry, "digest": digest}
            status_updated = True

    return {
        "escalation_sent": escalation_sent,
        "escalation_updated": escalation_updated,
        "status_sent": status_sent,
        "status_updated": status_updated,
        "ledger": ledger,
    }


def push_run_manager_cards_once(
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
    """Production sidecar caller with persistent ledger and optional signing."""
    from zf.integrations.feishu.callback_token import attach_action_token
    from zf.integrations.feishu.transport import FeishuMessage

    state_dir = Path(state_dir)
    issued_at = time.time() if now is None else now
    ledger_path = state_dir / "integrations" / "feishu" / "run_manager_ledger.json"
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
        if action_secret:
            attach_action_token(
                card,
                secret=action_secret,
                chat_id=receive_id,
                ttl_seconds=action_ttl_seconds,
                now=issued_at,
                key_version=action_key_version,
            )
        return transport.update_card(message_id, card)

    result = sync_run_manager_cards(
        state_dir,
        send_card=send_card,
        update_card=update_card,
        ledger=ledger,
    )
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(
        json.dumps(result["ledger"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def _button(text: str, typ: str, action: str) -> dict[str, Any]:
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": text},
        "type": typ,
        "value": {"action": action},
    }


def _status_template(
    summary: dict[str, Any],
    status_explain: dict[str, Any],
    pending_human_count: int,
) -> str:
    if pending_human_count:
        return "orange"
    if status_explain.get("blocking"):
        return "red"
    if summary.get("completion_status") == "complete":
        return "green"
    if summary.get("no_progress_status") == "tripped":
        return "red"
    return "blue"


def _resolved_template(state: str) -> str:
    if state == "applied":
        return "green"
    if state == "rejected":
        return "grey"
    return "blue"


def _fold_escalations(events: list) -> dict[str, dict[str, Any]]:
    items: dict[str, dict[str, Any]] = {}
    for event in events:
        etype = str(getattr(event, "type", "") or "")
        payload = getattr(event, "payload", None)
        payload = payload if isinstance(payload, dict) else {}
        if etype == "human.escalation.sent":
            token = _decision_token_from_payload(payload) or str(getattr(event, "id", "") or "")
            if not token:
                continue
            items[token] = {"state": "pending", "payload": payload, "decision": ""}
            continue
        if etype == "human.escalation.acknowledged":
            token = _decision_token_from_payload(payload) or str(getattr(event, "id", "") or "")
            if token and token in items:
                items[token] = {
                    **items[token],
                    "state": "acknowledged",
                    "decision": str(payload.get("decision") or "acknowledged"),
                }
            continue
        if etype in {"run.manager.human_decision.applied", "run.manager.human_decision.rejected"}:
            token = _decision_token_from_payload(payload) or str(getattr(event, "id", "") or "")
            if token and token in items:
                items[token] = {
                    **items[token],
                    "state": "applied" if etype.endswith(".applied") else "rejected",
                    "decision": str(payload.get("decision") or ""),
                }
    return items


def _decision_token_from_payload(payload: dict[str, Any]) -> str:
    raw = str(
        payload.get("decision_token")
        or payload.get("response_token")
        or payload.get("approval_ref")
        or payload.get("source_message_id")
        or payload.get("escalation_event_id")
        or ""
    )
    if raw.startswith("human:"):
        raw = raw.removeprefix("human:")
    return raw


def _load_run_manager_projection(state_dir: Path) -> dict[str, Any]:
    projection_path = state_dir / "projections" / "run_manager.json"
    try:
        data = json.loads(projection_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        pass
    try:
        from zf.runtime.run_manager import build_run_manager_projection

        return build_run_manager_projection(state_dir)
    except Exception:
        return {}


def _status_digest(projection: dict[str, Any]) -> str:
    summary = projection.get("summary") if isinstance(projection.get("summary"), dict) else {}
    monitor = projection.get("monitor") if isinstance(projection.get("monitor"), dict) else {}
    status = projection.get("status_explain") if isinstance(projection.get("status_explain"), dict) else {}
    completion = (
        projection.get("completion_profile")
        if isinstance(projection.get("completion_profile"), dict)
        else {}
    )
    raw = {
        "summary": {
            "pending_actions": summary.get("pending_actions"),
            "blocked_actions": summary.get("blocked_actions"),
            "goal_status": summary.get("goal_status"),
            "completion_status": summary.get("completion_status"),
            "no_progress_status": summary.get("no_progress_status"),
        },
        "monitor": {
            "state": monitor.get("state"),
            "next_wait": monitor.get("next_wait"),
            "latest_stage": monitor.get("latest_stage"),
        },
        "status": {
            "wait_reason": status.get("wait_reason"),
            "next_auto_action": status.get("next_auto_action"),
            "blocking": status.get("blocking"),
        },
        "pending_human": completion.get("pending_human_decisions") or [],
    }
    body = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()
