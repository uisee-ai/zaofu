"""Bounded context packs for Agent Channel replies."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from zf.core.security.redaction import redact_obj
from zf.runtime.channel_contracts import normalize_channel_skill_refs, normalize_permission_profile
from zf.runtime.channel_roles import (
    load_role_definition_excerpt,
    normalize_role_context_ref,
)
from zf.runtime.channel_profiles import load_channel_profile_role_definition, public_channel_profile_catalog
from zf.runtime.channel_question_dedup import (
    question_ledger as canonical_question_ledger,
    question_ledger_digest as canonical_question_ledger_digest,
)
from zf.runtime.channel_question_graph import (
    owner_questionnaire,
    question_frontier,
)
from zf.runtime.channel_semantic_sources import DEFAULT_MAX_SEMANTIC_SOURCE_CHARS, build_semantic_source_bundle
from zf.runtime.channel_templates import CHANNEL_TEMPLATES
from zf.runtime.channel_skill_context import load_channel_skill_guidance


def build_channel_context_pack(
    channel: dict[str, Any] | None,
    *,
    channel_id: str,
    thread_id: str,
    target_member_id: str,
    trigger_message_id: str,
    visibility_profile: str = "",
    channel_role: str = "",
    role_context_ref: str = "",
    skill_refs: object = None,
    resolved_skill_refs: object = None,
    permission_profile: str = "",
    profile_binding: object = None,
    agent_context: object = None,
    state_dir: Path | None = None,
    project_root: Path | None = None,
    config: Any = None,
    max_messages: int = 8,
    max_text_chars: int = 4000,
) -> dict[str, Any]:
    """Build a compact, reference-oriented context packet for one reply.

    Navigation excerpts stay bounded. The semantic source bundle separately
    carries complete, verified bodies up to the trigger, subject to phase
    visibility and a fail-closed source budget.
    """
    profile = visibility_profile or "minimal"
    normalized_permission_profile = normalize_permission_profile(permission_profile)
    safe_role_context_ref = normalize_role_context_ref(role_context_ref)
    safe_skill_refs = normalize_channel_skill_refs(skill_refs)
    safe_resolved_skill_refs = (
        [
            redact_obj(item)
            for item in resolved_skill_refs
            if isinstance(item, dict)
        ][:8]
        if isinstance(resolved_skill_refs, list)
        else []
    )
    member_profile = (
        profile_binding if isinstance(profile_binding, dict) else {}
    )
    if state_dir is not None and str(
        member_profile.get("profile_snapshot_ref") or ""
    ):
        role_definition = load_channel_profile_role_definition(
            Path(state_dir),
            member_profile,
        )
    else:
        role_definition = load_role_definition_excerpt(
            safe_role_context_ref,
            repo_root=project_root,
            max_chars=_profile_limits(
                profile,
                max_messages=max_messages,
                max_text_chars=max_text_chars,
            )["role_definition_chars"],
            fallback_to_builtin=(
                str(member_profile.get("profile_provenance") or "")
                != "project_catalog"
            ),
        )
    profile_limits = _profile_limits(profile, max_messages=max_messages, max_text_chars=max_text_chars)
    trigger_refs = _trigger_message_refs(
        channel,
        trigger_message_id=trigger_message_id,
    )
    session = ((channel or {}).get("discussions") or {}).get(thread_id) or {}
    blind = bool(trigger_message_id and trigger_message_id == session.get("requirement_message_id")
                 or trigger_refs.get("adaptive_next_round_id"))
    requires_complete_ledger = not blind and bool(
        trigger_refs.get("question_dedup_request_id")
        or trigger_refs.get("synthesis_request_id")
        or trigger_refs.get("cross_review_request_id")
        or trigger_refs.get("consensus_review_id")
    )
    skill_guidance = (
        load_channel_skill_guidance(
            safe_resolved_skill_refs, state_dir=Path(state_dir),
            project_root=project_root,
        )
        if requires_complete_ledger and state_dir is not None
        else []
    )
    visible_messages = list((channel or {}).get("messages") or (channel or {}).get("recent_messages") or [])
    if blind:
        visible_messages = [item for item in visible_messages if item.get("role") == "user"]
    selected = _select_messages(
        visible_messages,
        thread_id=thread_id,
        trigger_message_id=trigger_message_id,
        max_messages=profile_limits["max_messages"],
        max_text_chars=profile_limits["max_text_chars"],
    )
    question_ledger = (
        canonical_question_ledger(channel, thread_id=thread_id)
        if requires_complete_ledger
        else _select_question_ledger(
            channel,
            thread_id=thread_id,
            max_entries=profile_limits["question_entries"],
        )
    )
    ledger_digest = (
        canonical_question_ledger_digest(channel, thread_id=thread_id)
        if requires_complete_ledger
        else _stable_digest(question_ledger)
    )
    contribution_index = channel_contribution_index(
        channel,
        thread_id=thread_id,
    )
    frontier = question_frontier(channel, thread_id=thread_id)
    questionnaire = owner_questionnaire(channel, thread_id=thread_id)
    raw_cross_reviews = (channel or {}).get("cross_reviews") or []
    cross_review_candidates = (
        list(raw_cross_reviews.values())
        if isinstance(raw_cross_reviews, dict)
        else list(raw_cross_reviews)
    )
    cross_review_index = [
        dict(item)
        for item in cross_review_candidates
        if isinstance(item, dict)
        and str(item.get("thread_id") or "main") == thread_id
    ]
    collaboration_contract = _collaboration_contract(
        channel,
        thread_id=thread_id,
    )
    if blind:
        question_ledger = []
        ledger_digest = _stable_digest([])
        contribution_index = []
        cross_review_index = []
        frontier = {}
        questionnaire = {}
    semantic_sources = build_semantic_source_bundle(
        channel,
        thread_id=thread_id,
        trigger_message_id=trigger_message_id,
        state_dir=state_dir,
        max_source_chars=getattr(getattr(config, "channel", None), "semantic_source_max_chars", DEFAULT_MAX_SEMANTIC_SOURCE_CHARS),
    )
    context_pack_id = _stable_context_pack_id(
        channel_id,
        thread_id,
        trigger_message_id,
        target_member_id,
    )
    _, source_limits = context_pack_rejection_reason(channel)
    payload = {
        "context_pack_id": context_pack_id,
        "channel_id": channel_id,
        "thread_id": thread_id,
        "target_member_id": target_member_id,
        "trigger_message_id": trigger_message_id,
        "visibility_profile": profile,
        "channel_role": channel_role,
        "permission_profile": normalized_permission_profile,
        "profile_id": str(member_profile.get("profile_id") or ""),
        "profile_revision": int(
            member_profile.get("profile_revision") or 1
        ),
        "profile_provenance": str(
            member_profile.get("profile_provenance") or "legacy_inline"
        ),
        "profile_digest": str(member_profile.get("profile_digest") or ""),
        "config_digest": str(member_profile.get("config_digest") or ""),
        "skill_set_digest": str(
            member_profile.get("skill_set_digest") or ""
        ),
        "permission_digest": str(
            member_profile.get("permission_digest") or ""
        ),
        "profile_snapshot_ref": str(
            member_profile.get("profile_snapshot_ref") or ""
        ),
        "profile_snapshot_sha256": str(
            member_profile.get("profile_snapshot_sha256") or ""
        ),
        "role_context_ref": safe_role_context_ref,
        "skill_refs": safe_skill_refs,
        "resolved_skill_refs": safe_resolved_skill_refs,
        "skill_guidance": skill_guidance,
        "skill_metadata": _skill_metadata(safe_skill_refs),
        "collaboration_contract": collaboration_contract,
        "channel_profile_catalog": public_channel_profile_catalog(config),
        "current_channel_group": {
            "channel_id": channel_id,
            "thread_id": thread_id,
            "leader_member_id": str((channel or {}).get("leader_member_id") or ""),
            "leader_revision": int((channel or {}).get("leader_revision") or 1),
            "members": [{key: member.get(key) for key in (
                "member_id", "channel_role", "profile_id", "profile_revision", "status", "permissions",
            )} for member in (channel or {}).get("members") or [] if isinstance(member, dict)],
            "member_invitation_decisions": [{key: proposal.get(key) for key in (
                "proposal_id", "thread_id", "member_id", "status", "decided_by", "decision_event_id",
            )} for proposal in (channel or {}).get("member_invitation_proposals") or []
                if isinstance(proposal, dict) and str(proposal.get("thread_id") or "main") == thread_id],
        },
        "agent_context": (
            redact_obj(agent_context) if not blind and isinstance(agent_context, dict) else {}
        ),
        "role_definition": role_definition,
        "summary": "" if blind else str((channel or {}).get("summary") or ""),
        "message_refs": [
            {
                "message_id": str(item.get("message_id") or ""),
                "event_id": str(item.get("event_id") or ""),
                "member_id": str(item.get("member_id") or ""),
                "role": str(item.get("role") or ""),
                "source": str(item.get("source") or ""),
                "text_excerpt": _excerpt(str(item.get("text") or ""), 240),
            }
            for item in selected
        ],
        "question_ledger": question_ledger,
        "question_ledger_digest": ledger_digest,
        "question_ledger_complete": requires_complete_ledger,
        "question_frontier": frontier,
        "question_frontier_digest": _stable_digest(frontier),
        "owner_questionnaire": questionnaire,
        "owner_questionnaire_digest": _stable_digest(questionnaire),
        "cross_review_index": cross_review_index,
        "cross_review_index_digest": _stable_digest(cross_review_index),
        "contribution_index": contribution_index,
        "contribution_index_digest": _stable_digest(contribution_index),
        "semantic_source_required": semantic_sources["required"],
        "semantic_source_stage": semantic_sources["stage"],
        "semantic_source_complete": semantic_sources["complete"],
        "semantic_source_reason": semantic_sources["reason"],
        "semantic_source_manifest": semantic_sources["manifest"],
        "semantic_source_manifest_digest": semantic_sources[
            "manifest_digest"
        ],
        "semantic_source_required_digests": semantic_sources[
            "required_message_digests"
        ],
        "semantic_source_documents": semantic_sources["documents"],
        "artifact_refs": _select_artifact_refs(
            None if blind else channel,
            selected_messages=selected,
            visibility_profile=profile,
        ),
        "report_refs": [] if blind else _select_report_refs(channel, visibility_profile=profile),
        "limits": {
            "max_messages": profile_limits["max_messages"],
            "max_text_chars": profile_limits["max_text_chars"],
            "max_role_definition_chars": profile_limits["role_definition_chars"],
            "max_question_entries": profile_limits["question_entries"],
            "max_skill_refs": 8,
            "selected_messages": len(selected),
            "selected_questions": len(question_ledger),
            "frontier_questions": len(frontier),
            "owner_questions": len(questionnaire),
            "cross_reviews": len(cross_review_index),
            "indexed_contributions": len(contribution_index),
            "semantic_source_messages": len(
                semantic_sources["manifest"]
            ),
            "semantic_source_chars": semantic_sources["source_chars"],
            "max_semantic_source_chars": semantic_sources[
                "max_source_chars"
            ],
            "visibility_profile": profile,
            **source_limits,
        },
    }
    return redact_obj(payload)


def context_pack_rejection_reason(
    channel: dict[str, Any] | None,
    *,
    max_source_chars: int = 60000,
    max_source_messages: int = 200,
) -> tuple[str, dict[str, int]]:
    messages = list((channel or {}).get("messages") or (channel or {}).get("recent_messages") or [])
    source_chars = sum(len(str(item.get("text") or "")) for item in messages if isinstance(item, dict))
    limits = {
        "max_source_chars": max_source_chars,
        "source_chars": source_chars,
        "max_source_messages": max_source_messages,
        "source_messages": len(messages),
    }
    limits["compaction_required"] = int(
        source_chars > max_source_chars
        or len(messages) > max_source_messages
    )
    return "", limits


def _skill_metadata(skill_refs: list[str]) -> list[dict[str, str]]:
    return [
        {
            "ref": ref,
            "skill": ref.split("/")[1] if len(ref.split("/")) > 2 else ref,
            "source": "channel_member",
            "status": "referenced",
        }
        for ref in skill_refs[:8]
    ]


def _select_messages(
    messages: list[dict[str, Any]],
    *,
    thread_id: str,
    trigger_message_id: str,
    max_messages: int,
    max_text_chars: int,
) -> list[dict[str, Any]]:
    same_thread = [
        item for item in messages
        if str(item.get("thread_id") or "main") == thread_id
    ]
    position = next((i for i, item in enumerate(same_thread)
                     if item.get("message_id") == trigger_message_id), None)
    if position is not None:
        same_thread = same_thread[:position + 1]
    tail = same_thread[-max_messages:]
    selected: list[dict[str, Any]] = []
    used = 0
    for item in reversed(tail):
        text = str(item.get("text") or "")
        remaining = max(max_text_chars - used, 0)
        if remaining <= 0:
            break
        trimmed = dict(item)
        trimmed["text"] = _excerpt(text, min(remaining, 800))
        used += len(str(trimmed.get("text") or ""))
        selected.append(trimmed)
        if str(item.get("message_id") or "") == trigger_message_id:
            continue
    return list(reversed(selected))


def _select_question_ledger(
    channel: dict[str, Any] | None,
    *,
    thread_id: str,
    max_entries: int,
) -> list[dict[str, str]]:
    raw = (channel or {}).get("open_questions") or []
    items = list(raw.values()) if isinstance(raw, dict) else list(raw)
    selected = [
        item
        for item in items
        if isinstance(item, dict)
        and str(item.get("thread_id") or "main") == thread_id
    ]
    if max_entries > 0:
        selected = selected[-max_entries:]
    return [
        {
            "question_id": str(item.get("question_id") or ""),
            "question": _excerpt(str(item.get("question") or ""), 320),
            "category": str(item.get("category") or ""),
            "asked_by": str(item.get("asked_by") or ""),
            "status": str(item.get("status") or ""),
            "resolution": str(item.get("resolution") or ""),
            "resolved_by": str(item.get("resolved_by") or ""),
            "answer": _excerpt(str(item.get("answer") or ""), 600),
            "risk_note": _excerpt(str(item.get("risk_note") or ""), 240),
            "opened_event_id": str(item.get("opened_event_id") or ""),
            "resolved_event_id": str(item.get("resolved_event_id") or ""),
        }
        for item in selected
    ]


def _trigger_message_refs(
    channel: dict[str, Any] | None,
    *,
    trigger_message_id: str,
) -> dict[str, Any]:
    for item in (channel or {}).get("messages") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("message_id") or "") != trigger_message_id:
            continue
        refs = item.get("refs")
        return refs if isinstance(refs, dict) else {}
    return {}


def _collaboration_contract(
    channel: dict[str, Any] | None,
    *,
    thread_id: str,
) -> dict[str, Any]:
    scope = (
        (channel or {}).get("scope")
        if isinstance((channel or {}).get("scope"), dict)
        else {}
    )
    template = (
        scope.get("template")
        if isinstance(scope.get("template"), dict)
        else {}
    )
    sessions = (channel or {}).get("discussions")
    session = (
        sessions.get(thread_id)
        if isinstance(sessions, dict)
        and isinstance(sessions.get(thread_id), dict)
        else {}
    )
    template_id = str(template.get("id") or "")
    return {
        "schema_version": "channel.collaboration_contract.v1",
        "selected_template": {
            "id": template_id,
            "version": str(template.get("version") or ""),
            "digest": str(template.get("digest") or ""),
            "materialization_digest": str(
                template.get("materialization_digest") or ""
            ),
        },
        "template_binding_status": (
            "frozen" if template_id else "unbound"
        ),
        "allowed_template_ids": sorted(CHANNEL_TEMPLATES),
        "discussion_phase": str(session.get("state") or "idle"),
        "canonical_synthesis_availability": (
            "available_for_review"
            if str(session.get("state") or "idle") == "phase3_synthesis"
            else "not_created_until_phase3"
        ),
        "discussion_started_event_id": str(
            session.get("started_event_id") or ""
        ),
        "requirement_message_id": str(
            session.get("requirement_message_id") or ""
        ),
    }


def channel_contribution_index(
    channel: dict[str, Any] | None,
    *,
    thread_id: str,
) -> list[dict[str, Any]]:
    durable = (channel or {}).get("contributions") or []
    if isinstance(durable, dict):
        durable = list(durable.values())
    if isinstance(durable, list) and durable:
        return [
            {
                "event_id": str(item.get("event_id") or ""),
                "member_id": str(item.get("member_id") or ""),
                "contract_status": str(
                    item.get("contract_status") or ""
                ),
                "artifact_ref": str(item.get("artifact_ref") or ""),
                "artifact_digest": str(
                    item.get("artifact_digest") or ""
                ),
                "request_id": str(item.get("request_id") or ""),
                "message_id": str(item.get("message_id") or ""),
                "run_generation": int(item.get("run_generation") or 1),
                "source_reply_event_id": str(
                    item.get("source_reply_event_id") or ""
                ),
                "questions_frozen": item.get("questions_frozen") is True,
                "source_refs": [
                    str(ref)
                    for ref in item.get("source_refs") or []
                    if isinstance(ref, str)
                ],
                "evidence_refs": [
                    str(ref)
                    for ref in item.get("evidence_refs") or []
                    if isinstance(ref, str)
                ],
            }
            for item in durable
            if isinstance(item, dict)
            and str(item.get("thread_id") or "main") == thread_id
        ]
    rows: list[dict[str, Any]] = []
    for event in (channel or {}).get("linked_events") or []:
        if not isinstance(event, dict):
            continue
        if str(event.get("type") or "") != "channel.finding.recorded":
            continue
        payload = (
            event.get("payload")
            if isinstance(event.get("payload"), dict)
            else {}
        )
        if str(payload.get("thread_id") or "main") != thread_id:
            continue
        rows.append({
            "event_id": str(event.get("id") or ""),
            "member_id": str(payload.get("member_id") or ""),
            "contract_status": str(payload.get("contract_status") or ""),
            "artifact_ref": str(payload.get("artifact_ref") or ""),
            "artifact_digest": str(payload.get("artifact_digest") or ""),
            "request_id": str(payload.get("request_id") or ""),
            "message_id": str(payload.get("message_id") or ""),
            "run_generation": int(payload.get("run_generation") or 1),
            "source_reply_event_id": str(
                payload.get("source_reply_event_id") or ""
            ),
            "questions_frozen": payload.get("questions_frozen") is True,
            "source_refs": [
                str(item)
                for item in payload.get("source_refs") or []
                if isinstance(item, str)
            ],
            "evidence_refs": [
                str(item)
                for item in payload.get("evidence_refs") or []
                if isinstance(item, str)
            ],
        })
    return rows


def _stable_digest(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _stable_context_pack_id(
    channel_id: str,
    thread_id: str,
    trigger_message_id: str,
    target_member_id: str,
) -> str:
    digest = hashlib.sha1(
        f"{channel_id}:{thread_id}:{trigger_message_id}:{target_member_id}".encode("utf-8"),
    ).hexdigest()[:16]
    return f"ctx-{digest}"


def _profile_limits(profile: str, *, max_messages: int, max_text_chars: int) -> dict[str, int]:
    defaults = {
        "minimal": {"max_messages": 6, "max_text_chars": 2400, "question_entries": 12},
        "planner": {"max_messages": 12, "max_text_chars": 6000, "question_entries": 32},
        "reviewer": {"max_messages": 10, "max_text_chars": 5000, "question_entries": 24},
        "owner_report": {"max_messages": 8, "max_text_chars": 4000, "question_entries": 24},
        "full_audit": {"max_messages": 20, "max_text_chars": 10000, "question_entries": 48},
    }
    selected = defaults.get(profile, defaults["minimal"])
    role_definition_chars = {
        "minimal": 500,
        "planner": 900,
        "reviewer": 900,
        "owner_report": 700,
        "full_audit": 1200,
    }.get(profile, 500)
    return {
        "max_messages": min(max(selected["max_messages"], 1), max(max_messages, selected["max_messages"])),
        "max_text_chars": min(max(selected["max_text_chars"], 1), max(max_text_chars, selected["max_text_chars"])),
        "role_definition_chars": role_definition_chars,
        "question_entries": selected["question_entries"],
    }


def _select_report_refs(channel: dict[str, Any] | None, *, visibility_profile: str) -> list[dict[str, str]]:
    if visibility_profile != "owner_report":
        return []
    reports: list[dict[str, str]] = []
    for item in list((channel or {}).get("owner_reports") or [])[-3:]:
        if isinstance(item, dict):
            reports.append({
                "type": "owner_report",
                "report_id": str(item.get("report_id") or ""),
                "event_id": str(item.get("event_id") or ""),
                "summary_excerpt": _excerpt(str(item.get("summary") or ""), 240),
            })
    for item in list((channel or {}).get("automation_reports") or [])[-3:]:
        if isinstance(item, dict):
            reports.append({
                "type": "automation_report",
                "report_id": str(item.get("report_id") or ""),
                "event_id": str(item.get("event_id") or ""),
                "summary_excerpt": _excerpt(str(item.get("summary") or ""), 240),
            })
    return reports


def _select_artifact_refs(
    channel: dict[str, Any] | None,
    *,
    selected_messages: list[dict[str, Any]],
    visibility_profile: str,
) -> list[dict[str, Any]]:
    selected_message_ids = {
        str(item.get("message_id") or "")
        for item in selected_messages
        if str(item.get("message_id") or "")
    }
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(ref: dict[str, Any]) -> None:
        key = (
            str(ref.get("uri") or "")
            or str(ref.get("artifact_id") or "")
            or str(ref.get("attachment_id") or "")
            or f"{ref.get('type')}:{ref.get('message_id')}:{ref.get('name')}"
        )
        if not key or key in seen:
            return
        seen.add(key)
        refs.append(redact_obj(ref))

    for message in selected_messages:
        message_refs = message.get("refs") if isinstance(message.get("refs"), dict) else {}
        for attachment in _dict_list(message_refs.get("attachments")):
            add(_attachment_ref(attachment, message=message))
        for artifact in _dict_list(message_refs.get("artifacts")):
            add(_artifact_ref(artifact, message=message))

    for attachment in _dict_list((channel or {}).get("attachments")):
        message_id = str(attachment.get("message_id") or "")
        if selected_message_ids and message_id and message_id not in selected_message_ids:
            continue
        add(_attachment_ref(attachment))

    artifact_limit = 12 if visibility_profile == "full_audit" else 8
    artifact_items = _dict_list((channel or {}).get("artifacts"))[-artifact_limit:]
    for artifact in artifact_items:
        if str(artifact.get("status") or "") == "rejected":
            continue
        message_id = str(artifact.get("message_id") or "")
        if selected_message_ids and message_id and message_id not in selected_message_ids:
            continue
        add(_artifact_ref(artifact))
    for update in _dict_list((channel or {}).get("state_updates"))[-artifact_limit:]:
        update_refs = update.get("refs") if isinstance(update.get("refs"), dict) else {}
        for artifact in _dict_list(update_refs.get("artifact_refs")):
            add(_artifact_ref({
                **artifact,
                "event_id": update.get("event_id"),
                "thread_id": update.get("thread_id"),
                "summary": artifact.get("summary") or update.get("summary"),
            }))
        for key in (
            "workflow_prompt_ref",
            "workflow_input_manifest_ref",
            "research_artifact_ref",
            "artifact_ref",
            "report_ref",
            "path",
            "ref",
        ):
            value = str(update_refs.get(key) or "").strip()
            if not value:
                continue
            add(_artifact_ref({
                "artifact_id": f"{key}:{value}",
                "event_id": update.get("event_id"),
                "thread_id": update.get("thread_id"),
                "name": key,
                "kind": key,
                "path": value if "/" in value else "",
                "uri": value if "://" in value else "",
                "summary": update.get("summary"),
                "status": "attached",
            }))
    return refs[-artifact_limit:]


def _attachment_ref(item: dict[str, Any], *, message: dict[str, Any] | None = None) -> dict[str, Any]:
    message_id = str(item.get("message_id") or (message or {}).get("message_id") or "")
    return {
        "type": "attachment",
        "attachment_id": str(item.get("attachment_id") or item.get("id") or ""),
        "artifact_id": str(item.get("artifact_id") or ""),
        "event_id": str(item.get("event_id") or (message or {}).get("event_id") or ""),
        "thread_id": str(item.get("thread_id") or (message or {}).get("thread_id") or "main"),
        "message_id": message_id,
        "member_id": str(item.get("member_id") or (message or {}).get("member_id") or ""),
        "name": str(item.get("name") or item.get("filename") or ""),
        "mime": str(item.get("mime") or item.get("type") or item.get("content_type") or ""),
        "size": _safe_int(item.get("size") if item.get("size") is not None else item.get("bytes")),
        "hash": str(item.get("hash") or item.get("sha256") or ""),
        "uri": str(item.get("uri") or ""),
        "status": "uploaded" if str(item.get("uri") or "").startswith("channel://attachments/") else "content_unavailable",
    }


def _artifact_ref(item: dict[str, Any], *, message: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "type": "artifact",
        "artifact_id": str(item.get("artifact_id") or item.get("id") or ""),
        "attachment_id": str(item.get("attachment_id") or ""),
        "event_id": str(item.get("event_id") or (message or {}).get("event_id") or ""),
        "thread_id": str(item.get("thread_id") or (message or {}).get("thread_id") or "main"),
        "message_id": str(item.get("message_id") or (message or {}).get("message_id") or ""),
        "member_id": str(item.get("member_id") or (message or {}).get("member_id") or ""),
        "name": str(item.get("name") or item.get("filename") or ""),
        "kind": str(item.get("kind") or ""),
        "mime": str(item.get("mime") or item.get("type") or item.get("content_type") or ""),
        "size": _safe_int(item.get("size") if item.get("size") is not None else item.get("bytes")),
        "hash": str(item.get("hash") or item.get("sha256") or ""),
        "path": str(item.get("path") or ""),
        "uri": str(item.get("uri") or ""),
        "status": str(item.get("status") or ""),
        "summary_excerpt": _excerpt(str(item.get("summary") or ""), 240),
    }


def _dict_list(value: object) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [dict(item) for item in value.values() if isinstance(item, dict)]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _safe_int(value: object) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _excerpt(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(limit - 3, 0)] + "..."
