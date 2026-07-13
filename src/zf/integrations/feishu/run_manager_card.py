"""Run Manager Feishu cards: live run status + human-decision actions.

Pure projection helpers. The sidecar reads kernel-owned state/events and sends
or updates Feishu cards; button callbacks write intent events through
``zf cli feishu`` and Run Manager consumes those events on the next tick.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any


# Run Manager status → owner-facing Chinese (the raw status card dumped state
# codes like `diagnosis_required` and ignored the human-oriented fields the
# status_explain projection already computes: next_auto_action / active_task /
# intervention). Matched by substring so compound codes
# (`diagnosis_required_no_progress_tripped`) still map. Order specific-first.
_MONITOR_EMOJI = {
    "blocked": "🔴", "stalled": "🔴", "error": "🔴",
    "active": "🟢", "running": "🟢",
    "idle": "⚪", "waiting": "⚪",
    "done": "✅", "complete": "✅",
}
_WAIT_HUMAN: tuple[tuple[str, str], ...] = (
    ("no_progress", "任务停滞、无进展,需要诊断"),
    ("stale_recovery", "恢复停滞,待诊断或升级"),
    ("diagnosis", "需要诊断(autoresearch 介入)"),
    ("human_decision", "等待你的人工决策"),
    ("wait_for_human", "等待你的人工决策"),
    ("needs_approval", "有动作需你批准"),
    ("pending_action", "有待批动作等你确认"),
    ("continue_waiting", "正常等待中"),
)
_NEXT_HUMAN: tuple[tuple[str, str], ...] = (
    ("invoke_autoresearch", "触发 autoresearch 诊断"),
    ("autoresearch", "触发 autoresearch 诊断"),
    ("safe_resume", "安全恢复执行"),
    ("safe_halt", "安全暂停"),
    ("run_manager_action", "执行下一个动作"),
    ("auto_decide", "自动决策推进"),
    ("wait_for_human", "等你决策"),
    ("human_escalate", "升级给人工"),
    ("continue_waiting", "继续等待"),
)


def _map_substr(value: object, table: tuple[tuple[str, str], ...]) -> str:
    v = str(value or "").lower()
    for needle, human in table:
        if needle in v:
            return human
    return ""


def _monitor_badge(state: object) -> str:
    v = str(state or "").lower()
    for needle, emoji in _MONITOR_EMOJI.items():
        if needle in v:
            return emoji
    return "🔔"


def _short_time(iso: object) -> str:
    """2026-07-10T06:06:00.356202Z → 06:06(丢掉日期/微秒/时区噪声）。"""
    match = re.search(r"T(\d{2}:\d{2})", str(iso or ""))
    return match.group(1) if match else ""


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

    monitor_state = monitor.get("state")
    blocking = bool(status_explain.get("blocking"))
    pending = int(summary.get("pending_actions") or 0)
    blocked = int(summary.get("blocked_actions") or 0)

    # Header line: one plain-Chinese verdict + severity emoji (aligned with the
    # card header color from _status_template).
    if pending_human_count or blocking:
        head = "🔴 Run Manager · 需要你关注"
    elif _monitor_badge(monitor_state) == "🔴":
        head = "🟡 Run Manager · 监控阻塞"
    else:
        head = f"{_monitor_badge(monitor_state)} Run Manager · 运行中"
    lines = [f"**{head}**"]

    # What it is doing (from the projection's active-task fields).
    active_task = str(status_explain.get("active_task_id") or "")
    active_lane = str(status_explain.get("active_lane") or "")
    phase = str(status_explain.get("current_phase") or monitor.get("current_phase") or "")
    if active_task:
        ctx = f"正在做:`{active_task}`" + (f" @{active_lane}" if active_lane else "")
        if phase and phase != "unknown":
            ctx += f"(阶段 {phase})"
        lines.append(ctx)

    # Why it is waiting / blocked (human, not the raw wait code).
    wait_human = _map_substr(
        status_explain.get("wait_reason") or monitor.get("next_wait"), _WAIT_HUMAN)
    if wait_human:
        lines.append(f"卡在:{wait_human}")

    # What it will do next automatically.
    next_human = _map_substr(status_explain.get("next_auto_action"), _NEXT_HUMAN)
    if next_human:
        lines.append(f"下一步(自动):{next_human}")

    # What needs the operator.
    todo = []
    if pending_human_count:
        todo.append(f"{pending_human_count} 个人工决策")
    if pending:
        todo.append(f"{pending} 个待批动作")
    if blocked:
        todo.append(f"{blocked} 个阻塞动作")
    lines.append("待你处理:" + ("、".join(todo) if todo else "暂无"))

    generated_at = str(projection.get("generated_at") or "")
    short_time = _short_time(generated_at)
    if short_time:
        lines.append(f"🕐 {short_time} 更新")
    body = "\n".join(lines)
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
                _button("解释", "default", f"human-decision-explain:{token}"),
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
            context = _context_from_escalation_item(token, item)
            context["message_id"] = str(message_id or "")
            ledger[key] = {
                "message_id": str(message_id),
                "state": state,
                "decision": decision,
                "token": token,
                "context": context,
            }
            escalation_sent.append(token)
            continue
        if entry.get("state") != state or entry.get("decision") != decision:
            update_card(str(entry["message_id"]), card)
            ledger[key] = {
                **entry,
                "state": state,
                "decision": decision,
                "token": token,
                "context": entry.get("context") or _context_from_escalation_item(token, item),
            }
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
            items[token] = {
                "state": "pending",
                "payload": payload,
                "decision": "",
                "source_event_id": str(getattr(event, "id", "") or ""),
                "source_event_type": etype,
                "correlation_id": str(getattr(event, "correlation_id", "") or ""),
                "created_at": str(getattr(event, "ts", "") or ""),
            }
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


def resolve_run_manager_card_context(
    state_dir,
    *,
    decision_token: str = "",
    message_id: str = "",
    chat_id: str = "",
) -> dict[str, Any]:
    """Resolve a Feishu follow-up to the Run Manager card it references."""

    ledger_path = Path(state_dir) / "integrations" / "feishu" / "run_manager_ledger.json"
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    for key, entry in (ledger or {}).items():
        if not isinstance(entry, dict):
            continue
        context = entry.get("context") if isinstance(entry.get("context"), dict) else {}
        token = str(entry.get("token") or context.get("decision_token") or "")
        if decision_token and decision_token == token:
            return {"ledger_key": key, **context}
        if message_id and message_id == str(entry.get("message_id") or context.get("message_id") or ""):
            context_chat_id = str(context.get("chat_id") or "")
            if chat_id and context_chat_id and chat_id != context_chat_id:
                continue
            return {"ledger_key": key, **context}
    return {}


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


def _context_from_escalation_item(token: str, item: dict[str, Any]) -> dict[str, Any]:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    return {
        "schema_version": "run-manager.feishu-card-context.v1",
        "decision_token": token,
        "source_event_id": str(item.get("source_event_id") or ""),
        "source_event_type": str(item.get("source_event_type") or "human.escalation.sent"),
        "correlation_id": str(item.get("correlation_id") or ""),
        "created_at": str(item.get("created_at") or ""),
        "run_id": str(payload.get("run_id") or payload.get("pdd_id") or ""),
        "task_id": str(payload.get("task_id") or ""),
        "failure_class": str(payload.get("failure_class") or ""),
        "checkpoint_id": str(payload.get("checkpoint_id") or ""),
        "fingerprint": str(payload.get("fingerprint") or ""),
        "safe_resume_action": str(payload.get("safe_resume_action") or ""),
        "reason": str(payload.get("reason") or payload.get("message") or ""),
    }


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
