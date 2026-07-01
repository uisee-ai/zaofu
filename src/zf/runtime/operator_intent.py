"""Deterministic Kanban Agent operator intent contract.

This module is intentionally small and side-effect free. It gives Web,
bridges, and tests one conservative classifier for turning an operator chat
message into an auditable intent proposal. Runtime truth is still changed only
through controlled actions and kernel projections.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from zf.core.security.redaction import redact_obj


OPERATOR_INTENT_SCHEMA_VERSION = "operator.intent.v0"
OPERATOR_INTENT_TYPES = {
    "project_status_query",
    "idea_to_product",
    "runtime_diagnose",
    "runtime_restart",
    "workflow_config_change",
    "provider_dev_chat",
    "general_operator_request",
}
HIGH_RISK_INTENTS = {
    "runtime_restart",
    "workflow_config_change",
    "provider_dev_chat",
}
AGENT_FORBIDDEN_PROPOSED_ACTIONS = {
    "plan-approve",
    "plan.approve",
}


def infer_operator_intent(
    message: str = "",
    *,
    payload: dict[str, Any] | None = None,
    project_id: str = "",
    source: str = "kanban-agent",
) -> dict[str, Any]:
    """Infer an operator intent from text without mutating runtime state."""

    request = payload if isinstance(payload, dict) else {}
    text = _compact_text(
        message
        or str(request.get("message") or request.get("objective") or request.get("text") or "")
    )
    lowered = text.lower()
    intent_type = "general_operator_request"
    proposed_actions: list[str] = []
    risk = "low"
    requires_confirmation = False

    if _has_any(lowered, (
        "status",
        "progress",
        "blocked",
        "current",
        "summary",
        "状态",
        "进展",
        "卡住",
        "当前",
        "汇总",
    )):
        intent_type = "project_status_query"
    if _has_any(lowered, (
        "idea",
        "product",
        "build",
        "ship",
        "delivery",
        "从0",
        "从 0",
        "产品",
        "交付",
        "实现",
        "需求",
        "生成计划",
        "跑成产品",
    )):
        intent_type = "idea_to_product"
        proposed_actions = ["create-task", "workflow-invoke"]
        risk = "medium"
        requires_confirmation = True
    if _has_any(lowered, (
        "diagnose",
        "diagnosis",
        "autoresearch",
        "supervisor",
        "stuck",
        "rework",
        "诊断",
        "自修复",
        "告警",
        "卡住",
    )):
        intent_type = "runtime_diagnose"
        proposed_actions = ["maintenance-prepare"]
        risk = "medium"
    if _has_any(lowered, (
        "restart",
        "resume runtime",
        "stop runtime",
        "重启",
        "恢复 runtime",
        "停止 runtime",
        "重启工作流",
        "恢复工作流",
    )):
        intent_type = "runtime_restart"
        proposed_actions = ["runtime-restart"]
        risk = "high"
        requires_confirmation = True
    if _has_any(lowered, (
        "zf.yaml",
        "workflow yaml",
        "workflow config",
        "修改 yaml",
        "修改工作流",
        "工作流配置",
    )):
        intent_type = "workflow_config_change"
        proposed_actions = ["workflow-config-propose", "workflow-config-validate"]
        risk = "high"
        requires_confirmation = True
    if _has_any(lowered, (
        "dev chat",
        "coding chat",
        "codex",
        "claude",
        "openclaw",
        "hermes",
        "开发对话",
        "代码对话",
    )):
        intent_type = "provider_dev_chat"
        proposed_actions = ["provider-dev-chat-start"]
        risk = "high"
        requires_confirmation = True

    explicit_action = str(request.get("action") or request.get("requested_action") or "").strip()
    if (
        explicit_action
        and explicit_action not in AGENT_FORBIDDEN_PROPOSED_ACTIONS
        and explicit_action not in proposed_actions
    ):
        proposed_actions.append(explicit_action)

    seed = {
        "project_id": project_id or str(request.get("project_id") or ""),
        "source": source,
        "intent_type": intent_type,
        "text": text,
        "actions": proposed_actions,
    }
    intent_id = str(request.get("intent_id") or "") or intent_id_for(seed)
    return redact_obj({
        "schema_version": OPERATOR_INTENT_SCHEMA_VERSION,
        "intent_id": intent_id,
        "project_id": seed["project_id"],
        "source": source,
        "intent_type": intent_type,
        "objective": text,
        "risk": risk,
        "requires_confirmation": requires_confirmation,
        "requires_owner_approval": intent_type in HIGH_RISK_INTENTS,
        "proposed_actions": proposed_actions,
        "blocked_actions": (
            [explicit_action] if explicit_action in AGENT_FORBIDDEN_PROPOSED_ACTIONS else []
        ),
        "confidence": "heuristic",
        "mutates_truth_directly": False,
    })


def validate_operator_intent_payload(payload: dict[str, Any]) -> str:
    objective = str(
        payload.get("objective")
        or payload.get("message")
        or payload.get("text")
        or ""
    ).strip()
    if not objective:
        return "objective or message is required"
    intent_type = str(payload.get("intent_type") or "").strip()
    if intent_type and intent_type not in OPERATOR_INTENT_TYPES:
        return "intent_type must be one of " + ", ".join(sorted(OPERATOR_INTENT_TYPES))
    return ""


def intent_id_for(seed: dict[str, Any]) -> str:
    encoded = json.dumps(
        seed,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "opint-" + hashlib.sha1(encoded).hexdigest()[:12]


def _compact_text(value: str) -> str:
    return " ".join(str(value or "").strip().split())


def _has_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)
