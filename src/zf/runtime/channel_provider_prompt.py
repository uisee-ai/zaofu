"""Prompt assembly for Channel provider replies."""

from __future__ import annotations

import hashlib
import json
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
            "member, round, message_id, body ref, and digest. Return the source-consumption "
            "evidence required by the response contract: synthesis may explicitly attest the "
            "entire immutable pack; otherwise return exactly all semantic_source_required_digests "
            "in consumed_message_digests. Missing or invented bindings fail the turn."
        )
        if context_pack.get("semantic_source_stage") == "consensus_review":
            semantic_source_instruction = (
                "Read the complete PRD and readiness in context_pack.semantic_source_documents. "
                "Their artifact_ref/artifact_digest bind the verified canonical bodies. "
                "Review these supplied bodies, not a message excerpt; return the exact PRD "
                "artifact_digest in channel_consensus_review."
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
        "Attachment contract: native image inputs are supplied only for verified stored images in context_pack.artifact_refs. "
        "Their order matches those image refs. If an attachment has content_unavailable status or no stored URI, "
        "you only have metadata: ask the user to upload it again and never claim to have read it.",
        f"channel_id: {channel_id}",
        f"thread_id: {request.get('thread_id') or 'main'}",
        f"target_member_id: {request.get('target_member_id') or member.get('member_id') or ''}",
        f"channel_role: {member.get('channel_role') or member.get('role') or ''}",
        f"visibility_profile: {member.get('visibility_profile') or ''}",
        f"permission_profile: {normalize_permission_profile(member.get('permission_profile'))}",
        f"write_policy: {redact_obj(write_policy)}",
        f"skill_refs: {redact_obj(skill_refs)}",
        f"context_pack: {redact_obj(_intern_cross_review_coverage(context_pack))}",
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
            "sources. Do not call tools. Apply the supplied context_pack.skill_guidance "
            "as methods within the response contract and source requirements; "
            "method guidance does not add product scope or grant actions. Derive the required response solely "
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


def _intern_cross_review_coverage(context_pack: dict[str, Any]) -> dict[str, Any]:
    """Losslessly intern repeated mechanical manifests in the prompt view only."""
    reviews = context_pack.get("cross_review_index")
    if not isinstance(reviews, list):
        return context_pack
    encoded: dict[str, list[Any]] = {}
    counts: dict[str, int] = {}
    for row in reviews:
        coverage = row.get("source_coverage") if isinstance(row, dict) else None
        if isinstance(coverage, list) and coverage:
            key = json.dumps(coverage, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            encoded[key] = coverage
            counts[key] = counts.get(key, 0) + 1
    repeated = {key: "sha256:" + hashlib.sha256(key.encode()).hexdigest()
                for key, count in counts.items() if count > 1}
    if not repeated:
        return context_pack
    projected = []
    for row in reviews:
        coverage = row.get("source_coverage") if isinstance(row, dict) else None
        key = json.dumps(coverage, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if key in repeated:
            row = {name: value for name, value in row.items() if name != "source_coverage"}
            row["source_coverage_manifest_ref"] = repeated[key]
        projected.append(row)
    return {**context_pack, "cross_review_index": projected,
            "cross_review_source_coverage_manifests": {ref: encoded[key] for key, ref in repeated.items()},
            "cross_review_index_encoding": "source_coverage_manifest_ref resolves in cross_review_source_coverage_manifests; all other fields and full semantic source documents are unchanged. cross_review_index_digest binds the expanded original index."}
