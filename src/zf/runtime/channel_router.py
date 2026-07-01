"""Mention routing for Agent Channel messages."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from zf.core.config.schema import ZfConfig
from zf.core.events import EventWriter
from zf.core.events.model import ZfEvent
from zf.runtime.channel_adapter import dispatch_reply_request
from zf.runtime.openclaw_provider import OpenClawGatewayClient
from zf.runtime.channel_context import (
    build_channel_context_pack,
    context_pack_rejection_reason,
)
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_run_owner import provider_run_fields, provider_run_fields_for_request


MENTION_RE = re.compile(
    r"(?<![\w@./-])@([A-Za-z0-9_.-]+|all)(?![\w.-])",
    flags=re.ASCII,
)
BUSY_REPLY_STATUSES = {"pending", "running", "started"}


@dataclass(frozen=True)
class ChannelRouteResult:
    targets: list[str] = field(default_factory=list)
    reply_requests: list[str] = field(default_factory=list)
    intent_requests: list[str] = field(default_factory=list)
    queued: list[str] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "targets": self.targets,
            "reply_requests": self.reply_requests,
            "intent_requests": self.intent_requests,
            "queued": self.queued,
            "skipped": self.skipped,
        }


def resolve_channel_mentions(
    channel: dict[str, Any] | None,
    *,
    text: str,
    explicit_mentions: list[str] | None = None,
    sender_member_id: str = "",
    max_targets: int = 6,
) -> list[str]:
    members = _members(channel)
    tokens = detect_channel_mention_tokens(text, explicit_mentions=explicit_mentions)
    if not tokens:
        return []
    targets: list[str] = []
    for token in tokens:
        if token == "all":
            candidates = [
                str(member.get("member_id") or "")
                for member in members
                if _member_can_receive(member, sender_member_id)
            ]
        else:
            candidates = _match_member_ids(members, token, sender_member_id)
        for member_id in candidates:
            if member_id and member_id not in targets:
                targets.append(member_id)
            if len(targets) >= max_targets:
                return targets
    return targets


def route_channel_message(
    *,
    state_dir: Path,
    writer: EventWriter,
    message_event: ZfEvent,
    message_payload: dict[str, Any],
    actor: str,
    source: str,
    max_parallel_replies: int = 6,
    project_root: Path | None = None,
    headless_backends: dict[str, Any] | None = None,
    config: ZfConfig | None = None,
    openclaw_client: OpenClawGatewayClient | None = None,
    dispatch_inline: bool = True,
) -> ChannelRouteResult:
    channel_id = str(message_payload.get("channel_id") or "").strip()
    thread_id = str(message_payload.get("thread_id") or "main").strip() or "main"
    message_id = str(message_payload.get("message_id") or message_event.id).strip()
    sender = str(message_payload.get("member_id") or "").strip()
    text = str(message_payload.get("text") or message_payload.get("message") or "")
    role = str(message_payload.get("role") or "").strip().lower()
    if not channel_id:
        _emit_route_blocked(
            writer=writer,
            actor=actor,
            channel_id="",
            thread_id=thread_id,
            message_id=message_id,
            reason="missing_channel_id",
            source=source,
            message_event=message_event,
        )
        return ChannelRouteResult(skipped=[{"reason": "auto_route_not_allowed"}])

    channel = project_channel(Path(state_dir), channel_id) or {}
    agent_member_ids = _agent_member_ids(channel)
    if not _auto_route_allowed(
        role=role, source=source, sender=sender, agent_member_ids=agent_member_ids,
    ):
        _emit_route_blocked(
            writer=writer,
            actor=actor,
            channel_id=channel_id,
            thread_id=thread_id,
            message_id=message_id,
            reason="auto_route_not_allowed",
            source=source,
            message_event=message_event,
        )
        return ChannelRouteResult(skipped=[{"reason": "auto_route_not_allowed"}])
    targets = resolve_channel_mentions(
        channel,
        text=text,
        explicit_mentions=_string_list(message_payload.get("mentions")),
        sender_member_id=sender,
        max_targets=max_parallel_replies,
    )
    routing_reason = "mention"
    if not targets:
        tokens = detect_channel_mention_tokens(
            text,
            explicit_mentions=_string_list(message_payload.get("mentions")),
        )
        if not tokens:
            default_target = _default_responder_target(channel, sender_member_id=sender)
            if default_target:
                targets = [default_target]
                routing_reason = "default_responder"
            else:
                reason = _default_responder_block_reason(channel)
                _emit_route_blocked(
                    writer=writer,
                    actor=actor,
                    channel_id=channel_id,
                    thread_id=thread_id,
                    message_id=message_id,
                    reason=reason,
                    source=source,
                    message_event=message_event,
                )
                return ChannelRouteResult(skipped=[{"reason": reason}])
        else:
            reason = "all_no_receivers" if "all" in tokens else "no_target"
            _emit_route_blocked(
                writer=writer,
                actor=actor,
                channel_id=channel_id,
                thread_id=thread_id,
                message_id=message_id,
                reason=reason,
                source=source,
                message_event=message_event,
            )
            return ChannelRouteResult(skipped=[{"reason": reason}])
    if not targets:
        reason = "no_target"
        _emit_route_blocked(
            writer=writer,
            actor=actor,
            channel_id=channel_id,
            thread_id=thread_id,
            message_id=message_id,
            reason=reason,
            source=source,
            message_event=message_event,
        )
        return ChannelRouteResult(skipped=[{"reason": reason}])
    round_guard_reason = _debate_round_guard_reason(channel, thread_id=thread_id)
    if round_guard_reason:
        _emit_route_blocked(
            writer=writer,
            actor=actor,
            channel_id=channel_id,
            thread_id=thread_id,
            message_id=message_id,
            reason=round_guard_reason,
            source=source,
            message_event=message_event,
        )
        return ChannelRouteResult(
            targets=targets,
            skipped=[{"reason": round_guard_reason}],
        )

    reply_requests: list[str] = []
    intent_requests: list[str] = []
    queued: list[str] = []
    skipped: list[dict[str, str]] = []
    for target_member_id in targets:
        if _has_duplicate_request(channel, message_id, target_member_id):
            skipped.append({"target_member_id": target_member_id, "reason": "duplicate"})
            continue

        detected = writer.emit(
            "channel.route.defaulted" if routing_reason == "default_responder" else "channel.mention.detected",
            actor=actor,
            task_id=message_event.task_id,
            causation_id=message_event.id,
            correlation_id=channel_id,
            payload={
                "channel_id": channel_id,
                "thread_id": thread_id,
                "message_id": message_id,
                "member_id": sender,
                "target_member_id": target_member_id,
                "default_responder_id": target_member_id if routing_reason == "default_responder" else "",
                "routing_reason": routing_reason,
                "source": source,
            },
        )
        rejection_reason, limits = context_pack_rejection_reason(channel)
        if rejection_reason:
            writer.emit(
                "channel.context_pack.rejected",
                actor=actor,
                task_id=message_event.task_id,
                causation_id=detected.id,
                correlation_id=channel_id,
                payload={
                    "channel_id": channel_id,
                    "thread_id": thread_id,
                    "context_pack_id": _stable_reply_request_id(channel_id, thread_id, message_id, target_member_id).replace("reply-", "ctx-"),
                    "target_member_id": target_member_id,
                    "trigger_message_id": message_id,
                    "reason": rejection_reason,
                    "routing_reason": routing_reason,
                    "limits": limits,
                    "source": source,
                },
            )
            skipped.append({"target_member_id": target_member_id, "reason": "context_pack_rejected"})
            continue
        member = _member_by_id(channel, target_member_id)
        context_pack = build_channel_context_pack(
            channel,
            channel_id=channel_id,
            thread_id=thread_id,
            target_member_id=target_member_id,
            trigger_message_id=message_id,
            visibility_profile=str(member.get("visibility_profile") or ""),
            channel_role=str(member.get("channel_role") or ""),
            role_context_ref=str(member.get("role_context_ref") or ""),
            skill_refs=member.get("skill_refs", []),
            permission_profile=str(member.get("permission_profile") or ""),
        )
        writer.emit(
            "channel.context_pack.built",
            actor=actor,
            task_id=message_event.task_id,
            causation_id=detected.id,
            correlation_id=channel_id,
            payload={**context_pack, "routing_reason": routing_reason, "source": source},
        )
        if _is_spine_reviewer(member):
            intent = writer.emit(
                "channel.spine_review.requested",
                actor=actor,
                task_id=message_event.task_id,
                causation_id=detected.id,
                correlation_id=channel_id,
                payload={
                    "schema_version": "channel.spine_review.requested.v1",
                    "channel_id": channel_id,
                    "thread_id": thread_id,
                    "request_id": _stable_spine_review_request_id(
                        channel_id,
                        thread_id,
                        message_id,
                        target_member_id,
                    ),
                    "message_id": message_id,
                    "target_member_id": target_member_id,
                    "member_id": sender,
                    "status": "pending",
                    "intent": "project_spine_review_report_proposal",
                    "allowed_outputs": ["report", "proposal"],
                    "context_pack_id": context_pack["context_pack_id"],
                    "routing_reason": routing_reason,
                    "member_type": str(member.get("member_type") or ""),
                    "backend": str(member.get("backend") or ""),
                    "provider": str(member.get("provider") or ""),
                    "channel_role": str(member.get("channel_role") or ""),
                    "visibility_profile": str(member.get("visibility_profile") or ""),
                    "source": source,
                },
            )
            intent_requests.append(intent.id)
            continue
        busy = _member_busy(channel, target_member_id)
        request_id = _stable_reply_request_id(channel_id, thread_id, message_id, target_member_id)
        if busy:
            _supersede_queued_replies(
                writer,
                channel=channel,
                channel_id=channel_id,
                target_member_id=target_member_id,
                actor=actor,
                source=source,
                causation_id=detected.id,
                task_id=message_event.task_id,
            )
        reply = writer.emit(
            "channel.agent.reply.requested",
            actor=actor,
            task_id=message_event.task_id,
            causation_id=detected.id,
            correlation_id=channel_id,
            payload={
                "channel_id": channel_id,
                "thread_id": thread_id,
                "request_id": request_id,
                "message_id": message_id,
                "target_member_id": target_member_id,
                "member_id": sender,
                "status": "queued" if busy else "pending",
                "queue_state": "latest_only" if busy else "ready",
                "context_pack_id": context_pack["context_pack_id"],
                "routing_reason": routing_reason,
                "member_type": str(member.get("member_type") or ""),
                "backend": str(member.get("backend") or ""),
                "provider": str(member.get("provider") or ""),
                "provider_binding_id": str(member.get("provider_binding_id") or ""),
                "provider_session_id": str(member.get("provider_session_id") or ""),
                "channel_role": str(member.get("channel_role") or ""),
                "visibility_profile": str(member.get("visibility_profile") or ""),
                "permission_profile": str(member.get("permission_profile") or "read_only"),
                "worker_session_id": _worker_session(member),
                **provider_run_fields(
                    channel_id=channel_id,
                    thread_id=thread_id,
                    request_id=request_id,
                    target_member_id=target_member_id,
                ),
                "source": source,
            },
        )
        reply_requests.append(reply.id)
        if busy:
            queued.append(target_member_id)
            continue
        if dispatch_inline:
            dispatch_reply_request(
                state_dir=state_dir,
                writer=writer,
                channel_id=channel_id,
                request_id=request_id,
                actor=actor,
                source=source,
                project_root=project_root,
                headless_backends=headless_backends,
                config=config,
                openclaw_client=openclaw_client,
            )
    return ChannelRouteResult(
        targets=targets,
        reply_requests=reply_requests,
        intent_requests=intent_requests,
        queued=queued,
        skipped=skipped,
    )

def _emit_route_blocked(
    *,
    writer: EventWriter,
    actor: str,
    channel_id: str,
    thread_id: str,
    message_id: str,
    reason: str,
    source: str,
    message_event: ZfEvent,
) -> None:
    """Emit channel.route.blocked when route_channel_message early-returns.

    Closes the observability gap surfaced by channel review w4xl2gi11 (P0.4):
    operator could not distinguish "message had no @mention" from "router
    blocked the message" because all three early-return paths returned
    silently. Now every early-return writes one observable event.
    """
    writer.emit(
        "channel.route.blocked",
        actor=actor,
        task_id=message_event.task_id,
        causation_id=message_event.id,
        correlation_id=channel_id or None,
        payload={
            "channel_id": channel_id,
            "thread_id": thread_id,
            "message_id": message_id,
            "member_id": str((message_event.payload or {}).get("member_id") or "") if isinstance(message_event.payload, dict) else "",
            "reason": reason,
            "source": source,
        },
    )


def _auto_route_allowed(
    *,
    role: str,
    source: str,
    sender: str,
    agent_member_ids: frozenset[str] | set[str] | None = None,
) -> bool:
    if role in {"assistant", "agent", "system", "state_update"}:
        return False
    # Defense-in-depth on top of the empty-role fix: an agent member that claims
    # role='user'/'human'/'operator' is impersonating an operator. Block when the
    # sender member_id matches a known provider_agent / persona_agent channel
    # member, regardless of the claimed role.
    if sender and agent_member_ids and sender in agent_member_ids:
        return False
    if role in {"user", "human", "operator"}:
        return True
    # Empty/missing role: fall through to source/sender check. doc 64 §5 — an
    # agent member that forgets to set role must not be treated as user-authored
    # just because role is unset.
    return source in {"web", "operator", "human"} or sender in {"operator", "human"}


def _agent_member_ids(channel: dict[str, Any] | None) -> frozenset[str]:
    agent_types = {"provider_agent", "persona_agent"}
    ids: list[str] = []
    for member in _members(channel):
        member_type = str(member.get("member_type") or "").strip()
        member_id = str(member.get("member_id") or "").strip()
        if member_id and member_type in agent_types:
            ids.append(member_id)
    return frozenset(ids)


def _members(channel: dict[str, Any] | None) -> list[dict[str, Any]]:
    raw = (channel or {}).get("members") or []
    return [item for item in raw if isinstance(item, dict)]


def _default_responder_target(channel: dict[str, Any] | None, *, sender_member_id: str) -> str:
    discussion = (channel or {}).get("discussion")
    if not isinstance(discussion, dict):
        return ""
    member_id = str(discussion.get("default_responder_id") or "").strip()
    if not member_id:
        return ""
    member = _member_by_id(channel or {}, member_id)
    if not member or not _member_can_receive(member, sender_member_id):
        return ""
    return member_id


def _default_responder_block_reason(channel: dict[str, Any] | None) -> str:
    discussion = (channel or {}).get("discussion")
    if not isinstance(discussion, dict):
        return "no_target"
    if str(discussion.get("default_responder_id") or "").strip():
        return "default_responder_unavailable"
    return "no_target"


def _debate_round_guard_reason(channel: dict[str, Any] | None, *, thread_id: str) -> str:
    discussion = (channel or {}).get("discussion")
    if not isinstance(discussion, dict):
        return ""
    mode = str(discussion.get("mode") or "manual_mention").strip()
    speaker_policy = discussion.get("speaker_policy") if isinstance(discussion.get("speaker_policy"), dict) else {}
    structured = mode in {
        "round_robin",
        "fanout_then_synthesis",
        "debate_judge",
        "leader_delegation",
        "priority",
    }
    if not structured and not bool(speaker_policy.get("enforce_max_rounds")):
        return ""
    try:
        max_rounds = int(discussion.get("max_rounds") or 6)
    except Exception:
        max_rounds = 6
    if max_rounds <= 0:
        return "debate_round_limit_reached"
    replies = [
        item for item in (channel or {}).get("reply_requests") or []
        if isinstance(item, dict)
        and str(item.get("thread_id") or "main") == thread_id
        and str(item.get("status") or "") not in {"failed", "cancelled", "superseded"}
    ]
    if len(replies) >= max_rounds:
        return "debate_round_limit_reached"
    return ""


def _mention_tokens(text: str) -> list[str]:
    return [_normalize_token(match.group(1)) for match in MENTION_RE.finditer(text)]


def detect_channel_mention_tokens(
    text: str,
    *,
    explicit_mentions: list[str] | None = None,
) -> list[str]:
    tokens = _mention_tokens(text)
    tokens.extend(_normalize_explicit(explicit_mentions or []))
    out: list[str] = []
    for token in tokens:
        if token and token not in out:
            out.append(token)
    return out


def _normalize_explicit(values: list[str]) -> list[str]:
    return [_normalize_token(value) for value in values if _normalize_token(value)]


def _match_member_ids(
    members: list[dict[str, Any]],
    token: str,
    sender_member_id: str,
) -> list[str]:
    exact: list[str] = []
    prefix: list[str] = []
    for member in members:
        if not _member_can_receive(member, sender_member_id):
            continue
        member_id = str(member.get("member_id") or "")
        aliases = [
            member_id,
            str(member.get("persona") or ""),
            str(member.get("role") or ""),
            str(member.get("channel_role") or ""),
            str(member.get("backend") or ""),
            str(member.get("provider") or ""),
        ]
        normalized_aliases = [_normalize_token(alias) for alias in aliases if alias]
        if token in normalized_aliases:
            exact.append(member_id)
            continue
        normalized_id = _normalize_token(member_id)
        if normalized_id.startswith(token) and member_id:
            prefix.append(member_id)
    if exact:
        return exact
    if len(prefix) == 1:
        return prefix
    return []


def _member_can_receive(member: dict[str, Any], sender_member_id: str) -> bool:
    member_id = str(member.get("member_id") or "")
    if not member_id or member_id == sender_member_id:
        return False
    status = str(member.get("status") or "").lower()
    if status in {"removed", "suspended", "rejected", "failed"}:
        return False
    if str(member.get("member_type") or "") in {"readonly-reviewer", "observer"}:
        return False
    permissions = _string_list(member.get("permissions"))
    return not permissions or "message" in permissions


def routable_backing_worker_member(
    channel: dict[str, Any] | None,
    instance_id: str,
    *,
    sender_member_id: str = "",
) -> dict[str, Any] | None:
    """Return the channel member backed by ``instance_id`` if it may receive
    messages, else None.

    Gate for the direct ``instance_id`` post path (2026-06-10 review P1-7):
    a raw instance_id in the post payload must not drive a role pane unless
    that worker is a channel member with message permission — otherwise the
    channel surface becomes the direct_role_dispatch capability that
    docs 48/75 and operator_contract forbid.
    """
    if not instance_id:
        return None
    for member in _members(channel):
        if _worker_session(member) != instance_id:
            continue
        if _member_can_receive(member, sender_member_id):
            return member
        return None
    return None


def _member_by_id(channel: dict[str, Any], member_id: str) -> dict[str, Any]:
    for member in _members(channel):
        if str(member.get("member_id") or "") == member_id:
            return member
    return {}


def _member_busy(channel: dict[str, Any], member_id: str) -> bool:
    for item in channel.get("reply_requests") or []:
        if str(item.get("target_member_id") or "") != member_id:
            continue
        if str(item.get("status") or "") in BUSY_REPLY_STATUSES:
            return True
    return False


def _supersede_queued_replies(
    writer: EventWriter,
    *,
    channel: dict[str, Any],
    channel_id: str,
    target_member_id: str,
    actor: str,
    source: str,
    causation_id: str,
    task_id: str | None,
) -> None:
    for item in channel.get("reply_requests") or []:
        if str(item.get("target_member_id") or "") != target_member_id:
            continue
        if str(item.get("status") or "") != "queued":
            continue
        writer.emit(
            "channel.agent.reply.failed",
            actor=actor,
            task_id=task_id,
            causation_id=causation_id,
            correlation_id=channel_id,
            payload={
                "channel_id": channel_id,
                "thread_id": str(item.get("thread_id") or "main"),
                "request_id": str(item.get("request_id") or ""),
                "message_id": str(item.get("message_id") or ""),
                "target_member_id": target_member_id,
                "reason": "superseded by latest queued mention",
                **provider_run_fields_for_request(channel_id, item),
                "source": source,
            },
        )


def _has_duplicate_request(channel: dict[str, Any], message_id: str, target_member_id: str) -> bool:
    for item in channel.get("reply_requests") or []:
        if (
            str(item.get("message_id") or "") == message_id
            and str(item.get("target_member_id") or "") == target_member_id
        ):
            return True
    return False


def _worker_session(member: dict[str, Any]) -> str:
    return str(
        member.get("backing_worker_session_id")
        or member.get("worker_session_id")
        or member.get("instance_id")
        or "",
    ).strip()


def _is_spine_reviewer(member: dict[str, Any]) -> bool:
    return _normalize_token(member.get("channel_role")) == "spinereviewer"


def _stable_reply_request_id(
    channel_id: str,
    thread_id: str,
    message_id: str,
    target_member_id: str,
) -> str:
    digest = hashlib.sha1(
        f"{channel_id}:{thread_id}:{message_id}:{target_member_id}".encode("utf-8"),
    ).hexdigest()[:16]
    return f"reply-{digest}"


def _stable_spine_review_request_id(
    channel_id: str,
    thread_id: str,
    message_id: str,
    target_member_id: str,
) -> str:
    digest = hashlib.sha1(
        f"spine:{channel_id}:{thread_id}:{message_id}:{target_member_id}".encode("utf-8"),
    ).hexdigest()[:16]
    return f"spine-{digest}"


def _normalize_token(value: object) -> str:
    raw = str(value or "").strip().lower().lstrip("@")
    return re.sub(r"[^a-z0-9]+", "", raw)


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, list | tuple | set):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []
