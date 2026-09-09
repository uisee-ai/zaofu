"""Rebuildable Agent Channel projection over events.jsonl."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.security.redaction import redact_obj
from zf.runtime.channel_contracts import (
    default_debate_max_rounds,
    discussion_engine_mode,
    normalize_channel_skill_refs,
    normalize_channel_role,
    normalize_member_type,
    normalize_permission_profile,
    normalize_permissions,
    normalize_product_discussion_mode,
    normalize_provider,
    normalize_visibility_profile,
    permission_profile_write_policy,
)
from zf.runtime.channel_discussion_attention import project_discussion_attention
from zf.runtime.channel_roles import normalize_role_context_ref
from zf.runtime.channel_question_graph import (
    normalize_question_payload,
    owner_questionnaire,
    question_frontier,
    question_graph_digest,
    question_text_identity,
    validate_question_graph,
)
from zf.runtime.channel_run_owner import (
    RUN_ACTIVE_STATUSES,
    provider_run_record,
    stable_provider_run_id,
)
from zf.runtime.channel_sidecar import hydrate_channel_context_pack_payload
from zf.runtime.provider_usage import sum_turn_usage


CHANNEL_EVENT_TYPES = {
    "channel.created",
    "channel.archived",
    "channel.member.added",
    "channel.member.add.rejected",
    "channel.member.connected",
    "channel.member.invited",
    "channel.member.removed",
    "channel.member.resumed",
    "channel.member.suspended",
    "channel.member.permissions.updated",
    "channel.member.permission_profile.audit",
    "channel.member.visibility.updated",
    "channel.leader.updated",
    "channel.owner_report.requested",
    "channel.owner_report.generated",
    "channel.owner_report.delivered",
    "channel.owner_report.rejected",
    "channel.route.blocked",
    "channel.route.defaulted",
    "channel.automation_report.ingested",
    "channel.mention.detected",
    "channel.spine_review.requested",
    "channel.agent.reply.requested",
    "channel.agent.reply.started",
    "channel.agent.reply.completed",
    "channel.agent.reply.failed",
    "channel.typing.started",
    "channel.typing.stopped",
    "channel.message.stream.started",
    "channel.message.stream.delta",
    "channel.message.stream.ended",
    "channel.attachment.uploaded",
    "channel.artifact.proposed",
    "channel.artifact.attached",
    "channel.artifact.rejected",
    "agent.session.run.started",
    "agent.session.run.completed",
    "agent.session.run.failed",
    "agent.session.run.cancelled",
    "agent.session.part.started",
    "agent.session.part.delta",
    "agent.session.part.completed",
    "agent.session.part.failed",
    "channel.context_pack.built",
    "channel.context_pack.rejected",
    "channel.handoff.requested",
    "channel.handoff.accepted",
    "channel.handoff.rejected",
    "channel.handoff.failed",
    "channel.state_update.posted",
    "channel.discussion.mode.set",
    "channel.discussion.started",
    "channel.discussion.continued",
    "channel.discussion.phase.changed",
    "channel.discussion.closed",
    "channel.discussion.participant.missed",
    "channel.relay.routed",
    "channel.relay.suppressed",
    "channel.question.opened",
    "channel.question.updated",
    "channel.question.resolved",
    "channel.question.merged",
    "channel.question.dedup.requested",
    "channel.question.dedup.applied",
    "channel.question.dedup.rejected",
    "channel.question.dedup.remediation.exhausted",
    "channel.question.resolve.rejected",
    "channel.questions.frozen",
    "channel.cross_review.requested",
    "channel.cross_review.completed",
    "channel.cross_review.rejected",
    "channel.consensus.proposed",
    "channel.consensus.signed",
    "channel.consensus.blocked",
    "channel.consensus.review.rejected",
    "channel.consensus.reached",
    "channel.message.posted",
    "channel.message.delivered",
    "channel.message.failed",
    "channel.message.read",
    "channel.message.pinned",
    "channel.history.cleared",
    "channel.result.receipt.recorded",
    "channel.finding.recorded",
    "channel.summary.updated",
    "channel.synthesis.requested",
    "channel.synthesis.stale_ignored",
    "channel.synthesis.repair.requested",
    "channel.synthesis.repair.completed",
    "channel.synthesis.repair.stale_ignored",
    "channel.synthesis.blocked",
    "channel.synthesis.proposed",
    "workflow.invoke.requested",
    "workflow.invoke.accepted",
    "workflow.invoke.rejected",
    "workflow.adjust.requested",
    "workflow.adjust.accepted",
    "workflow.adjust.rejected",
}
DEFAULT_CHANNEL_IDS = {"ch-zaofu", "zaofu"}
DEFAULT_PROVIDER_CAPABILITIES = {
    "codex": {"supports_resume": True, "supports_interrupt": True, "supports_stream": False, "cost_class": "medium"},
    "claude-code": {"supports_resume": True, "supports_interrupt": True, "supports_stream": False, "cost_class": "medium"},
    "hermes": {"supports_resume": True, "supports_interrupt": True, "supports_stream": True, "cost_class": "medium"},
    "openclaw": {"supports_resume": False, "supports_interrupt": False, "supports_stream": False, "cost_class": "unknown"},
    "runtime-role": {"supports_resume": True, "supports_interrupt": True, "supports_stream": False, "cost_class": "existing"},
    "fake": {"supports_resume": False, "supports_interrupt": False, "supports_stream": False, "cost_class": "test"},
}


def project_channels(
    state_dir: Path,
    *,
    events: list[ZfEvent] | None = None,
) -> dict[str, Any]:
    event_list = _with_seq(events if events is not None else EventLog(state_dir / "events.jsonl").read_all())
    channels = _build(event_list, state_dir=Path(state_dir))
    items = [
        _public_channel(
            channel,
            include_messages=False,
            include_linked_events=False,
        )
        for channel in sorted(channels.values(), key=lambda item: item["channel_id"])
        if channel.get("status") != "archived"
    ]
    return {
        "schema_version": "channels.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seq": event_list[-1][0] if event_list else 0,
        "source": "events.jsonl",
        "channels": items,
    }


def project_channel(
    state_dir: Path,
    channel_id: str,
    *,
    events: list[ZfEvent] | None = None,
    include_linked_events: bool = True,
) -> dict[str, Any] | None:
    event_list = _with_seq(events if events is not None else EventLog(state_dir / "events.jsonl").read_all())
    channels = _build(event_list, state_dir=Path(state_dir))
    key = _resolve_channel_key(channels, channel_id)
    channel = channels.get(key) if key else None
    if channel is None:
        return None
    return _public_channel(
        channel,
        include_messages=True,
        include_linked_events=include_linked_events,
    )


def project_empty_channel(channel_id: str) -> dict[str, Any]:
    """Return a read-only placeholder for the built-in Web bootstrap channel.

    This does not create source-of-truth state. It only keeps the Web detail
    pane stable before the first channel event has been appended.
    """
    channel = _empty_channel(channel_id)
    if _public_channel_id(channel_id) in DEFAULT_CHANNEL_IDS:
        channel["name"] = "# zaofu"
        channel["status"] = "empty"
    out = _public_channel(channel, include_messages=True)
    out["empty"] = True
    return out


def search_channel_history(
    state_dir: Path,
    channel_id: str,
    *,
    q: str = "",
    thread_id: str = "",
    member_id: str = "",
    mention: str = "",
    limit: int = 50,
    events: list[ZfEvent] | None = None,
) -> dict[str, Any]:
    """Search the rebuildable Channel message projection.

    This is an index helper over ``events.jsonl`` projections, not a second
    message store. Results carry refs needed by Web to locate a thread/message.
    """
    detail = project_channel(state_dir, channel_id, events=events)
    if detail is None:
        return {
            "schema_version": "channel_history_search.v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "channel_id": channel_id,
            "query": q,
            "filters": {
                "thread_id": thread_id,
                "member_id": member_id,
                "mention": mention,
            },
            "history_index": _history_index([]),
            "results": [],
            "items": [],
        }
    messages = [
        item for item in list(detail.get("messages") or detail.get("recent_messages") or [])
        if isinstance(item, dict)
    ]
    terms = [part.casefold() for part in str(q or "").split() if part.strip()]
    safe_thread_id = str(thread_id or "").strip()
    safe_member_id = str(member_id or "").strip()
    safe_mention = str(mention or "").strip()
    matches: list[dict[str, Any]] = []
    for message in messages:
        if safe_thread_id and str(message.get("thread_id") or "main") != safe_thread_id:
            continue
        if safe_member_id and str(message.get("member_id") or "") != safe_member_id:
            continue
        mentions = _string_list(message.get("mentions"))
        if safe_mention and safe_mention not in mentions:
            continue
        haystack = _message_search_text(message)
        if terms and not all(term in haystack for term in terms):
            continue
        matches.append(_history_result(message, terms))
    limit = max(1, min(int(limit or 50), 200))
    results = sorted(matches, key=lambda item: str(item.get("ts") or ""), reverse=True)[:limit]
    payload = {
        "schema_version": "channel_history_search.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "channel_id": str(detail.get("channel_id") or channel_id),
        "query": q,
        "filters": {
            "thread_id": safe_thread_id,
            "member_id": safe_member_id,
            "mention": safe_mention,
        },
        "history_index": _history_index(messages),
        "result_count": len(results),
        "results": results,
        "items": results,
    }
    return redact_obj(payload)


def _history_index(messages: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_thread: dict[str, dict[str, Any]] = {}
    by_member: dict[str, dict[str, Any]] = {}
    by_mention: dict[str, dict[str, Any]] = {}

    def touch(index: dict[str, dict[str, Any]], key: str, message: dict[str, Any]) -> None:
        if not key:
            return
        row = index.setdefault(key, {
            "id": key,
            "message_count": 0,
            "last_message_id": "",
            "last_seen_at": "",
        })
        row["message_count"] = int(row.get("message_count") or 0) + 1
        if str(message.get("ts") or "") >= str(row.get("last_seen_at") or ""):
            row["last_seen_at"] = str(message.get("ts") or "")
            row["last_message_id"] = str(message.get("message_id") or "")

    for message in messages:
        touch(by_thread, str(message.get("thread_id") or "main"), message)
        touch(by_member, str(message.get("member_id") or ""), message)
        for mention in _string_list(message.get("mentions")):
            touch(by_mention, mention, message)
    return {
        "threads": sorted(by_thread.values(), key=lambda item: str(item.get("id") or "")),
        "members": sorted(by_member.values(), key=lambda item: str(item.get("id") or "")),
        "mentions": sorted(by_mention.values(), key=lambda item: str(item.get("id") or "")),
    }


def _message_search_text(message: dict[str, Any]) -> str:
    parts = [
        str(message.get("text") or ""),
        str(message.get("message") or ""),
        str(message.get("summary") or ""),
        str(message.get("message_id") or ""),
        str(message.get("thread_id") or ""),
        str(message.get("member_id") or ""),
        str(message.get("role") or ""),
        " ".join(_string_list(message.get("mentions"))),
    ]
    refs = message.get("refs") if isinstance(message.get("refs"), dict) else {}
    for key in ("attachments", "artifacts", "artifact_refs"):
        values = refs.get(key)
        if isinstance(values, list):
            for item in values:
                if isinstance(item, dict):
                    parts.extend([
                        str(item.get("name") or ""),
                        str(item.get("filename") or ""),
                        str(item.get("artifact_id") or ""),
                        str(item.get("attachment_id") or ""),
                    ])
    return " ".join(parts).casefold()


def _history_result(message: dict[str, Any], terms: list[str]) -> dict[str, Any]:
    refs = message.get("refs") if isinstance(message.get("refs"), dict) else {}
    text = str(message.get("text") or message.get("message") or message.get("summary") or "")
    return {
        "message_id": str(message.get("message_id") or ""),
        "event_id": str(message.get("event_id") or ""),
        "thread_id": str(message.get("thread_id") or "main"),
        "member_id": str(message.get("member_id") or ""),
        "role": str(message.get("role") or ""),
        "source": str(message.get("source") or ""),
        "ts": str(message.get("ts") or ""),
        "text_excerpt": _history_excerpt(text, terms),
        "mentions": _string_list(message.get("mentions")),
        "attachment_count": _ref_count(refs, "attachments"),
        "artifact_count": _ref_count(refs, "artifacts") + _ref_count(refs, "artifact_refs"),
    }


def _history_excerpt(text: str, terms: list[str], limit: int = 220) -> str:
    if len(text) <= limit:
        return text
    lowered = text.casefold()
    first = min((lowered.find(term) for term in terms if term and lowered.find(term) >= 0), default=-1)
    if first < 0:
        return text[: max(limit - 3, 0)] + "..."
    start = max(first - limit // 3, 0)
    end = min(start + limit, len(text))
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(text) else ""
    return prefix + text[start:end].strip() + suffix


def _ref_count(refs: dict[str, Any], key: str) -> int:
    values = refs.get(key)
    return len(values) if isinstance(values, list) else 0


def _with_seq(events: list[ZfEvent]) -> list[tuple[int, ZfEvent]]:
    return [(index, event) for index, event in enumerate(events, 1)]


def _build(events: list[tuple[int, ZfEvent]], *, state_dir: Path | None = None) -> dict[str, dict[str, Any]]:
    channels: dict[str, dict[str, Any]] = {}
    for seq, event in events:
        if event.type not in CHANNEL_EVENT_TYPES:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        channel_id = _payload_str(payload, "channel_id") or _channel_from_event(event)
        if not channel_id:
            continue
        channel = channels.setdefault(channel_id, _empty_channel(channel_id))
        channel["last_event_id"] = event.id
        channel["last_event_seq"] = seq
        channel["last_event_type"] = event.type
        channel["updated_at"] = event.ts
        # Keep the raw in-memory reference while folding. Public serializers
        # materialize and redact only the bounded rows they actually expose;
        # eagerly redacting every historical event dominated Channel reads.
        channel["linked_events"].append((seq, event))
        if event.type == "channel.created":
            channel["status"] = "open"
            channel["created_by_event"] = True
            channel["created_at"] = channel["created_at"] or event.ts
            channel["created_by"] = _payload_str(payload, "created_by") or event.actor or channel["created_by"]
            channel["task_id"] = _payload_str(payload, "task_id") or event.task_id or channel["task_id"]
            channel["name"] = (
                _payload_str(payload, "name")
                or _payload_str(payload, "channel_name")
                or channel["name"]
                or channel_id
            )
            channel["scope"] = payload.get("scope") if isinstance(payload.get("scope"), dict) else channel["scope"]
            channel["owner_actor_ref"] = (
                _payload_str(payload, "owner_actor_ref")
                or channel["owner_actor_ref"]
            )
            channel["owner_delegates"] = (
                redact_obj(payload.get("owner_delegates"))
                if isinstance(payload.get("owner_delegates"), list)
                else channel["owner_delegates"]
            )
            channel["leader_member_id"] = (
                _payload_str(payload, "leader_member_id")
                or channel["leader_member_id"]
            )
            channel["leader_revision"] = int(
                payload.get("leader_revision")
                or channel["leader_revision"]
                or 0
            )
            channel["origin_binding"] = (
                redact_obj(payload.get("origin_binding"))
                if isinstance(payload.get("origin_binding"), dict)
                else channel["origin_binding"]
            )
        elif event.type == "channel.archived":
            channel["status"] = "archived"
        elif event.type == "channel.leader.updated":
            channel["leader_member_id"] = _payload_str(
                payload, "leader_member_id"
            )
            channel["leader_revision"] = int(
                payload.get("leader_revision")
                or channel["leader_revision"]
                or 0
            )
            channel["leader_event_id"] = event.id
        elif event.type in {
            "channel.member.added",
            "channel.member.connected",
            "channel.member.invited",
            "channel.member.removed",
            "channel.member.resumed",
            "channel.member.suspended",
            "channel.member.permissions.updated",
            "channel.member.visibility.updated",
        }:
            _apply_member(channel, event, payload)
        elif event.type == "channel.member.add.rejected":
            _apply_member_rejection(channel, event, payload)
        elif event.type == "channel.mention.detected":
            _apply_mention(channel, event, payload)
        elif event.type == "channel.route.defaulted":
            _apply_route_defaulted(channel, event, payload)
        elif event.type == "channel.route.blocked":
            _apply_route_blocked(channel, event, payload)
        elif event.type.startswith("channel.agent.reply."):
            _apply_reply(channel, event, payload)
        elif event.type.startswith("channel.typing."):
            _apply_typing(channel, event, payload)
        elif event.type.startswith("channel.message.stream."):
            _apply_channel_stream(channel, event, payload)
        elif event.type == "channel.attachment.uploaded":
            _apply_attachment(channel, event, payload)
        elif event.type.startswith("channel.artifact."):
            _apply_artifact(channel, event, payload)
        elif event.type.startswith("agent.session."):
            _apply_agent_session(channel, event, payload)
        elif event.type == "channel.finding.recorded":
            _apply_contribution(channel, event, payload)
        elif event.type.startswith("channel.context_pack."):
            _apply_context_pack(channel, event, payload, state_dir=state_dir)
        elif event.type.startswith("channel.handoff."):
            _apply_handoff(channel, event, payload)
        elif event.type == "channel.state_update.posted":
            _apply_state_update(channel, event, payload)
        elif event.type == "channel.discussion.mode.set":
            _apply_discussion_mode(channel, event, payload)
        elif event.type == "channel.discussion.started":
            _apply_discussion_started(channel, event, payload)
        elif event.type == "channel.discussion.continued":
            _apply_discussion_continued(channel, event, payload)
        elif event.type.startswith("channel.discussion.next_round."):
            _apply_next_round(channel, event, payload)
        elif event.type == "channel.discussion.phase.changed":
            _apply_discussion_phase(channel, event, payload)
        elif event.type == "channel.discussion.closed":
            _apply_discussion_closed(channel, event, payload)
        elif event.type.startswith("channel.relay."):
            _apply_relay(channel, event, payload)
        elif event.type == "channel.question.resolve.rejected":
            _apply_question_resolve_rejected(channel, event, payload)
        elif event.type.startswith("channel.question.dedup."):
            _apply_question_dedup(channel, event, payload)
        elif event.type == "channel.questions.frozen":
            _apply_questions_frozen(channel, event, payload)
        elif event.type.startswith("channel.cross_review."):
            _apply_cross_review(channel, event, payload)
        elif event.type.startswith("channel.question."):
            _apply_question(channel, event, payload)
        elif event.type.startswith("channel.consensus."):
            _apply_consensus(channel, event, payload)
        elif event.type == "channel.message.posted":
            _apply_message(channel, event, payload)
        elif event.type in {"channel.message.delivered", "channel.message.failed"}:
            _apply_delivery(channel, event, payload)
        elif event.type == "channel.message.read":
            _apply_read(channel, event, payload)
        elif event.type == "channel.message.pinned":
            _apply_message_pinned(channel, event, payload)
        elif event.type == "channel.history.cleared":
            _apply_history_cleared(channel, event, payload)
        elif event.type == "channel.result.receipt.recorded":
            _apply_result_receipt(channel, event, payload)
        elif event.type == "channel.summary.updated":
            channel["summary"] = _payload_str(payload, "summary")
            channel["summary_event_id"] = event.id
        elif event.type in {
            "channel.synthesis.requested",
            "channel.synthesis.stale_ignored",
            "channel.synthesis.repair.requested",
            "channel.synthesis.blocked",
        }:
            _apply_synthesis_request(channel, event, payload)
        elif event.type == "channel.synthesis.proposed":
            _apply_synthesis(channel, event, payload)
        elif event.type.startswith("channel.owner_report."):
            _apply_owner_report(channel, event, payload)
        elif event.type == "channel.automation_report.ingested":
            _apply_automation_report(channel, event, payload)
        elif event.type.startswith("workflow."):
            _apply_workflow(channel, event, payload)
    for channel in channels.values():
        _refresh_attention(channel)
    return channels


def _empty_channel(channel_id: str) -> dict[str, Any]:
    return {
        "channel_id": channel_id,
        "name": channel_id,
        "status": "observed",
        "scope": {},
        "task_id": "",
        "created_at": "",
        "created_by": "",
        "created_by_event": False,
        "owner_actor_ref": "",
        "owner_delegates": [],
        "leader_member_id": "",
        "leader_revision": 0,
        "leader_event_id": "",
        "origin_binding": {},
        "members": {},
        "messages": {},
        "threads": {},
        "read_state": {},
        "pinned_message_ids": {},
        "result_receipts": {},
        "attention": [],
        "syntheses": [],
        "synthesis_requests": [],
        "question_dedup_requests": [],
        "contributions": {},
        "workflow_requests": [],
        "mentions_detected": [],
        "routes": [],
        "reply_requests": {},
        "typing": {},
        "agent_session_runs": {},
        "attachments": {},
        "artifacts": {},
        "context_packs": {},
        "handoffs": [],
        "state_updates": [],
        "owner_reports": [],
        "automation_reports": [],
        "discussion": {
            "mode": "conversation",
            "engine_mode": "manual_mention",
            "legacy_mode": "",
            "max_rounds": 6,
            "max_parallel_replies": 6,
            "default_responder_id": "",
            "speaker_policy": {},
            "provider_capabilities": DEFAULT_PROVIDER_CAPABILITIES,
        },
        "discussions": {},
        "open_questions": {},
        "question_graph_rejections": [],
        "cross_reviews": {},
        "question_activity": [],
        "questions_frozen": {},
        "next_rounds": [],
        "consensus": {},
        "rejected_resolutions": [],
        "question_resolve_rejections": [],
        "relay_events": [],
        "summary": "",
        "last_event_id": "",
        "last_event_seq": 0,
        "last_event_type": "",
        "updated_at": "",
        "linked_events": [],
        "history_cleared_at": "",
        "history_clear_event_id": "",
        "history_clear_reason": "",
    }


def _apply_member(channel: dict[str, Any], event: ZfEvent, payload: dict[str, Any]) -> None:
    member_id = _payload_str(payload, "member_id") or _payload_str(payload, "persona")
    if not member_id:
        return
    member = channel["members"].setdefault(member_id, {
        "member_id": member_id,
        "persona": member_id,
        "status": "invited",
        "backing_worker_session_id": "",
    })
    legacy_member_type = _payload_str(payload, "legacy_member_type") or _payload_str(payload, "member_type")
    provider = normalize_provider(
        _payload_str(payload, "provider")
        or _payload_str(payload, "backend")
        or legacy_member_type
    ) or member.get("provider", "")
    member_type = normalize_member_type(
        _payload_str(payload, "member_type") or member.get("member_type", ""),
        backend=provider,
    ) or member.get("member_type", "")
    channel_role = normalize_channel_role(
        _payload_str(payload, "channel_role")
        or _payload_str(payload, "role")
        or member.get("channel_role", ""),
        member_type=member_type,
    )
    visibility_profile = normalize_visibility_profile(
        _payload_str(payload, "visibility_profile") or member.get("visibility_profile", ""),
        channel_role=channel_role,
        member_type=member_type,
    )
    member["persona"] = _payload_str(payload, "persona") or member["persona"]
    member["display_name"] = _payload_str(payload, "display_name") or member.get("display_name", "") or member["persona"]
    for field in (
        "profile_id",
        "profile_provenance",
        "profile_digest",
        "config_digest",
        "skill_set_digest",
        "permission_digest",
        "role_definition_digest",
        "profile_snapshot_ref",
        "profile_snapshot_sha256",
    ):
        member[field] = _payload_str(payload, field) or member.get(field, "")
    member["profile_revision"] = int(
        payload.get("profile_revision")
        or member.get("profile_revision")
        or 1
    )
    member["member_type"] = member_type
    if legacy_member_type and legacy_member_type != member_type:
        member["legacy_member_type"] = legacy_member_type
    member["provider"] = provider
    member["backend"] = _payload_str(payload, "backend") or provider or member.get("backend", "")
    member["model"] = _payload_str(payload, "model") or member.get("model", "")
    member["provider_binding_id"] = (
        _payload_str(payload, "provider_binding_id")
        or member.get("provider_binding_id", "")
    )
    member["remote_agent_id"] = (
        _payload_str(payload, "remote_agent_id")
        or member.get("remote_agent_id", "")
    )
    member["channel_role"] = channel_role
    member["visibility_profile"] = visibility_profile
    permission_profile = normalize_permission_profile(
        _payload_str(payload, "permission_profile") or member.get("permission_profile", "")
    )
    member["permission_profile"] = permission_profile
    member["write_policy"] = _canonical_member_write_policy(
        permission_profile=permission_profile,
        payload=payload,
        current=member.get("write_policy"),
    )
    # Optional reply-output contract appended to the member's system prompt at
    # dispatch (e.g. the Feishu kanban agent's action-proposal JSON contract).
    member["reply_contract"] = (
        _payload_str(payload, "reply_contract") or member.get("reply_contract", "")
    )
    member["role_context_ref"] = (
        normalize_role_context_ref(_payload_str(payload, "role_context_ref"))
        or member.get("role_context_ref", "")
    )
    member["skill_refs"] = normalize_channel_skill_refs(
        payload.get("skill_refs") if "skill_refs" in payload else member.get("skill_refs", []),
    )
    member["resolved_skill_refs"] = (
        redact_obj(payload.get("resolved_skill_refs"))
        if isinstance(payload.get("resolved_skill_refs"), list)
        else member.get("resolved_skill_refs", [])
    )
    member["scope"] = _payload_str(payload, "scope") or member.get("scope", "")
    if isinstance(payload.get("writer_scope"), list):
        member["writer_scope"] = _string_list(payload.get("writer_scope"))
    member["permissions"] = normalize_permissions(
        payload.get("permissions") if "permissions" in payload else member.get("permissions", []),
        member_type=member_type,
    )
    member["reason"] = _payload_str(payload, "reason") or member.get("reason", "")
    member["role"] = _payload_str(payload, "role") or member.get("role", "")
    member["workflow_role_binding"] = (
        redact_obj(payload.get("workflow_role_binding"))
        if isinstance(payload.get("workflow_role_binding"), dict)
        else member.get("workflow_role_binding", {})
    )
    member["discussion_policy"] = (
        redact_obj(payload.get("discussion_policy"))
        if isinstance(payload.get("discussion_policy"), dict)
        else member.get("discussion_policy", {})
    )
    member["output_contract"] = (
        redact_obj(payload.get("output_contract"))
        if isinstance(payload.get("output_contract"), dict)
        else member.get("output_contract", {})
    )
    member["backing_worker_session_id"] = (
        _payload_str(payload, "backing_worker_session_id")
        or _payload_str(payload, "worker_session_id")
        or member.get("backing_worker_session_id", "")
    )
    member["provider_session_id"] = (
        _payload_str(payload, "provider_session_id")
        or member.get("provider_session_id", "")
    )
    member["capabilities"] = (
        redact_obj(payload.get("capabilities"))
        if isinstance(payload.get("capabilities"), dict)
        else member.get("capabilities", {})
    )
    member["last_error"] = _payload_str(payload, "reason") if event.type.endswith("rejected") else member.get("last_error", "")
    member["last_event_id"] = event.id
    member["last_event_ts"] = event.ts
    if event.type == "channel.member.removed":
        member["status"] = "removed"
    elif event.type == "channel.member.suspended":
        member["status"] = "suspended"
    elif event.type == "channel.member.connected":
        member["status"] = "connected"
    elif event.type == "channel.member.resumed":
        member["status"] = "active"
    elif event.type == "channel.member.added":
        member["status"] = _payload_str(payload, "status") or "active"
    else:
        member["status"] = _payload_str(payload, "status") or member["status"]


def _apply_member_rejection(channel: dict[str, Any], event: ZfEvent, payload: dict[str, Any]) -> None:
    member_id = _payload_str(payload, "member_id") or _payload_str(payload, "persona")
    if not member_id:
        return
    member = channel["members"].setdefault(member_id, {
        "member_id": member_id,
        "persona": member_id,
        "status": "rejected",
        "backing_worker_session_id": "",
    })
    provider = normalize_provider(_payload_str(payload, "provider") or _payload_str(payload, "backend"))
    member_type = normalize_member_type(_payload_str(payload, "member_type"), backend=provider)
    channel_role = normalize_channel_role(_payload_str(payload, "channel_role"), member_type=member_type)
    member["member_type"] = member_type or member.get("member_type", "")
    member["provider"] = provider or member.get("provider", "")
    member["backend"] = _payload_str(payload, "backend") or provider or member.get("backend", "")
    member["provider_binding_id"] = (
        _payload_str(payload, "provider_binding_id")
        or member.get("provider_binding_id", "")
    )
    member["channel_role"] = channel_role or member.get("channel_role", "")
    member["visibility_profile"] = normalize_visibility_profile(
        _payload_str(payload, "visibility_profile") or member.get("visibility_profile", ""),
        channel_role=member.get("channel_role", ""),
        member_type=member.get("member_type", ""),
    )
    permission_profile = normalize_permission_profile(
        _payload_str(payload, "permission_profile") or member.get("permission_profile", "")
    )
    member["permission_profile"] = permission_profile
    member["write_policy"] = _canonical_member_write_policy(
        permission_profile=permission_profile,
        payload=payload,
        current=member.get("write_policy"),
    )
    member["scope"] = _payload_str(payload, "scope") or member.get("scope", "")
    member["skill_refs"] = normalize_channel_skill_refs(
        payload.get("skill_refs") if "skill_refs" in payload else member.get("skill_refs", []),
    )
    member["permissions"] = normalize_permissions(
        payload.get("permissions") if "permissions" in payload else member.get("permissions", []),
        member_type=member.get("member_type", ""),
    )
    member["reason"] = _payload_str(payload, "reason") or member.get("reason", "")
    member["last_error"] = _payload_str(payload, "reason")
    member["last_event_id"] = event.id
    member["last_event_ts"] = event.ts
    member["status"] = "rejected"


def _canonical_member_write_policy(
    *,
    permission_profile: str,
    payload: dict[str, Any],
    current: Any,
) -> dict[str, Any]:
    """Return the current canonical policy for known profile payloads.

    Older member events persist a profile's write policy snapshot. Channel
    projections should reflect the current contract for the named profile, so
    adding a path such as project-local ``skills/`` does not require every
    existing member to be re-invited.
    """
    canonical = permission_profile_write_policy(permission_profile)
    raw = payload.get("write_policy")
    if isinstance(raw, dict):
        mode = str(raw.get("mode") or "").strip()
        if mode == permission_profile:
            return canonical
        return redact_obj(raw)
    if isinstance(current, dict):
        mode = str(current.get("mode") or "").strip()
        if mode == permission_profile:
            return canonical
        return redact_obj(current)
    return canonical


def _external_origin(refs: dict[str, Any]) -> dict[str, str]:
    """feishu-C #1/§4: standardized external origin from refs.feishu/openclaw.

    Returns {channel, chat_id} for a message that entered from Feishu/OpenClaw,
    or {} for a Web-native message. The Web reads this for the source chip.
    """
    for namespace in ("feishu", "openclaw"):
        ext = refs.get(namespace)
        if isinstance(ext, dict) and ext.get("chat_id"):
            return {"channel": namespace, "chat_id": str(ext["chat_id"])}
    return {}


def _apply_message(channel: dict[str, Any], event: ZfEvent, payload: dict[str, Any]) -> None:
    message_id = _payload_str(payload, "message_id") or event.id
    thread_id = _payload_str(payload, "thread_id") or "main"
    mentions = _string_list(payload.get("mentions"))
    mention_tokens = _string_list(payload.get("mention_tokens"))
    message = {
        "message_id": message_id,
        "client_message_id": (
            _payload_str(payload, "client_message_id") or message_id
        ),
        "idempotency_key": (
            _payload_str(payload, "idempotency_key")
            or _payload_str(payload, "client_message_id")
            or message_id
        ),
        "reply_to_message_id": _payload_str(
            payload, "reply_to_message_id"
        ),
        "thread_id": thread_id,
        "event_id": event.id,
        "ts": event.ts,
        "actor": event.actor or _payload_str(payload, "actor"),
        "member_id": _payload_str(payload, "member_id"),
        "role": _payload_str(payload, "role"),
        "source": _payload_str(payload, "source"),
        "text": (
            _payload_str(payload, "text")
            or _payload_str(payload, "message")
            or _payload_str(payload, "text_preview")
            or _payload_str(payload, "reason")
        ),
        "text_preview": _payload_str(payload, "text_preview"),
        "body_ref": _payload_str(payload, "body_ref"),
        "body_sha256": _payload_str(payload, "body_sha256"),
        "body_byte_count": payload.get("body_byte_count") or 0,
        "mentions": mentions,
        "mention_tokens": mention_tokens,
        "refs": payload.get("refs") if isinstance(payload.get("refs"), dict) else {},
        # feishu-C #1: expose the external origin (Feishu/OpenClaw chat) so Web
        # can render a "from Feishu" source chip; "" when message is Web-native.
        "origin": _external_origin(
            payload.get("refs") if isinstance(payload.get("refs"), dict) else {}),
        "delivery": {},
    }
    channel["messages"][message_id] = redact_obj(message)
    consensus = channel.get("consensus", {}).get(thread_id)
    if (
        isinstance(consensus, dict)
        and str(consensus.get("status") or "") == "reached"
        and (
            str(message.get("role") or "") == "user"
            or str(message.get("member_id") or "") == "operator"
        )
    ):
        consensus["revision_pending"] = True
        consensus["revision_pending_since_event_id"] = event.id
    thread = channel["threads"].setdefault(thread_id, {
        "thread_id": thread_id,
        "message_ids": [],
    })
    if message_id not in thread["message_ids"]:
        thread["message_ids"].append(message_id)
    for member_id in mentions:
        state = _read_state(channel, thread_id, member_id)
        state["mention_count"] += 1


def _apply_delivery(channel: dict[str, Any], event: ZfEvent, payload: dict[str, Any]) -> None:
    message_id = _payload_str(payload, "message_id")
    member_id = _payload_str(payload, "member_id")
    if not message_id or not member_id:
        return
    message = channel["messages"].setdefault(message_id, {
        "message_id": message_id,
        "thread_id": _payload_str(payload, "thread_id") or "main",
        "delivery": {},
    })
    status = "failed" if event.type == "channel.message.failed" else "delivered"
    message.setdefault("delivery", {})[member_id] = {
        "status": status,
        "event_id": event.id,
        "reason": _payload_str(payload, "reason"),
    }
    thread_id = str(message.get("thread_id") or _payload_str(payload, "thread_id") or "main")
    state = _read_state(channel, thread_id, member_id)
    if status == "delivered":
        state["last_delivered_message_id"] = message_id
    else:
        state["attention"] = {
            "level": "blocked",
            "reason": "delivery_failed",
            "raised_at": event.ts,
            "message_id": message_id,
        }


def _apply_read(channel: dict[str, Any], event: ZfEvent, payload: dict[str, Any]) -> None:
    thread_id = _payload_str(payload, "thread_id") or "main"
    member_id = _payload_str(payload, "member_id")
    if not member_id:
        return
    state = _read_state(channel, thread_id, member_id)
    state["last_read_message_id"] = _payload_str(payload, "message_id")
    state["last_read_event_id"] = event.id
    state["unread_count"] = 0
    state["mention_count"] = 0
    state["attention"] = {"level": "none", "reason": "", "raised_at": event.ts}


def _apply_message_pinned(
    channel: dict[str, Any],
    event: ZfEvent,
    payload: dict[str, Any],
) -> None:
    message_id = _payload_str(payload, "message_id")
    if not message_id or message_id not in channel["messages"]:
        return
    pinned = bool(payload.get("pinned", True))
    channel["pinned_message_ids"][message_id] = pinned
    channel["messages"][message_id]["pinned"] = pinned
    channel["messages"][message_id]["pin_event_id"] = event.id
    channel["messages"][message_id]["pinned_by"] = (
        _payload_str(payload, "member_id") or event.actor
    )


def _apply_result_receipt(
    channel: dict[str, Any],
    event: ZfEvent,
    payload: dict[str, Any],
) -> None:
    receipt_id = _payload_str(payload, "receipt_id") or event.id
    channel["result_receipts"][receipt_id] = redact_obj({
        "receipt_id": receipt_id,
        "event_id": event.id,
        "receipt_kind": _payload_str(payload, "receipt_kind"),
        "status": _payload_str(payload, "status"),
        "thread_id": _payload_str(payload, "thread_id") or "main",
        "source_event_id": _payload_str(payload, "source_event_id"),
        "source_event_type": _payload_str(payload, "source_event_type"),
        "receipt_ref": _payload_str(payload, "receipt_ref"),
        "receipt_digest": _payload_str(payload, "receipt_digest"),
        "artifact_ref": _payload_str(payload, "artifact_ref"),
        "artifact_digest": _payload_str(payload, "artifact_digest"),
        "revision": int(payload.get("revision") or 0),
        "task_id": _payload_str(payload, "task_id") or str(event.task_id or ""),
        "workflow_run_id": _payload_str(payload, "workflow_run_id"),
        "delivery_id": _payload_str(payload, "delivery_id"),
        "links": (
            payload.get("links")
            if isinstance(payload.get("links"), dict)
            else {}
        ),
        "idempotency_key": _payload_str(payload, "idempotency_key"),
        "origin_binding": (
            payload.get("origin_binding")
            if isinstance(payload.get("origin_binding"), dict)
            else {}
        ),
        "ts": event.ts,
    })


def _apply_history_cleared(channel: dict[str, Any], event: ZfEvent, payload: dict[str, Any]) -> None:
    channel["messages"] = {}
    channel["threads"] = {}
    channel["read_state"] = {}
    channel["pinned_message_ids"] = {}
    channel["attention"] = []
    channel["mentions_detected"] = []
    channel["routes"] = []
    channel["reply_requests"] = {}
    channel["typing"] = {}
    channel["agent_session_runs"] = {}
    channel["attachments"] = {}
    channel["artifacts"] = {}
    channel["context_packs"] = {}
    channel["handoffs"] = []
    channel["state_updates"] = []
    channel["synthesis_requests"] = []
    channel["question_dedup_requests"] = []
    channel["history_cleared_at"] = event.ts
    channel["history_clear_event_id"] = event.id
    channel["history_clear_reason"] = _payload_str(payload, "reason")


def _apply_mention(channel: dict[str, Any], event: ZfEvent, payload: dict[str, Any]) -> None:
    item = {
        "event_id": event.id,
        "thread_id": _payload_str(payload, "thread_id") or "main",
        "message_id": _payload_str(payload, "message_id"),
        "member_id": _payload_str(payload, "member_id"),
        "target_member_id": _payload_str(payload, "target_member_id"),
        "source": _payload_str(payload, "source"),
        "ts": event.ts,
    }
    channel["mentions_detected"].append(redact_obj(item))


def _apply_route_defaulted(channel: dict[str, Any], event: ZfEvent, payload: dict[str, Any]) -> None:
    item = {
        "event_id": event.id,
        "thread_id": _payload_str(payload, "thread_id") or "main",
        "message_id": _payload_str(payload, "message_id"),
        "member_id": _payload_str(payload, "member_id"),
        "target_member_id": _payload_str(payload, "target_member_id"),
        "default_responder_id": _payload_str(payload, "default_responder_id"),
        "routing_reason": _payload_str(payload, "routing_reason") or "default_responder",
        "source": _payload_str(payload, "source"),
        "ts": event.ts,
    }
    channel["routes"].append(redact_obj(item))


def _apply_route_blocked(channel: dict[str, Any], event: ZfEvent, payload: dict[str, Any]) -> None:
    item = {
        "event_id": event.id,
        "thread_id": _payload_str(payload, "thread_id") or "main",
        "message_id": _payload_str(payload, "message_id"),
        "member_id": _payload_str(payload, "member_id"),
        "target_member_id": "",
        "routing_reason": "blocked",
        "reason": _payload_str(payload, "reason"),
        "source": _payload_str(payload, "source"),
        "ts": event.ts,
    }
    channel["routes"].append(redact_obj(item))


def _apply_reply(channel: dict[str, Any], event: ZfEvent, payload: dict[str, Any]) -> None:
    request_id = _payload_str(payload, "request_id") or event.id
    status = _reply_status(event.type, payload)
    item = channel["reply_requests"].setdefault(request_id, {
        "request_id": request_id,
        "event_id": event.id,
        "created_at": event.ts,
    })
    incoming_generation = _payload_int(payload.get("run_generation"), _payload_int(item.get("run_generation"), 1))
    current_generation = _payload_int(item.get("run_generation"), 1)
    if (
        event.type.endswith((".started", ".completed", ".failed"))
        and "run_generation" in payload
        and incoming_generation < current_generation
    ):
        item.setdefault("stale_events", []).append(redact_obj({
            "event_id": event.id,
            "type": event.type,
            "run_generation": incoming_generation,
            "current_generation": current_generation,
            "reason": _payload_str(payload, "reason") or "stale run generation",
            "ts": event.ts,
        }))
        return
    if incoming_generation > current_generation:
        item.pop("started_at", None)
        item.pop("ended_at", None)
    run_id = (
        _payload_str(payload, "run_id")
        or _payload_str(payload, "provider_run_id")
        or str(item.get("run_id") or item.get("provider_run_id") or "")
    )
    provider_run_id = (
        _payload_str(payload, "provider_run_id")
        or _payload_str(payload, "run_id")
        or str(item.get("provider_run_id") or item.get("run_id") or "")
    )
    item.update({
        "event_id": event.id,
        "type": event.type,
        "thread_id": _payload_str(payload, "thread_id") or str(item.get("thread_id") or "main"),
        "message_id": _payload_str(payload, "message_id") or str(item.get("message_id") or ""),
        "member_id": _payload_str(payload, "member_id") or str(item.get("member_id") or ""),
        "target_member_id": _payload_str(payload, "target_member_id") or str(item.get("target_member_id") or ""),
        "task_id": event.task_id or _payload_str(payload, "task_id") or str(item.get("task_id") or ""),
        "status": status,
        "queue_state": _payload_str(payload, "queue_state") or str(item.get("queue_state") or ""),
        "context_pack_id": _payload_str(payload, "context_pack_id") or str(item.get("context_pack_id") or ""),
        "routing_reason": _payload_str(payload, "routing_reason") or str(item.get("routing_reason") or ""),
        "member_type": _payload_str(payload, "member_type") or str(item.get("member_type") or ""),
        "backend": _payload_str(payload, "backend") or str(item.get("backend") or ""),
        "provider": _payload_str(payload, "provider") or str(item.get("provider") or ""),
        "provider_binding_id": (
            _payload_str(payload, "provider_binding_id")
            or str(item.get("provider_binding_id") or "")
        ),
        "channel_role": _payload_str(payload, "channel_role") or str(item.get("channel_role") or ""),
        "visibility_profile": (
            _payload_str(payload, "visibility_profile")
            or str(item.get("visibility_profile") or "")
        ),
        "worker_session_id": _payload_str(payload, "worker_session_id") or str(item.get("worker_session_id") or ""),
        "provider_session_id": (
            _payload_str(payload, "provider_session_id")
            or str(item.get("provider_session_id") or "")
        ),
        "run_id": run_id,
        "provider_run_id": provider_run_id,
        "run_generation": incoming_generation,
        "reason": _payload_str(payload, "reason") or str(item.get("reason") or ""),
        "continuation_count": _payload_int(
            payload.get("continuation_count"),
            _payload_int(item.get("continuation_count"), 0),
        ),
        "completion_reason": (
            _payload_str(payload, "completion_reason")
            or str(item.get("completion_reason") or "")
        ),
        # P0.2: thread the originating handoff event id through projection so
        # channel_reply_remediation.emit_channel_reply_failed can wire failure back to
        # channel.handoff.failed via this field on the reply_request snapshot.
        "handoff_request_event_id": (
            _payload_str(payload, "handoff_request_event_id")
            or str(item.get("handoff_request_event_id") or "")
        ),
        "updated_at": event.ts,
    })
    if status in {"running", "started"}:
        item["started_at"] = str(item.get("started_at") or event.ts)
        item.pop("ended_at", None)
    elif status in {"completed", "failed", "rejected", "escalated"}:
        item["ended_at"] = event.ts
    if not str(item.get("run_id") or "") and str(item.get("target_member_id") or ""):
        item["run_id"] = stable_provider_run_id(
            channel_id=str(channel.get("channel_id") or ""),
            thread_id=str(item.get("thread_id") or "main"),
            request_id=request_id,
            target_member_id=str(item.get("target_member_id") or ""),
        )
    if not str(item.get("provider_run_id") or ""):
        item["provider_run_id"] = str(item.get("run_id") or "")


def _apply_typing(channel: dict[str, Any], event: ZfEvent, payload: dict[str, Any]) -> None:
    thread_id = _payload_str(payload, "thread_id") or "main"
    member_id = (
        _payload_str(payload, "target_member_id")
        or _payload_str(payload, "member_id")
        or _payload_str(payload, "actor")
    )
    if not member_id:
        return
    key = f"{thread_id}:{member_id}"
    typing = event.type.endswith(".started")
    channel["typing"][key] = redact_obj({
        "thread_id": thread_id,
        "member_id": member_id,
        "target_member_id": member_id,
        "run_id": _run_id_from_payload(channel, payload),
        "request_id": _payload_str(payload, "request_id"),
        "status": "typing" if typing else "idle",
        "typing": typing,
        "reason": _payload_str(payload, "reason"),
        "event_id": event.id,
        "started_at": event.ts if typing else str(channel["typing"].get(key, {}).get("started_at") or ""),
        "stopped_at": event.ts if not typing else "",
        "updated_at": event.ts,
    })


def _apply_channel_stream(channel: dict[str, Any], event: ZfEvent, payload: dict[str, Any]) -> None:
    run = _session_run(channel, event, payload)
    if not run:
        return
    if event.type.endswith(".started"):
        _upsert_session_part(
            run,
            event,
            payload,
            default_part_id="text",
            default_kind="text",
            state="streaming",
            append=False,
        )
    elif event.type.endswith(".delta"):
        _upsert_session_part(
            run,
            event,
            payload,
            default_part_id="text",
            default_kind="text",
            state="streaming",
            append=True,
        )
    elif event.type.endswith(".ended"):
        _upsert_session_part(
            run,
            event,
            payload,
            default_part_id=_payload_str(payload, "part_id") or "text",
            default_kind=_payload_str(payload, "kind") or "text",
            state="completed",
            append=False,
        )
        if str(run.get("status") or "") not in {"failed", "cancelled", "completed"}:
            run["status"] = _payload_str(payload, "status") or "completed"
            run["ended_at"] = event.ts
            run["updated_at"] = event.ts


def _apply_agent_session(channel: dict[str, Any], event: ZfEvent, payload: dict[str, Any]) -> None:
    run = _session_run(channel, event, payload)
    if not run:
        return
    if event.type.startswith("agent.session.part."):
        state = "streaming"
        if event.type.endswith(".completed"):
            state = "completed"
        elif event.type.endswith(".failed"):
            state = "failed"
        _upsert_session_part(
            run,
            event,
            payload,
            default_part_id=_payload_str(payload, "part_id") or "part",
            default_kind=_payload_str(payload, "kind") or _payload_str(payload, "message_type") or "status",
            state=state,
            append=event.type.endswith(".delta"),
        )
        return
    status = _session_run_status(event.type, payload)
    run["status"] = status
    run["updated_at"] = event.ts
    if isinstance(payload.get("usage"), dict):
        run["usage"] = redact_obj(payload.get("usage"))
    if isinstance(payload.get("usage_accounting"), dict):
        run["usage_accounting"] = redact_obj(
            payload.get("usage_accounting")
        )
    if status == "streaming":
        run["started_at"] = run.get("started_at") or event.ts
        _upsert_session_part(
            run,
            event,
            payload,
            default_part_id="status-started",
            default_kind="status",
            state="submitted",
            append=False,
        )
    elif status in {"completed", "failed", "cancelled"}:
        run["ended_at"] = event.ts
        _upsert_session_part(
            run,
            event,
            payload,
            default_part_id=f"status-{status}",
            default_kind="error" if status == "failed" else "status",
            state=status,
            append=False,
        )


def _session_run(channel: dict[str, Any], event: ZfEvent, payload: dict[str, Any]) -> dict[str, Any]:
    run_id = _run_id_from_payload(channel, payload)
    if not run_id:
        return {}
    run = channel["agent_session_runs"].setdefault(run_id, {
        "run_id": run_id,
        "provider_run_id": run_id,
        "parts": {},
        "source_event_ids": [],
        "created_at": event.ts,
    })
    source_event_ids = run.setdefault("source_event_ids", [])
    if event.id not in source_event_ids:
        source_event_ids.append(event.id)
    status = _session_run_status(event.type, payload) or str(run.get("status") or "streaming")
    run.update({
        "run_id": run_id,
        "provider_run_id": _payload_str(payload, "provider_run_id") or run_id,
        "run_generation": _payload_int(payload.get("run_generation"), _payload_int(run.get("run_generation"), 1)),
        "request_id": _payload_str(payload, "request_id") or str(run.get("request_id") or ""),
        "thread_id": _payload_str(payload, "thread_id") or str(run.get("thread_id") or "main"),
        "message_id": _payload_str(payload, "message_id") or str(run.get("message_id") or ""),
        "target_member_id": (
            _payload_str(payload, "target_member_id")
            or _payload_str(payload, "member_id")
            or str(run.get("target_member_id") or "")
        ),
        "member_id": _payload_str(payload, "member_id") or str(run.get("member_id") or ""),
        "provider": _payload_str(payload, "provider") or str(run.get("provider") or ""),
        "backend": _payload_str(payload, "backend") or str(run.get("backend") or ""),
        "provider_session_id": (
            _payload_str(payload, "provider_session_id")
            or str(run.get("provider_session_id") or "")
        ),
        "status": status,
        "reason": _payload_str(payload, "reason") or str(run.get("reason") or ""),
        "updated_at": event.ts,
    })
    if status == "streaming":
        run["started_at"] = str(run.get("started_at") or event.ts)
    if status in {"completed", "failed", "cancelled"}:
        run["ended_at"] = event.ts
    return run


def _upsert_session_part(
    run: dict[str, Any],
    event: ZfEvent,
    payload: dict[str, Any],
    *,
    default_part_id: str,
    default_kind: str,
    state: str,
    append: bool,
) -> None:
    parts = run.setdefault("parts", {})
    part_id = _payload_str(payload, "part_id") or default_part_id
    if not part_id:
        part_id = "part"
    part = parts.setdefault(part_id, {
        "part_id": part_id,
        "run_id": run.get("run_id", ""),
        "created_at": event.ts,
        "content": "",
    })
    content = (
        _payload_text(payload, "delta")
        or _payload_text(payload, "content")
        or _payload_text(payload, "text")
        or _payload_text(payload, "summary")
        or _payload_str(payload, "status")
        or _payload_str(payload, "reason")
    )
    if append and content:
        part["content"] = f"{part.get('content', '')}{content}"
    elif content:
        part["content"] = content
    part.update({
        "part_id": part_id,
        "run_id": str(run.get("run_id") or ""),
        "kind": _payload_str(payload, "kind") or _payload_str(payload, "message_type") or _payload_str(payload, "type") or default_kind,
        "state": _payload_str(payload, "state") or state,
        "title": _payload_str(payload, "title") or _session_part_title(default_kind, payload),
        "summary": _payload_str(payload, "summary") or str(part.get("summary") or ""),
        "seq": _payload_int(payload.get("seq"), _payload_int(part.get("seq"), 0)),
        "tool_call_id": _payload_str(payload, "tool_call_id"),
        "tool_name": _payload_str(payload, "tool"),
        "refs": payload.get("refs") if isinstance(payload.get("refs"), dict) else part.get("refs", {}),
        "source_event_id": event.id,
        "updated_at": event.ts,
    })
    run["updated_at"] = event.ts


def _session_run_status(event_type: str, payload: dict[str, Any]) -> str:
    explicit = _payload_str(payload, "status")
    if explicit:
        if explicit in {"started", "running"}:
            return "streaming"
        return explicit
    if event_type.endswith((".started", ".delta")):
        return "streaming"
    if event_type.endswith(".completed") or event_type.endswith(".ended"):
        return "completed"
    if event_type.endswith(".failed"):
        return "failed"
    if event_type.endswith(".cancelled"):
        return "cancelled"
    return ""


def _session_part_title(default_kind: str, payload: dict[str, Any]) -> str:
    kind = _payload_str(payload, "kind") or _payload_str(payload, "message_type") or default_kind
    if kind == "thinking":
        return "Thinking"
    if kind == "tool":
        return _payload_str(payload, "tool") or "Tool"
    if kind == "error":
        return "Error"
    if kind == "text":
        return "Response"
    return "Status"


def _run_id_from_payload(channel: dict[str, Any], payload: dict[str, Any]) -> str:
    run_id = _payload_str(payload, "run_id") or _payload_str(payload, "provider_run_id")
    if run_id:
        return run_id
    request_id = _payload_str(payload, "request_id")
    target_member_id = _payload_str(payload, "target_member_id") or _payload_str(payload, "member_id")
    if request_id and target_member_id:
        return stable_provider_run_id(
            channel_id=str(channel.get("channel_id") or ""),
            thread_id=_payload_str(payload, "thread_id") or "main",
            request_id=request_id,
            target_member_id=target_member_id,
        )
    return ""


def _apply_attachment(channel: dict[str, Any], event: ZfEvent, payload: dict[str, Any]) -> None:
    attachment_id = _payload_str(payload, "attachment_id") or _payload_str(payload, "artifact_id") or event.id
    channel["attachments"][attachment_id] = redact_obj({
        "attachment_id": attachment_id,
        "artifact_id": _payload_str(payload, "artifact_id"),
        "event_id": event.id,
        "thread_id": _payload_str(payload, "thread_id") or "main",
        "message_id": _payload_str(payload, "message_id"),
        "member_id": _payload_str(payload, "member_id"),
        "name": _payload_str(payload, "name") or _payload_str(payload, "filename"),
        "mime": _payload_str(payload, "mime") or _payload_str(payload, "content_type"),
        "size": _payload_int(payload.get("size"), _payload_int(payload.get("bytes"), 0)),
        "hash": _payload_str(payload, "hash") or _payload_str(payload, "sha256"),
        "uri": _payload_str(payload, "uri"),
        "status": "uploaded",
        "refs": payload.get("refs") if isinstance(payload.get("refs"), dict) else {},
        "ts": event.ts,
    })


def _apply_artifact(channel: dict[str, Any], event: ZfEvent, payload: dict[str, Any]) -> None:
    artifact_id = _payload_str(payload, "artifact_id") or _payload_str(payload, "attachment_id") or event.id
    status = event.type.rsplit(".", 1)[-1]
    reason = _payload_str(payload, "reason")
    if (
        status == "proposed"
        and _payload_str(payload, "path")
        and not (_payload_str(payload, "hash") or _payload_str(payload, "sha256"))
        and not isinstance(payload.get("provenance"), dict)
    ):
        status = "rejected"
        reason = reason or "artifact proposal missing provenance"
    channel["artifacts"][artifact_id] = redact_obj({
        "artifact_id": artifact_id,
        "event_id": event.id,
        "type": event.type,
        "thread_id": _payload_str(payload, "thread_id") or "main",
        "message_id": _payload_str(payload, "message_id"),
        "member_id": _payload_str(payload, "member_id"),
        "target_member_id": _payload_str(payload, "target_member_id"),
        "run_id": _payload_str(payload, "run_id"),
        "request_id": _payload_str(payload, "request_id"),
        "task_id": event.task_id or _payload_str(payload, "task_id"),
        "name": _payload_str(payload, "name") or _payload_str(payload, "filename"),
        "kind": _payload_str(payload, "kind") or _payload_str(payload, "mime"),
        "path": _payload_str(payload, "path"),
        "uri": _payload_str(payload, "uri"),
        "hash": _payload_str(payload, "hash") or _payload_str(payload, "sha256"),
        "mime": _payload_str(payload, "mime") or _payload_str(payload, "content_type"),
        "size": _payload_int(payload.get("size"), _payload_int(payload.get("bytes"), 0)),
        "summary": _payload_str(payload, "summary"),
        "status": status,
        "reason": reason,
        "provenance": payload.get("provenance") if isinstance(payload.get("provenance"), dict) else {},
        "refs": payload.get("refs") if isinstance(payload.get("refs"), dict) else {},
        "ts": event.ts,
    })


def _apply_context_pack(
    channel: dict[str, Any],
    event: ZfEvent,
    payload: dict[str, Any],
    *,
    state_dir: Path | None = None,
) -> None:
    if state_dir is not None and event.type == "channel.context_pack.built":
        payload = hydrate_channel_context_pack_payload(state_dir, payload)
    context_pack_id = _payload_str(payload, "context_pack_id") or event.id
    channel["context_packs"][context_pack_id] = redact_obj({
        "context_pack_id": context_pack_id,
        "event_id": event.id,
        "type": event.type,
        "thread_id": _payload_str(payload, "thread_id") or "main",
        "target_member_id": _payload_str(payload, "target_member_id"),
        "trigger_message_id": _payload_str(payload, "trigger_message_id"),
        "status": "rejected" if event.type.endswith(".rejected") else "built",
        "visibility_profile": _payload_str(payload, "visibility_profile"),
        "channel_role": _payload_str(payload, "channel_role"),
        "routing_reason": _payload_str(payload, "routing_reason"),
        "role_context_ref": _payload_str(payload, "role_context_ref"),
        "skill_refs": _string_list(payload.get("skill_refs")),
        "resolved_skill_refs": payload.get("resolved_skill_refs") if isinstance(payload.get("resolved_skill_refs"), list) else [],
        "skill_metadata": payload.get("skill_metadata") if isinstance(payload.get("skill_metadata"), list) else [],
        "collaboration_contract": payload.get("collaboration_contract") if isinstance(payload.get("collaboration_contract"), dict) else {},
        "role_definition": payload.get("role_definition") if isinstance(payload.get("role_definition"), dict) else {},
        "summary": _payload_str(payload, "summary"),
        "message_refs": payload.get("message_refs") if isinstance(payload.get("message_refs"), list) else [],
        "question_ledger": payload.get("question_ledger") if isinstance(payload.get("question_ledger"), list) else [],
        "question_ledger_digest": _payload_str(payload, "question_ledger_digest"),
        "question_ledger_complete": bool(payload.get("question_ledger_complete")),
        "question_frontier": payload.get("question_frontier") if isinstance(payload.get("question_frontier"), list) else [],
        "question_frontier_digest": _payload_str(payload, "question_frontier_digest"),
        "owner_questionnaire": payload.get("owner_questionnaire") if isinstance(payload.get("owner_questionnaire"), list) else [],
        "owner_questionnaire_digest": _payload_str(payload, "owner_questionnaire_digest"),
        "cross_review_index": payload.get("cross_review_index") if isinstance(payload.get("cross_review_index"), list) else [],
        "cross_review_index_digest": _payload_str(payload, "cross_review_index_digest"),
        "contribution_index": payload.get("contribution_index") if isinstance(payload.get("contribution_index"), list) else [],
        "contribution_index_digest": _payload_str(payload, "contribution_index_digest"),
        "semantic_source_required": bool(
            payload.get("semantic_source_required")
        ),
        "semantic_source_stage": _payload_str(
            payload, "semantic_source_stage"
        ),
        "semantic_source_complete": bool(
            payload.get("semantic_source_complete")
        ),
        "semantic_source_reason": _payload_str(
            payload, "semantic_source_reason"
        ),
        "semantic_source_manifest": (
            payload.get("semantic_source_manifest")
            if isinstance(payload.get("semantic_source_manifest"), list)
            else []
        ),
        "semantic_source_manifest_digest": _payload_str(
            payload, "semantic_source_manifest_digest"
        ),
        "semantic_source_required_digests": _string_list(
            payload.get("semantic_source_required_digests")
        ),
        "artifact_refs": payload.get("artifact_refs") if isinstance(payload.get("artifact_refs"), list) else [],
        "report_refs": payload.get("report_refs") if isinstance(payload.get("report_refs"), list) else [],
        "limits": payload.get("limits") if isinstance(payload.get("limits"), dict) else {},
        "context_pack_ref": _payload_str(payload, "context_pack_ref"),
        "context_pack_sha256": _payload_str(payload, "context_pack_sha256"),
        "context_pack_byte_count": payload.get("context_pack_byte_count") or 0,
        "reason": _payload_str(payload, "reason"),
        "ts": event.ts,
    })


def _apply_handoff(channel: dict[str, Any], event: ZfEvent, payload: dict[str, Any]) -> None:
    channel["handoffs"].append(redact_obj({
        "event_id": event.id,
        "type": event.type,
        "thread_id": _payload_str(payload, "thread_id") or "main",
        "message_id": _payload_str(payload, "message_id"),
        "member_id": _payload_str(payload, "member_id"),
        "target_member_id": _payload_str(payload, "target_member_id"),
        "status": event.type.rsplit(".", 1)[-1],
        "reason": _payload_str(payload, "reason"),
        "depth": _payload_str(payload, "depth"),
        "round": _payload_str(payload, "round"),
        "ts": event.ts,
    }))


def _apply_state_update(channel: dict[str, Any], event: ZfEvent, payload: dict[str, Any]) -> None:
    channel["state_updates"].append(redact_obj({
        "event_id": event.id,
        "thread_id": _payload_str(payload, "thread_id") or "main",
        "status": _payload_str(payload, "status"),
        "task_id": event.task_id or _payload_str(payload, "task_id"),
        "run_id": _payload_str(payload, "run_id"),
        "summary": _payload_str(payload, "summary"),
        "refs": payload.get("refs") if isinstance(payload.get("refs"), dict) else {},
        "ts": event.ts,
    }))


def _apply_discussion_mode(channel: dict[str, Any], event: ZfEvent, payload: dict[str, Any]) -> None:
    discussion = channel["discussion"]
    raw_mode = _payload_str(payload, "mode") or discussion.get(
        "mode", "conversation"
    )
    product_mode = normalize_product_discussion_mode(raw_mode)
    discussion["mode"] = product_mode
    discussion["engine_mode"] = discussion_engine_mode(raw_mode)
    discussion["legacy_mode"] = (
        raw_mode if raw_mode != product_mode else ""
    )
    # 2026-07-03: _empty_channel() bootstraps discussion["max_rounds"] = 6
    # before any event is processed, so falling back to
    # `discussion.get("max_rounds")` here can never be distinguished from
    # "never explicitly set" — it always short-circuits the OR chain before
    # the roster-scaled default below is ever reached. Only an explicit
    # value on *this* event should override the recomputed default.
    default_max_rounds = default_debate_max_rounds(len(channel.get("members") or []))
    try:
        discussion["max_rounds"] = int(payload.get("max_rounds") or default_max_rounds)
    except (TypeError, ValueError):
        discussion["max_rounds"] = default_max_rounds
    # 2026-07-03 (operator ruling): the router's round-budget guard only
    # fires for an EXPLICITLY configured max_rounds. The projected default
    # stays (the discussion driver still uses it as a synthesis fallback),
    # but it must be distinguishable from an operator-set cap.
    try:
        discussion["max_rounds_explicit"] = int(payload.get("max_rounds") or 0) > 0
    except (TypeError, ValueError):
        discussion["max_rounds_explicit"] = False
    if payload.get("max_parallel_replies") is not None:
        try:
            discussion["max_parallel_replies"] = max(
                1,
                min(int(payload.get("max_parallel_replies")), 64),
            )
        except (TypeError, ValueError):
            pass
    if isinstance(payload.get("speaker_policy"), dict):
        discussion["speaker_policy"] = redact_obj(payload.get("speaker_policy"))
    if "default_responder_id" in payload:
        discussion["default_responder_id"] = _payload_str(payload, "default_responder_id")
    if isinstance(payload.get("provider_capabilities"), dict):
        discussion["provider_capabilities"] = redact_obj(payload.get("provider_capabilities"))
    if payload.get("participants") is not None:
        discussion["participants"] = _string_list(payload.get("participants"))
    if "synthesizer" in payload:
        discussion["synthesizer"] = _payload_str(payload, "synthesizer")
    if payload.get("max_relay_depth") is not None:
        try:
            discussion["max_relay_depth"] = int(payload.get("max_relay_depth"))
        except (TypeError, ValueError):
            pass
    if isinstance(payload.get("phase_deadline_seconds"), dict):
        discussion["phase_deadline_seconds"] = redact_obj(payload.get("phase_deadline_seconds"))
    discussion["updated_at"] = event.ts
    discussion["event_id"] = event.id


def _channel_agent_member_ids(channel: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for member in channel["members"].values():
        if str(member.get("member_type") or "") in {"provider_agent", "persona_agent", "persona"}:
            member_id = str(member.get("member_id") or "")
            if member_id:
                ids.add(member_id)
    return ids


def _question_activity(channel: dict[str, Any], event: ZfEvent, thread_id: str) -> None:
    channel["question_activity"].append({
        "thread_id": thread_id,
        "event_id": event.id,
        "type": event.type,
        "ts": event.ts,
    })


def _apply_discussion_started(channel: dict[str, Any], event: ZfEvent, payload: dict[str, Any]) -> None:
    thread_id = _payload_str(payload, "thread_id") or "main"
    state = _payload_str(payload, "state") or "phase1_blind"
    channel["discussions"][thread_id] = {
        "thread_id": thread_id,
        "discussion_id": (
            _payload_str(payload, "discussion_id")
            or f"discussion-{event.id.removeprefix('evt-')}"
        ),
        "revision": int(payload.get("revision") or 1),
        "context_digest": _payload_str(payload, "context_digest"),
        "product_mode": normalize_product_discussion_mode(
            payload.get("product_mode")
            or channel.get("discussion", {}).get("mode")
        ),
        "state": state,
        "trigger": _payload_str(payload, "trigger"),
        "roster": _string_list(payload.get("roster")),
        "synthesizer": _payload_str(payload, "synthesizer"),
        "requirement_message_id": _payload_str(payload, "requirement_message_id"),
        "started_event_id": event.id,
        "started_at": event.ts,
        "phase_changed_at": event.ts,
        "phase_reason": "discussion_started",
    }


def _apply_discussion_continued(
    channel: dict[str, Any],
    event: ZfEvent,
    payload: dict[str, Any],
) -> None:
    thread_id = _payload_str(payload, "thread_id") or "main"
    session = channel["discussions"].setdefault(
        thread_id,
        {"thread_id": thread_id, "state": "active"},
    )
    session["discussion_id"] = (
        _payload_str(payload, "discussion_id")
        or session.get("discussion_id", "")
    )
    session["revision"] = int(
        payload.get("revision") or session.get("revision") or 1
    )
    session["context_digest"] = (
        _payload_str(payload, "context_digest")
        or session.get("context_digest", "")
    )
    if payload.get("roster") is not None:
        session["roster"] = _string_list(payload.get("roster"))
    if _payload_str(payload, "requirement_message_id"):
        session["requirement_message_id"] = _payload_str(
            payload, "requirement_message_id"
        )
    if _payload_str(payload, "adaptive_next_round_message_id"):
        session["adaptive_next_round_message_id"] = _payload_str(
            payload, "adaptive_next_round_message_id"
        )
    if _payload_str(payload, "synthesis_event_id"):
        session["last_next_round_synthesis_event_id"] = _payload_str(
            payload, "synthesis_event_id"
        )
    if _payload_str(payload, "synthesis_request_id"):
        session["last_next_round_synthesis_request_id"] = _payload_str(
            payload, "synthesis_request_id"
        )
    if _payload_str(payload, "next_round_id"):
        session["last_next_round_id"] = _payload_str(payload, "next_round_id")
    session["continued_event_id"] = event.id
    session["phase_changed_at"] = event.ts
    if str(session.get("state") or "") == "idle":
        session["state"] = "active"


def _apply_next_round(
    channel: dict[str, Any],
    event: ZfEvent,
    payload: dict[str, Any],
) -> None:
    channel["next_rounds"].append({
        "event_id": event.id,
        "type": event.type,
        "thread_id": _payload_str(payload, "thread_id") or "main",
        "synthesis_event_id": _payload_str(payload, "synthesis_event_id"),
        "synthesis_request_id": _payload_str(payload, "synthesis_request_id"),
        "discussion_id": _payload_str(payload, "discussion_id"),
        "expected_revision": int(payload.get("expected_revision") or 0),
        "reason": _payload_str(payload, "reason"),
        "objective": _payload_str(payload, "objective"),
        "target_member_ids": _string_list(payload.get("target_member_ids")),
        "source": _payload_str(payload, "source"),
    })


def _apply_discussion_phase(channel: dict[str, Any], event: ZfEvent, payload: dict[str, Any]) -> None:
    thread_id = _payload_str(payload, "thread_id") or "main"
    session = channel["discussions"].setdefault(thread_id, {"thread_id": thread_id, "state": "idle"})
    phase = _payload_str(payload, "phase")
    if phase in {
        "active",
        "phase1_blind",
        "phase2_relay",
        "phase3_synthesis",
        "idle",
    }:
        session["state"] = phase
    session["phase_reason"] = _payload_str(payload, "reason")
    session["phase_event_id"] = event.id
    session["phase_changed_at"] = event.ts
    if _payload_str(payload, "reason") == "consensus_blocked":
        consensus = channel["consensus"].get(thread_id)
        if isinstance(consensus, dict):
            consensus["reopened"] = True


def _apply_discussion_closed(channel: dict[str, Any], event: ZfEvent, payload: dict[str, Any]) -> None:
    thread_id = _payload_str(payload, "thread_id") or "main"
    session = channel["discussions"].setdefault(thread_id, {"thread_id": thread_id})
    session["state"] = "idle"
    session["last_outcome"] = _payload_str(payload, "outcome")
    session["closed_event_id"] = event.id
    session["closed_at"] = event.ts


def _apply_relay(channel: dict[str, Any], event: ZfEvent, payload: dict[str, Any]) -> None:
    channel["relay_events"].append(redact_obj({
        "event_id": event.id,
        "type": event.type,
        "thread_id": _payload_str(payload, "thread_id") or "main",
        "message_id": _payload_str(payload, "message_id"),
        "member_id": _payload_str(payload, "member_id"),
        "targets": _string_list(payload.get("targets")),
        "relay_depth": payload.get("relay_depth") or 0,
        "reason": _payload_str(payload, "reason"),
        "ts": event.ts,
    }))


def _apply_question(channel: dict[str, Any], event: ZfEvent, payload: dict[str, Any]) -> None:
    thread_id = _payload_str(payload, "thread_id") or "main"
    question_id = _payload_str(payload, "question_id") or event.id
    if event.type == "channel.question.opened":
        existing = channel["open_questions"].get(question_id)
        if isinstance(existing, dict):
            return
        identity = question_text_identity(payload.get("question"))
        if identity and any(
            str(item.get("thread_id") or "main") == thread_id
            and question_text_identity(item.get("question")) == identity
            for item in channel["open_questions"].values()
            if isinstance(item, dict)
        ):
            return
        normalized, error = normalize_question_payload(
            payload,
            question_id=question_id,
            question=_payload_str(payload, "question"),
            asked_by=_payload_str(payload, "asked_by") or event.actor,
            member_ids=channel.get("members", {}).keys(),
        )
        candidate = {
            **normalized,
            "thread_id": thread_id,
            "status": "open",
        }
        graph_error = error or validate_question_graph([
            *channel["open_questions"].values(),
            candidate,
        ])
        if graph_error:
            channel["question_graph_rejections"].append({
                "question_id": question_id,
                "thread_id": thread_id,
                "event_id": event.id,
                "reason": graph_error,
                "ts": event.ts,
            })
            return
        channel["open_questions"][question_id] = redact_obj({
            "question_id": question_id,
            "thread_id": thread_id,
            **normalized,
            "status": "open",
            "opened_event_id": event.id,
            "ts": event.ts,
        })
        _question_activity(channel, event, thread_id)
        return
    question = channel["open_questions"].get(question_id)
    if not isinstance(question, dict):
        return
    if event.type == "channel.question.updated":
        if str(question.get("status") or "") != "open":
            return
        normalized, error = normalize_question_payload(
            {**question, **payload},
            question_id=question_id,
            question=str(question.get("question") or ""),
            asked_by=str(question.get("asked_by") or event.actor),
            member_ids=channel.get("members", {}).keys(),
        )
        candidate = {**question, **normalized}
        records = [
            candidate if key == question_id else value
            for key, value in channel["open_questions"].items()
        ]
        graph_error = error or validate_question_graph(records)
        if graph_error:
            channel["question_graph_rejections"].append({
                "question_id": question_id,
                "thread_id": thread_id,
                "event_id": event.id,
                "reason": graph_error,
                "ts": event.ts,
            })
            return
        question.update(redact_obj({
            **normalized,
            "updated_event_id": event.id,
            "updated_at": event.ts,
        }))
        _question_activity(channel, event, thread_id)
        return
    if event.type == "channel.question.resolved":
        resolution = _payload_str(payload, "resolution")
        if resolution not in {
            "answered",
            "assumption",
            "out_of_scope",
            "evidence",
        }:
            return
        resolved_by = _payload_str(payload, "resolved_by") or event.actor
        agent_ids = _channel_agent_member_ids(channel)
        if resolution == "answered" and (event.actor in agent_ids or resolved_by in agent_ids):
            # doc 122 §12-4: `answered` is owner-only; agent attempts leave the
            # question open and are surfaced for a rejection event.
            channel["rejected_resolutions"].append({
                "question_id": question_id,
                "thread_id": thread_id,
                "event_id": event.id,
                "actor": event.actor,
                "ts": event.ts,
            })
            _question_activity(channel, event, thread_id)
            return
        if resolution == "evidence":
            evidence_refs = _string_list(payload.get("evidence_refs"))
            target_member_id = str(
                question.get("target_member_id") or ""
            )
            if (
                str(question.get("kind") or "") != "fact"
                or not evidence_refs
                or resolved_by not in agent_ids
                or (
                    target_member_id not in {"", "owner"}
                    and resolved_by != target_member_id
                )
            ):
                channel["rejected_resolutions"].append({
                    "question_id": question_id,
                    "thread_id": thread_id,
                    "event_id": event.id,
                    "actor": event.actor,
                    "reason": "invalid_evidence_resolution",
                    "ts": event.ts,
                })
                _question_activity(channel, event, thread_id)
                return
            question["evidence_refs"] = evidence_refs
        question["status"] = "resolved"
        question["resolution"] = resolution
        question["resolved_by"] = resolved_by
        question["answer"] = _payload_str(payload, "answer")
        question["risk_note"] = _payload_str(payload, "risk_note")
        question["resolved_event_id"] = event.id
        _question_activity(channel, event, thread_id)
        return
    if event.type == "channel.question.merged":
        into = _payload_str(payload, "into_question_id")
        target = channel["open_questions"].get(into)
        if (
            question.get("status") != "open"
            or not isinstance(target, dict)
            or target.get("status") != "open"
            or str(target.get("thread_id") or "main") != thread_id
            or into == question_id
        ):
            return
        question["status"] = "merged"
        question["merged_into"] = into
        question["merged_event_id"] = event.id
        _question_activity(channel, event, thread_id)


def _apply_cross_review(
    channel: dict[str, Any],
    event: ZfEvent,
    payload: dict[str, Any],
) -> None:
    request_id = _payload_str(payload, "request_id") or event.id
    item = channel["cross_reviews"].setdefault(request_id, {
        "request_id": request_id,
    })
    if event.type == "channel.cross_review.requested":
        if item.get("requested_event_id"):
            return
        item.update(redact_obj({
            "thread_id": _payload_str(payload, "thread_id") or "main",
            "question_id": _payload_str(payload, "question_id"),
            "target_member_id": _payload_str(payload, "target_member_id"),
            "prompt": _payload_str(payload, "prompt"),
            "reason": _payload_str(payload, "reason"),
            "source_refs": _string_list(payload.get("source_refs")),
            "dissent": payload.get("dissent") if isinstance(payload.get("dissent"), list) else [],
            "status": "pending",
            "requested_event_id": event.id,
            "ts": event.ts,
        }))
        return
    item.update(redact_obj({
        "status": (
            "completed"
            if event.type == "channel.cross_review.completed"
            else "rejected"
        ),
        "result_event_id": event.id,
        "summary": _payload_str(payload, "summary"),
        "answer": _payload_str(payload, "answer"),
        "reason": _payload_str(payload, "reason"),
        "artifact_ref": _payload_str(payload, "artifact_ref"),
        "artifact_digest": _payload_str(payload, "artifact_digest"),
        "source_refs": _string_list(payload.get("source_refs")),
        "evidence_refs": _string_list(payload.get("evidence_refs")),
        "consumed_message_digests": _string_list(
            payload.get("consumed_message_digests")
        ),
        "source_coverage": payload.get("source_coverage")
        if isinstance(payload.get("source_coverage"), list)
        else [],
        "semantic_source_manifest_digest": _payload_str(
            payload, "semantic_source_manifest_digest"
        ),
        "semantic_coverage_ref": _payload_str(
            payload, "semantic_coverage_ref"
        ),
        "semantic_coverage_digest": _payload_str(
            payload, "semantic_coverage_digest"
        ),
        "next_round": (
            payload.get("next_round")
            if isinstance(payload.get("next_round"), dict)
            else {}
        ),
        "updated_at": event.ts,
    }))


def _apply_question_dedup(
    channel: dict[str, Any],
    event: ZfEvent,
    payload: dict[str, Any],
) -> None:
    request_id = _payload_str(payload, "request_id") or event.id
    existing = next(
        (
            item
            for item in channel["question_dedup_requests"]
            if str(item.get("request_id") or "") == request_id
        ),
        None,
    )
    if event.type == "channel.question.dedup.requested":
        if existing is not None:
            return
        channel["question_dedup_requests"].append(redact_obj({
            "request_id": request_id,
            "event_id": event.id,
            "thread_id": _payload_str(payload, "thread_id") or "main",
            "target_member_id": _payload_str(payload, "target_member_id"),
            "ledger_digest": _payload_str(payload, "ledger_digest"),
            "question_count": payload.get("question_count") or 0,
            "generation": payload.get("generation") or 1,
            "prior_request_id": _payload_str(payload, "prior_request_id"),
            "repair_reason": _payload_str(payload, "repair_reason"),
            "status": "requested",
            "source": _payload_str(payload, "source"),
            "ts": event.ts,
        }))
        return
    if existing is None:
        existing = {
            "request_id": request_id,
            "thread_id": _payload_str(payload, "thread_id") or "main",
        }
        channel["question_dedup_requests"].append(existing)
    existing.update(redact_obj({
        "result_event_id": event.id,
        "status": {
            "channel.question.dedup.applied": "applied",
            "channel.question.dedup.rejected": "rejected",
            "channel.question.dedup.remediation.exhausted": "exhausted",
        }.get(event.type, "rejected"),
        "reason": _payload_str(payload, "reason"),
        "input_ledger_digest": _payload_str(
            payload,
            "input_ledger_digest",
        ),
        "output_ledger_digest": _payload_str(
            payload,
            "output_ledger_digest",
        ),
        "group_count": payload.get("group_count") or 0,
        "merge_count": payload.get("merge_count") or 0,
        "updated_at": event.ts,
    }))


def _apply_question_resolve_rejected(channel: dict[str, Any], event: ZfEvent, payload: dict[str, Any]) -> None:
    channel["question_resolve_rejections"].append({
        "question_id": _payload_str(payload, "question_id"),
        "attempt_event_id": _payload_str(payload, "attempt_event_id"),
        "thread_id": _payload_str(payload, "thread_id") or "main",
        "event_id": event.id,
        "ts": event.ts,
    })


def _apply_questions_frozen(channel: dict[str, Any], event: ZfEvent, payload: dict[str, Any]) -> None:
    thread_id = _payload_str(payload, "thread_id") or "main"
    member_id = _payload_str(payload, "member_id") or event.actor
    if not member_id:
        return
    channel["questions_frozen"].setdefault(thread_id, {})[member_id] = event.ts
    _question_activity(channel, event, thread_id)


def _apply_consensus(channel: dict[str, Any], event: ZfEvent, payload: dict[str, Any]) -> None:
    thread_id = _payload_str(payload, "thread_id") or "main"
    item = channel["consensus"].setdefault(thread_id, {"signed": {}, "blocked": []})
    if event.type == "channel.consensus.proposed":
        item.update({
            "artifact_ref": _payload_str(payload, "artifact_ref"),
            "artifact_digest": _payload_str(payload, "artifact_digest"),
            "required_signers": _string_list(
                payload.get("required_signers")
            ),
            "source_refs": _string_list(payload.get("source_refs")),
            "prd_ref": (
                _payload_str(payload, "prd_ref")
                or _payload_str(payload, "artifact_ref")
            ),
            "prd_digest": (
                _payload_str(payload, "prd_digest")
                or _payload_str(payload, "artifact_digest")
            ),
            "prd_revision": int(payload.get("prd_revision") or 0),
            "readiness_ref": _payload_str(payload, "readiness_ref"),
            "readiness_digest": _payload_str(
                payload, "readiness_digest"
            ),
            "readiness_verdict": _payload_str(
                payload, "readiness_verdict"
            ),
            "implementation_start": (
                payload.get("implementation_start") is True
            ),
            "conclusion_ref": _payload_str(
                payload, "conclusion_ref"
            ),
            "conclusion_digest": _payload_str(
                payload, "conclusion_digest"
            ),
            "owner_actor_ref": _payload_str(
                payload, "owner_actor_ref"
            ),
            "product_mode": normalize_product_discussion_mode(
                payload.get("product_mode")
            ),
            "proposed_by": _payload_str(payload, "proposed_by") or event.actor,
            "proposed_event_id": event.id,
            "signed": {},
            "blocked": [],
            "reached_event_id": "",
            "reopened": False,
            "human_confirmed": False,
            "status": "proposed",
            "revision_pending": False,
            "ts": event.ts,
        })
        return
    if event.type == "channel.consensus.signed":
        member_id = _payload_str(payload, "member_id") or event.actor
        if not member_id:
            return
        if member_id in _channel_agent_member_ids(channel):
            item.setdefault("signed", {})[member_id] = event.ts
        else:
            item["human_confirmed"] = True
            item["human_confirmed_by"] = member_id
            item["risk_accepted"] = payload.get("risk_accepted") is True
            item["confirmed_readiness_ref"] = _payload_str(
                payload, "readiness_ref"
            )
            item["confirmed_readiness_digest"] = _payload_str(
                payload, "readiness_digest"
            )
        return
    if event.type == "channel.consensus.blocked":
        item.setdefault("blocked", []).append({
            "member_id": _payload_str(payload, "member_id") or event.actor,
            "blocker_question_id": _payload_str(payload, "blocker_question_id"),
            "blocker_question": _payload_str(payload, "blocker_question"),
            "dissent": _payload_str(payload, "dissent"),
            "evidence_refs": _string_list(payload.get("evidence_refs")),
            "event_id": event.id,
            "ts": event.ts,
        })
        item["reopened"] = False
        return
    if event.type == "channel.consensus.review.rejected":
        item.setdefault("review_rejections", []).append({
            "member_id": _payload_str(payload, "member_id") or event.actor,
            "review_id": _payload_str(payload, "review_id"),
            "reason": _payload_str(payload, "reason"),
            "event_id": event.id,
            "ts": event.ts,
        })
        return
    if event.type == "channel.consensus.reached":
        for field in (
            "artifact_ref",
            "artifact_digest",
            "prd_ref",
            "prd_digest",
            "readiness_ref",
            "readiness_digest",
            "readiness_verdict",
            "conclusion_ref",
            "conclusion_digest",
            "product_mode",
        ):
            value = _payload_str(payload, field)
            if value:
                item[field] = value
        if isinstance(payload.get("source_refs"), list):
            item["source_refs"] = _string_list(payload.get("source_refs"))
        if payload.get("confirmed_by"):
            item["human_confirmed"] = True
            item["human_confirmed_by"] = _payload_str(
                payload, "confirmed_by"
            )
        if payload.get("risk_accepted") is True:
            item["risk_accepted"] = True
            item["confirmed_readiness_ref"] = _payload_str(
                payload, "readiness_ref"
            )
            item["confirmed_readiness_digest"] = _payload_str(
                payload, "readiness_digest"
            )
        if "implementation_start" in payload:
            item["implementation_start"] = (
                payload.get("implementation_start") is True
            )
        if payload.get("prd_revision") is not None:
            item["prd_revision"] = int(payload.get("prd_revision") or 0)
        item["reached_event_id"] = event.id
        item["status"] = "reached"
        item["confirmed_revision"] = int(
            payload.get("prd_revision")
            or item.get("prd_revision")
            or 0
        )
        item["revision_pending"] = False


def _apply_synthesis_request(channel: dict[str, Any], event: ZfEvent, payload: dict[str, Any]) -> None:
    request_id = _payload_str(payload, "request_id") or event.id
    if event.type == "channel.synthesis.stale_ignored":
        for item in reversed(channel["synthesis_requests"]):
            if str(item.get("request_id") or "") == request_id:
                item.update(redact_obj({
                    "status": "stale_ignored",
                    "reason": _payload_str(payload, "reason"),
                    "stale_event_id": event.id,
                    "source_reply_event_id": _payload_str(
                        payload, "source_reply_event_id"
                    ),
                    "superseded_by_request_id": _payload_str(
                        payload, "superseded_by_request_id"
                    ),
                    "updated_at": event.ts,
                }))
                return
    item = {
        "event_id": event.id,
        "thread_id": _payload_str(payload, "thread_id") or "main",
        "request_id": request_id,
        "target_member_id": _payload_str(payload, "target_member_id"),
        "status": _payload_str(payload, "status") or (
            "blocked"
            if event.type == "channel.synthesis.blocked"
            else "repair_requested"
            if event.type == "channel.synthesis.repair.requested"
            else "requested"
        ),
        "reason": _payload_str(payload, "reason"),
        "prompt": _payload_str(payload, "prompt"),
        "source": _payload_str(payload, "source"),
        "repair_id": _payload_str(payload, "repair_id"),
        "repair_revision": int(payload.get("repair_revision") or 0),
        "max_repair_attempts": int(payload.get("max_repair_attempts") or 0),
        "contract_status": _payload_str(payload, "contract_status"),
        "contract_error": _payload_str(payload, "contract_error"),
        "invalid_reply_ref": (
            redact_obj(payload.get("invalid_reply_ref"))
            if isinstance(payload.get("invalid_reply_ref"), dict)
            else {}
        ),
        "type": event.type,
        "ts": event.ts,
    }
    channel["synthesis_requests"].append(redact_obj(item))


def _apply_contribution(
    channel: dict[str, Any],
    event: ZfEvent,
    payload: dict[str, Any],
) -> None:
    if str(payload.get("contract_status") or "") != "structured":
        return
    artifact_ref = str(payload.get("artifact_ref") or "").strip()
    artifact_digest = str(payload.get("artifact_digest") or "").strip()
    if not artifact_ref or not artifact_digest:
        return
    thread_id = str(payload.get("thread_id") or "main")
    member_id = str(payload.get("member_id") or event.actor or "").strip()
    if not member_id:
        return
    channel["contributions"][f"{thread_id}:{member_id}"] = {
        "event_id": event.id,
        "ts": event.ts,
        "thread_id": thread_id,
        "member_id": member_id,
        "contract_status": "structured",
        "artifact_ref": artifact_ref,
        "artifact_digest": artifact_digest,
        "request_id": _payload_str(payload, "request_id"),
        "message_id": _payload_str(payload, "message_id"),
        "run_generation": _payload_int(payload.get("run_generation"), 1),
        "source_reply_event_id": _payload_str(
            payload,
            "source_reply_event_id",
        ),
        "questions_frozen": payload.get("questions_frozen") is True,
        "source_refs": _string_list(payload.get("source_refs")),
        "evidence_refs": _string_list(payload.get("evidence_refs")),
    }


def _apply_synthesis(channel: dict[str, Any], event: ZfEvent, payload: dict[str, Any]) -> None:
    item = {
        "event_id": event.id,
        "thread_id": _payload_str(payload, "thread_id") or "main",
        "decision": _payload_str(payload, "decision"),
        "summary": _payload_str(payload, "summary"),
        "open_questions": _string_list(payload.get("open_questions")),
        "risks": _string_list(payload.get("risks")),
        "recommended_workflow": (
            payload.get("recommended_workflow")
            if isinstance(payload.get("recommended_workflow"), dict) else {}
        ),
        "artifact_ref": _payload_str(payload, "artifact_ref"),
        "artifact_digest": _payload_str(payload, "artifact_digest"),
        "prd_ref": (
            _payload_str(payload, "prd_ref")
            or _payload_str(payload, "artifact_ref")
        ),
        "prd_digest": (
            _payload_str(payload, "prd_digest")
            or _payload_str(payload, "artifact_digest")
        ),
        "prd_revision": int(payload.get("prd_revision") or 0),
        "previous_prd_ref": _payload_str(payload, "previous_prd_ref"),
        "previous_prd_digest": _payload_str(
            payload, "previous_prd_digest"
        ),
        "readiness_ref": _payload_str(payload, "readiness_ref"),
        "readiness_digest": _payload_str(payload, "readiness_digest"),
        "readiness_verdict": _payload_str(
            payload, "readiness_verdict"
        ),
        "implementation_start": (
            payload.get("implementation_start") is True
        ),
        "conclusion_ref": _payload_str(payload, "conclusion_ref"),
        "conclusion_digest": _payload_str(
            payload, "conclusion_digest"
        ),
        "contract_ref": _payload_str(payload, "contract_ref"),
        "contract_digest": _payload_str(payload, "contract_digest"),
        "spec_path": _payload_str(payload, "spec_path"),
        "spec_digest": _payload_str(payload, "spec_digest"),
        "source_refs": _string_list(payload.get("source_refs")),
        "evidence_refs": _string_list(payload.get("evidence_refs")),
        "consumed_contribution_refs": _string_list(
            payload.get("consumed_contribution_refs")
        ),
        "consumed_contribution_digests": _string_list(
            payload.get("consumed_contribution_digests")
        ),
        "consumed_message_digests": _string_list(
            payload.get("consumed_message_digests")
        ),
        "source_coverage": payload.get("source_coverage")
        if isinstance(payload.get("source_coverage"), list)
        else [],
        "semantic_source_manifest_digest": _payload_str(
            payload, "semantic_source_manifest_digest"
        ),
        "semantic_coverage_ref": _payload_str(
            payload, "semantic_coverage_ref"
        ),
        "semantic_coverage_digest": _payload_str(
            payload, "semantic_coverage_digest"
        ),
        "confidence": _payload_str(payload, "confidence"),
        "dissent": payload.get("dissent") if isinstance(payload.get("dissent"), list) else [],
    }
    channel["syntheses"].append(redact_obj(item))


def _apply_owner_report(channel: dict[str, Any], event: ZfEvent, payload: dict[str, Any]) -> None:
    channel["owner_reports"].append(redact_obj({
        "event_id": event.id,
        "type": event.type,
        "thread_id": _payload_str(payload, "thread_id") or "main",
        "owner_id": _payload_str(payload, "owner_id"),
        "member_id": _payload_str(payload, "member_id"),
        "report_id": _payload_str(payload, "report_id") or event.id,
        "status": event.type.rsplit(".", 1)[-1],
        "period": _payload_str(payload, "period"),
        "summary": _payload_str(payload, "summary"),
        "decisions": _string_list(payload.get("decisions")),
        "risks": payload.get("risks") if isinstance(payload.get("risks"), list) else [],
        "blockers": _string_list(payload.get("blockers")),
        "workflow_status": payload.get("workflow_status") if isinstance(payload.get("workflow_status"), dict) else {},
        "replan_status": payload.get("replan_status") if isinstance(payload.get("replan_status"), dict) else {},
        "recommended_actions": _string_list(payload.get("recommended_actions")),
        "refs": payload.get("refs") if isinstance(payload.get("refs"), dict) else {},
        "ts": event.ts,
    }))


def _apply_automation_report(channel: dict[str, Any], event: ZfEvent, payload: dict[str, Any]) -> None:
    channel["automation_reports"].append(redact_obj({
        "event_id": event.id,
        "thread_id": _payload_str(payload, "thread_id") or "main",
        "report_id": _payload_str(payload, "report_id") or event.id,
        "automation_id": _payload_str(payload, "automation_id"),
        "run_id": _payload_str(payload, "run_id"),
        "summary": _payload_str(payload, "summary"),
        "artifact_ref": _payload_str(payload, "artifact_ref"),
        "refs": payload.get("refs") if isinstance(payload.get("refs"), dict) else {},
        "ts": event.ts,
    }))


def _apply_workflow(channel: dict[str, Any], event: ZfEvent, payload: dict[str, Any]) -> None:
    item = {
        "event_id": event.id,
        "type": event.type,
        "thread_id": _payload_str(payload, "thread_id") or "main",
        "task_id": event.task_id or _payload_str(payload, "task_id"),
        "pattern_id": _payload_str(payload, "pattern_id") or _payload_str(payload, "stage_id"),
        "status": event.type.rsplit(".", 1)[-1],
        "reason": _payload_str(payload, "reason"),
    }
    channel["workflow_requests"].append(redact_obj(item))


def _reply_status(event_type: str, payload: dict[str, Any]) -> str:
    if event_type.endswith(".requested"):
        return _payload_str(payload, "status") or "pending"
    if event_type.endswith(".started"):
        return "running"
    if event_type.endswith(".completed"):
        return "completed"
    if event_type.endswith(".failed"):
        return "failed"
    return _payload_str(payload, "status")


def _read_state(channel: dict[str, Any], thread_id: str, member_id: str) -> dict[str, Any]:
    key = f"{thread_id}:{member_id}"
    return channel["read_state"].setdefault(key, {
        "thread_id": thread_id,
        "member_id": member_id,
        "last_read_message_id": "",
        "last_delivered_message_id": "",
        "unread_count": 0,
        "mention_count": 0,
        "attention": {"level": "none", "reason": "", "raised_at": ""},
    })


def _refresh_attention(channel: dict[str, Any]) -> None:
    recipient_ids = {"operator"}
    recipient_ids.update(
        str(member.get("member_id") or "")
        for member in channel["members"].values()
        if str(member.get("member_id") or "")
        and str(member.get("status") or "active").lower()
        not in {"failed", "rejected", "removed", "suspended"}
    )
    for state in channel["read_state"].values():
        state["unread_count"] = 0
        state["mention_count"] = 0
        if str(state.get("attention", {}).get("reason") or "") == "mention":
            state["attention"] = {
                "level": "none",
                "reason": "",
                "raised_at": "",
            }
    for message in channel["messages"].values():
        thread_id = str(message.get("thread_id") or "main")
        message_id = str(message.get("message_id") or "")
        sender = str(message.get("member_id") or "")
        mentions = set(_string_list(message.get("mentions")))
        for member_id in sorted(recipient_ids - {sender}):
            state = _read_state(channel, thread_id, member_id)
            if not _message_is_after_read(
                channel,
                thread_id,
                message_id,
                state,
            ):
                continue
            state["unread_count"] = int(state.get("unread_count") or 0) + 1
            if member_id not in mentions:
                continue
            state["mention_count"] = int(
                state.get("mention_count") or 0
            ) + 1
            if state.get("attention", {}).get("level") in {"", "none"}:
                state["attention"] = {
                    "level": "waiting",
                    "reason": "mention",
                    "raised_at": message.get("ts", ""),
                    "message_id": message_id,
                }
    channel["attention"] = [
        state for state in channel["read_state"].values()
        if state.get("attention", {}).get("level") not in {"", "none"}
    ]


def _message_is_after_read(
    channel: dict[str, Any],
    thread_id: str,
    message_id: str,
    state: dict[str, Any],
) -> bool:
    last_read = str(state.get("last_read_message_id") or "")
    if not last_read or not message_id:
        return True
    if message_id == last_read:
        return False
    thread = channel["threads"].get(thread_id) if isinstance(channel.get("threads"), dict) else None
    message_ids = list((thread or {}).get("message_ids") or []) if isinstance(thread, dict) else []
    if message_id in message_ids and last_read in message_ids:
        return message_ids.index(message_id) > message_ids.index(last_read)
    return True


def _public_channel(
    channel: dict[str, Any],
    *,
    include_messages: bool,
    include_linked_events: bool = True,
) -> dict[str, Any]:
    messages = [
        channel["messages"][key]
        for key in sorted(channel["messages"], key=lambda item: str(channel["messages"][item].get("ts", "")))
    ]
    raw_members = sorted(
        [
            member for member in channel["members"].values()
            if str(member.get("status") or "") != "removed"
        ],
        key=lambda item: item["member_id"],
    )
    workflow_requests = channel["workflow_requests"]
    reply_requests = [
        channel["reply_requests"][key]
        for key in sorted(channel["reply_requests"], key=lambda item: str(channel["reply_requests"][item].get("created_at", "")))
    ]
    context_packs = [
        channel["context_packs"][key]
        for key in sorted(channel["context_packs"], key=lambda item: str(channel["context_packs"][item].get("ts", "")))
    ]
    attachments = [
        channel["attachments"][key]
        for key in sorted(channel["attachments"], key=lambda item: str(channel["attachments"][item].get("ts", "")))
    ]
    artifacts = [
        channel["artifacts"][key]
        for key in sorted(channel["artifacts"], key=lambda item: str(channel["artifacts"][item].get("ts", "")))
    ]
    agent_session_runs = _public_agent_session_runs(channel["agent_session_runs"])
    typing = sorted(
        channel["typing"].values(),
        key=lambda item: (str(item.get("thread_id", "")), str(item.get("member_id", ""))),
    )
    active_typing = [
        item for item in typing if bool(item.get("typing"))
    ]
    provider_runs = _provider_runs_with_sessions(
        reply_requests,
        agent_session_runs,
        channel_id=str(channel.get("channel_id") or ""),
    )
    members = redact_obj(_members_with_runtime(
        raw_members,
        reply_requests,
        context_packs,
        typing,
        agent_session_runs,
        discussion=channel["discussion"],
        channel_id=str(channel.get("channel_id") or ""),
    ))
    linked_event_records = channel["linked_events"]
    linked_event_count = len(linked_event_records)
    linked_events = (
        [_public_linked_event(item) for item in linked_event_records[-80:]]
        if include_linked_events
        else []
    )
    question_thread_ids = {
        str(item.get("thread_id") or "main")
        for item in channel["open_questions"].values()
        if isinstance(item, dict)
    } | {
        str(thread_id)
        for thread_id in channel["discussions"]
    } | {"main"}
    question_frontiers = {
        thread_id: question_frontier(channel, thread_id=thread_id)
        for thread_id in sorted(question_thread_ids)
    }
    owner_questionnaires = {
        thread_id: owner_questionnaire(channel, thread_id=thread_id)
        for thread_id in sorted(question_thread_ids)
    }
    question_graph_digests = {
        thread_id: question_graph_digest(channel, thread_id=thread_id)
        for thread_id in sorted(question_thread_ids)
    }
    discussion_attention = project_discussion_attention(
        channel,
        reply_requests,
        owner_questionnaires,
    )
    out = {
        "schema_version": "channel.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seq": channel["last_event_seq"],
        "empty": not bool(channel["last_event_id"]),
        "id": _public_channel_id(channel["channel_id"]),
        "channel_id": channel["channel_id"],
        "name": channel["name"],
        "status": channel["status"],
        "task_id": channel["task_id"],
        "created_at": channel["created_at"],
        "created_by": channel["created_by"],
        "created_by_event": bool(channel["created_by_event"]),
        "owner_actor_ref": channel["owner_actor_ref"],
        "owner_delegates": redact_obj(channel["owner_delegates"]),
        "leader_member_id": channel["leader_member_id"],
        "leader_revision": channel["leader_revision"],
        "leader_event_id": channel["leader_event_id"],
        "origin_binding": redact_obj(channel["origin_binding"]),
        "scope": redact_obj(channel["scope"]),
        "members": members,
        "member_count": len(members),
        "threads": sorted(channel["threads"].values(), key=lambda item: item["thread_id"]),
        "read_state": sorted(
            channel["read_state"].values(),
            key=lambda item: (item["thread_id"], item["member_id"]),
        ),
        "unread_count": sum(
            int(item.get("unread_count") or 0)
            for item in channel["read_state"].values()
            if str(item.get("member_id") or "") == "operator"
        ),
        "pinned_message_ids": sorted(
            message_id
            for message_id, pinned in channel["pinned_message_ids"].items()
            if pinned
        ),
        "result_receipts": sorted(
            channel["result_receipts"].values(),
            key=lambda item: (
                str(item.get("thread_id") or ""),
                int(item.get("revision") or 0),
                str(item.get("ts") or ""),
            ),
        ),
        "attention": channel["attention"],
        "contributions": sorted(
            channel["contributions"].values(),
            key=lambda item: (
                str(item.get("thread_id") or ""),
                str(item.get("member_id") or ""),
            ),
        ),
        "syntheses": channel["syntheses"],
        "synthesis_requests": channel["synthesis_requests"],
        "question_dedup_requests": channel["question_dedup_requests"],
        "workflow_requests": workflow_requests,
        "mentions_detected": channel["mentions_detected"],
        "routes": channel["routes"],
        "reply_requests": reply_requests,
        "provider_runs": provider_runs,
        "agent_session_runs": agent_session_runs,
        "usage_summary": sum_turn_usage(agent_session_runs),
        "typing": typing,
        "active_typing": active_typing,
        "attachments": attachments,
        "artifacts": artifacts,
        "running_replies": [
            item for item in reply_requests
            if str(item.get("status") or "") in {"running", "started"}
        ],
        "queued_replies": [
            item for item in reply_requests
            if str(item.get("status") or "") == "queued"
        ],
        "pending_reply_count": sum(
            1 for item in reply_requests if str(item.get("status") or "") in {"pending", "running", "started"}
        ),
        "context_packs": context_packs,
        "handoffs": channel["handoffs"],
        "state_updates": channel["state_updates"],
        "owner_reports": channel["owner_reports"],
        "automation_reports": channel["automation_reports"],
        "discussion": channel["discussion"],
        "discussions": channel["discussions"],
        "discussion_attention": discussion_attention,
        "open_questions": sorted(
            channel["open_questions"].values(),
            key=lambda item: str(item.get("ts") or ""),
        ),
        "question_frontiers": question_frontiers,
        "owner_questionnaires": owner_questionnaires,
        "question_graph_digests": question_graph_digests,
        "question_graph_rejections": channel["question_graph_rejections"],
        "cross_reviews": sorted(
            channel["cross_reviews"].values(),
            key=lambda item: (
                str(item.get("thread_id") or ""),
                str(item.get("request_id") or ""),
            ),
        ),
        "question_activity": channel["question_activity"],
        "questions_frozen": channel["questions_frozen"],
        "consensus": channel["consensus"],
        "rejected_resolutions": channel["rejected_resolutions"],
        "question_resolve_rejections": channel["question_resolve_rejections"],
        "relay_events": channel["relay_events"][-80:],
        "pending_workflow_requests": sum(
            1 for item in workflow_requests if str(item.get("status") or "") == "requested"
        ),
        "summary": channel["summary"],
        "history_cleared_at": channel["history_cleared_at"],
        "history_clear_event_id": channel["history_clear_event_id"],
        "history_clear_reason": channel["history_clear_reason"],
        "message_count": len(messages),
        "last_event_id": channel["last_event_id"],
        "last_event_seq": channel["last_event_seq"],
        "last_event_type": channel["last_event_type"],
        "updated_at": channel["updated_at"],
        "recent_messages": messages[-20:],
        "linked_event_count": linked_event_count,
        "linked_events": linked_events,
        "source": "events.jsonl",
    }
    if include_messages:
        out["messages"] = messages
    else:
        out.update(_compact_channel_summary_payload(
            messages=messages,
            reply_requests=reply_requests,
            provider_runs=provider_runs,
            agent_session_runs=agent_session_runs,
            context_packs=context_packs,
            mentions_detected=channel["mentions_detected"],
            linked_event_count=linked_event_count,
        ))
    return out


def _compact_channel_summary_payload(
    *,
    messages: list[dict[str, Any]],
    reply_requests: list[dict[str, Any]],
    provider_runs: list[dict[str, Any]],
    agent_session_runs: list[dict[str, Any]],
    context_packs: list[dict[str, Any]],
    mentions_detected: list[dict[str, Any]],
    linked_event_count: int,
) -> dict[str, Any]:
    reply_status_counts: dict[str, int] = {}
    for item in reply_requests:
        status = str(item.get("status") or "unknown")
        reply_status_counts[status] = reply_status_counts.get(status, 0) + 1
    return {
        "recent_messages": [_compact_channel_message(item) for item in messages[-5:]],
        "reply_requests": [],
        "provider_runs": [],
        "agent_session_runs": [],
        "context_packs": [],
        "mentions_detected": [],
        "linked_events": [],
        "running_replies": [
            _compact_reply_request(item)
            for item in reply_requests
            if str(item.get("status") or "") in {"running", "started"}
        ],
        "queued_replies": [
            _compact_reply_request(item)
            for item in reply_requests
            if str(item.get("status") or "") == "queued"
        ],
        "reply_request_count": len(reply_requests),
        "reply_request_status_counts": reply_status_counts,
        "failed_reply_count": reply_status_counts.get("failed", 0),
        "provider_run_count": len(provider_runs),
        "agent_session_run_count": len(agent_session_runs),
        "context_pack_count": len(context_packs),
        "mention_count": len(mentions_detected),
        "linked_event_count": linked_event_count,
        "latest_reply": _compact_reply_request(reply_requests[-1]) if reply_requests else {},
    }


def _compact_channel_message(item: dict[str, Any]) -> dict[str, Any]:
    text = str(item.get("text") or item.get("message") or item.get("summary") or "")
    return {
        "event_id": str(item.get("event_id") or ""),
        "message_id": str(item.get("message_id") or ""),
        "thread_id": str(item.get("thread_id") or "main"),
        "member_id": str(item.get("member_id") or ""),
        "role": str(item.get("role") or ""),
        "source": str(item.get("source") or ""),
        "ts": str(item.get("ts") or ""),
        "text": text[:240],
        "text_length": len(text),
    }


def _compact_reply_request(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "request_id": str(item.get("request_id") or ""),
        "run_id": str(item.get("run_id") or item.get("provider_run_id") or ""),
        "thread_id": str(item.get("thread_id") or "main"),
        "message_id": str(item.get("message_id") or ""),
        "target_member_id": str(item.get("target_member_id") or ""),
        "member_id": str(item.get("member_id") or ""),
        "provider": str(item.get("provider") or item.get("backend") or ""),
        "status": str(item.get("status") or ""),
        "routing_reason": str(item.get("routing_reason") or ""),
        "reason": str(item.get("reason") or "")[:240],
        "created_at": str(item.get("created_at") or ""),
        "started_at": str(item.get("started_at") or ""),
        "ended_at": str(item.get("ended_at") or ""),
        "updated_at": str(item.get("updated_at") or ""),
    }


def _members_with_runtime(
    members: list[dict[str, Any]],
    reply_requests: list[dict[str, Any]],
    context_packs: list[dict[str, Any]],
    typing: list[dict[str, Any]],
    agent_session_runs: list[dict[str, Any]],
    *,
    discussion: dict[str, Any],
    channel_id: str,
) -> list[dict[str, Any]]:
    latest_by_target: dict[str, dict[str, Any]] = {}
    active_by_target: dict[str, dict[str, Any]] = {}
    for reply in reply_requests:
        target = str(reply.get("target_member_id") or "")
        if not target:
            continue
        latest_by_target[target] = reply
        if str(reply.get("status") or "") in {"running", "started"}:
            active_by_target[target] = reply

    context_by_id = {
        str(item.get("context_pack_id") or ""): item
        for item in context_packs
        if str(item.get("context_pack_id") or "")
    }
    context_by_target: dict[str, dict[str, Any]] = {}
    for item in context_packs:
        target = str(item.get("target_member_id") or "")
        if target:
            context_by_target[target] = item
    typing_by_member = {
        str(item.get("member_id") or item.get("target_member_id") or ""): item
        for item in typing
        if bool(item.get("typing"))
    }
    streaming_by_member: dict[str, dict[str, Any]] = {}
    for run in agent_session_runs:
        target = str(run.get("target_member_id") or run.get("member_id") or "")
        if target and str(run.get("status") or "") in {"streaming", "submitted"}:
            streaming_by_member[target] = run

    enriched: list[dict[str, Any]] = []
    for member in members:
        out = dict(member)
        member_id = str(out.get("member_id") or "")
        out["is_default_responder"] = member_id == str(discussion.get("default_responder_id") or "")
        latest = latest_by_target.get(member_id, {})
        active = active_by_target.get(member_id, {})
        typing_state = typing_by_member.get(member_id, {})
        streaming = streaming_by_member.get(member_id, {})
        latest_status = str(latest.get("status") or "")
        run = provider_run_record(latest, channel_id=channel_id) if latest else {}
        context = (
            context_by_id.get(str(latest.get("context_pack_id") or ""))
            or context_by_target.get(member_id, {})
        )
        out["presence"] = _member_presence(
            out,
            latest_status,
            typing=bool(typing_state),
            streaming=bool(streaming),
            active=bool(active),
        )
        out["latest_run_id"] = str(streaming.get("run_id") or run.get("run_id") or "")
        out["latest_run_status"] = str(streaming.get("status") or latest_status)
        out["latest_request_id"] = str(latest.get("request_id") or "")
        out["active_request_id"] = str(active.get("request_id") or streaming.get("request_id") or "")
        out["active_part_id"] = str(streaming.get("active_part_id") or "")
        out["context_status"] = str(context.get("status") or "")
        out["provider_capabilities"] = _member_provider_capabilities(out, discussion)
        out["last_seen_at"] = (
            str(streaming.get("updated_at") or "")
            or str(typing_state.get("updated_at") or "")
            or str(latest.get("updated_at") or "")
            or str(context.get("ts") or "")
            or str(out.get("last_event_ts") or "")
        )
        enriched.append(out)
    return enriched


def _public_agent_session_runs(runs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for run_id in sorted(runs, key=lambda key: str(runs[key].get("created_at", ""))):
        run = dict(runs[run_id])
        parts = run.get("parts") if isinstance(run.get("parts"), dict) else {}
        run["parts"] = [
            redact_obj(parts[key])
            for key in sorted(parts, key=lambda item: _payload_int(parts[item].get("seq"), 0))
        ]
        active_part = next(
            (
                part for part in reversed(run["parts"])
                if str(part.get("state") or "") in {"streaming", "submitted"}
            ),
            {},
        )
        run["active_part_id"] = str(active_part.get("part_id") or "")
        out.append(redact_obj(run))
    return out


def _provider_runs_with_sessions(
    reply_requests: list[dict[str, Any]],
    agent_session_runs: list[dict[str, Any]],
    *,
    channel_id: str,
) -> list[dict[str, Any]]:
    by_run_id: dict[str, dict[str, Any]] = {}
    ordered: list[str] = []
    session_by_run_id = {
        str(run.get("run_id") or run.get("provider_run_id") or ""): run
        for run in agent_session_runs
        if str(run.get("run_id") or run.get("provider_run_id") or "")
    }
    for reply in reply_requests:
        record = provider_run_record(reply, channel_id=channel_id)
        run_id = str(record.get("run_id") or "")
        session = session_by_run_id.get(run_id, {})
        if session:
            record = _merge_provider_session(record, session)
        if run_id:
            by_run_id[run_id] = record
            ordered.append(run_id)
    for run in agent_session_runs:
        run_id = str(run.get("run_id") or run.get("provider_run_id") or "")
        if run_id and run_id not in by_run_id:
            by_run_id[run_id] = _merge_provider_session({}, run)
            ordered.append(run_id)
    return redact_obj([by_run_id[run_id] for run_id in ordered if run_id in by_run_id])


def _merge_provider_session(base: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    session_status = str(session.get("status") or "")
    base_status = str(out.get("status") or "")
    terminal = {"completed", "failed", "cancelled", "stale"}
    out.update({
        "run_id": str(out.get("run_id") or session.get("run_id") or session.get("provider_run_id") or ""),
        "provider_run_id": str(out.get("provider_run_id") or session.get("provider_run_id") or session.get("run_id") or ""),
        "run_generation": _payload_int(session.get("run_generation"), _payload_int(out.get("run_generation"), 1)),
        "request_id": str(out.get("request_id") or session.get("request_id") or ""),
        "thread_id": str(out.get("thread_id") or session.get("thread_id") or "main"),
        "message_id": str(out.get("message_id") or session.get("message_id") or ""),
        "target_member_id": str(out.get("target_member_id") or session.get("target_member_id") or ""),
        "member_id": str(out.get("member_id") or session.get("member_id") or ""),
        "provider": str(out.get("provider") or session.get("provider") or ""),
        "backend": str(out.get("backend") or session.get("backend") or ""),
        "provider_session_id": str(out.get("provider_session_id") or session.get("provider_session_id") or ""),
        "live_status": session_status,
        "status": base_status if base_status in terminal else (session_status or base_status),
        "parts": session.get("parts") if isinstance(session.get("parts"), list) else [],
        "active_part_id": str(session.get("active_part_id") or ""),
        "started_at": str(session.get("started_at") or out.get("started_at") or ""),
        "ended_at": str(session.get("ended_at") or out.get("ended_at") or ""),
        "updated_at": str(session.get("updated_at") or out.get("updated_at") or ""),
    })
    return out


def _member_provider_capabilities(member: dict[str, Any], discussion: dict[str, Any]) -> dict[str, Any]:
    matrix = discussion.get("provider_capabilities") if isinstance(discussion.get("provider_capabilities"), dict) else {}
    provider = str(member.get("provider") or member.get("backend") or member.get("member_type") or "").strip()
    if provider in {"codex-headless", "codex-app-server"}:
        provider = "codex"
    elif provider in {"claude", "claude-headless", "claude-code-headless"}:
        provider = "claude-code"
    capabilities = matrix.get(provider) if isinstance(matrix.get(provider), dict) else {}
    return redact_obj(capabilities)


def _member_presence(
    member: dict[str, Any],
    latest_status: str,
    *,
    typing: bool = False,
    streaming: bool = False,
    active: bool = False,
) -> str:
    if typing:
        return "typing"
    if streaming:
        return "streaming"
    if active:
        return "running"
    if latest_status in {"running", "started"}:
        return "running"
    if latest_status in RUN_ACTIVE_STATUSES:
        return "pending"
    if latest_status == "queued":
        return "queued"
    if latest_status == "completed":
        return "ready"
    if latest_status == "failed":
        return "failed"
    return str(member.get("status") or "unknown")


def _event_record(seq: int, event: ZfEvent) -> dict[str, Any]:
    data = asdict(event)
    data["seq"] = seq
    return redact_obj(data)


def _public_linked_event(item: object) -> dict[str, Any]:
    if (
        isinstance(item, tuple)
        and len(item) == 2
        and isinstance(item[0], int)
        and isinstance(item[1], ZfEvent)
    ):
        return _event_record(item[0], item[1])
    return redact_obj(item) if isinstance(item, dict) else {}


def _public_channel_id(channel_id: str) -> str:
    return str(channel_id).strip().lower()


def _resolve_channel_key(channels: dict[str, dict[str, Any]], channel_id: str) -> str:
    if channel_id in channels:
        return channel_id
    normalized = _public_channel_id(channel_id)
    for key in channels:
        if _public_channel_id(key) == normalized:
            return key
    # Writes canonicalize channel ids with a `ch-` prefix (_normal_channel_id),
    # so a read for the bare human slug ("streamval-review") must resolve to the
    # created canonical id ("ch-streamval-review"). Match on the canonical form
    # as a fallback (lazy import avoids a module-load cycle).
    from zf.runtime.control_actions_helpers import _normal_channel_id

    canonical = _normal_channel_id(channel_id)
    if canonical:
        for key in channels:
            if _normal_channel_id(key) == canonical:
                return key
    return channel_id


def _payload_str(payload: object, key: str) -> str:
    if not isinstance(payload, dict):
        return ""
    value = payload.get(key)
    return str(value).strip() if value not in (None, "") else ""


def _payload_text(payload: object, key: str) -> str:
    if not isinstance(payload, dict):
        return ""
    value = payload.get(key)
    return str(value) if value not in (None, "") else ""


def _payload_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, list | tuple | set):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _channel_from_event(event: ZfEvent) -> str:
    if event.correlation_id and event.correlation_id.startswith("ch-"):
        return event.correlation_id
    return ""
