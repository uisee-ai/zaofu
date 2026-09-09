"""Prompt assembly for Channel provider replies."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from zf.core.security.redaction import redact_obj
from zf.runtime.channel_contracts import (
    active_channel_skill_refs,
    normalize_permission_profile,
    permission_profile_write_policy,
)
from zf.runtime.channel_reply_contract import channel_reply_response_contract
from zf.runtime.channel_sidecar import hydrate_channel_context_pack_payload


def build_channel_provider_prompt(
    *,
    channel: dict[str, Any],
    member: dict[str, Any],
    message: dict[str, Any],
    request: dict[str, Any],
    state_dir: Path | None = None,
) -> str:
    context_pack = channel_context_pack_by_id(
        channel,
        str(request.get("context_pack_id") or ""),
        state_dir=state_dir,
    )
    channel_id = str(channel.get("channel_id") or request.get("channel_id") or "")
    discussion = (
        channel.get("discussion")
        if isinstance(channel.get("discussion"), dict)
        else {}
    )
    mode = discussion.get("mode") or discussion.get("product_mode") or "conversation"
    skill_refs = active_channel_skill_refs(
        member.get("skill_refs") or [],
        discussion_mode=mode,
    )
    agent_context = (
        context_pack.get("agent_context")
        if isinstance(context_pack.get("agent_context"), dict)
        else {}
    )
    repair_context = ""
    if str(request.get("routing_reason") or "") == "remediation_redispatch":
        repair_context = str(
            redact_obj(
                request.get("reason")
                or "retry the rejected reply contract"
            )
        )
    semantic_source_instruction = ""
    if context_pack.get("semantic_source_required") is True:
        semantic_source_instruction = (
            "Completion-critical source contract: read every full item in "
            "context_pack.semantic_source_documents. The manifest binds each "
            "member, round, message_id, body ref, and digest. Return exactly all "
            "semantic_source_required_digests in consumed_message_digests; "
            "missing or invented digests fail the turn."
        )
    write_policy = (
        member.get("write_policy")
        if isinstance(member.get("write_policy"), dict)
        else permission_profile_write_policy(member.get("permission_profile"))
    )
    response_contract = channel_reply_response_contract(
        channel,
        request,
        message,
    )
    refs = message.get("refs") if isinstance(message.get("refs"), dict) else {}
    typed_phase_ref = next(
        (
            key
            for key in (
                "question_dedup_request_id",
                "synthesis_request_id",
                "cross_review_request_id",
                "consensus_review_id",
            )
            if str(refs.get(key) or "").strip()
        ),
        "",
    )
    lines = [
        "ZaoFu Agent Channel reply request",
        f"channel_id: {channel_id}",
        f"thread_id: {request.get('thread_id') or 'main'}",
        f"target_member_id: {request.get('target_member_id') or member.get('member_id') or ''}",
        f"channel_role: {member.get('channel_role') or member.get('role') or ''}",
        f"visibility_profile: {member.get('visibility_profile') or ''}",
        f"permission_profile: {normalize_permission_profile(member.get('permission_profile'))}",
        f"write_policy: {redact_obj(write_policy)}",
        f"skill_refs: {redact_obj(skill_refs)}",
        f"context_pack: {redact_obj(context_pack)}",
        f"agent_context: {redact_obj(agent_context)}",
    ]
    if semantic_source_instruction:
        lines.append(f"semantic_source_instruction: {semantic_source_instruction}")
    if typed_phase_ref:
        lines.append(
            "typed_phase_constraint: This is a self-contained, bounded "
            f"{typed_phase_ref} operation. The supplied context_pack is the "
            "complete authoritative evidence set. Do not inspect the project "
            "filesystem, prior Channel state, event logs, skills, or external "
            "sources. Do not call tools. Derive the required response solely "
            "from the supplied context_pack and trigger message."
        )
    if repair_context:
        lines.append(f"repair_context: {repair_context}")
    return "\n".join([
        *lines,
        "",
        "Trigger message:",
        str(message.get("text") or message.get("message") or ""),
        "",
        "Response contract (authoritative for this reply):",
        response_contract,
    ])


def channel_context_pack_by_id(
    channel: dict[str, Any],
    context_pack_id: str,
    *,
    state_dir: Path | None = None,
) -> dict[str, Any]:
    if not context_pack_id:
        return {}
    raw = channel.get("context_packs")
    if isinstance(raw, dict):
        item = raw.get(context_pack_id)
        found = item if isinstance(item, dict) else {}
    else:
        found = {}
    for item in [] if isinstance(raw, dict) else raw or []:
        if (
            isinstance(item, dict)
            and str(item.get("context_pack_id") or "") == context_pack_id
        ):
            found = item
            break
    if not found or state_dir is None:
        return found
    return hydrate_channel_context_pack_payload(
        state_dir,
        found,
        strict=True,
    )


__all__ = [
    "build_channel_provider_prompt",
    "channel_context_pack_by_id",
]
