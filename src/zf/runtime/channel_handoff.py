"""Explicit handoff guard for Agent Channel."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from zf.core.config.schema import ZfConfig
from zf.core.events import EventWriter
from zf.runtime.channel_adapter import dispatch_reply_request
from zf.runtime.channel_context import build_channel_context_pack
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_run_owner import provider_run_fields
from zf.runtime.channel_sidecar import channel_context_pack_event_payload
from zf.runtime.openclaw_provider import OpenClawGatewayClient


@dataclass(frozen=True)
class HandoffResult:
    targets: list[str] = field(default_factory=list)
    reply_requests: list[str] = field(default_factory=list)
    queued: list[str] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "targets": self.targets,
            "reply_requests": self.reply_requests,
            "queued": self.queued,
            "skipped": self.skipped,
        }


def request_channel_handoff(
    *,
    state_dir: Path,
    writer: EventWriter,
    channel_id: str,
    thread_id: str,
    message_id: str,
    member_id: str,
    target_member_id: str,
    reason: str,
    actor: str,
    source: str,
    depth: int = 0,
    round_no: int = 0,
    max_depth: int = 3,
    max_rounds: int = 6,
    project_root: Path | None = None,
    headless_backends: dict[str, Any] | None = None,
    config: ZfConfig | None = None,
    openclaw_client: OpenClawGatewayClient | None = None,
) -> HandoffResult:
    channel = project_channel(Path(state_dir), channel_id) or {}
    requested = writer.emit(
        "channel.handoff.requested",
        actor=actor,
        causation_id=message_id if message_id.startswith("evt-") else None,
        correlation_id=channel_id,
        payload={
            "channel_id": channel_id,
            "thread_id": thread_id or "main",
            "message_id": message_id,
            "member_id": member_id,
            "target_member_id": target_member_id,
            "reason": reason,
            "depth": depth,
            "round": round_no,
            "source": source,
        },
    )
    reject_reason = _handoff_reject_reason(
        channel,
        member_id=member_id,
        target_member_id=target_member_id,
        message_id=message_id,
        depth=depth,
        round_no=round_no,
        max_depth=max_depth,
        max_rounds=max_rounds,
    )
    if reject_reason:
        writer.emit(
            "channel.handoff.rejected",
            actor=actor,
            causation_id=requested.id,
            correlation_id=channel_id,
            payload={
                "channel_id": channel_id,
                "thread_id": thread_id or "main",
                "message_id": message_id,
                "member_id": member_id,
                "target_member_id": target_member_id,
                "reason": reject_reason,
                "depth": depth,
                "round": round_no,
                "source": source,
            },
        )
        return HandoffResult(skipped=[{"target_member_id": target_member_id, "reason": reject_reason}])

    accepted = writer.emit(
        "channel.handoff.accepted",
        actor=actor,
        causation_id=requested.id,
        correlation_id=channel_id,
        payload={
            "channel_id": channel_id,
            "thread_id": thread_id or "main",
            "message_id": message_id,
            "member_id": member_id,
            "target_member_id": target_member_id,
            "reason": reason,
            "depth": depth,
            "round": round_no,
            "source": source,
        },
    )
    member = _member_by_id(channel, target_member_id)
    context_pack = build_channel_context_pack(
        channel,
        channel_id=channel_id,
        thread_id=thread_id or "main",
        target_member_id=target_member_id,
        trigger_message_id=message_id,
    )
    writer.emit(
        "channel.context_pack.built",
        actor=actor,
        causation_id=accepted.id,
        correlation_id=channel_id,
        payload=channel_context_pack_event_payload(
            Path(state_dir),
            {**context_pack, "source": source},
            created_by=f"channel-handoff:{source}",
            source_event_id=accepted.id,
        ),
    )
    request_id = _stable_reply_request_id(channel_id, thread_id or "main", message_id, target_member_id)
    busy = _member_busy(channel, target_member_id)
    reply = writer.emit(
        "channel.agent.reply.requested",
        actor=actor,
        causation_id=accepted.id,
        correlation_id=channel_id,
        payload={
            "channel_id": channel_id,
            "thread_id": thread_id or "main",
            "request_id": request_id,
            "message_id": message_id,
            "target_member_id": target_member_id,
            "member_id": member_id,
            "status": "queued" if busy else "pending",
            "queue_state": "latest_only" if busy else "ready",
            "context_pack_id": context_pack["context_pack_id"],
            "member_type": str(member.get("member_type") or ""),
            "backend": str(member.get("backend") or ""),
            "worker_session_id": _worker_session(member),
            # P0.2: thread the handoff event id through so that adapter
            # failure callbacks (channel_adapter._emit_headless_failed) can
            # write channel.handoff.failed and unstick the handoff state
            # machine instead of leaving it suspended at "accepted".
            "handoff_request_event_id": requested.id,
            **provider_run_fields(
                channel_id=channel_id,
                thread_id=thread_id or "main",
                request_id=request_id,
                target_member_id=target_member_id,
            ),
            "source": source,
        },
    )
    if not busy:
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
    return HandoffResult(
        targets=[target_member_id],
        reply_requests=[reply.id],
        queued=[target_member_id] if busy else [],
    )


def _handoff_reject_reason(
    channel: dict[str, Any],
    *,
    member_id: str,
    target_member_id: str,
    message_id: str,
    depth: int,
    round_no: int,
    max_depth: int,
    max_rounds: int,
) -> str:
    if depth > max_depth:
        return "handoff depth exceeded"
    if round_no > max_rounds:
        return "handoff round budget exceeded"
    if member_id == target_member_id:
        return "self handoff is not allowed"
    member = _member_by_id(channel, target_member_id)
    if not _member_can_receive(member, member_id):
        return "target member cannot receive handoff"
    if _has_duplicate_request(channel, message_id, target_member_id):
        return "duplicate handoff target"
    return ""


def _member_by_id(channel: dict[str, Any], member_id: str) -> dict[str, Any]:
    for member in channel.get("members") or []:
        if isinstance(member, dict) and str(member.get("member_id") or "") == member_id:
            return member
    return {}


def _member_can_receive(member: dict[str, Any], sender_member_id: str) -> bool:
    member_id = str(member.get("member_id") or "")
    if not member_id or member_id == sender_member_id:
        return False
    if str(member.get("status") or "").lower() in {"removed", "suspended", "rejected", "failed"}:
        return False
    permissions = _string_list(member.get("permissions"))
    return not permissions or "message" in permissions


def _member_busy(channel: dict[str, Any], member_id: str) -> bool:
    for item in channel.get("reply_requests") or []:
        if str(item.get("target_member_id") or "") == member_id and str(item.get("status") or "") in {"pending", "running", "started"}:
            return True
    return False


def _has_duplicate_request(channel: dict[str, Any], message_id: str, target_member_id: str) -> bool:
    for item in channel.get("reply_requests") or []:
        if str(item.get("message_id") or "") == message_id and str(item.get("target_member_id") or "") == target_member_id:
            return True
    return False


def _worker_session(member: dict[str, Any]) -> str:
    return str(member.get("backing_worker_session_id") or member.get("worker_session_id") or member.get("instance_id") or "").strip()


def _stable_reply_request_id(channel_id: str, thread_id: str, message_id: str, target_member_id: str) -> str:
    return "reply-" + hashlib.sha1(f"{channel_id}:{thread_id}:{message_id}:{target_member_id}".encode("utf-8")).hexdigest()[:16]


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, list | tuple | set):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []
