"""Headless action-proposal extraction — kanban agent LLM output -> proposal.

Moved out of ``zf/web/server.py`` (which imports fastapi at module top) so
non-web consumers — the Feishu-bound kanban agent conversation in
``zf/integrations/feishu/agent_conversation.py`` — extract proposals through
the exact same gates as the Web panel: a dedicated final JSON envelope,
agent-declared semantic intent bound to the source message, canonical action
names, the KANBAN_AGENT_ALLOWED_ACTIONS surface, contract shape normalization,
and the empty-contract guard.

Payload validation stays injectable: the Web server passes its full
``_validate_action_payload`` (config-aware, ~270 lines, not portable); other
callers pass a lighter validator or rely on the built-in title check.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from zf.core.security.redaction import redact_obj
from zf.runtime.kanban_proposals import proposal_payload_digest
from zf.web.operator_contract import KANBAN_AGENT_ALLOWED_ACTIONS, canonical_action
from zf.web.projections.common import normalize_proposed_task_contract


_SEMANTIC_INTENT_REQUIRED_ACTIONS = frozenset({
    "create-task",
    "idea-to-product",
})
_FINAL_ACTION_FENCE = re.compile(
    r"```(?:json)?[ \t]*\r?\n?((?:(?!```)[\s\S])*?)(?:```[ \t]*|$)$",
    re.IGNORECASE,
)


def is_dedicated_bare_plan(decoded: Any) -> bool:
    if not isinstance(decoded, dict):
        return False
    if any(
        key in decoded
        for key in (
            "action",
            "action_proposal",
            "input_request",
            "plan_request",
            "requested_action",
        )
    ):
        return False
    return bool(
        str(decoded.get("subject_type") or "").strip()
        and (
            isinstance(decoded.get("questions"), list)
            or str(decoded.get("question") or "").strip()
        )
    )


def label_is_recommended(label: str) -> bool:
    return "(Recommended)" in label or "(推荐)" in label


def default_validate_payload(action: str, payload: dict[str, Any]) -> str:
    """Minimal portable validation: mirrors the controlled-action hard gate."""
    if action == "channel-profile-register":
        from zf.runtime.channel_profile_registration import validate_registration_payload
        return validate_registration_payload(payload)
    if action == "create-task":
        if not str(payload.get("title") or "").strip():
            return "title is required"
        if "prepare_workflow" in payload and not isinstance(payload["prepare_workflow"], bool):
            return "prepare_workflow must be a boolean"
        if payload.get("prepare_workflow") and payload.get("execution_mode") != "workflow":
            return "prepare_workflow requires execution_mode workflow"
        execution_mode = str(payload.get("execution_mode") or "").strip()
        if execution_mode and execution_mode not in {"direct", "workflow"}:
            return "execution_mode must be direct or workflow"
        if execution_mode == "direct" and payload.get("workflow_plan") is not None:
            return "execution_mode direct cannot include workflow_plan"
        if str(payload.get("request_id") or "").strip():
            try:
                request_revision = int(payload.get("request_revision"))
            except (TypeError, ValueError):
                request_revision = 0
            if request_revision < 1:
                return "request_revision must be a positive integer"
            if execution_mode == "direct":
                return "workflow Request binding requires execution_mode workflow"
        contract = payload.get("contract")
        if execution_mode == "workflow" and isinstance(contract, dict):
            compilation_pending = _channel_prd_contract_compilation_pending(
                payload
            )
            if (
                payload.get("workflow_plan") is not None
                and not compilation_pending
            ):
                if not str(contract.get("behavior") or "").strip():
                    return (
                        "contract.behavior is required when workflow_plan "
                        "is attached"
                    )
                if not str(contract.get("verification") or "").strip():
                    return (
                        "contract.verification is required when workflow_plan "
                        "is attached"
                    )
            has_semantic_contract = bool(
                str(contract.get("behavior") or "").strip()
                or str(contract.get("verification") or "").strip()
            )
            if (
                has_semantic_contract
                and not compilation_pending
            ):
                tiers = contract.get("verification_tiers")
                if not isinstance(tiers, list) or not any(
                    str(item or "").strip() for item in tiers
                ):
                    return (
                        "contract.verification_tiers is required for "
                        "workflow execution"
                    )
            scoped_product_work = bool(
                contract.get("scope") or contract.get("affected_files")
            )
            source_fields = (
                "product_contract_ref",
                "spec_skip_reason",
                "spec_ref",
                "source_ref",
                "source_key",
                "source_task_id",
                "source_backlog_task_id",
            )
            if (
                payload.get("workflow_plan") is not None
                and scoped_product_work
                and not any(
                str(contract.get(key) or "").strip() for key in source_fields
                )
            ):
                return (
                    "scoped workflow Task requires a source contract ref or "
                    "contract.spec_skip_reason"
                )
        elif execution_mode == "workflow" and payload.get("workflow_plan") is not None:
            return "contract is required when workflow_plan is attached"
    if action == "idea-to-product" and not any(
        str(payload.get(key) or "").strip()
        for key in ("objective", "message", "title")
    ):
        return "objective or message is required"
    if action == "channel-create-from-template" and not str(
        payload.get("template_id") or ""
    ).strip():
        return "template_id is required"
    if action == "channel-create-and-start":
        if not str(payload.get("template_id") or "").strip():
            return "template_id is required"
        if not any(
            str(payload.get(key) or "").strip()
            for key in ("objective", "message", "text")
        ):
            return "objective, message, or text is required"
    if action in {"workflow-start", "task-workflow-start"}:
        return "workflow-start must originate from a task_workflow Plan"
    if action == "stage-replan-new-generation":
        if not str(payload.get("escalation_event_id") or "").strip():
            return "escalation_event_id is required"
        if not str(payload.get("approval_ref") or "").strip():
            return "approval_ref is required"
    if action == "channel-discussion-start":
        if not str(payload.get("channel_id") or "").strip():
            return "channel_id is required"
        if not any(
            str(payload.get(key) or "").strip()
            for key in ("objective", "message", "text")
        ):
            return "objective, message, or text is required"
    if action == "channel-invite-member":
        if not str(payload.get("channel_id") or "").strip():
            return "channel_id is required"
        if not str(payload.get("member_id") or "").strip():
            return "member_id is required"
        if not str(payload.get("profile_id") or "").strip():
            return "profile_id is required for roster proposals"
    if action == "channel-remove-member":
        if not str(payload.get("channel_id") or "").strip():
            return "channel_id is required"
        if not str(payload.get("member_id") or "").strip():
            return "member_id is required"
    if action in {
        "channel-delete",
        "channel-clear-history",
        "channel-mark-read",
        "channel-pin-message",
    } and not str(payload.get("channel_id") or "").strip():
        return "channel_id is required"
    if action == "channel-pin-message" and not str(
        payload.get("message_id") or ""
    ).strip():
        return "message_id is required"
    if action == "channel-set-leader":
        if not str(payload.get("channel_id") or "").strip():
            return "channel_id is required"
        if not str(payload.get("leader_member_id") or "").strip():
            return "leader_member_id is required"
        try:
            expected_revision = int(payload.get("expected_revision"))
        except (TypeError, ValueError):
            expected_revision = -1
        if expected_revision < 0:
            return "expected_revision must be a non-negative integer"
    if action == "workflow-invoke":
        if not str(payload.get("task_id") or "").strip():
            return "task_id is required"
        if not str(payload.get("pattern_id") or "").strip():
            return "pattern_id is required"
    if action == "research-start":
        if not str(payload.get("task_id") or "").strip():
            return "task_id is required"
        if not any(
            str(payload.get(key) or "").strip()
            for key in ("topic", "objective", "message")
        ):
            return "topic, objective, or message is required"
        if str(payload.get("channel_id") or "").strip():
            if not str(payload.get("request_id") or "").strip():
                return "channel-bound research requires request_id"
            try:
                request_revision = int(payload.get("request_revision"))
            except (TypeError, ValueError):
                request_revision = 0
            if request_revision < 1:
                return "channel-bound research requires request_revision"
    if action == "research-adopt":
        for key in (
            "result_event_id",
            "request_id",
            "artifact_ref",
            "artifact_digest",
            "summary",
        ):
            if not str(payload.get(key) or "").strip():
                return f"{key} is required"
        try:
            request_revision = int(payload.get("request_revision"))
        except (TypeError, ValueError):
            request_revision = 0
        if request_revision < 1:
            return "request_revision must be a positive integer"
    return ""


def _channel_prd_contract_compilation_pending(payload: dict[str, Any]) -> bool:
    """Recognize the exact envelope compiled by Channel PRD Task admission.

    The Web gate sees the Plan-bound seed contract before the controlled action
    hydrates the immutable PRD/readiness/spec artifacts. Completeness remains
    fail-closed in ``compile_channel_prd_task_payload``; this predicate only
    prevents the earlier generic Task validator from rejecting that two-phase
    contract before the compiler can run.
    """

    if str(payload.get("execution_mode") or "") != "workflow":
        return False
    authority = payload.get("channel_authority")
    contract = payload.get("contract")
    artifact = payload.get("source_artifact")
    if not all(isinstance(value, dict) for value in (authority, contract, artifact)):
        return False
    required_authority = (
        "channel_id",
        "thread_id",
        "channel_member_id",
        "leader_revision",
        "prd_revision",
        "source_ref",
        "source_digest",
    )
    if any(authority.get(key) in (None, "") for key in required_authority):
        return False
    source_ref = str(authority.get("source_ref") or "")
    source_digest = str(authority.get("source_digest") or "").removeprefix(
        "sha256:"
    )
    return bool(
        str(contract.get("source_mode") or "") == "channel_prd"
        and str(contract.get("source_ref") or "") == source_ref
        and str(artifact.get("kind") or "") == "channel_prd"
        and str(artifact.get("ref") or "") == source_ref
        and str(artifact.get("digest") or "").removeprefix("sha256:")
        == source_digest
        and str(artifact.get("revision") or "")
        == str(authority.get("prd_revision") or "")
    )


def json_candidates(text: str) -> list[str]:
    """Broad JSON candidates used by the resumable Plan parser.

    Action proposals use the narrower ``action_json_candidates`` contract
    below. Plan extraction keeps its compatibility surface independently.
    """
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


def action_json_candidates(text: str) -> list[str]:
    """Accept a final envelope, including a complete JSON body at an open EOF fence.

    JSON decoding still rejects truncated objects and trailing prose; this does
    not scan arbitrary prose for executable examples.
    """
    stripped = str(text or "").strip()
    candidates: list[str] = []
    if stripped.startswith("{"):
        candidates.append(stripped)
    fenced = _FINAL_ACTION_FENCE.search(stripped)
    if fenced:
        body = fenced.group(1).strip()
        if body and body not in candidates:
            candidates.append(body)
    return candidates


def action_proposal_format_error(answer: str) -> str:
    """Expose malformed dedicated control output instead of completing silently."""
    for candidate in action_json_candidates(answer):
        if re.match(r'\s*\{\s*"action_proposal"\s*:', candidate):
            if extract_action_proposal(answer) is None:
                return "Invalid action proposal output. Retry the reply to regenerate the confirmation."
    return ""


def _decode_action_candidate(candidate: str) -> Any | None:
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        # Preserve the real-provider recovery for a complete object followed by
        # one or more stray closing braces. Other trailing text is not a
        # dedicated final envelope and must not be interpreted as an action.
        try:
            decoded, end = json.JSONDecoder().raw_decode(candidate)
        except json.JSONDecodeError:
            return None
        trailing = candidate[end:].strip()
        if trailing and set(trailing) != {"}"}:
            return None
        return decoded


def extract_action_proposal(
    answer: str,
    *,
    user_message: str = "",
    proposal_context: dict[str, Any] | None = None,
    validate_payload: Callable[[str, dict[str, Any]], str] | None = None,
) -> dict[str, Any] | None:
    for candidate in action_json_candidates(answer):
        decoded = _decode_action_candidate(candidate)
        if decoded is None:
            continue
        proposal = normalize_action_proposal(
            decoded,
            user_message=user_message,
            proposal_context=proposal_context or {},
            validate_payload=validate_payload,
        )
        if proposal is not None:
            return proposal
    fenced = _FINAL_ACTION_FENCE.search(str(answer or "").strip())
    if fenced:
        decoded = _decode_action_candidate(fenced.group(1).strip())
        if _is_dedicated_bare_action(decoded):
            return normalize_action_proposal(
                {"action_proposal": decoded},
                user_message=user_message,
                proposal_context=proposal_context or {},
                validate_payload=validate_payload,
            )
    return None


def _is_dedicated_bare_action(decoded: Any) -> bool:
    if not isinstance(decoded, dict):
        return False
    allowed = {
        "action",
        "confidence",
        "expires_at",
        "intent",
        "payload",
        "reason",
        "requested_action",
        "revision",
        "summary",
        "supersedes",
    }
    if set(decoded) - allowed:
        return False
    return bool(
        str(decoded.get("action") or decoded.get("requested_action") or "").strip()
        and isinstance(decoded.get("intent"), dict)
        and isinstance(decoded.get("payload"), dict)
    )


def normalize_action_proposal(
    decoded: Any,
    *,
    user_message: str = "",
    proposal_context: dict[str, Any] | None = None,
    validate_payload: Callable[[str, dict[str, Any]], str] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(decoded, dict):
        return None
    proposal = decoded.get("action_proposal")
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
    intent, intent_error = _normalize_agent_intent(
        proposal,
        user_message=user_message,
        proposal_context=proposal_context or {},
        required=action in _SEMANTIC_INTENT_REQUIRED_ACTIONS,
    )
    payload = proposal.get("payload") or proposal.get("params") or {}
    if not isinstance(payload, dict):
        return None
    payload = dict(payload)
    if proposal_context is not None and "plan_continuation" in proposal_context:
        # A provider cannot redirect the post-approval continuation to another Plan.
        payload.pop("plan_continuation", None)
    if action == "create-task" and not str(
        payload.get("execution_mode") or ""
    ).strip():
        # Agent-created Tasks are inert until an approved Workflow starts.
        # Legacy direct dispatch remains available only as an explicit choice.
        payload["execution_mode"] = "workflow"
    for key, value in (proposal_context or {}).items():
        if value and not payload.get(key):
            payload[key] = value
    if action in {"create-task", "update-task", "idea-to-product"}:
        payload = normalize_proposed_task_contract(payload)
    validator = validate_payload or default_validate_payload
    validation_errors = [
        error
        for error in (
            intent_error,
            validator(action, payload),
        )
        if error
    ]
    if not validation_errors and action in {"create-task", "idea-to-product"}:
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
            validation_errors.append(
                "contract has no behavior/verification after normalization"
            )
    validation_error = "; ".join(validation_errors)
    proposal_digest = proposal_payload_digest(action, payload)
    try:
        revision = max(1, int(proposal.get("revision") or 1))
    except (TypeError, ValueError):
        revision = 1
    normalized = {
        "proposal_id": f"proposal-{proposal_digest[:24]}",
        "proposal_digest": proposal_digest,
        "revision": revision,
        "expires_at": str(proposal.get("expires_at") or ""),
        "supersedes": str(proposal.get("supersedes") or ""),
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
    if intent:
        normalized["intent"] = redact_obj(intent)
    return normalized


def _normalize_agent_intent(
    proposal: dict[str, Any],
    *,
    user_message: str,
    proposal_context: dict[str, Any],
    required: bool,
) -> tuple[dict[str, str], str]:
    raw = proposal.get("intent")
    if raw is None and not required:
        return {}, ""
    if not isinstance(raw, dict):
        return {}, "intent is required and must be a mapping"

    errors: list[str] = []
    unknown = sorted(set(raw) - {"decision", "source_quote"})
    if unknown:
        errors.append("unsupported intent field(s): " + ", ".join(unknown))
    decision = str(raw.get("decision") or "").strip()
    source_quote = str(raw.get("source_quote") or "").strip()
    if decision != "propose_action":
        errors.append("intent.decision must be propose_action")
    if not source_quote:
        errors.append("intent.source_quote is required")
    elif not user_message or source_quote not in user_message:
        errors.append(
            "intent.source_quote must occur verbatim in the user semantic context"
        )

    intent = {
        "decision": decision,
        "source_quote": source_quote,
    }
    source_message_event_id = str(
        proposal_context.get("causation_id")
        or proposal_context.get("source_message_event_id")
        or ""
    ).strip()
    if source_message_event_id:
        intent["source_message_event_id"] = source_message_event_id
    return intent, "; ".join(errors)
