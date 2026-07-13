"""Feishu -> resident Run Manager Agent inbound driver.

Feishu routes the architect bot here. This handler records receipt, then sends
every message to the Run Manager Agent's normal channel conversation path.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def run_manager_inbound_reply(state_dir, config, event, writer) -> dict[str, Any]:
    state = Path(state_dir)
    payload = getattr(event, "payload", None) or {}
    text = str(payload.get("text") or "")
    original_text = text
    user_id = str(getattr(event, "user_id", "") or payload.get("member_id") or "")
    message_id = str(payload.get("message_id") or "")
    chat_id = str(getattr(event, "chat_id", "") or "")
    received = writer.emit(
        "run.manager.inbound.received",
        actor=f"feishu:{user_id or 'unknown'}",
        payload={
            "schema_version": "run-manager.feishu-inbound.v1",
            "chat_id": chat_id,
            "message_id": message_id,
            "parent_message_id": str(payload.get("parent_message_id") or ""),
            "root_message_id": str(payload.get("root_message_id") or ""),
            "quote_message_id": str(payload.get("quote_message_id") or ""),
            "text_excerpt": text[:500],
        },
    )
    context = _resolve_followup_context(state, text=text, payload=payload, chat_id=chat_id)
    if context:
        writer.emit(
            "run.manager.context.resolved",
            actor="feishu-run-manager-agent",
            causation_id=received.id,
            correlation_id=received.correlation_id or chat_id,
            payload={
                "schema_version": "run-manager.context-resolved.v1",
                "chat_id": chat_id,
                "message_id": message_id,
                "decision_token": str(context.get("decision_token") or ""),
                "ledger_key": str(context.get("ledger_key") or ""),
                "source_event_id": str(context.get("source_event_id") or ""),
                "resolution": "run_manager_card_ledger",
            },
        )
    if _is_resident_handoff_request(text):
        resident_instance = _resident_instance_id(config)
        handoff = writer.emit(
            "run.manager.inbound.handoff.requested",
            actor=f"feishu:{user_id or 'unknown'}",
            causation_id=received.id,
            correlation_id=received.correlation_id or chat_id,
            payload={
                "schema_version": "run-manager.inbound-handoff.v1",
                "chat_id": chat_id,
                "message_id": message_id,
                "resident_instance_id": resident_instance,
                "context": _compact_context(context),
            },
        )
        writer.emit(
            "worker.reply.requested",
            actor="feishu-run-manager-agent",
            causation_id=handoff.id,
            correlation_id=chat_id,
            payload={
                "instance_id": resident_instance,
                "message": _resident_handoff_message(text, context),
                "channel_id": f"feishu-run-manager-{chat_id or 'unknown'}",
                "thread_id": "main",
                "message_id": message_id,
                "target_member_id": resident_instance,
            },
        )
        return {
            "status": "resident_handoff_requested",
            "target": "run_manager_resident",
            "instance_id": resident_instance,
        }
    context_attached = False
    recommendation = _recommendation_from_text(text, context, source_event_id=received.id)
    if recommendation:
        writer.emit(
            "run.manager.agent.recommendation",
            actor="feishu-run-manager-specialist",
            causation_id=received.id,
            correlation_id=received.correlation_id or chat_id,
            payload={
                "schema_version": "run-manager.agent-recommendation.v1",
                "source": "feishu-run-manager-specialist",
                "surface": "feishu",
                "chat_id": chat_id,
                "message_id": message_id,
                **recommendation,
            },
        )
        text = _message_with_context(text, context) if context else text
        context_attached = bool(context)
        payload = {**payload, "text": text}
        setattr(event, "payload", payload)
    if not recommendation and _is_explanation_request(original_text) and context:
        writer.emit(
            "run.manager.explanation.requested",
            actor=f"feishu:{user_id or 'unknown'}",
            causation_id=received.id,
            correlation_id=received.correlation_id or chat_id,
            payload={
                "schema_version": "run-manager.explanation-requested.v1",
                "chat_id": chat_id,
                "message_id": message_id,
                "decision_token": str(context.get("decision_token") or ""),
                "question": text[:500],
                "context": _compact_context(context),
            },
        )
        writer.emit(
            "run.manager.explanation.generated",
            actor="feishu-run-manager-agent",
            causation_id=received.id,
            correlation_id=received.correlation_id or chat_id,
            payload={
                "schema_version": "run-manager.explanation-generated.v1",
                "chat_id": chat_id,
                "message_id": message_id,
                "decision_token": str(context.get("decision_token") or ""),
                "summary": _explanation_summary(context),
            },
        )
        text = _message_with_context(text, context)
        context_attached = True
        payload = {**payload, "text": text}
        setattr(event, "payload", payload)
    if context and not context_attached:
        text = _message_with_context(text, context)
        payload = {**payload, "text": text}
        setattr(event, "payload", payload)
    from zf.integrations.feishu.agent_conversation import run_specialist_conversation

    route = getattr(event, "route", None)
    if route is None:
        from zf.integrations.feishu.routing import resolve_feishu_route

        route = resolve_feishu_route(
            config,
            str(getattr(event, "chat_id", "") or ""),
            bot_open_id=str(payload.get("bot_open_id") or ""),
            app_id=str(payload.get("app_id") or ""),
        )
    return run_specialist_conversation(
        state_dir=state,
        config=config,
        event=event,
        writer=writer,
        route=route,
        agent_kind="run_manager",
        default_member="run-manager",
        display_name="Run Manager Agent",
        source="feishu-run-manager-agent",
    )


def _resolve_followup_context(
    state_dir: Path,
    *,
    text: str,
    payload: dict[str, Any],
    chat_id: str,
) -> dict[str, Any]:
    from zf.integrations.feishu.run_manager_card import resolve_run_manager_card_context

    token = _decision_token_from_text(text)
    for key in ("message_id", "parent_message_id", "quote_message_id", "root_message_id"):
        context = resolve_run_manager_card_context(
            state_dir,
            decision_token=token,
            message_id=str(payload.get(key) or ""),
            chat_id=chat_id,
        )
        if context:
            return context
    if token:
        return resolve_run_manager_card_context(
            state_dir,
            decision_token=token,
            chat_id=chat_id,
        )
    return {}


def _decision_token_from_text(text: str) -> str:
    match = re.search(r"\b(?:hdec|human|source-repair|attn)[-:_][A-Za-z0-9_.:-]+\b", text)
    if not match:
        return ""
    return match.group(0).removeprefix("human:")


def _is_explanation_request(text: str) -> bool:
    lowered = text.lower()
    return any(word in lowered for word in ("解释", "为什么", "原因", "explain", "why", "what happened"))


def _is_resident_handoff_request(text: str) -> bool:
    lowered = text.lower()
    return (
        ("常驻" in text and ("监工" in text or "run manager" in lowered))
        or "转交常驻监工" in text
        or "打入tmux" in lowered
        or "send to resident" in lowered
    )


def _recommendation_from_text(
    text: str,
    context: dict[str, Any],
    *,
    source_event_id: str,
) -> dict[str, Any]:
    lowered = text.lower()
    compact = _compact_context(context)
    checkpoint = (
        compact.get("checkpoint_id")
        or compact.get("decision_token")
        or f"feishu-rm-{source_event_id[-8:] if source_event_id else 'request'}"
    )
    base = {
        "checkpoint_id": checkpoint,
        "fingerprint": f"feishu-run-manager:{checkpoint}",
        "failure_class": compact.get("failure_class") or "feishu_run_manager_request",
        "title": "Feishu Run Manager request",
        "summary": text[:1000],
        "source_event_ids": [source_event_id] if source_event_id else [],
        "decision_token": compact.get("decision_token", ""),
        "task_id": compact.get("task_id", ""),
    }
    if any(word in lowered for word in ("诊断", "排查", "为什么", "blocked", "stuck", "diagnose", "debug")):
        return {
            **base,
            "recommended_route": "autoresearch",
            "safe_resume_action": "diagnose_attention",
            "recommended_actions": ["inspect_referenced_run_manager_context"],
            "expected_output": ["run.manager.autoresearch.requested"],
        }
    if any(word in lowered for word in ("继续", "恢复", "批准继续", "resume", "continue", "approve")):
        return {
            **base,
            "recommended_route": "controlled_action",
            "controlled_action": "workflow-batch-resume",
            "safe_resume_action": compact.get("safe_resume_action") or "workflow_resume_apply",
            "policy_decision": {
                "decision": "needs_diagnosis",
                "reason": "Feishu free-text requested resume; Run Manager must diagnose before applying.",
            },
            "expected_output": ["run.manager.autoresearch.requested"],
        }
    if any(word in lowered for word in ("修复", "fix", "repair")):
        return {
            **base,
            "recommended_route": "repair",
            "safe_resume_action": "bounded_repair",
            "recommended_actions": ["create_bounded_repair_candidate"],
            "expected_output": ["run.manager.repair.accepted"],
        }
    return {}


def _resident_instance_id(config: object | None) -> str:
    resident = getattr(getattr(getattr(config, "runtime", None), "run_manager", None), "resident_agent", None)
    return str(getattr(resident, "instance_id", "") or "run-manager")


def _compact_context(context: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "decision_token",
        "source_event_id",
        "run_id",
        "task_id",
        "failure_class",
        "checkpoint_id",
        "fingerprint",
        "safe_resume_action",
        "reason",
    }
    return {key: str(value) for key, value in context.items() if key in allowed and value}


def _explanation_summary(context: dict[str, Any]) -> str:
    compact = _compact_context(context)
    parts = [
        f"failure={compact.get('failure_class') or '-'}",
        f"checkpoint={compact.get('checkpoint_id') or '-'}",
        f"safe_resume={compact.get('safe_resume_action') or '-'}",
    ]
    reason = compact.get("reason")
    if reason:
        parts.append(f"reason={reason[:240]}")
    return " | ".join(parts)


def _message_with_context(text: str, context: dict[str, Any]) -> str:
    compact = _compact_context(context)
    lines = [
        text,
        "",
        "Run Manager context:",
    ]
    for key in ("decision_token", "failure_class", "checkpoint_id", "safe_resume_action", "reason", "source_event_id"):
        value = compact.get(key)
        if value:
            lines.append(f"- {key}: {value}")
    lines.append("请基于以上上下文解释为什么需要这个 Run Manager 决策，以及推荐下一步。")
    return "\n".join(lines)


def _resident_handoff_message(text: str, context: dict[str, Any]) -> str:
    if not context:
        return text
    return _message_with_context(text, context)
