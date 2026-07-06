"""Headless action-proposal extraction — kanban agent LLM output → proposal.

Moved out of ``zf/web/server.py`` (which imports fastapi at module top) so
non-web consumers — the Feishu-bound kanban agent conversation in
``zf/integrations/feishu/agent_conversation.py`` — extract proposals through
the exact same gates as the Web panel: canonical action names, the
KANBAN_AGENT_ALLOWED_ACTIONS surface, the explicit create-task/idea-to-product
message phrases, contract shape normalization, and the empty-contract guard.

Payload validation stays injectable: the Web server passes its full
``_validate_action_payload`` (config-aware, ~270 lines, not portable); other
callers pass a lighter validator or rely on the built-in title check.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from zf.core.security.redaction import redact_obj
from zf.web.operator_contract import KANBAN_AGENT_ALLOWED_ACTIONS, canonical_action
from zf.web.projections.common import (
    _message_allows_create_task_proposal,
    _message_allows_idea_to_product_proposal,
    normalize_proposed_task_contract,
)


def default_validate_payload(action: str, payload: dict[str, Any]) -> str:
    """Minimal portable validation: mirrors the controlled-action hard gate."""
    if action in {"create-task", "idea-to-product"} and not str(
        payload.get("title") or ""
    ).strip():
        return "title is required"
    return ""


def json_candidates(text: str) -> list[str]:
    stripped = str(text or "").strip()
    candidates: list[str] = []
    if stripped:
        candidates.append(stripped)
    for marker in ("```json", "```JSON", "```"):
        start = stripped.find(marker)
        while start >= 0:
            body_start = start + len(marker)
            end = stripped.find("```", body_start)
            if end < 0:
                break
            body = stripped[body_start:end].strip()
            if body:
                candidates.append(body)
            start = stripped.find(marker, end + 3)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if 0 <= start < end:
        candidates.append(stripped[start:end + 1])
    return candidates


def extract_action_proposal(
    answer: str,
    *,
    user_message: str = "",
    proposal_context: dict[str, Any] | None = None,
    validate_payload: Callable[[str, dict[str, Any]], str] | None = None,
) -> dict[str, Any] | None:
    for candidate in json_candidates(answer):
        try:
            decoded = json.loads(candidate)
        except json.JSONDecodeError:
            # LLMs routinely append a stray brace or prose after an otherwise
            # valid object (combined-e2e: codex emitted 1018 valid chars + one
            # extra '}'); recover the leading object instead of dropping the
            # proposal.
            try:
                decoded, _ = json.JSONDecoder().raw_decode(candidate)
            except json.JSONDecodeError:
                continue
        proposal = normalize_action_proposal(
            decoded,
            user_message=user_message,
            proposal_context=proposal_context or {},
            validate_payload=validate_payload,
        )
        if proposal is not None:
            return proposal
    return None


def normalize_action_proposal(
    decoded: Any,
    *,
    user_message: str = "",
    proposal_context: dict[str, Any] | None = None,
    validate_payload: Callable[[str, dict[str, Any]], str] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(decoded, dict):
        return None
    proposal = decoded.get("action_proposal") or decoded.get("proposal") or decoded
    if not isinstance(proposal, dict):
        return None
    requested_action = str(
        proposal.get("action")
        or proposal.get("requested_action")
        or proposal.get("name")
        or ""
    ).strip()
    if not requested_action:
        return None
    action = canonical_action(requested_action)
    if action not in KANBAN_AGENT_ALLOWED_ACTIONS:
        return None
    if action in {"chat-orchestrator", "start-operator-session"}:
        return None
    if action == "create-task" and not _message_allows_create_task_proposal(user_message):
        return None
    if action == "idea-to-product" and not _message_allows_idea_to_product_proposal(user_message):
        return None
    payload = proposal.get("payload") or proposal.get("params") or {}
    if not isinstance(payload, dict):
        return None
    payload = dict(payload)
    for key, value in (proposal_context or {}).items():
        if value and not payload.get(key):
            payload[key] = value
    if action in {"create-task", "update-task", "idea-to-product"}:
        payload = normalize_proposed_task_contract(payload)
    validator = validate_payload or default_validate_payload
    validation_error = validator(action, payload)
    if not validation_error and action in {"create-task", "idea-to-product"}:
        # chat-e2e F3: a contract whose semantic fields all normalized away
        # (e.g. every criterion sat in an unknown key) must not sail through
        # as valid — the task would land with no behavior/verification.
        contract = payload.get("contract")
        if (
            isinstance(contract, dict)
            and contract
            and not str(contract.get("behavior") or "").strip()
            and not str(contract.get("verification") or "").strip()
        ):
            validation_error = "contract has no behavior/verification after normalization"
    return {
        "action": action,
        "requested_action": requested_action,
        "payload": redact_obj(payload),
        "reason": str(proposal.get("reason") or proposal.get("summary") or ""),
        "confidence": str(proposal.get("confidence") or ""),
        "valid": not validation_error,
        "validation_error": validation_error,
        "mutates_task_state": action in {
            "create-task",
            "update-task",
            "archive-task",
            "link-evidence",
        },
    }
