"""Channel-specific OpenClaw provider lifecycle helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from zf.core.config.schema import ZfConfig
from zf.core.events import EventWriter
from zf.core.security.redaction import redact_obj
from zf.runtime.channel_contracts import (
    normalize_permission_profile,
    permission_profile_write_policy,
)
from zf.runtime.channel_sidecar import channel_message_event_payload
from zf.runtime.openclaw_provider import (
    OpenClawGatewayClient,
    build_openclaw_agent_descriptor,
    resolve_openclaw_binding,
)


@dataclass(frozen=True)
class OpenClawMemberConnectResult:
    ok: bool
    reason: str = ""
    provider_binding_id: str = ""
    remote_agent_id: str = ""
    provider_session_id: str = ""
    capabilities: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OpenClawReplyResult:
    ok: bool
    reason: str = ""
    provider_session_id: str = ""
    provider_binding_id: str = ""
    remote_agent_id: str = ""


def prepare_openclaw_member_connection(
    *,
    config: ZfConfig | None,
    state_dir: Path,
    writer: EventWriter,
    actor: str,
    causation_id: str,
    channel_id: str,
    member_id: str,
    display_name: str,
    channel_role: str,
    permissions: list[str],
    requested_binding_id: str,
    source: str,
    model: str = "",
    client: OpenClawGatewayClient | None = None,
) -> OpenClawMemberConnectResult:
    binding, binding_error = resolve_openclaw_binding(config, requested_binding_id)
    if binding_error or binding is None:
        return OpenClawMemberConnectResult(
            ok=False,
            reason=binding_error,
            provider_binding_id=requested_binding_id,
        )

    descriptor = build_openclaw_agent_descriptor(
        binding=binding,
        project_name=str(
            getattr(config.project, "name", "") if config else "project"
        ) or "project",
        state_dir=state_dir,
        channel_id=channel_id,
        member_id=member_id,
        display_name=display_name,
        channel_role=channel_role,
        permissions=permissions,
        model=model,
    )
    gateway = client or OpenClawGatewayClient()
    preflight = gateway.preflight(binding)
    if not preflight.ok:
        _emit_provider_health(
            writer,
            actor=actor,
            channel_id=channel_id,
            causation_id=causation_id,
            status="blocked",
            reason=preflight.reason or preflight.status,
            binding_id=binding.id,
            member_id=member_id,
            source=source,
        )
        return OpenClawMemberConnectResult(
            ok=False,
            reason=preflight.reason or preflight.status,
            provider_binding_id=binding.id,
        )

    provision = gateway.ensure_agent(binding, descriptor)
    if not provision.ok:
        _emit_provider_health(
            writer,
            actor=actor,
            channel_id=channel_id,
            causation_id=causation_id,
            status="degraded",
            reason=provision.reason or provision.status,
            binding_id=binding.id,
            member_id=member_id,
            source=source,
        )
        return OpenClawMemberConnectResult(
            ok=False,
            reason=provision.reason or provision.status,
            provider_binding_id=binding.id,
        )

    remote_agent_id = str(descriptor.get("id") or "")
    provider_session_id = (
        provision.provider_session_id
        or preflight.provider_session_id
        or f"openclaw:{binding.id}:{remote_agent_id}"
    )
    _emit_provider_health(
        writer,
        actor=actor,
        channel_id=channel_id,
        causation_id=causation_id,
        status="healthy",
        reason="preflight_ok",
        binding_id=binding.id,
        member_id=member_id,
        source=source,
    )
    capabilities = {
        "binding_id": binding.id,
        "remote_agent_id": remote_agent_id,
        "supports_remote_gateway": True,
        "tool_profile": binding.tool_profile,
        "workspace_policy": binding.default_workspace_policy,
        "provision_agent": binding.provision_agent,
    }
    return OpenClawMemberConnectResult(
        ok=True,
        provider_binding_id=binding.id,
        remote_agent_id=remote_agent_id,
        provider_session_id=provider_session_id,
        capabilities=capabilities,
    )


def dispatch_openclaw_channel_reply(
    *,
    state_dir: Path,
    writer: EventWriter,
    config: ZfConfig | None,
    channel: dict[str, Any],
    member: dict[str, Any],
    message: dict[str, Any],
    request: dict[str, Any],
    request_id: str,
    started_event_id: str,
    actor: str,
    source: str,
    client: OpenClawGatewayClient | None = None,
) -> OpenClawReplyResult:
    channel_id = str(channel.get("channel_id") or request.get("channel_id") or "")
    binding_id = str(
        member.get("provider_binding_id")
        or request.get("provider_binding_id")
        or ""
    )
    binding, binding_error = resolve_openclaw_binding(config, binding_id)
    if binding_error or binding is None:
        _emit_openclaw_failed(
            writer=writer,
            request=request,
            request_id=request_id,
            started_event_id=started_event_id,
            actor=actor,
            source=source,
            channel_id=channel_id,
            reason=binding_error,
            provider_session_id=str(member.get("provider_session_id") or ""),
            provider_binding_id=binding_id,
            remote_agent_id=str(member.get("remote_agent_id") or ""),
        )
        return OpenClawReplyResult(ok=False, reason=binding_error)

    descriptor = build_openclaw_agent_descriptor(
        binding=binding,
        project_name=str(
            getattr(config.project, "name", "") if config else "project"
        ) or "project",
        state_dir=state_dir,
        channel_id=channel_id,
        member_id=str(member.get("member_id") or request.get("target_member_id") or ""),
        display_name=str(member.get("display_name") or member.get("persona") or ""),
        channel_role=str(member.get("channel_role") or member.get("role") or ""),
        permissions=[
            str(item)
            for item in (member.get("permissions") or [])
            if str(item).strip()
        ],
        model=str(member.get("model") or ""),
    )
    remote_agent_id = str(member.get("remote_agent_id") or descriptor.get("id") or "")
    provider_session_id = str(member.get("provider_session_id") or "")
    gateway = client or OpenClawGatewayClient()
    try:
        result = gateway.run_turn(
            binding,
            agent_id=remote_agent_id,
            prompt=_build_channel_prompt(
                channel=channel,
                member=member,
                message=message,
                request=request,
            ),
            system_prompt=_build_channel_system_prompt(member),
            timeout_seconds=binding.timeout_seconds,
            metadata={
                "channel_id": channel_id,
                "thread_id": str(request.get("thread_id") or "main"),
                "request_id": request_id,
                "target_member_id": str(request.get("target_member_id") or ""),
                "binding_id": binding.id,
                "remote_agent_id": remote_agent_id,
            },
        )
    except Exception as exc:
        result = None
        reason = str(exc)
    else:
        reason = result.reason or result.status

    if result is None or not result.ok:
        provider_session_id = (
            str(getattr(result, "provider_session_id", "") or "")
            or provider_session_id
        )
        _emit_provider_health(
            writer,
            actor=actor,
            channel_id=channel_id,
            causation_id=started_event_id,
            status="degraded",
            reason=reason,
            binding_id=binding.id,
            member_id=str(member.get("member_id") or request.get("target_member_id") or ""),
            source=source,
        )
        _emit_openclaw_failed(
            writer=writer,
            request=request,
            request_id=request_id,
            started_event_id=started_event_id,
            actor=actor,
            source=source,
            channel_id=channel_id,
            reason=reason,
            provider_session_id=provider_session_id,
            provider_binding_id=binding.id,
            remote_agent_id=remote_agent_id,
        )
        return OpenClawReplyResult(
            ok=False,
            reason=reason,
            provider_session_id=provider_session_id,
            provider_binding_id=binding.id,
            remote_agent_id=remote_agent_id,
        )

    provider_session_id = result.provider_session_id or provider_session_id
    reply = result.reply.strip() if result.reply else ""
    if not reply:
        reply = "(OpenClaw completed without text)"
    thread_id = str(request.get("thread_id") or "main")
    reply_payload = channel_message_event_payload(Path(state_dir), {
        "channel_id": channel_id,
        "thread_id": thread_id,
        "message_id": f"msg-{request_id}-reply",
        "member_id": str(request.get("target_member_id") or ""),
        "role": "assistant",
        "source": "openclaw",
        "text": reply,
        "mentions": [],
        "refs": {
            "request_id": request_id,
            "provider_session_id": provider_session_id,
            "provider_binding_id": binding.id,
            "remote_agent_id": remote_agent_id,
            "usage": result.usage,
        },
    }, created_by=f"channel-openclaw:{source}", source_event_id=started_event_id)
    message_event = writer.emit(
        "channel.message.posted",
        actor=str(request.get("target_member_id") or actor),
        task_id=str(request.get("task_id") or "") or None,
        causation_id=started_event_id,
        correlation_id=channel_id,
        payload=redact_obj(reply_payload),
    )
    writer.emit(
        "channel.agent.reply.completed",
        actor=actor,
        task_id=str(request.get("task_id") or "") or None,
        causation_id=message_event.id,
        correlation_id=channel_id,
        payload=redact_obj({
            "channel_id": channel_id,
            "thread_id": thread_id,
            "request_id": request_id,
            "message_id": str(request.get("message_id") or ""),
            "target_member_id": str(request.get("target_member_id") or ""),
            "context_pack_id": str(request.get("context_pack_id") or ""),
            "provider_session_id": provider_session_id,
            "provider_binding_id": binding.id,
            "remote_agent_id": remote_agent_id,
            "reason": "openclaw provider completed",
            "source": source,
        }),
    )
    _emit_provider_health(
        writer,
        actor=actor,
        channel_id=channel_id,
        causation_id=message_event.id,
        status="healthy",
        reason="reply_completed",
        binding_id=binding.id,
        member_id=str(member.get("member_id") or request.get("target_member_id") or ""),
        source=source,
    )
    return OpenClawReplyResult(
        ok=True,
        provider_session_id=provider_session_id,
        provider_binding_id=binding.id,
        remote_agent_id=remote_agent_id,
    )


def _emit_provider_health(
    writer: EventWriter,
    *,
    actor: str,
    channel_id: str,
    causation_id: str,
    status: str,
    reason: str,
    binding_id: str,
    member_id: str,
    source: str,
) -> None:
    writer.emit(
        "provider.health.changed",
        actor=actor,
        causation_id=causation_id,
        correlation_id=channel_id,
        payload=redact_obj({
            "backend": "openclaw",
            "status": status,
            "reason": reason,
            "binding_id": binding_id,
            "channel_id": channel_id,
            "member_id": member_id,
            "source": source,
        }),
    )


def _emit_openclaw_failed(
    *,
    writer: EventWriter,
    request: dict[str, Any],
    request_id: str,
    started_event_id: str,
    actor: str,
    source: str,
    channel_id: str,
    reason: str,
    provider_session_id: str,
    provider_binding_id: str,
    remote_agent_id: str,
) -> None:
    writer.emit(
        "channel.agent.reply.failed",
        actor=actor,
        task_id=str(request.get("task_id") or "") or None,
        causation_id=started_event_id,
        correlation_id=channel_id,
        payload=redact_obj({
            "channel_id": channel_id,
            "thread_id": str(request.get("thread_id") or "main"),
            "request_id": request_id,
            "message_id": str(request.get("message_id") or ""),
            "target_member_id": str(request.get("target_member_id") or ""),
            "context_pack_id": str(request.get("context_pack_id") or ""),
            "provider_session_id": provider_session_id,
            "provider_binding_id": provider_binding_id,
            "remote_agent_id": remote_agent_id,
            "reason": reason,
            "source": source,
        }),
    )


def _build_channel_system_prompt(member: dict[str, Any]) -> str:
    member_id = str(member.get("member_id") or "agent")
    role = str(member.get("channel_role") or member.get("role") or "channel member")
    permission_profile = normalize_permission_profile(member.get("permission_profile"))
    write_policy = (
        member.get("write_policy")
        if isinstance(member.get("write_policy"), dict)
        else permission_profile_write_policy(permission_profile)
    )
    return (
        f"You are {member_id}, an OpenClaw agent participating in a ZaoFu "
        f"Agent Channel. Your channel role is {role}. Reply as a channel "
        "teammate. Keep the answer concise, grounded in the provided channel "
        "context, and do not mutate runtime state directly. "
        "Do not include step-by-step progress narration such as 'I will check' "
        "or 'I will write' in the final reply; ZaoFu renders runtime activity "
        "separately. Use concise Markdown. When reporting completed work, "
        "prefer sections like Result, Path, Changes, Not changed, Risks, and "
        "Next step when applicable. "
        f"Your channel permission_profile is {permission_profile}. "
        f"Write policy: {redact_obj(write_policy)}. If work should be "
        "executed by ZaoFu, recommend a controlled workflow/action request. "
        "Only write files when the permission_profile and write policy explicitly allow it."
    )


def _build_channel_prompt(
    *,
    channel: dict[str, Any],
    member: dict[str, Any],
    message: dict[str, Any],
    request: dict[str, Any],
) -> str:
    context_pack = _context_pack_by_id(channel, str(request.get("context_pack_id") or ""))
    channel_id = str(channel.get("channel_id") or request.get("channel_id") or "")
    recent = [
        {
            "member_id": item.get("member_id"),
            "role": item.get("role"),
            "text": str(item.get("text") or item.get("summary") or "")[:1000],
        }
        for item in (channel.get("messages") or channel.get("recent_messages") or [])[-8:]
        if isinstance(item, dict)
    ]
    return "\n".join([
        "ZaoFu Agent Channel reply request",
        f"channel_id: {channel_id}",
        f"thread_id: {request.get('thread_id') or 'main'}",
        f"target_member_id: {request.get('target_member_id') or member.get('member_id') or ''}",
        f"channel_role: {member.get('channel_role') or member.get('role') or ''}",
        f"visibility_profile: {member.get('visibility_profile') or ''}",
        f"permission_profile: {normalize_permission_profile(member.get('permission_profile'))}",
        f"write_policy: {redact_obj(member.get('write_policy') if isinstance(member.get('write_policy'), dict) else permission_profile_write_policy(member.get('permission_profile')))}",
        f"context_pack: {redact_obj(context_pack)}",
        f"recent_messages: {redact_obj(recent)}",
        "",
        "Trigger message:",
        str(message.get("text") or message.get("message") or ""),
    ])


def _context_pack_by_id(channel: dict[str, Any], context_pack_id: str) -> dict[str, Any]:
    if not context_pack_id:
        return {}
    for item in channel.get("context_packs") or []:
        if isinstance(item, dict) and str(item.get("context_pack_id") or "") == context_pack_id:
            return item
    raw = channel.get("context_packs")
    if isinstance(raw, dict):
        item = raw.get(context_pack_id)
        if isinstance(item, dict):
            return item
    return {}
